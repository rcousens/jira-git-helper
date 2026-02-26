from __future__ import annotations

import json
import shutil
import subprocess

import click
import requests
from jira import JIRA, JIRAError

from .config import (
    get_config,
    get_projects,
    get_jql_for_project,
    get_effective_filter_name,
    _FALLBACK_JQL,
)

# --- JIRA field caches ---

_field_id_by_name: dict[str, str] = {}   # lower display name → field id
_field_name_by_id: dict[str, str] = {}   # field id → display name

# --- Rich style dicts ---

STATUS_STYLES: dict[str, str] = {
    "to do":        "white",
    "in progress":  "bold blue",
    "in review":    "bold yellow",
    "done":         "bold green",
    "closed":       "bold green",
    "build":        "bold cyan",
    "blocked":      "bold red",
}

PRIORITY_STYLES: dict[str, str] = {
    "highest":  "bold red",
    "critical": "bold red",
    "high":     "red",
    "medium":   "yellow",
    "low":      "green",
    "lowest":   "dim green",
}

PR_STATUS_STYLES = {
    "OPEN":     "bold green",
    "DRAFT":    "bold yellow",
    "MERGED":   "bold blue",
    "DECLINED": "bold red",
}

# --- JIRA helpers ---


def get_jira_server() -> str:
    server = get_config("server")
    if not server:
        raise click.ClickException(
            "JIRA server not configured. Run: jg config set server https://yourcompany.atlassian.net"
        )
    return server.rstrip("/")


def get_jira_client() -> JIRA:
    server = get_jira_server()
    token = get_config("token")
    if not token:
        raise click.ClickException(
            "JIRA token not configured. Run: jg config set token <api-token>"
        )
    email = get_config("email")
    if not email:
        raise click.ClickException(
            "JIRA email not configured. Run: jg config set email you@example.com"
        )
    return JIRA(server=server, basic_auth=(email, token))


def ensure_fields_cached(jira_client: JIRA) -> None:
    if _field_name_by_id:
        return
    for field in jira_client.fields():
        _field_id_by_name[field["name"].lower()] = field["id"]
        _field_name_by_id[field["id"]] = field["name"]


def get_jira_field_id(jira_client: JIRA, field_name: str) -> str | None:
    ensure_fields_cached(jira_client)
    return _field_id_by_name.get(field_name.lower())


def get_jira_field_name(field_id: str) -> str:
    return _field_name_by_id.get(field_id, field_id)


def fetch_issues_for_projects(
    jira: JIRA,
    projects: list[str],
    max_results: int,
    extra_fields: list[str] | None = None,
) -> list:
    """Fetch issues across all configured projects, returning a merged list.

    Strategy:
    - 0 projects: use _FALLBACK_JQL (single query)
    - 1 project: use get_jql_for_project() (single query)
    - Multiple projects, none with an active filter: build a combined OR query
      so JIRA handles sorting in a single round-trip
    - Multiple projects, any with an active filter: run one query per project
      and merge/deduplicate in Python
    """
    fields = ["summary", "status", "assignee", "priority", "parent", "issuetype"] + (extra_fields or [])

    if not projects:
        return list(jira.search_issues(_FALLBACK_JQL, maxResults=max_results, fields=fields))

    if len(projects) == 1:
        return list(jira.search_issues(
            get_jql_for_project(projects[0]), maxResults=max_results, fields=fields
        ))

    # Multiple projects
    has_custom_jql = any(get_effective_filter_name(p) for p in projects)

    if not has_custom_jql:
        project_clause = " OR ".join(f"project = {p}" for p in projects)
        combined_jql = f"({project_clause}) AND assignee = currentUser() ORDER BY updated DESC"
        return list(jira.search_issues(combined_jql, maxResults=max_results, fields=fields))

    # Per-project queries — merge and deduplicate, preserving insertion order
    seen: set[str] = set()
    merged = []
    per_project_max = max(50, max_results // len(projects))
    for project in projects:
        for issue in jira.search_issues(
            get_jql_for_project(project), maxResults=per_project_max, fields=fields
        ):
            if issue.key not in seen:
                seen.add(issue.key)
                merged.append(issue)
    return merged


def get_prs(issue_id: str) -> list[dict]:
    """Fetch linked GitHub PRs via the JIRA dev-status API."""
    server = get_jira_server()
    token = get_config("token")
    email = get_config("email")
    r = requests.get(
        f"{server}/rest/dev-status/1.0/issue/details",
        params={"issueId": issue_id, "applicationType": "GitHub", "dataType": "pullrequest"},
        auth=(email, token),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    prs: list[dict] = []
    for detail in r.json().get("detail", []):
        prs.extend(detail.get("pullRequests", []))
    return prs


_GH_STATE_MAP = {"OPEN": "OPEN", "MERGED": "MERGED", "CLOSED": "DECLINED"}


def get_gh_prs(ticket: str) -> list[dict]:
    """Fetch PRs from GitHub CLI matching *ticket* and return in JIRA PR dict shape."""
    if not shutil.which("gh"):
        return []
    result = subprocess.run(
        ["gh", "pr", "list", "--search", ticket, "--state", "all",
         "--json", "title,url,author,headRefName,state,updatedAt"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        items = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    prs: list[dict] = []
    for item in items:
        prs.append({
            "status": _GH_STATE_MAP.get(item.get("state", "").upper(), "OPEN"),
            "author": {"name": item.get("author", {}).get("login", "")},
            "repositoryName": "",
            "source": {"branch": item.get("headRefName", "")},
            "name": item.get("title", ""),
            "url": item.get("url", ""),
            "lastUpdate": item.get("updatedAt", ""),
        })
    return prs


def get_default_jql() -> str:
    """Return the JQL to use when no project has been explicitly selected.

    Resolution order:
      1. Per-project JQL via get_jql_for_project() when exactly one project is configured
      2. _FALLBACK_JQL (assigned to currentUser, no project filter)

    When multiple projects are configured, callers should prompt the user to pick
    a project first and then call get_jql_for_project() directly.
    """
    projects = get_projects()
    if len(projects) == 1:
        return get_jql_for_project(projects[0])
    return _FALLBACK_JQL
