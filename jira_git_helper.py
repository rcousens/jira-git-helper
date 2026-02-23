import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

try:
    __version__ = _pkg_version("jira-git-helper")
except PackageNotFoundError:
    __version__ = "unknown"

import requests

import click
from jira import JIRA, JIRAError
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static, Tree

# --- paths ---

STATE_FILE = Path.home() / ".local" / "share" / "jira-git-helper" / "ticket"
CONFIG_FILE = Path.home() / ".config" / "jira-git-helper" / "config"

# Generic JQL used when none is set in config
_FALLBACK_JQL = "assignee = currentUser() ORDER BY updated DESC"

# --- state helpers ---


def get_ticket() -> str | None:
    # Prefer the shell env var (set by the fish hook) so multiple shells stay independent
    if env := os.environ.get("JG_TICKET"):
        return env or None
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip() or None
    return None


def save_ticket(ticket: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(ticket)


def clear_ticket() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def ensure_ticket() -> str:
    """Return the current ticket. If none is set, show the interactive picker.

    Tickets are fetched from all configured projects and shown in a single merged list.
    """
    ticket = get_ticket()
    if ticket:
        return ticket

    jira = get_jira_client()
    click.echo("No ticket set — fetching tickets…", err=True)

    try:
        issues = fetch_issues_for_projects(jira, get_projects(), max_results=200)
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    if not issues:
        raise click.ClickException("No issues found.")

    app = JiraListApp(issues)
    app.run()

    if not app.selected_ticket:
        click.echo("No ticket selected.", err=True)
        sys.exit(1)

    save_ticket(app.selected_ticket)
    click.echo(f"Ticket set to {app.selected_ticket}", err=True)
    return app.selected_ticket


# --- config helpers ---


def _read_config() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    config: dict[str, str] = {}
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


def _write_config(config: dict[str, str]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        "\n".join(f"{k}={v}" for k, v in sorted(config.items())) + "\n"
    )


def get_config(key: str) -> str | None:
    return _read_config().get(key)


def set_config(key: str, value: str) -> None:
    config = _read_config()
    config[key] = value
    _write_config(config)


def get_projects() -> list[str]:
    """Return the list of configured project keys (from the comma-separated 'projects' config)."""
    raw = get_config("projects") or ""
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def get_fields_for_project(project: str) -> list[str]:
    """Return extra field IDs configured for a project via fields.<PROJECT> config key."""
    raw = get_config(f"fields.{project}") or ""
    return [f.strip() for f in raw.split(",") if f.strip()]


def get_filters_for_project(project: str) -> list[dict]:
    """Return saved named filters for a project as a list of {name, jql} dicts."""
    raw = get_config(f"filters.{project}") or "[]"
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []


def set_filters_for_project(project: str, filters: list[dict]) -> None:
    """Persist the named filter list for a project."""
    set_config(f"filters.{project}", json.dumps(filters, separators=(",", ":")))


def get_active_filter_name(project: str) -> str | None:
    """Return the persisted default filter name for a project, or None."""
    return get_config(f"filters.{project}.default") or None


def set_active_filter_name(project: str, name: str | None) -> None:
    """Set (or clear) the persisted default filter name for a project."""
    config = _read_config()
    key = f"filters.{project}.default"
    if name:
        config[key] = name
    else:
        config.pop(key, None)
    _write_config(config)


def get_formatters() -> list[dict]:
    """Return all configured formatters as a list of {name, glob, cmd} dicts."""
    raw = get_config("fmt") or "[]"
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []


def set_formatters(formatters: list[dict]) -> None:
    """Persist the formatter list."""
    set_config("fmt", json.dumps(formatters, separators=(",", ":")))


# Session-level active filter overrides (not persisted — live for the process lifetime).
# Maps project key → filter name (or None to explicitly use no filter this session).
# Key absent → fall back to config default.
_session_active_filters: dict[str, str | None] = {}


def get_effective_filter_name(project: str) -> str | None:
    """Return the currently active filter name for a project (session > config default)."""
    if project in _session_active_filters:
        return _session_active_filters[project]
    return get_active_filter_name(project)


def get_jql_for_project(project: str) -> str:
    """Return the JQL for a specific project key.

    Resolution order:
      1. Session-active filter (set via Enter in the filter picker — not persisted)
      2. Persisted default filter (set via Space in the filter picker)
      3. Built-in default: project = PROJECT AND assignee = currentUser() ORDER BY updated DESC
    """
    active_name = get_effective_filter_name(project)
    if active_name:
        for f in get_filters_for_project(project):
            if f["name"] == active_name:
                return f["jql"]
    return f"project = {project} AND assignee = currentUser() ORDER BY updated DESC"


# --- JIRA helpers ---


def get_jira_server() -> str:
    server = get_config("server")
    if not server:
        raise click.ClickException(
            "JIRA server not configured. Run: jg config set server https://yourcompany.atlassian.net"
        )
    return server.rstrip("/")


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            result = subprocess.run(cmd, input=text.encode(), capture_output=True)
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


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


_field_id_by_name: dict[str, str] = {}   # lower display name → field id
_field_name_by_id: dict[str, str] = {}   # field id → display name


def _ensure_fields_cached(jira_client: JIRA) -> None:
    if _field_name_by_id:
        return
    for field in jira_client.fields():
        _field_id_by_name[field["name"].lower()] = field["id"]
        _field_name_by_id[field["id"]] = field["name"]


def _get_jira_field_id(jira_client: JIRA, field_name: str) -> str | None:
    _ensure_fields_cached(jira_client)
    return _field_id_by_name.get(field_name.lower())


def _get_jira_field_name(field_id: str) -> str:
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


# --- TUI ---


def _context_bar_text() -> str:
    """Return a one-line context string showing the active ticket and current branch."""
    ticket = get_ticket() or "—"
    branch = _get_current_branch() or "—"
    return f"  ticket: {ticket}   branch: {branch}"


class JiraListApp(App):
    CSS = """
    Screen { background: #0a0e0a; }
    .context-bar {
        height: 1;
        background: #0d1a0d;
        color: #00ff41;
        padding: 0 1;
        text-style: bold;
    }
    DataTable {
        height: 1fr;
        background: #0a0e0a;
    }
    DataTable > .datatable--header {
        background: #0d1a0d;
        color: #00e5ff;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #003d00;
        color: #00ff41;
        text-style: bold;
    }
    DataTable > .datatable--hover { background: #001a00; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    Tree {
        height: 1fr;
        background: #0a0e0a;
        display: none;
        padding: 0 1;
        color: #b8d4b8;
    }
    Tree > .tree--cursor {
        background: #003d00;
        text-style: bold;
    }
    Tree > .tree--guides       { color: #1a3a1a; }
    Tree > .tree--guides-hover { color: #2a5a2a; }
    #filter-bar {
        display: none;
        border: tall #00ff41;
        background: #0d1a0d;
        color: #00ff41;
    }
    #filter-status {
        height: 1;
        background: #0d1a0d;
        color: #ffb300;
        padding: 0 1;
        display: none;
    }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("enter", "select_ticket", "Select", show=True),
        Binding("i", "show_info", "Info", show=True),
        Binding("o", "open_ticket", "Open", show=True),
        Binding("c", "copy_url", "Copy URL", show=True),
        Binding("d", "show_fields", "Fields", show=True),
        Binding("f", "show_filters", "Filters", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("t", "toggle_tree", "Tree", show=True),
        Binding("slash", "activate_filter", "Filter", show=True),
    ]

    def __init__(
        self,
        issues: list,
        jira_client=None,
        extra_field_ids: list[str] | None = None,
        field_names: dict[str, str] | None = None,
        projects: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.all_issues = issues
        self.visible_keys: list[str] = [i.key for i in issues]
        self.selected_ticket: str | None = None
        self.jira_client = jira_client
        self.extra_field_ids: list[str] = extra_field_ids or []
        self.field_names: dict[str, str] = field_names or {}
        self.reload_needed: bool = False
        self.projects: list[str] = projects or []
        self._tree_mode: bool = False

    def compose(self) -> ComposeResult:
        yield Static(_context_bar_text(), classes="context-bar")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Tree("Issues", id="issue-tree")
        yield Static("", id="filter-status")
        yield Input(id="filter-bar", placeholder="Filter…")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Key", width=14)
        table.add_column("Status", width=16)
        table.add_column("Assignee", width=20)
        for fid in self.extra_field_ids:
            table.add_column(self.field_names.get(fid, fid), width=16)
        table.add_column("Summary")
        self._populate_table(self.all_issues)
        self._update_filter_status()
        table.focus()

    def _field_str(self, issue, field_id: str) -> str:
        val = getattr(issue.fields, field_id, None)
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            return ", ".join(str(getattr(v, "name", v)) for v in val)
        for attr in ("value", "name", "displayName"):
            if (s := getattr(val, attr, None)) is not None:
                return str(s)
        return str(val)

    def _populate_table(self, issues: list) -> None:
        from rich.text import Text
        table = self.query_one(DataTable)
        table.clear()
        self.visible_keys = []
        for issue in issues:
            assignee = (
                issue.fields.assignee.displayName
                if issue.fields.assignee
                else "Unassigned"
            )
            row = [
                Text(issue.key, style="bold #00e5ff"),
                Text(issue.fields.status.name, style="#ffb300"),
                Text(assignee, style="#b39ddb"),
            ]
            for fid in self.extra_field_ids:
                row.append(Text(self._field_str(issue, fid), style="#b8d4b8"))
            row.append(Text(issue.fields.summary, style="#b8d4b8"))
            table.add_row(*row, key=issue.key)
            self.visible_keys.append(issue.key)

    # --- tree helpers ---

    def _build_issue_tree(self) -> tuple[list, dict]:
        """Group all_issues into (roots, children_dict) using fields.parent (next-gen only)."""
        by_key = {i.key: i for i in self.all_issues}
        children: dict[str, list] = {}
        roots = []
        for issue in self.all_issues:
            parent = getattr(issue.fields, "parent", None)
            parent_key = parent.key if parent else None
            if parent_key and parent_key in by_key:
                children.setdefault(parent_key, []).append(issue)
            else:
                roots.append(issue)
        return roots, children

    def _issue_matches_filter(self, issue, query: str) -> bool:
        q = query.lower()
        return (
            q in issue.key.lower()
            or q in issue.fields.summary.lower()
            or q in (issue.fields.assignee.displayName.lower() if issue.fields.assignee else "")
            or q in issue.fields.status.name.lower()
            or any(q in self._field_str(issue, fid).lower() for fid in self.extra_field_ids)
        )

    def _branch_matches(self, issue, children: dict, query: str) -> bool:
        if not query:
            return True
        if self._issue_matches_filter(issue, query):
            return True
        return any(self._branch_matches(c, children, query) for c in children.get(issue.key, []))

    def _tree_node_label(self, issue, dim: bool = False):
        from rich.text import Text
        assignee = issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned"
        status = issue.fields.status.name
        summary = (issue.fields.summary or "")[:80]
        t = Text()
        t.append(issue.key, style="#4d8a4d" if dim else "bold #00e5ff")
        t.append("  ")
        t.append(summary, style="#4d8a4d" if dim else "#b8d4b8")
        t.append("  ")
        t.append(f"[{status}]", style="#665500" if dim else "#ffb300")
        t.append("  ")
        t.append(assignee, style="#3a3060" if dim else "#b39ddb")
        return t

    def _populate_tree(self, query: str = "") -> None:
        from rich.text import Text
        tree = self.query_one(Tree)
        tree.clear()
        # Relabel root to dim header
        tree.root.label = Text("issues", style="#1a3a1a")
        roots, children = self._build_issue_tree()
        for issue in roots:
            if self._branch_matches(issue, children, query):
                self._add_tree_node(tree.root, issue, children, query)
        tree.root.expand()

    def _add_tree_node(self, parent_node, issue, children: dict, query: str) -> None:
        matches_directly = not query or self._issue_matches_filter(issue, query)
        label = self._tree_node_label(issue, dim=not matches_directly)
        visible_children = [
            c for c in children.get(issue.key, [])
            if self._branch_matches(c, children, query)
        ]
        if visible_children:
            node = parent_node.add(label, data=issue, expand=True)
            for child in visible_children:
                self._add_tree_node(node, child, children, query)
        else:
            parent_node.add_leaf(label, data=issue)

    def action_toggle_tree(self) -> None:
        self._tree_mode = not self._tree_mode
        table = self.query_one(DataTable)
        tree = self.query_one(Tree)
        query = self.query_one("#filter-bar", Input).value
        if self._tree_mode:
            table.display = False
            tree.display = True
            self._populate_tree(query)
            tree.focus()
        else:
            tree.display = False
            table.display = True
            table.focus()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            self.selected_ticket = event.node.data.key
            self.exit()

    # --- end tree helpers ---

    def _active_key(self) -> str | None:
        """Return the key of the currently highlighted issue in whichever mode is active."""
        if self._tree_mode:
            tree = self.query_one(Tree)
            node = tree.cursor_node
            if node and node.data:
                return node.data.key
            return None
        return self._cursor_key()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.lower()
        if self._tree_mode:
            self._populate_tree(query)
            return
        if not query:
            self._populate_table(self.all_issues)
            return
        filtered = [i for i in self.all_issues if self._issue_matches_filter(i, query)]
        self._populate_table(filtered)

    def on_key(self, event) -> None:
        # Don't handle navigation keys when a modal is overlaying this screen
        if len(self.screen_stack) > 1:
            return

        focused = self.focused
        filter_bar = self.query_one("#filter-bar", Input)

        if isinstance(focused, Input):
            if event.key == "escape":
                focused.value = ""
                focused.display = False
                if self._tree_mode:
                    self._populate_tree()
                    self.query_one(Tree).focus()
                else:
                    self._populate_table(self.all_issues)
                    self.query_one(DataTable).focus()
                event.prevent_default()
                return
            if event.key == "enter":
                (self.query_one(Tree) if self._tree_mode else self.query_one(DataTable)).focus()
                event.prevent_default()
                return

        if self._tree_mode:
            # Tree handles its own up/down/enter navigation natively.
            # Only intercept escape to close the filter bar.
            if event.key == "escape" and filter_bar.display:
                filter_bar.value = ""
                filter_bar.display = False
                self._populate_tree()
                event.prevent_default()
            return

        # Table mode
        table = self.query_one(DataTable)
        if event.key == "down":
            table.move_cursor(row=table.cursor_row + 1)
            event.prevent_default()
        elif event.key == "up":
            table.move_cursor(row=table.cursor_row - 1)
            event.prevent_default()
        elif event.key == "enter":
            self.action_select_ticket()
            event.prevent_default()
        elif event.key == "escape" and filter_bar.display:
            filter_bar.value = ""
            filter_bar.display = False
            self._populate_table(self.all_issues)
            event.prevent_default()

    def action_activate_filter(self) -> None:
        filter_bar = self.query_one("#filter-bar", Input)
        filter_bar.display = True
        filter_bar.focus()

    def _cursor_key(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def action_select_ticket(self) -> None:
        if isinstance(self.focused, Input):
            return
        key = self._active_key()
        if key:
            self.selected_ticket = key
            self.exit()

    def action_open_ticket(self) -> None:
        if isinstance(self.focused, Input):
            return
        key = self._active_key()
        if key:
            server = get_jira_server()
            webbrowser.open(f"{server}/browse/{key}")

    def action_copy_url(self) -> None:
        if isinstance(self.focused, Input):
            return
        key = self._active_key()
        if not key:
            return
        url = f"{get_jira_server()}/browse/{key}"
        if _copy_to_clipboard(url):
            self.notify(f"Copied: {url}")
        else:
            self.notify("No clipboard utility found (install pbcopy, wl-copy, or xclip)", severity="warning")

    def action_show_fields(self) -> None:
        if isinstance(self.focused, Input) or self.jira_client is None:
            return
        key = self._active_key()
        if key:
            self.run_worker(lambda: self._fetch_fields_worker(key), thread=True)

    def _fetch_fields_worker(self, key: str) -> None:
        full_issue = self.jira_client.issue(key)
        project = key.split("-")[0]
        current = set(get_fields_for_project(project))
        raw_fields = full_issue.raw.get("fields", {})
        field_list = []
        for fid, raw_val in raw_fields.items():
            if raw_val is None or raw_val == [] or raw_val == "":
                continue
            fname = _get_jira_field_name(fid)
            fval = _preview_raw_value(raw_val)
            field_list.append((fid, fname, fval))
        field_list.sort(key=lambda x: x[1].lower())
        self.call_from_thread(
            self.push_screen,
            FieldPickerModal(field_list, current, project),
            self._on_fields_saved,
        )

    def _on_fields_saved(self, result: set[str] | None) -> None:
        if result is None:
            return
        self.reload_needed = True
        self.exit()

    def action_refresh(self) -> None:
        if isinstance(self.focused, Input):
            return
        self.reload_needed = True
        self.exit()

    def _update_filter_status(self) -> None:
        status = self.query_one("#filter-status", Static)
        if not self.projects:
            status.display = False
            return
        parts = [f"{p}: {get_effective_filter_name(p) or '*'}" for p in self.projects]
        status.update("  ".join(parts))
        status.display = True

    def action_show_filters(self) -> None:
        if isinstance(self.focused, Input) or not self.projects:
            return
        key = self._active_key()
        project = key.split("-")[0] if key else self.projects[0]
        self.push_screen(FilterListModal(project), self._on_filters_closed)

    def _on_filters_closed(self, changed: bool | None) -> None:
        self._update_filter_status()
        if changed:
            self.reload_needed = True
            self.exit()

    def action_show_info(self) -> None:
        if isinstance(self.focused, Input) or self.jira_client is None:
            return
        key = self._active_key()
        if key:
            self.push_screen(TicketInfoModal(key, self.jira_client))

    def action_quit(self) -> None:
        self.exit()


def _preview_raw_value(val) -> str:
    """Convert a raw JIRA field value (from issue.raw) to a short display string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val[:80]
    if isinstance(val, list):
        if not val:
            return ""
        items = []
        for v in val[:4]:
            if isinstance(v, str):
                items.append(v)
            elif isinstance(v, dict):
                for key in ("name", "value", "displayName", "key"):
                    if key in v:
                        items.append(str(v[key]))
                        break
        preview = ", ".join(items)
        if len(val) > 4:
            preview += f" +{len(val) - 4}"
        return preview[:80]
    if isinstance(val, dict):
        for key in ("value", "name", "displayName", "key"):
            if key in val:
                return str(val[key])[:80]
        return str(val)[:80]
    return str(val)[:80]


class FieldPickerModal(ModalScreen):
    CSS = """
    FieldPickerModal {
        align: center middle;
        background: #0a0e0a 85%;
    }
    #fp-dialog {
        width: 95%;
        height: 90%;
        border: thick #00ff41;
        background: #0d1a0d;
    }
    #fp-title {
        text-style: bold;
        padding: 0 1;
        background: #152015;
        color: #00ff41;
    }
    #fp-table { height: 1fr; }
    DataTable > .datatable--header { background: #0d1a0d; color: #00e5ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #003d00; color: #00ff41; text-style: bold; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Save & close", show=True),
        Binding("space", "toggle_field", "Toggle", show=True),
    ]

    def __init__(
        self,
        fields: list[tuple[str, str, str]],  # (id, name, value_preview)
        selected_ids: set[str],
        project: str,
    ) -> None:
        super().__init__()
        self._fields = fields
        self.selected_ids = set(selected_ids)
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical(id="fp-dialog"):
            yield Label(
                f" Field picker — {self.project}   Space to toggle · Enter to save",
                id="fp-title",
            )
            yield DataTable(id="fp-table", cursor_type="row", zebra_stripes=True)
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#fp-table", DataTable)
        table.add_column(" ", width=3)
        table.add_column("Field name", width=32)
        table.add_column("Field ID", width=22)
        table.add_column("Current value")
        for fid, fname, fval in self._fields:
            marker = "✓" if fid in self.selected_ids else " "
            table.add_row(marker, fname, fid, fval, key=fid)
        table.focus()

    def on_key(self, event) -> None:
        if event.key == "space":
            table = self.query_one("#fp-table", DataTable)
            if table.row_count == 0:
                return
            fid = table.coordinate_to_cell_key(
                Coordinate(table.cursor_row, 0)
            ).row_key.value
            if fid in self.selected_ids:
                self.selected_ids.discard(fid)
            else:
                self.selected_ids.add(fid)
            marker = "✓" if fid in self.selected_ids else " "
            table.update_cell_at(Coordinate(table.cursor_row, 0), marker)
            event.prevent_default()
        elif event.key == "enter":
            self.action_confirm()
            event.prevent_default()

    def action_confirm(self) -> None:
        set_config(
            f"fields.{self.project}",
            ",".join(sorted(self.selected_ids)),
        )
        self.dismiss(self.selected_ids)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextInputModal(ModalScreen):
    """Generic single-line text prompt. Dismisses with the entered string, or None on cancel."""

    CSS = """
    TextInputModal { align: center middle; background: #0a0e0a 80%; }
    #tip-dialog {
        width: 80%;
        height: auto;
        padding: 1 2;
        border: thick #00ff41;
        background: #0d1a0d;
    }
    #tip-title { text-style: bold; padding-bottom: 1; color: #00ff41; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="tip-dialog"):
            yield Label(self._title, id="tip-title")
            yield Input(value=self._initial, placeholder=self._placeholder)
            yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if val:
            self.dismiss(val)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Yes/No confirmation dialog. Dismisses with True (yes) or False (no)."""

    CSS = """
    ConfirmModal { align: center middle; background: #0a0e0a 80%; }
    #confirm-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick #ffb300;
        background: #0d1a0d;
    }
    #confirm-message { padding-bottom: 1; color: #b8d4b8; }
    #confirm-hint { color: #ffb300; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("y", "confirm_yes", "Yes", show=True),
        Binding("n", "confirm_no", "No", show=True),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._message, id="confirm-message")
            yield Label("y — yes    n / Escape — no", id="confirm-hint")
            yield Footer()

    def on_key(self, event) -> None:
        if event.key in ("y", "enter"):
            self.dismiss(True)
            event.prevent_default()
        elif event.key in ("n", "escape"):
            self.dismiss(False)
            event.prevent_default()

    def action_confirm_yes(self) -> None:
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        self.dismiss(False)


class FilterListModal(ModalScreen):
    """Manage named JQL filters for a single project."""

    CSS = """
    FilterListModal { align: center middle; background: #0a0e0a 85%; }
    #fl-dialog {
        width: 95%;
        height: 90%;
        border: thick #00ff41;
        background: #0d1a0d;
    }
    #fl-header {
        text-style: bold;
        padding: 0 1;
        background: #152015;
        color: #00ff41;
    }
    #fl-table { height: 1fr; }
    DataTable > .datatable--header { background: #0d1a0d; color: #00e5ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #003d00; color: #00ff41; text-style: bold; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "close_modal", "Close"),
        Binding("n", "new_filter", "New", show=True),
        Binding("e", "edit_filter", "Edit JQL", show=True),
        Binding("d", "delete_filter", "Delete", show=True),
        Binding("space", "set_default", "Set default", show=True),
    ]

    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project
        self._filters: list[dict] = get_filters_for_project(project)
        self._changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="fl-dialog"):
            yield Label(
                f" Filters — {self.project}   Enter activate · Space set default · n new · e edit JQL · d delete",
                id="fl-header",
            )
            yield DataTable(id="fl-table", cursor_type="row", zebra_stripes=True)
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#fl-table", DataTable)
        table.add_column(" ", width=3)
        table.add_column("Name", width=28)
        table.add_column("JQL")
        self._refresh_table()
        table.focus()

    def _refresh_table(self) -> None:
        table = self.query_one("#fl-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        effective = get_effective_filter_name(self.project)
        default = get_active_filter_name(self.project)
        for i, f in enumerate(self._filters):
            name = f["name"]
            if name == effective:
                marker = "▶" if name != default else "●"
            elif name == default:
                marker = "○"
            else:
                marker = " "
            table.add_row(marker, name, f["jql"], key=str(i))
        if table.row_count > 0:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def _cursor_idx(self) -> int | None:
        table = self.query_one("#fl-table", DataTable)
        if table.row_count == 0:
            return None
        return int(
            table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        )

    def on_key(self, event) -> None:
        if event.key == "enter":
            self._do_activate()
            event.prevent_default()
        elif event.key == "space":
            self._do_set_default()
            event.prevent_default()

    def _do_activate(self) -> None:
        """Activate filter for this session only (not persisted)."""
        idx = self._cursor_idx()
        if idx is None:
            return
        name = self._filters[idx]["name"]
        current = get_effective_filter_name(self.project)
        if current == name:
            # Toggle off session override — fall back to config default
            _session_active_filters.pop(self.project, None)
        else:
            _session_active_filters[self.project] = name
        self._changed = True
        self.dismiss(True)

    def _do_set_default(self) -> None:
        """Set filter as the persisted config default."""
        idx = self._cursor_idx()
        if idx is None:
            return
        name = self._filters[idx]["name"]
        current_default = get_active_filter_name(self.project)
        if current_default == name:
            # Toggle off — clear config default
            set_active_filter_name(self.project, None)
            _session_active_filters.pop(self.project, None)
        else:
            set_active_filter_name(self.project, name)
            _session_active_filters[self.project] = name
        self._changed = True
        self._refresh_table()

    def action_new_filter(self) -> None:
        self.app.push_screen(
            TextInputModal("New filter — name", placeholder="e.g. My Sprint"),
            self._on_new_name,
        )

    def _on_new_name(self, name: str | None) -> None:
        if not name:
            return
        if any(f["name"] == name for f in self._filters):
            return  # duplicate — silently ignore
        default_jql = f"project = {self.project} AND assignee = currentUser() ORDER BY updated DESC"
        self.app.push_screen(
            TextInputModal("New filter — JQL", placeholder="project = ...", initial=default_jql),
            lambda jql: self._on_new_jql(name, jql),
        )

    def _on_new_jql(self, name: str, jql: str | None) -> None:
        if not jql:
            return
        self._filters.append({"name": name, "jql": jql})
        set_filters_for_project(self.project, self._filters)
        self._refresh_table()

    def action_edit_filter(self) -> None:
        idx = self._cursor_idx()
        if idx is None:
            return
        name = self._filters[idx]["name"]
        current_jql = self._filters[idx]["jql"]
        self.app.push_screen(
            TextInputModal("Edit JQL", initial=current_jql),
            lambda jql: self._on_edit_jql(name, jql),
        )

    def _on_edit_jql(self, name: str, jql: str | None) -> None:
        if not jql:
            return
        for f in self._filters:
            if f["name"] == name:
                f["jql"] = jql
                break
        set_filters_for_project(self.project, self._filters)
        self._refresh_table()

    def action_delete_filter(self) -> None:
        idx = self._cursor_idx()
        if idx is None:
            return
        name = self._filters[idx]["name"]
        self.app.push_screen(
            ConfirmModal(f"Delete filter '{name}'?"),
            lambda confirmed: self._on_delete_confirmed(name, confirmed),
        )

    def _on_delete_confirmed(self, name: str, confirmed: bool | None) -> None:
        if not confirmed:
            return
        self._filters = [f for f in self._filters if f["name"] != name]
        set_filters_for_project(self.project, self._filters)
        # If the deleted filter was active, clear it
        if get_active_filter_name(self.project) == name:
            set_active_filter_name(self.project, None)
        if _session_active_filters.get(self.project) == name:
            _session_active_filters.pop(self.project, None)
        self._changed = True
        self._refresh_table()

    def action_close_modal(self) -> None:
        self.dismiss(self._changed)


class TicketInfoModal(ModalScreen):
    CSS = """
    TicketInfoModal {
        align: center middle;
        background: #0a0e0a 85%;
    }
    #ti-container {
        width: 90%;
        height: 90%;
        border: thick #00ff41;
        background: #0d1a0d;
    }
    #ti-title {
        text-style: bold;
        padding: 0 1;
        background: #152015;
        color: #00ff41;
    }
    #ti-scroll { height: 1fr; }
    #ti-content { padding: 1 2; color: #b8d4b8; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, key: str, jira_client) -> None:
        super().__init__()
        self._key = key
        self._jira_client = jira_client

    def compose(self) -> ComposeResult:
        with Vertical(id="ti-container"):
            yield Label(f" {self._key}", id="ti-title")
            with ScrollableContainer(id="ti-scroll"):
                yield Static("Loading…", id="ti-content")
            yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._fetch_info, thread=True)

    def _fetch_info(self) -> None:
        from rich.console import Group
        from rich.rule import Rule
        from rich.table import Table
        from rich.text import Text

        try:
            issue = self._jira_client.issue(
                self._key,
                fields=["summary", "status", "assignee", "reporter", "priority", "labels", "description", "issuetype"],
            )
        except Exception as e:
            self.app.call_from_thread(self._update_content, f"[red]Error: {e}[/red]")
            return

        f = issue.fields
        assignee  = f.assignee.displayName if f.assignee else "Unassigned"
        reporter  = f.reporter.displayName if f.reporter else "Unknown"
        labels    = ", ".join(f.labels) if f.labels else "—"
        priority  = f.priority.name if f.priority else "—"
        status    = f.status.name
        description = (f.description or "").strip()

        status_style   = STATUS_STYLES.get(status.lower(), "white")
        priority_style = PRIORITY_STYLES.get(priority.lower(), "white")

        url = f"{get_jira_server()}/browse/{issue.key}"

        meta = Table.grid(padding=(0, 3), expand=False)
        meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
        meta.add_column(min_width=22)
        meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
        meta.add_column(min_width=16)
        meta.add_row("STATUS",   Text(status, style=status_style),
                     "PRIORITY", Text(priority, style=priority_style))
        meta.add_row("ASSIGNEE", assignee, "REPORTER", reporter)
        meta.add_row("LABELS",   Text(labels, style="cyan"), "", "")

        truncated = (description[:800] + "\n[dim]…truncated[/dim]") if len(description) > 800 else (description or "[dim]—[/dim]")
        desc_block = Group(
            Rule(style="bright_black"),
            Text.from_markup("[bold bright_black]DESCRIPTION[/bold bright_black]"),
            Text.from_markup(f"\n{truncated}"),
        )

        url_line = Text.assemble(("URL  ", "bold bright_black"), (url, f"link {url} bright_cyan"))

        content = Group(
            Text(f.summary, style="bold white"),
            Text(""),
            meta,
            Text(""),
            url_line,
            Text(""),
            desc_block,
        )

        self.app.call_from_thread(self._update_content, content)

    def _update_content(self, content) -> None:
        self.query_one("#ti-content", Static).update(content)


class BranchPromptApp(App):
    """Standalone TUI: shows ticket info and prompts for a new branch suffix.

    After app.run(), inspect app.branch_suffix — non-None means the user confirmed.
    The caller is responsible for creating the branch.
    """

    CSS = """
    Screen { background: #0a0e0a; }
    #bp-title {
        height: 1;
        padding: 0 1;
        background: #152015;
        color: #00ff41;
        text-style: bold;
    }
    #bp-scroll { height: 1fr; }
    #bp-content { padding: 1 2; color: #b8d4b8; }
    #bp-prompt-label {
        height: 1;
        padding: 0 1;
        color: #ffb300;
    }
    #bp-input {
        border: tall #00ff41;
        background: #0a0e0a;
        color: #00ff41;
    }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "submit", "Create branch", show=True),
    ]

    def __init__(self, ticket: str, jira_client) -> None:
        super().__init__()
        self._ticket = ticket
        self._jira_client = jira_client
        self.branch_suffix: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(f"  {self._ticket}  —  create a branch to continue", id="bp-title")
        with ScrollableContainer(id="bp-scroll"):
            yield Static("Loading…", id="bp-content")
        yield Label(f"  Branch suffix  (→ {self._ticket}-<suffix>)", id="bp-prompt-label")
        yield Input(placeholder="e.g. fix-login", id="bp-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bp-input", Input).focus()
        self.run_worker(self._fetch_info, thread=True)

    def _fetch_info(self) -> None:
        from rich.console import Group
        from rich.rule import Rule
        from rich.table import Table
        from rich.text import Text

        try:
            issue = self._jira_client.issue(
                self._ticket,
                fields=["summary", "status", "assignee", "reporter", "priority", "labels", "description", "issuetype"],
            )
        except Exception as e:
            self.call_from_thread(self._update_content, f"[red]Error loading ticket info: {e}[/red]")
            return

        f = issue.fields
        assignee    = f.assignee.displayName if f.assignee else "Unassigned"
        reporter    = f.reporter.displayName if f.reporter else "Unknown"
        labels      = ", ".join(f.labels) if f.labels else "—"
        priority    = f.priority.name if f.priority else "—"
        status      = f.status.name
        description = (f.description or "").strip()

        status_style   = STATUS_STYLES.get(status.lower(), "white")
        priority_style = PRIORITY_STYLES.get(priority.lower(), "white")
        url = f"{get_jira_server()}/browse/{issue.key}"

        meta = Table.grid(padding=(0, 3), expand=False)
        meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
        meta.add_column(min_width=22)
        meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
        meta.add_column(min_width=16)
        meta.add_row("STATUS",   Text(status, style=status_style),
                     "PRIORITY", Text(priority, style=priority_style))
        meta.add_row("ASSIGNEE", assignee, "REPORTER", reporter)
        meta.add_row("LABELS",   Text(labels, style="cyan"), "", "")

        truncated = (description[:800] + "\n[dim]…truncated[/dim]") if len(description) > 800 else (description or "[dim]—[/dim]")
        desc_block = Group(
            Rule(style="bright_black"),
            Text.from_markup("[bold bright_black]DESCRIPTION[/bold bright_black]"),
            Text.from_markup(f"\n{truncated}"),
        )
        url_line = Text.assemble(("URL  ", "bold bright_black"), (url, f"link {url} bright_cyan"))
        content = Group(
            Text(f.summary, style="bold white"),
            Text(""),
            meta,
            Text(""),
            url_line,
            Text(""),
            desc_block,
        )
        self.call_from_thread(self._update_content, content)

    def _update_content(self, content) -> None:
        self.query_one("#bp-content", Static).update(content)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if val:
            self.branch_suffix = val
            self.exit()

    def action_submit(self) -> None:
        val = self.query_one("#bp-input", Input).value.strip()
        if val:
            self.branch_suffix = val
            self.exit()

    def action_cancel(self) -> None:
        self.exit()


# --- PR helpers ---

PR_STATUS_STYLES = {
    "OPEN":     "bold green",
    "DRAFT":    "bold yellow",
    "MERGED":   "bold blue",
    "DECLINED": "bold red",
}


def _get_prs(issue_id: str) -> list[dict]:
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


class PrPickerApp(App):
    CSS = """
    Screen { background: #0a0e0a; }
    .context-bar {
        height: 1;
        background: #0d1a0d;
        color: #00ff41;
        padding: 0 1;
        text-style: bold;
    }
    DataTable {
        height: 1fr;
        background: #0a0e0a;
    }
    DataTable > .datatable--header { background: #0d1a0d; color: #00e5ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #003d00; color: #00ff41; text-style: bold; }
    DataTable > .datatable--hover  { background: #001a00; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    #filter-bar {
        display: none;
        border: tall #00ff41;
        background: #0d1a0d;
        color: #00ff41;
    }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("o", "open_pr", "Open", show=True),
        Binding("d", "show_diff", "Diff", show=True),
        Binding("s", "switch_branch", "Switch branch", show=True),
        Binding("slash", "activate_filter", "Filter", show=True),
    ]

    def __init__(self, prs: list[dict], *, open_on_enter: bool = False) -> None:
        super().__init__()
        self.prs = prs
        self.selected_pr: dict | None = None
        self.open_on_enter = open_on_enter
        self.branch_to_switch: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(_context_bar_text(), classes="context-bar")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Input(id="filter-bar", placeholder="Filter…")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Status", width=10)
        table.add_column("Author", width=20)
        table.add_column("Repo", width=20)
        table.add_column("Source branch", width=26)
        table.add_column("Title")
        self._populate_table(list(enumerate(self.prs)))
        table.focus()

    def _populate_table(self, indexed_prs: list[tuple[int, dict]]) -> None:
        from rich.text import Text as RichText
        table = self.query_one(DataTable)
        table.clear()
        for i, pr in indexed_prs:
            status = pr.get("status", "")
            style = PR_STATUS_STYLES.get(status, "white")
            author = pr.get("author", {}).get("name", "")
            table.add_row(
                RichText(status, style=style),
                RichText(author, style="#b39ddb"),
                RichText(pr.get("repositoryName", ""), style="#ffb300"),
                RichText(pr.get("source", {}).get("branch", ""), style="#00e5ff"),
                RichText(pr.get("name", ""), style="#b8d4b8"),
                key=str(i),
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        search = event.value.lower()
        if not search:
            self._populate_table(list(enumerate(self.prs)))
            return
        self._populate_table([
            (i, p) for i, p in enumerate(self.prs)
            if search in p.get("status", "").lower()
            or search in p.get("author", {}).get("name", "").lower()
            or search in p.get("repositoryName", "").lower()
            or search in p.get("source", {}).get("branch", "").lower()
            or search in p.get("name", "").lower()
        ])

    def on_key(self, event) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            if event.key == "escape":
                focused.value = ""
                focused.display = False
                self._populate_table(list(enumerate(self.prs)))
                self.query_one(DataTable).focus()
                event.prevent_default()
                return
            if event.key == "enter":
                self.query_one(DataTable).focus()
                event.prevent_default()
                return

        filter_bar = self.query_one("#filter-bar", Input)
        table = self.query_one(DataTable)
        if event.key == "down":
            table.move_cursor(row=table.cursor_row + 1)
            event.prevent_default()
        elif event.key == "up":
            table.move_cursor(row=table.cursor_row - 1)
            event.prevent_default()
        elif event.key == "enter" and not self.open_on_enter:
            self.action_select_pr()
            event.prevent_default()
        elif event.key == "escape" and filter_bar.display:
            filter_bar.value = ""
            filter_bar.display = False
            self._populate_table(list(enumerate(self.prs)))
            event.prevent_default()

    def action_activate_filter(self) -> None:
        filter_bar = self.query_one("#filter-bar", Input)
        filter_bar.display = True
        filter_bar.focus()

    def _selected_pr(self) -> dict | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        return self.prs[int(cell_key.row_key.value)]

    def action_open_pr(self) -> None:
        if isinstance(self.focused, Input):
            return
        pr = self._selected_pr()
        if pr is None:
            return
        url = pr.get("url", "")
        if url:
            webbrowser.open(url)

    def action_select_pr(self) -> None:
        pr = self._selected_pr()
        if pr is None:
            return
        self.selected_pr = pr
        self.exit()

    def action_show_diff(self) -> None:
        if isinstance(self.focused, Input):
            return
        pr = self._selected_pr()
        if pr is None:
            return
        self.push_screen(DiffModal(pr))

    def action_switch_branch(self) -> None:
        if isinstance(self.focused, Input):
            return
        pr = self._selected_pr()
        if pr is None:
            return
        branch = pr.get("source", {}).get("branch", "")
        if not branch:
            self.notify("No source branch found for this PR.", severity="warning")
            return
        self.branch_to_switch = branch
        self.exit()

    def action_quit(self) -> None:
        self.exit()


# --- CLI ---


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="jg")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Manage JIRA ticket context for git workflows."""
    if ctx.invoked_subcommand is None:
        ticket = get_ticket()
        if ticket:
            click.echo(ticket)
        else:
            click.echo("No ticket set. Use 'jg set TICKET-123' to set one.", err=True)
            sys.exit(1)


@main.command("set")
@click.argument("ticket", required=False)
@click.option("--jql", default=None, help="Raw JQL override — bypasses all filters and project config for this run")
@click.option("--max", "max_results", default=200, show_default=True, help="Max results to fetch")
def cmd_set(ticket: str | None, jql: str | None, max_results: int) -> None:
    """Set the current JIRA ticket, or browse interactively if no ticket given.

    Shows tickets from all configured projects in a single merged list. Each project
    uses its active named filter (if set) or the built-in default JQL. Pass --jql to
    bypass all filters and use a raw query instead.
    """
    if ticket:
        save_ticket(ticket)
        click.echo(f"Ticket set to {ticket}")
        return

    jira = get_jira_client()
    _ensure_fields_cached(jira)
    projects = get_projects()

    # Migrate any legacy jql.<PROJECT> config keys to named filters
    for proj in projects:
        legacy_jql = get_config(f"jql.{proj}")
        if legacy_jql:
            if not get_filters_for_project(proj):
                set_filters_for_project(proj, [{"name": "Default", "jql": legacy_jql}])
                set_active_filter_name(proj, "Default")
                _session_active_filters[proj] = "Default"
                click.echo(f"Migrated jql.{proj} to a named filter 'Default'.", err=True)
            cfg = _read_config()
            cfg.pop(f"jql.{proj}", None)
            _write_config(cfg)

    def _collect_extra_fields() -> tuple[list[str], dict[str, str]]:
        seen: set[str] = set()
        ordered: list[str] = []
        for proj in projects:
            for fid in get_fields_for_project(proj):
                if fid not in seen:
                    seen.add(fid)
                    ordered.append(fid)
        names = {fid: _get_jira_field_name(fid) for fid in ordered}
        return ordered, names

    extra_field_ids, field_names = _collect_extra_fields()

    click.echo("Fetching tickets…", err=True)
    try:
        if jql:
            issues = list(jira.search_issues(
                jql, maxResults=max_results,
                fields=["summary", "status", "assignee", "priority", "parent", "issuetype"] + extra_field_ids,
            ))
        else:
            issues = fetch_issues_for_projects(jira, projects, max_results, extra_field_ids)
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    if not issues:
        click.echo("No issues found.")
        return

    while True:
        app = JiraListApp(
            issues,
            jira_client=jira,
            extra_field_ids=extra_field_ids,
            field_names=field_names,
            projects=projects,
        )
        app.run()

        if app.reload_needed:
            extra_field_ids, field_names = _collect_extra_fields()
            click.echo("Reloading with updated fields…", err=True)
            try:
                issues = fetch_issues_for_projects(jira, projects, max_results, extra_field_ids)
            except JIRAError as e:
                raise click.ClickException(f"JIRA API error: {e.text}") from e
            continue

        if app.selected_ticket:
            save_ticket(app.selected_ticket)
            click.echo(f"Ticket set to {app.selected_ticket}")
        else:
            click.echo("No ticket selected.", err=True)
        break


@main.command("clear")
def cmd_clear() -> None:
    """Clear the current JIRA ticket."""
    clear_ticket()
    click.echo("Ticket cleared")


@main.command("version")
def cmd_version() -> None:
    """Show the jg version."""
    click.echo(f"jg {__version__}")


def _get_file_statuses() -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (staged, modified, deleted, untracked) as lists of (status_code, filepath)."""
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException("Not a git repository or git not available.")
    staged, modified, deleted, untracked = [], [], [], []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        if x == "?" and y == "?":
            untracked.append(("?", path))
        else:
            if x not in (" ", "?"):
                staged.append((x, path))
            if y not in (" ", "?"):
                if y == "D":
                    deleted.append(("D", path))
                else:
                    modified.append((y, path))
    return staged, modified, deleted, untracked


def _get_current_branch() -> str | None:
    """Return the current git branch name, or None if not on a branch."""
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _check_not_main_branch() -> None:
    """Abort with an error if the current branch is main or master."""
    branch = _get_current_branch()
    if branch in ("main", "master"):
        raise click.ClickException(
            f"You are on '{branch}', which is branch-protected. "
            "Create a feature branch first (e.g. jg branch <name>)."
        )


def _get_default_branch() -> str:
    """Return the default branch name by asking origin, falling back to main/master."""
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        # e.g. "refs/remotes/origin/main\n"
        return result.stdout.strip().split("/")[-1]
    # Fallback: look for main or master in local branches
    branches_out = subprocess.run(
        ["git", "branch"], capture_output=True, text=True,
    ).stdout
    local = {b.lstrip("* ").strip() for b in branches_out.splitlines()}
    for name in ("main", "master"):
        if name in local:
            return name
    return "main"


def _get_local_branches() -> list[tuple[str, bool]]:
    """Return (branch_name, is_current) for all local branches."""
    result = subprocess.run(["git", "branch"], capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException("Not a git repository or git not available.")
    branches = []
    for line in result.stdout.splitlines():
        is_current = line.startswith("*")
        branch = line.lstrip("* ").strip()
        if branch:
            branches.append((branch, is_current))
    return branches


class BranchPickerApp(App):
    CSS = """
    Screen { background: #0a0e0a; }
    .context-bar {
        height: 1;
        background: #0d1a0d;
        color: #00ff41;
        padding: 0 1;
        text-style: bold;
    }
    DataTable {
        height: 1fr;
        background: #0a0e0a;
    }
    DataTable > .datatable--header { background: #0d1a0d; color: #00e5ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #003d00; color: #00ff41; text-style: bold; }
    DataTable > .datatable--hover  { background: #001a00; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    #filter-bar {
        display: none;
        border: tall #00ff41;
        background: #0d1a0d;
        color: #00ff41;
    }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("enter", "select_branch", "Switch", show=True),
        Binding("slash", "activate_filter", "Filter", show=True),
    ]

    def __init__(self, branches: list[tuple[str, bool]]) -> None:
        super().__init__()
        self.all_branches = branches
        self.selected_branch: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(_context_bar_text(), classes="context-bar")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Input(id="filter-bar", placeholder="Filter…")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", width=2)      # current marker
        table.add_column("Branch")
        self._populate_table(self.all_branches)
        table.focus()

    def _populate_table(self, branches: list[tuple[str, bool]]) -> None:
        from rich.text import Text as RichText

        table = self.query_one(DataTable)
        table.clear()
        for branch, is_current in branches:
            marker = RichText("*", style="bold #00ff41") if is_current else RichText("")
            label  = RichText(branch, style="bold #00e5ff") if is_current else RichText(branch, style="#b8d4b8")
            table.add_row(marker, label, key=branch)

    def on_input_changed(self, event: Input.Changed) -> None:
        search = event.value.lower()
        filtered = [
            (b, cur) for b, cur in self.all_branches
            if not search or search in b.lower()
        ]
        self._populate_table(filtered)

    def on_key(self, event) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            if event.key == "escape":
                focused.value = ""
                focused.display = False
                self._populate_table(self.all_branches)
                self.query_one(DataTable).focus()
                event.prevent_default()
                return
            if event.key == "enter":
                self.query_one(DataTable).focus()
                event.prevent_default()
                return

        filter_bar = self.query_one("#filter-bar", Input)
        table = self.query_one(DataTable)
        if event.key == "down":
            table.move_cursor(row=table.cursor_row + 1)
            event.prevent_default()
        elif event.key == "up":
            table.move_cursor(row=table.cursor_row - 1)
            event.prevent_default()
        elif event.key == "enter":
            self.action_select_branch()
            event.prevent_default()
        elif event.key == "escape" and filter_bar.display:
            filter_bar.value = ""
            filter_bar.display = False
            self._populate_table(self.all_branches)
            event.prevent_default()

    def action_activate_filter(self) -> None:
        filter_bar = self.query_one("#filter-bar", Input)
        filter_bar.display = True
        filter_bar.focus()

    def action_select_branch(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        self.selected_branch = cell_key.row_key.value
        self.exit()

    def action_quit(self) -> None:
        self.exit()


class CommitModal(ModalScreen):
    CSS = """
    CommitModal {
        align: center middle;
        background: #0a0e0a 85%;
    }
    #dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick #00ff41;
        background: #0d1a0d;
    }
    #title       { text-style: bold; padding-bottom: 1; color: #00ff41; }
    #hint        { color: #00e5ff; padding-bottom: 1; }
    #hint-escape { color: #4d8a4d; padding-bottom: 1; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [Binding("escape", "cancel", "Stage without commit")]

    def __init__(self, ticket: str | None) -> None:
        super().__init__()
        self.ticket = ticket

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Commit message", id="title")
            if self.ticket:
                yield Label(
                    f"Will commit as: [bold cyan]{self.ticket}[/bold cyan] <message>",
                    id="hint",
                )
            yield Label("Escape to stage files without committing", id="hint-escape")
            yield Input(placeholder="Enter commit message…")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if msg:
            self.dismiss(msg)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DiffModal(ModalScreen):
    CSS = """
    DiffModal { background: #0a0e0a 92%; }
    #diff-scroll { width: 100%; height: 1fr; border: thick #00ff41; }
    #diff-content { padding: 0; }
    #search-bar { display: none; background: #0d1a0d; color: #00ff41; border: tall #00ff41; }
    #search-status {
        display: none;
        background: #ffb300;
        color: #0a0e0a;
        padding: 0 1;
        text-style: bold;
    }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("slash", "activate_search", "Search", show=True),
        Binding("n", "next_file", "Next file", show=True),
        Binding("p", "prev_file", "Prev file", show=True),
    ]

    def __init__(self, pr: dict) -> None:
        super().__init__()
        self.pr = pr
        self._raw_lines: list[str] = []
        self._file_starts: list[int] = []  # line indices of "diff --git" headers
        self._base_ansi: str = ""
        self._search_query: str = ""
        self._match_lines: list[int] = []
        self._match_idx: int = -1
        self._file_idx: int = 0

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="diff-scroll"):
            yield Static("Loading diff…", id="diff-content")
        yield Input(id="search-bar", placeholder="Search…")
        yield Static("", id="search-status")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._fetch_diff, thread=True)

    def _fetch_diff(self) -> None:
        url = self.pr.get("url", "")
        if not url:
            self.app.call_from_thread(self._set_content, "No PR URL available.")
            return
        result = subprocess.run(
            ["gh", "pr", "diff", url],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout:
            self.app.call_from_thread(self._set_content, "No diff available.")
            return
        raw = result.stdout
        self._raw_lines = raw.splitlines()
        self._file_starts = [
            i for i, line in enumerate(self._raw_lines)
            if line.startswith("diff --git")
        ]
        if shutil.which("delta"):
            delta = subprocess.run(
                ["delta", "--color-only"],
                input=raw,
                capture_output=True,
                text=True,
            )
            self._base_ansi = delta.stdout if delta.returncode == 0 else raw
        else:
            from rich.syntax import Syntax
            from rich.console import Console
            from io import StringIO
            buf = StringIO()
            Console(file=buf, force_terminal=True, width=220, highlight=False).print(
                Syntax(raw, "diff", theme="monokai")
            )
            self._base_ansi = buf.getvalue()
        self.app.call_from_thread(self._refresh_display)

    def _refresh_display(self) -> None:
        from rich.text import Text as RichText
        content = RichText.from_ansi(self._base_ansi)
        if self._search_query:
            plain = content.plain
            query_lower = self._search_query.lower()
            plain_lower = plain.lower()
            pos = 0
            while True:
                idx = plain_lower.find(query_lower, pos)
                if idx == -1:
                    break
                content.stylize("black on yellow", idx, idx + len(self._search_query))
                pos = idx + len(self._search_query)
        self.query_one("#diff-content", Static).update(content)

    def _set_content(self, content) -> None:
        self.query_one("#diff-content", Static).update(content)

    def _update_search_status(self) -> None:
        status = self.query_one("#search-status", Static)
        if not self._search_query:
            status.display = False
            return
        count = len(self._match_lines)
        if count:
            current = self._match_idx + 1
            status.update(f" Search: [bold]{self._search_query}[/bold]  {current}/{count} matches — [dim]Enter[/dim] next  [dim]Esc[/dim] clear ")
        else:
            status.update(f" Search: [bold]{self._search_query}[/bold]  no matches — [dim]Esc[/dim] clear ")
        status.display = True

    def _scroll_to_line(self, line_idx: int) -> None:
        self.query_one("#diff-scroll", ScrollableContainer).scroll_to(y=line_idx, animate=False)

    def action_activate_search(self) -> None:
        bar = self.query_one("#search-bar", Input)
        bar.display = True
        bar.focus()

    def action_close(self) -> None:
        bar = self.query_one("#search-bar", Input)
        if bar.display:
            # Cancel search input — don't commit, keep any existing committed search
            bar.display = False
            bar.value = ""
        elif self._search_query:
            # Clear committed search
            self._search_query = ""
            self._match_lines = []
            self._match_idx = -1
            self._refresh_display()
            self._update_search_status()
        else:
            self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "enter":
            bar = self.query_one("#search-bar", Input)
            if bar.display and bar.has_focus:
                self._commit_search(bar.value)
            else:
                self.action_next_match()
            event.prevent_default()

    def _commit_search(self, raw_query: str) -> None:
        query = raw_query.strip()
        bar = self.query_one("#search-bar", Input)
        bar.display = False
        bar.value = ""
        if not query:
            return
        self._search_query = query
        query_lower = query.lower()
        self._match_lines = [
            i for i, line in enumerate(self._raw_lines)
            if query_lower in line.lower()
        ]
        self._match_idx = 0 if self._match_lines else -1
        self._refresh_display()
        self._update_search_status()
        if self._match_idx >= 0:
            self._scroll_to_line(self._match_lines[0])

    def action_next_match(self) -> None:
        if not self._match_lines:
            return
        self._match_idx = (self._match_idx + 1) % len(self._match_lines)
        self._update_search_status()
        self._scroll_to_line(self._match_lines[self._match_idx])

    def action_next_file(self) -> None:
        if not self._file_starts:
            return
        self._file_idx = min(self._file_idx + 1, len(self._file_starts) - 1)
        self._scroll_to_line(self._file_starts[self._file_idx])

    def action_prev_file(self) -> None:
        if not self._file_starts:
            return
        self._file_idx = max(self._file_idx - 1, 0)
        self._scroll_to_line(self._file_starts[self._file_idx])


class FmtModal(ModalScreen):
    CSS = """
    FmtModal { background: #0a0e0a 85%; }
    #fmt-outer {
        width: 90%;
        height: 80%;
        border: thick #00ff41;
        background: #0d1a0d;
        padding: 1 2;
    }
    #fmt-title  { text-style: bold; padding-bottom: 1; color: #00ff41; }
    #fmt-scroll { height: 1fr; }
    #fmt-hint   { color: #4d8a4d; padding-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="fmt-outer"):
            yield Label("Format results", id="fmt-title")
            with ScrollableContainer(id="fmt-scroll"):
                yield Static("Running formatters…", id="fmt-content")
            yield Label("Press any key to close", id="fmt-hint")

    def on_mount(self) -> None:
        self.run_worker(self._run, thread=True)

    def _run(self) -> None:
        msg, table = _build_fmt_table()
        if msg == "clean":
            content = "Nothing to format — working tree clean."
        else:
            content = table
        self.app.call_from_thread(self._update, content)

    def _update(self, content) -> None:
        self.query_one("#fmt-content", Static).update(content)

    def on_key(self, event) -> None:
        self.dismiss()
        event.prevent_default()


class BranchDiffModal(ModalScreen):
    """Full-screen diff of a branch against a base branch."""

    CSS = """
    BranchDiffModal { background: #0a0e0a 92%; }
    #bdiff-scroll { width: 100%; height: 1fr; border: thick #00ff41; }
    #bdiff-content { padding: 0; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, branch: str, base: str) -> None:
        super().__init__()
        self.branch = branch
        self.base = base

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="bdiff-scroll"):
            yield Static(f"Loading diff {self.base}...{self.branch}…", id="bdiff-content")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._fetch_diff, thread=True)

    def _fetch_diff(self) -> None:
        result = subprocess.run(
            ["git", "diff", f"{self.base}...{self.branch}"],
            capture_output=True,
            text=True,
        )
        raw = result.stdout or "No differences found."
        if shutil.which("delta"):
            proc = subprocess.run(
                ["delta", "--color-only"],
                input=raw,
                capture_output=True,
                text=True,
            )
            from rich.text import Text
            content = Text.from_ansi(proc.stdout if proc.returncode == 0 else raw)
        else:
            from rich.syntax import Syntax
            content = Syntax(raw, "diff", theme="monokai")
        self.app.call_from_thread(self._update, content)

    def _update(self, content) -> None:
        self.query_one("#bdiff-content", Static).update(content)


class FilePickerApp(App):
    CSS = """
    Screen { layout: vertical; background: #0a0e0a; }

    .context-bar {
        height: 1;
        background: #0d1a0d;
        color: #00ff41;
        padding: 0 1;
        text-style: bold;
    }
    .section {
        height: 1fr;
        border: tall #1a3a1a;
    }
    .section:focus-within {
        border: tall #00ff41;
    }
    .section-label {
        padding: 0 1;
        background: #0d1a0d;
        color: #00e5ff;
        text-style: bold;
    }
    DataTable {
        height: 1fr;
        background: #0a0e0a;
    }
    DataTable > .datatable--header { background: #0d1a0d; color: #00e5ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #003d00; color: #00ff41; text-style: bold; }
    DataTable > .datatable--hover  { background: #001a00; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    .section-filter {
        display: none;
        border-top: tall #00ff41;
        background: #0d1a0d;
        color: #00ff41;
    }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Cancel"),
        Binding("space", "toggle_select", "Toggle", show=True),
        Binding("enter", "confirm", "Stage / Commit", show=True),
        Binding("slash", "activate_filter", "Filter", show=True),
        Binding("f", "run_fmt", "Format", show=True),
    ]

    def __init__(
        self,
        staged: list[tuple[str, str]],
        modified: list[tuple[str, str]],
        deleted: list[tuple[str, str]],
        untracked: list[tuple[str, str]],
    ) -> None:
        super().__init__()
        # Store originals as (status, path, item_key) triples.
        # item_key = "origin:path" — unique even when the same path appears in multiple sections.
        self.orig_staged   = [(s, p, f"staged:{p}")    for s, p in staged]
        self.orig_modified = [(s, p, f"modified:{p}")  for s, p in modified]
        self.orig_deleted  = [(s, p, f"deleted:{p}")   for s, p in deleted]
        self.orig_untracked= [(s, p, f"untracked:{p}") for s, p in untracked]

        # Build lookup: item_key -> (status, origin)
        self.file_info: dict[str, tuple[str, str]] = {}
        for status, path, ik in self.orig_staged:
            self.file_info[ik] = (status, "staged")
        for status, path, ik in self.orig_modified:
            self.file_info[ik] = (status, "modified")
        for status, path, ik in self.orig_deleted:
            self.file_info[ik] = (status, "deleted")
        for status, path, ik in self.orig_untracked:
            self.file_info[ik] = (status, "untracked")

        # Ordered list of item_keys currently in the staged section
        self._staged_paths: list[str] = [ik for _, _, ik in self.orig_staged]

        # Output
        self.to_stage: set[str] = set()
        self.to_unstage: set[str] = set()
        self.aborted: bool = False
        self.commit_message: str | None = None
        self._section_filters: dict[str, str] = {}

    # --- data helpers ---

    def _files_for_section(self, section_id: str) -> list[tuple[str, str, str]]:
        """Return (status, path, item_key) triples for a section given current staged state."""
        staged_set = set(self._staged_paths)
        if section_id == "staged":
            return sorted(
                [(self.file_info[ik][0], ik.split(":", 1)[1], ik) for ik in self._staged_paths],
                key=lambda x: x[1],
            )
        if section_id == "modified":
            files = [(s, p, ik) for s, p, ik in self.orig_modified if ik not in staged_set]
            # orig-staged items that were unstaged back — non-A/? and non-D go to modified
            files += [(s, p, ik) for s, p, ik in self.orig_staged if ik not in staged_set and s not in ("A", "?", "D")]
            return sorted(files, key=lambda x: x[1])
        if section_id == "deleted":
            files = [(s, p, ik) for s, p, ik in self.orig_deleted if ik not in staged_set]
            # orig-staged D items that were unstaged — deletion is no longer staged, shows as working-tree delete
            files += [(s, p, ik) for s, p, ik in self.orig_staged if ik not in staged_set and s == "D"]
            return sorted(files, key=lambda x: x[1])
        if section_id == "untracked":
            files = [(s, p, ik) for s, p, ik in self.orig_untracked if ik not in staged_set]
            # orig-staged A/? items that were unstaged go back to untracked
            files += [(s, p, ik) for s, p, ik in self.orig_staged if ik not in staged_set and s in ("A", "?")]
            return sorted(files, key=lambda x: x[1])
        return []

    def _compute_ops(self) -> None:
        orig_staged_keys = {ik for _, _, ik in self.orig_staged}
        current_staged = set(self._staged_paths)
        self.to_stage   = {ik.split(":", 1)[1] for ik in current_staged - orig_staged_keys}
        self.to_unstage = {ik.split(":", 1)[1] for ik in orig_staged_keys - current_staged}

    # --- compose / mount ---

    def compose(self) -> ComposeResult:
        yield Static(_context_bar_text(), classes="context-bar")
        with Vertical(classes="section"):
            yield Label("  Staged  (space to unstage)", classes="section-label")
            yield DataTable(id="staged", cursor_type="row", zebra_stripes=True)
            yield Input(id="filter-staged", placeholder="Filter…", classes="section-filter")
        with Vertical(classes="section", id="section-modified"):
            yield Label("  Modified  (space to stage)", classes="section-label")
            yield DataTable(id="modified", cursor_type="row", zebra_stripes=True)
            yield Input(id="filter-modified", placeholder="Filter…", classes="section-filter")
        with Vertical(classes="section", id="section-deleted"):
            yield Label("  Deleted  (space to stage)", classes="section-label")
            yield DataTable(id="deleted", cursor_type="row", zebra_stripes=True)
            yield Input(id="filter-deleted", placeholder="Filter…", classes="section-filter")
        with Vertical(classes="section", id="section-untracked"):
            yield Label("  Untracked  (space to stage)", classes="section-label")
            yield DataTable(id="untracked", cursor_type="row", zebra_stripes=True)
            yield Input(id="filter-untracked", placeholder="Filter…", classes="section-filter")
        yield Footer()

    def _update_section_visibility(self) -> None:
        show_modified  = bool(self.orig_modified)  or any(s not in ("A", "?", "D") for s, _, _ in self.orig_staged)
        show_deleted   = bool(self.orig_deleted)   or any(s == "D"               for s, _, _ in self.orig_staged)
        show_untracked = bool(self.orig_untracked) or any(s in ("A", "?")        for s, _, _ in self.orig_staged)
        self.query_one("#section-modified").display  = show_modified
        self.query_one("#section-deleted").display   = show_deleted
        self.query_one("#section-untracked").display = show_untracked

    def on_mount(self) -> None:
        self._init_table("staged")
        self._init_table("modified")
        self._init_table("deleted")
        self._init_table("untracked")
        self._update_section_visibility()
        # Focus the first non-empty visible table; fall back to staged.
        for tid in ("modified", "deleted", "untracked", "staged"):
            t = self.query_one(f"#{tid}", DataTable)
            if t.display and t.row_count > 0:
                t.focus()
                return

    def _init_table(self, table_id: str) -> None:
        try:
            table = self.query_one(f"#{table_id}", DataTable)
        except Exception:
            return
        table.add_column("STATUS", width=12)
        table.add_column("FILE")
        for status, path, item_key in self._files_for_section(table_id):
            self._add_row(table, status, path, item_key, table_id)

    def _add_row(self, table: DataTable, status: str, path: str, item_key: str, section_id: str) -> None:
        from rich.text import Text as RichText

        is_untracked_type = status in ("A", "?")

        label = FILE_STATUS_LABELS.get(status, status)

        # Status label: matrix green in staged section, type colour everywhere else
        if section_id == "staged":
            status_style = "bold #00ff41"
        elif is_untracked_type:
            status_style = "bold #ff5555"
        elif status == "D":
            status_style = "bold #ff5555"
        else:
            status_style = "bold #ffb300"

        # Filename colour
        if is_untracked_type:
            path_style = "#ff5555"
        elif status in ("M", "D", "R", "C"):
            path_style = "#ffb300"
        else:
            path_style = ""

        path_cell = RichText(path, style=path_style) if path_style else RichText(path)
        table.add_row(RichText(label, style=status_style), path_cell, key=item_key)

    # --- refresh ---

    def _refresh_table(self, table_id: str) -> None:
        try:
            table = self.query_one(f"#{table_id}", DataTable)
        except Exception:
            return
        all_files = self._files_for_section(table_id)
        filt = self._section_filters.get(table_id, "").lower()
        files = [(s, p, ik) for s, p, ik in all_files if filt in p.lower()] if filt else all_files
        cursor = table.cursor_row
        table.clear()
        for status, path, item_key in files:
            self._add_row(table, status, path, item_key, table_id)
        table.move_cursor(row=min(cursor, max(0, table.row_count - 1)))

    def _refresh_all(self) -> None:
        for tid in ("staged", "modified", "deleted", "untracked"):
            self._refresh_table(tid)

    # --- helpers ---

    def _focused_table(self) -> DataTable | None:
        w = self.focused
        return w if isinstance(w, DataTable) else None

    # --- event handlers ---

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("filter-"):
            table_id = event.input.id.removeprefix("filter-")
            self._section_filters[table_id] = event.value
            self._refresh_table(table_id)
            event.stop()

    def on_key(self, event) -> None:
        focused = self.focused

        # Keys while a filter Input is focused
        if isinstance(focused, Input) and focused.id and focused.id.startswith("filter-"):
            table_id = focused.id.removeprefix("filter-")
            if event.key == "escape":
                self._section_filters[table_id] = ""
                focused.value = ""
                focused.display = False
                self._refresh_table(table_id)
                try:
                    self.query_one(f"#{table_id}", DataTable).focus()
                except Exception:
                    pass
                event.prevent_default()
            elif event.key == "enter":
                try:
                    self.query_one(f"#{table_id}", DataTable).focus()
                except Exception:
                    pass
                event.prevent_default()
            return  # let Input handle all other keys (typing, backspace…)

        # Keys while a DataTable is focused
        table = self._focused_table()
        if table is None:
            return

        if event.key == "down":
            table.move_cursor(row=table.cursor_row + 1)
            event.prevent_default()
        elif event.key == "up":
            table.move_cursor(row=table.cursor_row - 1)
            event.prevent_default()
        elif event.key == "space":
            self.action_toggle_select()
            event.prevent_default()
        elif event.key == "enter":
            self.action_confirm()
            event.prevent_default()

    # --- actions ---

    def action_activate_filter(self) -> None:
        table = self._focused_table()
        if table is None:
            return
        filter_id = f"filter-{table.id}"
        try:
            filter_input = self.query_one(f"#{filter_id}", Input)
            filter_input.display = True
            filter_input.value = ""
            filter_input.focus()
        except Exception:
            pass

    def action_toggle_select(self) -> None:
        table = self._focused_table()
        if table is None or table.row_count == 0:
            return
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        item_key = cell_key.row_key.value
        cursor = table.cursor_row

        if table.id == "staged":
            self._staged_paths.remove(item_key)
        else:
            if item_key not in self._staged_paths:
                self._staged_paths.append(item_key)

        self._refresh_all()
        # Keep cursor near same position in the table the user was in
        table.move_cursor(row=min(cursor, max(0, table.row_count - 1)))

    def action_confirm(self) -> None:
        if not self._staged_paths:
            # Nothing staged — apply any pending unstage ops and exit without commit modal
            self._compute_ops()
            self.exit()
            return
        if get_config("fmt.on_commit") == "true":
            self.push_screen(FmtModal(), self._on_fmt_before_commit)
        else:
            self.push_screen(CommitModal(get_ticket()), self._on_commit_modal)

    def _on_fmt_before_commit(self, _: None) -> None:
        self._reload_statuses()
        if not self._staged_paths:
            self._compute_ops()
            self.exit()
            return
        self.push_screen(CommitModal(get_ticket()), self._on_commit_modal)

    def _on_commit_modal(self, message: str | None) -> None:
        self._compute_ops()
        if message is not None:
            self.commit_message = message
        self.exit()

    def action_quit(self) -> None:
        self.aborted = True
        self.exit()

    def action_run_fmt(self) -> None:
        if isinstance(self.focused, Input):
            return
        self.push_screen(FmtModal(), self._on_fmt_closed)

    def _on_fmt_closed(self, _: None) -> None:
        self._reload_statuses()

    def _reload_statuses(self) -> None:
        """Re-fetch git status after external changes (e.g. formatting) and refresh all tables."""
        staged, modified, deleted, untracked = _get_file_statuses()
        self.orig_staged   = [(s, p, f"staged:{p}")    for s, p in staged]
        self.orig_modified = [(s, p, f"modified:{p}")  for s, p in modified]
        self.orig_deleted  = [(s, p, f"deleted:{p}")   for s, p in deleted]
        self.orig_untracked= [(s, p, f"untracked:{p}") for s, p in untracked]
        self.file_info = {}
        for status, path, ik in self.orig_staged:
            self.file_info[ik] = (status, "staged")
        for status, path, ik in self.orig_modified:
            self.file_info[ik] = (status, "modified")
        for status, path, ik in self.orig_deleted:
            self.file_info[ik] = (status, "deleted")
        for status, path, ik in self.orig_untracked:
            self.file_info[ik] = (status, "untracked")
        # Drop any staged item_keys that no longer exist in the refreshed status
        self._staged_paths = [ik for ik in self._staged_paths if ik in self.file_info]
        self._update_section_visibility()
        self._refresh_all()


@main.command("branch")
@click.argument("name", required=False)
@click.option("--all", "show_all", is_flag=True, help="Browse all branches for the configured project and set the active ticket")
def cmd_branch(name: str | None, show_all: bool) -> None:
    """Switch to a ticket branch interactively, or create one with the given name."""
    if show_all and not name:
        projects = get_projects()
        if not projects:
            raise click.ClickException(
                "No projects configured. Run: jg config set projects MYPROJECT"
            )

        all_branches = _get_local_branches()
        matching = [
            (b, cur) for b, cur in all_branches
            if any(b.upper().startswith(p.upper() + "-") for p in projects)
        ]

        if not matching:
            project_list = ", ".join(projects)
            click.echo(f"No local branches found for projects: {project_list}.")
            return

        app = BranchPickerApp(matching)
        app.run()

        if not app.selected_branch:
            click.echo("No branch selected.", err=True)
            return

        # Extract ticket key from the branch name using whichever project prefix matches
        for project in projects:
            m = re.match(rf"^({re.escape(project)}-\d+)", app.selected_branch, re.IGNORECASE)
            if m:
                ticket_key = m.group(1).upper()
                save_ticket(ticket_key)
                click.echo(f"Ticket set to {ticket_key}")
                break

        subprocess.run(["git", "switch", app.selected_branch], check=True)
        return

    ticket = ensure_ticket()

    if name:
        branch_name = f"{ticket}-{name}"
        default_branch = _get_default_branch()
        click.echo(f"Creating branch: {branch_name} (from {default_branch})")
        subprocess.run(["git", "switch", "-C", branch_name, default_branch], check=True)
        return

    if _get_current_branch() in ("main", "master"):
        jira_client = get_jira_client()
        prompt_app = BranchPromptApp(ticket, jira_client)
        prompt_app.run()
        if not prompt_app.branch_suffix:
            click.echo("Cancelled.", err=True)
            return
        branch_name = f"{ticket}-{prompt_app.branch_suffix}"
        default_branch = _get_default_branch()
        click.echo(f"Creating branch: {branch_name} (from {default_branch})")
        subprocess.run(["git", "switch", "-C", branch_name, default_branch], check=True)

    all_branches = _get_local_branches()
    matching = [(b, cur) for b, cur in all_branches if ticket.lower() in b.lower()]

    if not matching:
        return

    app = BranchPickerApp(matching)
    app.run()

    if app.selected_branch:
        subprocess.run(["git", "switch", app.selected_branch], check=True)


@main.command("add")
def cmd_add() -> None:
    """Interactively stage and unstage files."""
    ticket = ensure_ticket()

    if _get_current_branch() in ("main", "master"):
        jira_client = get_jira_client()
        prompt_app = BranchPromptApp(ticket, jira_client)
        prompt_app.run()
        if not prompt_app.branch_suffix:
            click.echo("Cancelled.", err=True)
            return
        branch_name = f"{ticket}-{prompt_app.branch_suffix}"
        default_branch = _get_default_branch()
        click.echo(f"Creating branch: {branch_name} (from {default_branch})")
        subprocess.run(["git", "switch", "-C", branch_name, default_branch], check=True)

    staged, modified, deleted, untracked = _get_file_statuses()

    if not staged and not modified and not deleted and not untracked:
        click.echo("Nothing to do — working tree clean.")
        return

    app = FilePickerApp(staged, modified, deleted, untracked)
    app.run()

    if app.aborted:
        click.echo("Aborted.", err=True)
        return

    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    if app.to_stage:
        subprocess.run(["git", "add", "--", *app.to_stage], check=True, cwd=git_root)
        click.echo(f"Staged {len(app.to_stage)} file(s):")
        for f in sorted(app.to_stage):
            click.echo(f"  + {f}")

    if app.to_unstage:
        subprocess.run(["git", "restore", "--staged", "--", *app.to_unstage], check=True, cwd=git_root)
        click.echo(f"Unstaged {len(app.to_unstage)} file(s):")
        for f in sorted(app.to_unstage):
            click.echo(f"  - {f}")

    if app.commit_message:
        branch = _get_current_branch()
        if branch is None or not branch.lower().startswith(ticket.lower()):
            suffix = click.prompt(
                f"Not on a {ticket} branch. Branch suffix (will create {ticket}-<suffix>)"
            )
            branch_name = f"{ticket}-{suffix}"
            click.echo(f"Creating branch: {branch_name}")
            subprocess.run(["git", "switch", "-C", branch_name], check=True)
        full_msg = f"{ticket} {app.commit_message}"
        subprocess.run(["git", "commit", "--no-verify", "-m", full_msg], check=True)
    elif not app.to_stage and not app.to_unstage:
        click.echo("No changes made.", err=True)


# ---------------------------------------------------------------------------
# jg fmt
# ---------------------------------------------------------------------------

@main.group("fmt", invoke_without_command=True)
@click.pass_context
def cmd_fmt(ctx: click.Context) -> None:
    """Run configured formatters against modified files, or manage formatter config."""
    if ctx.invoked_subcommand is None:
        _run_formatters()


def _get_binary_paths(paths: list[str], git_root: str) -> set[str]:
    """Return the subset of paths that git identifies as binary (w/-text) via git ls-files --eol."""
    if not paths:
        return set()
    result = subprocess.run(
        ["git", "ls-files", "--eol", "--", *paths],
        capture_output=True, text=True, cwd=git_root,
    )
    binary: set[str] = set()
    for line in result.stdout.splitlines():
        # Format: "i/<eol>\tw/<eol>\tattr/<attrs>\t<path>"
        # Use split(None, 3) to handle varying whitespace between columns.
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        w_eol = parts[1]  # e.g. "w/lf", "w/-text", "w/crlf"
        path = parts[3]
        if w_eol == "w/-text":
            binary.add(path)
    return binary


def _fix_eof(abs_path: str) -> tuple[bool, str]:
    """Ensure file ends with exactly one newline. Returns (ok, error_msg)."""
    try:
        with open(abs_path, "rb") as f:
            content = f.read()
        if not content:
            return True, ""
        fixed = content.rstrip(b"\r\n") + b"\n"
        if fixed != content:
            with open(abs_path, "wb") as f:
                f.write(fixed)
        return True, ""
    except OSError as e:
        return False, str(e)


def _build_fmt_table() -> "tuple[str, object]":
    """Run all formatters and return (message | None, table | None).

    Returns ("clean", None) if working tree is clean.
    Otherwise returns (None, rich.table.Table) with all results.
    """
    from rich.table import Table
    from rich.text import Text

    user_formatters = get_formatters()

    staged, modified, deleted, untracked = _get_file_statuses()
    seen: set[str] = set()
    all_paths: list[str] = []
    for _, path in staged + modified + untracked:
        if path not in seen:
            seen.add(path)
            all_paths.append(path)

    if not all_paths:
        return "clean", None

    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    binary_paths = _get_binary_paths(all_paths, git_root)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("", width=1, no_wrap=True)
    table.add_column("File", style="dim")
    table.add_column("Formatter", style="cyan", no_wrap=True)
    table.add_column("Exit", no_wrap=True)
    table.add_column("Note", style="red")

    for path in sorted(all_paths):
        abs_path = os.path.join(git_root, path)
        basename = os.path.basename(path)

        if path in binary_paths:
            table.add_row(
                Text("—", style="dim"),
                Text(path, style="dim"),
                Text("—", style="dim"),
                Text("—", style="dim"),
                Text("skipped (binary)", style="dim"),
            )
            continue

        # Built-in eof formatter — runs on every text file
        ok, err = _fix_eof(abs_path)
        if ok:
            table.add_row(Text("✓", style="bold green"), path, "eof", Text("0", style="green"), "")
        else:
            table.add_row(Text("✗", style="bold red"), path, "eof", Text("1", style="red"), err)

        # User-configured formatters
        for fmt in user_formatters:
            if fnmatch.fnmatch(basename, fmt["glob"]):
                cmd = fmt["cmd"].replace("{}", abs_path)
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    table.add_row(
                        Text("✓", style="bold green"),
                        path,
                        fmt["name"],
                        Text("0", style="green"),
                        "",
                    )
                else:
                    error_msg = (result.stderr or result.stdout or "").strip()
                    table.add_row(
                        Text("✗", style="bold red"),
                        path,
                        fmt["name"],
                        Text(str(result.returncode), style="red"),
                        error_msg,
                    )

    return None, table


def _run_formatters() -> None:
    from rich.console import Console

    msg, table = _build_fmt_table()
    if msg == "clean":
        click.echo("Nothing to format — working tree clean.")
        return
    Console().print(table)


@cmd_fmt.command("add")
@click.argument("name", required=False)
def cmd_fmt_add(name: str | None) -> None:
    """Add a new formatter (prompts for glob and command)."""
    formatters = get_formatters()
    if not name:
        name = click.prompt("Formatter name")
    if any(f["name"] == name for f in formatters):
        raise click.ClickException(
            f"A formatter named '{name}' already exists. "
            f"Delete it first with: jg fmt delete {name}"
        )
    glob_pattern = click.prompt("File glob (e.g. *.hcl, *.tf)")
    cmd = click.prompt("Command (use {} for the filename, e.g. terragrunt hcl fmt {})")
    formatters.append({"name": name, "glob": glob_pattern, "cmd": cmd})
    set_formatters(formatters)
    click.echo(f"Added formatter '{name}'.")


@cmd_fmt.command("list")
def cmd_fmt_list() -> None:
    """List all configured formatters."""
    formatters = get_formatters()
    if not formatters:
        click.echo("No formatters configured.")
        return
    for fmt in formatters:
        click.echo(fmt["name"])
        click.echo(f"  glob:    {fmt['glob']}")
        click.echo(f"  command: {fmt['cmd']}")


@cmd_fmt.command("delete")
@click.argument("name")
def cmd_fmt_delete(name: str) -> None:
    """Delete a formatter by name."""
    formatters = get_formatters()
    new_formatters = [f for f in formatters if f["name"] != name]
    if len(new_formatters) == len(formatters):
        raise click.ClickException(f"No formatter named '{name}'.")
    set_formatters(new_formatters)
    click.echo(f"Deleted formatter '{name}'.")


@main.command("push")
def cmd_push() -> None:
    """Push the current branch and open any linked open PR in the browser."""
    ticket = ensure_ticket()

    # Capture stderr so we can parse GitHub's "Create a pull request" URL,
    # but still stream stdout normally.
    result = subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        sys.exit(result.returncode)

    # Pull out any https://github.com URL from git's remote: lines
    push_url: str | None = None
    for line in result.stderr.splitlines():
        if "https://" in line and "github.com" in line:
            for word in line.split():
                if word.startswith("https://"):
                    push_url = word
                    break
            if push_url:
                break

    # Prefer an existing open PR from JIRA; fall back to the push URL
    try:
        jira = get_jira_client()
        issue = jira.issue(ticket, fields=["summary"])
        prs = _get_prs(issue.id)
        open_prs = [p for p in prs if p.get("status") == "OPEN"]
        if open_prs:
            url = open_prs[0]["url"]
            click.echo(f"Opening PR: {url}")
            webbrowser.open(url)
            return
    except Exception:
        pass  # Don't fail the push if JIRA lookup errors

    if push_url:
        click.echo(f"Opening: {push_url}")
        webbrowser.open(push_url)


@main.command("reset")
def cmd_reset() -> None:
    """Switch to the main branch and pull latest from origin.

    Offers to stash uncommitted changes if they would block the branch switch.
    """
    default_branch = _get_default_branch()
    current_branch = _get_current_branch()
    stashed = False

    # --- Switch to default branch if needed ---
    if current_branch != default_branch:
        click.echo(f"Switching to {default_branch}…")
        result = subprocess.run(
            ["git", "switch", default_branch],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            click.echo(err, err=True)
            # Only offer stash when git says changes would be overwritten
            is_dirty = "overwritten" in err or "commit your changes or stash" in err
            if is_dirty and click.confirm(
                "Stash your local changes and continue?", default=False
            ):
                subprocess.run(
                    ["git", "stash", "--include-untracked"], check=True
                )
                stashed = True
                switch_retry = subprocess.run(
                    ["git", "switch", default_branch],
                    capture_output=True, text=True,
                )
                if switch_retry.returncode != 0:
                    raise click.ClickException(
                        f"Still could not switch to {default_branch}:\n"
                        + switch_retry.stderr.strip()
                    )
            else:
                raise click.Abort()

    # --- Pull latest ---
    click.echo(f"Pulling latest from origin/{default_branch}…")
    pull_result = subprocess.run(["git", "pull", "origin", default_branch])
    if pull_result.returncode != 0:
        if stashed:
            click.echo(
                "\nYour changes are safely stashed. "
                "Run 'git stash pop' to restore them.",
                err=True,
            )
        raise click.Abort()

    # --- Offer to restore stash ---
    if stashed:
        if click.confirm("Restore your stashed changes?", default=True):
            pop = subprocess.run(
                ["git", "stash", "pop"], capture_output=True, text=True
            )
            if pop.returncode != 0:
                click.echo(pop.stdout.strip(), err=True)
                click.echo(
                    "Stash pop had conflicts — resolve them, then run 'git stash drop'.",
                    err=True,
                )
            else:
                click.echo("Stashed changes restored.")


@main.command("sync")
def cmd_sync() -> None:
    """Rebase the current branch onto the latest default branch from origin."""
    current_branch = _get_current_branch()
    if current_branch is None:
        raise click.ClickException("Not on a branch (detached HEAD).")

    default_branch = _get_default_branch()
    if current_branch == default_branch:
        raise click.ClickException(
            f"Already on {default_branch}. Use 'jg reset' to pull the latest."
        )

    click.echo(f"Fetching origin…")
    fetch = subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True)
    if fetch.returncode != 0:
        raise click.ClickException(f"Fetch failed:\n{fetch.stderr.strip()}")

    click.echo(f"Rebasing {current_branch} onto origin/{default_branch}…")
    rebase = subprocess.run(["git", "rebase", f"origin/{default_branch}"])
    if rebase.returncode != 0:
        click.echo(
            "\nRebase conflict detected. Resolve the conflicts then run:\n"
            "  git rebase --continue\n"
            "Or to cancel:\n"
            "  git rebase --abort",
            err=True,
        )
        raise click.Abort()

    click.echo(f"Done. {current_branch} is up to date with origin/{default_branch}.")


class PruneApp(App):
    CSS = """
    Screen { background: #0a0e0a; }
    .context-bar {
        height: 1;
        background: #0d1a0d;
        color: #00ff41;
        padding: 0 1;
        text-style: bold;
    }
    DataTable {
        height: 1fr;
        background: #0a0e0a;
    }
    DataTable > .datatable--header { background: #0d1a0d; color: #00e5ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #003d00; color: #00ff41; text-style: bold; }
    DataTable > .datatable--hover  { background: #001a00; }
    DataTable > .datatable--odd-row  { background: #080c08; color: #b8d4b8; }
    DataTable > .datatable--even-row { background: #0a0e0a; color: #b8d4b8; }
    Footer { background: #0d1a0d; color: #4d8a4d; }
    Footer > .footer--key { background: #152015; color: #00e5ff; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("a", "select_all", "All", show=True),
        Binding("d", "show_diff", "Diff", show=True),
        Binding("x", "delete_selected", "Delete", show=True),
        Binding("s", "switch_branch", "Switch", show=True),
    ]

    def __init__(self, branches: list[dict]) -> None:
        super().__init__()
        self.branches = list(branches)  # [{"name": str, "status": str}]
        self._selected: set[str] = set()
        self.deleted: list[str] = []
        self.branch_to_switch: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(_context_bar_text(), classes="context-bar")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        from rich.text import Text
        table = self.query_one(DataTable)
        table.add_column("", key="sel", width=3)
        table.add_column("Branch", key="name")
        table.add_column("Status", key="status")
        for b in self.branches:
            table.add_row(" ", Text(b["name"], style="#00e5ff"), self._status_text(b["status"]), key=b["name"])
        table.focus()

    @staticmethod
    def _status_text(status: str):
        from rich.text import Text
        if status == "remote deleted":
            t = Text("remote deleted")
            t.stylize("#ffb300")
            return t
        t = Text("never pushed")
        t.stylize("#00e5ff")
        return t

    @staticmethod
    def _sel_marker(selected: bool):
        from rich.text import Text
        if selected:
            t = Text("●")
            t.stylize("bold #00ff41")
            return t
        return " "

    def _cursor_branch(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        return cell_key.row_key.value

    def action_toggle_select(self) -> None:
        name = self._cursor_branch()
        if name is None:
            return
        table = self.query_one(DataTable)
        if name in self._selected:
            self._selected.discard(name)
        else:
            self._selected.add(name)
        table.update_cell(name, "sel", self._sel_marker(name in self._selected))

    def action_select_all(self) -> None:
        table = self.query_one(DataTable)
        all_names = {b["name"] for b in self.branches}
        if self._selected == all_names:
            self._selected.clear()
            for name in all_names:
                table.update_cell(name, "sel", " ")
        else:
            self._selected = all_names.copy()
            for name in all_names:
                table.update_cell(name, "sel", self._sel_marker(True))

    def action_delete_selected(self) -> None:
        if not self._selected:
            self.notify("No branches selected — press Space to select.", severity="warning")
            return
        count = len(self._selected)
        self.push_screen(
            ConfirmModal(f"Delete {count} selected branch(es)?"),
            self._on_confirm_delete,
        )

    def _on_confirm_delete(self, confirmed: bool) -> None:
        if not confirmed:
            return
        table = self.query_one(DataTable)
        to_delete = sorted(self._selected)
        failed = []
        for name in to_delete:
            r = subprocess.run(["git", "branch", "-D", name], capture_output=True, text=True)
            if r.returncode == 0:
                self.deleted.append(name)
                table.remove_row(name)
                self.branches = [b for b in self.branches if b["name"] != name]
            else:
                failed.append((name, r.stderr.strip()))
        self._selected -= set(self.deleted)
        if failed:
            for name, err in failed:
                self.notify(f"Failed: {name}: {err}", severity="error", timeout=6)
        if table.row_count == 0:
            self.exit()

    def action_show_diff(self) -> None:
        name = self._cursor_branch()
        if name is None:
            return
        base = _get_default_branch()
        self.push_screen(BranchDiffModal(name, base))

    def action_switch_branch(self) -> None:
        name = self._cursor_branch()
        if name is None:
            return
        self.branch_to_switch = name
        self.exit()

    def action_quit(self) -> None:
        self.exit()


@main.command("prune")
def cmd_prune() -> None:
    """Interactively prune local branches with no remote (deleted upstream or never pushed)."""
    click.echo("Fetching and pruning remote refs…")
    fetch = subprocess.run(["git", "fetch", "--prune"], capture_output=True, text=True)
    if fetch.returncode != 0:
        raise click.ClickException(f"Fetch failed:\n{fetch.stderr.strip()}")

    result = subprocess.run(["git", "branch", "-vv"], capture_output=True, text=True, check=True)

    current = _get_current_branch()
    default_branch = _get_default_branch()

    branches: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.lstrip("* ").split()
        if not parts:
            continue
        branch = parts[0]
        if branch in (current, default_branch):
            continue
        if ": gone]" in line:
            branches.append({"name": branch, "status": "remote deleted"})
        elif "[origin/" not in line:
            branches.append({"name": branch, "status": "never pushed"})

    if not branches:
        click.echo("No prunable local branches found.")
        return

    app = PruneApp(branches)
    app.run()

    if app.deleted:
        click.echo(f"Deleted {len(app.deleted)} branch(es): {', '.join(app.deleted)}")

    if app.branch_to_switch:
        branch = app.branch_to_switch
        r = subprocess.run(["git", "switch", branch], capture_output=True, text=True)
        if r.returncode == 0:
            click.echo(f"Switched to branch: {branch}")
        else:
            raise click.ClickException(f"Failed to switch to '{branch}':\n{r.stderr.strip()}")


@main.command("commit", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("message")
@click.argument("git_args", nargs=-1, type=click.UNPROCESSED)
def cmd_commit(message: str, git_args: tuple[str, ...]) -> None:
    """Commit with message prefixed by the current ticket (TICKET-123 <message>)."""
    _check_not_main_branch()
    ticket = get_ticket()
    if not ticket:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)
    commit_msg = f"{ticket} {message}"
    subprocess.run(["git", "commit", "-m", commit_msg, *git_args], check=True)


FILE_STATUS_LABELS: dict[str, str] = {
    "M": "modified",
    "D": "deleted",
    "?": "untracked",
    "R": "renamed",
    "A": "added",
    "C": "copied",
}

FILE_STATUS_STYLES: dict[str, str] = {
    "M": "yellow",
    "D": "red",
    "?": "cyan",
    "R": "blue",
    "A": "green",
    "C": "magenta",
}

# Default filename colour in the file picker (when not selected)
FILE_PATH_STYLES: dict[str, str] = {
    "M": "dark_orange",
    "?": "red",
}

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


@main.command("debug")
@click.argument("ticket")
def cmd_debug(ticket: str) -> None:
    """Dump every raw JIRA field for a ticket — useful for inspecting API shape."""
    import json
    from rich.console import Console
    from rich.syntax import Syntax

    jira = get_jira_client()
    try:
        issue = jira.issue(ticket)
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    console = Console()
    console.print(f"\n[bold #00e5ff]{issue.key}[/]  [#b8d4b8]{issue.fields.summary}[/]\n")

    raw = issue.raw.get("fields", {})
    # Pretty-print as JSON, skipping null/empty values
    filtered = {k: v for k, v in raw.items() if v not in (None, [], "", {})}
    console.print(Syntax(json.dumps(filtered, indent=2, default=str), "json", theme="monokai"))


@main.command("info")
@click.argument("ticket", required=False)
def cmd_info(ticket: str | None) -> None:
    """Show details for the current (or given) ticket."""
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    key = ticket or get_ticket()
    if not key:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)

    jira = get_jira_client()
    try:
        issue = jira.issue(
            key,
            fields=["summary", "status", "assignee", "reporter", "priority", "labels", "description", "issuetype"],
        )
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    f = issue.fields
    assignee  = f.assignee.displayName if f.assignee else "Unassigned"
    reporter  = f.reporter.displayName if f.reporter else "Unknown"
    labels    = ", ".join(f.labels) if f.labels else "—"
    priority  = f.priority.name if f.priority else "—"
    status    = f.status.name
    description = (f.description or "").strip()

    status_style   = STATUS_STYLES.get(status.lower(), "white")
    priority_style = PRIORITY_STYLES.get(priority.lower(), "white")

    url = f"{get_jira_server()}/browse/{issue.key}"

    # Two-column metadata grid
    meta = Table.grid(padding=(0, 3), expand=False)
    meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
    meta.add_column(min_width=22)
    meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
    meta.add_column(min_width=16)

    meta.add_row("STATUS",   Text(status, style=status_style),
                 "PRIORITY", Text(priority, style=priority_style))
    meta.add_row("ASSIGNEE", assignee,
                 "REPORTER", reporter)
    meta.add_row("LABELS",   Text(labels, style="cyan"), "", "")

    # Description block
    truncated = (description[:800] + "\n[dim]…truncated[/dim]") if len(description) > 800 else (description or "[dim]—[/dim]")
    desc_block = Group(
        Rule(style="bright_black"),
        Text.from_markup(f"[bold bright_black]DESCRIPTION[/bold bright_black]"),
        Text.from_markup(f"\n{truncated}"),
    )

    url_line = Text.assemble(("URL  ", "bold bright_black"), (url, f"link {url} bright_cyan"))

    content = Group(
        Text(f.summary, style="bold white"),
        Text(""),
        meta,
        Text(""),
        url_line,
        Text(""),
        desc_block,
    )

    Console().print(Panel(content, title=f"[bold bright_blue]{issue.key}[/bold bright_blue]", border_style="bright_blue", padding=(1, 2)))


@main.command("open")
@click.argument("ticket", required=False)
def cmd_open(ticket: str | None) -> None:
    """Open the current (or given) ticket in the browser."""
    key = ticket or get_ticket()
    if not key:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)
    url = f"{get_jira_server()}/browse/{key}"
    click.echo(f"Opening {url}")
    webbrowser.open(url)



@main.command("prs")
@click.argument("ticket", required=False)
def cmd_prs(ticket: str | None) -> None:
    """Browse PRs linked to the current (or given) ticket. Press Enter to open in browser."""
    key = ticket or ensure_ticket()

    jira = get_jira_client()
    try:
        issue = jira.issue(key, fields=["summary"])
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    click.echo(f"Fetching PRs for {key}…", err=True)
    try:
        prs = _get_prs(issue.id)
    except requests.HTTPError as e:
        raise click.ClickException(f"Failed to fetch PRs: {e}") from e

    if not prs:
        click.echo(f"No PRs linked to {key}.")
        return

    sorted_prs = sorted(prs, key=lambda p: (p.get("status") != "OPEN", p.get("lastUpdate", "")))
    app = PrPickerApp(sorted_prs, open_on_enter=True)
    app.run()

    if app.branch_to_switch:
        branch = app.branch_to_switch
        # Check whether the local branch already exists.
        local_exists = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True,
        ).returncode == 0

        if local_exists:
            result = subprocess.run(["git", "switch", branch], capture_output=True, text=True)
            if result.returncode == 0:
                click.echo(f"Switched to branch: {branch}")
            else:
                raise click.ClickException(f"Failed to switch to '{branch}':\n{result.stderr.strip()}")
        else:
            default = _get_default_branch()
            click.echo(
                f"Branch '{branch}' not found locally. Creating from {default}…",
                err=True,
            )
            result = subprocess.run(
                ["git", "switch", "-c", branch, default],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                click.echo(f"Created and switched to branch: {branch} (from {default})")
            else:
                raise click.ClickException(
                    f"Failed to create branch '{branch}' from {default}:\n{result.stderr.strip()}"
                )


@main.group("config")
def cmd_config() -> None:
    """Get and set configuration values."""


@cmd_config.command("get")
@click.argument("key")
def config_get(key: str) -> None:
    """Get a config value."""
    value = get_config(key)
    if value is None:
        click.echo(f"{key} is not set", err=True)
        sys.exit(1)
    click.echo(value)


@cmd_config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config value.

    Standard keys: server, email, token, projects

    Custom fields for the ticket picker: fields.<PROJECT> (comma-separated field IDs)

    Named filters are managed interactively in 'jg set' via the 'f' key.
    """
    set_config(key, value)
    click.echo(f"{key} = {value}")


@cmd_config.command("list")
def config_list() -> None:
    """List all config values and configured named filters."""
    known = [
        ("server",  "JIRA server URL, e.g. https://yourcompany.atlassian.net",              False),
        ("email",   "JIRA account email",                                                    False),
        ("token",   "JIRA API token",                                                        True),
        ("projects", "Project key(s) for the ticket picker, e.g. SWY or SWY,ABC (optional)", False),
    ]
    config = _read_config()
    for key, description, secret in known:
        value = config.get(key)
        if value:
            display = "****" if secret else value
            click.echo(f"{key} = {display}")
        else:
            click.echo(f"{key} = (not set)  # {description}")

    # Named filters — one section per project
    filter_projects = sorted({
        k[len("filters."):] for k in config
        if k.startswith("filters.") and "." not in k[len("filters."):]
    })
    if filter_projects:
        click.echo()
        for proj in filter_projects:
            filters = get_filters_for_project(proj)
            default = get_active_filter_name(proj)
            for f in filters:
                marker = " (default)" if f["name"] == default else ""
                click.echo(f"filters.{proj}  {f['name']}{marker}")
                click.echo(f"  jql: {f['jql']}")

    formatters = get_formatters()
    if formatters:
        click.echo()
        for fmt in formatters:
            click.echo(f"fmt  {fmt['name']}")
            click.echo(f"  glob:    {fmt['glob']}")
            click.echo(f"  command: {fmt['cmd']}")



@main.command("hook")
@click.option(
    "--shell", "shell",
    default="fish",
    type=click.Choice(["fish", "bash", "zsh"]),
    show_default=True,
    help="Shell to emit hook for",
)
def cmd_hook(shell: str) -> None:
    """Print the shell hook to set JG_TICKET in the current shell.

    Fish:  eval (jg hook)
    Bash:  eval "$(jg hook --shell bash)"
    Zsh:   eval "$(jg hook --shell zsh)"

    The bash/zsh hook defines __jg_ps1 which can be spliced into PS1/PROMPT.
    For fish/tide prompt integration run: jg setup
    """
    if shell == "fish":
        click.echo(f"""\
# Seed JG_TICKET from the persisted default when this shell starts
if not set -q JG_TICKET
    set -l _jg_default (cat {STATE_FILE} 2>/dev/null)
    if test -n "$_jg_default"
        set -gx JG_TICKET $_jg_default
    end
end

function jg
    command jg $argv
    set -l _jg_exit $status
    switch "$argv[1]"
        case set
            set -l _jg_ticket (cat {STATE_FILE} 2>/dev/null)
            if test -n "$_jg_ticket"
                set -gx JG_TICKET $_jg_ticket
            end
        case branch
            # Only update JG_TICKET when --all is passed; plain 'jg branch' does not change STATE_FILE
            if contains -- --all $argv
                set -l _jg_ticket (cat {STATE_FILE} 2>/dev/null)
                if test -n "$_jg_ticket"
                    set -gx JG_TICKET $_jg_ticket
                end
            end
        case clear
            set -e JG_TICKET
    end
    return $_jg_exit
end""")
    else:
        # bash and zsh share the same syntax
        click.echo(f"""\
# Seed JG_TICKET from the persisted default when this shell starts
if [ -z "${{JG_TICKET:-}}" ]; then
    _jg_default=$(cat {STATE_FILE} 2>/dev/null)
    if [ -n "$_jg_default" ]; then
        export JG_TICKET="$_jg_default"
    fi
    unset _jg_default
fi

# Splice into your prompt:
#   bash: PS1='$(__jg_ps1)\\$ '
#   zsh:  PROMPT='$(__jg_ps1)%% '
__jg_ps1() {{
    [ -n "${{JG_TICKET:-}}" ] && printf '%s ' "$JG_TICKET"
}}

jg() {{
    command jg "$@"
    local _jg_exit=$?
    case "$1" in
        set)
            local _jg_ticket
            _jg_ticket=$(cat {STATE_FILE} 2>/dev/null)
            if [ -n "$_jg_ticket" ]; then
                export JG_TICKET="$_jg_ticket"
            fi
            ;;
        branch)
            # Only update JG_TICKET when --all is passed; plain 'jg branch' does not change STATE_FILE
            local _jg_has_all=0
            for _jg_arg in "$@"; do
                [ "$_jg_arg" = "--all" ] && _jg_has_all=1 && break
            done
            if [ "$_jg_has_all" = "1" ]; then
                local _jg_ticket
                _jg_ticket=$(cat {STATE_FILE} 2>/dev/null)
                if [ -n "$_jg_ticket" ]; then
                    export JG_TICKET="$_jg_ticket"
                fi
            fi
            ;;
        clear)
            unset JG_TICKET
            ;;
    esac
    return $_jg_exit
}}""")


@main.command("setup")
def cmd_setup() -> None:
    """Configure fish/tide prompt integration."""
    tide_fn_file = Path.home() / ".config" / "fish" / "functions" / "_tide_item_jg.fish"
    fish_fn = """\
function _tide_item_jg
    if set -q JG_TICKET
        _tide_print_item jg $tide_jg_icon' ' $JG_TICKET
    end
end
"""

    if tide_fn_file.exists():
        click.confirm(
            f"{tide_fn_file} already exists. Overwrite?", abort=True
        )
    else:
        click.confirm(f"Create {tide_fn_file}?", abort=True)

    tide_fn_file.parent.mkdir(parents=True, exist_ok=True)
    tide_fn_file.write_text(fish_fn)
    click.echo(f"Wrote {tide_fn_file}")
    click.echo()
    click.echo("To finish setup, run these in fish:")
    click.echo("  set -U tide_right_prompt_items $tide_right_prompt_items jg")
    click.echo("  set -U tide_jg_icon '󰔖'")
    click.echo("  set -U tide_jg_bg_color blue")
    click.echo("  set -U tide_jg_color white")
