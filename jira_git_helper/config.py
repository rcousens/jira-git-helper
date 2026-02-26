from __future__ import annotations

import json
import os
import re
from pathlib import Path

# --- paths ---

STATE_FILE = Path.home() / ".local" / "share" / "jira-git-helper" / "ticket"
CONFIG_FILE = Path.home() / ".config" / "jira-git-helper" / "config"

# Generic JQL used when none is set in config
_FALLBACK_JQL = "assignee = currentUser() ORDER BY updated DESC"

# Session-level active filter overrides (not persisted — live for the process lifetime).
# Maps project key → filter name (or None to explicitly use no filter this session).
# Key absent → fall back to config default.
_session_active_filters: dict[str, str | None] = {}

# --- state helpers ---


def get_ticket() -> str | None:
    # When the shell hook is active, JG_TICKET is always defined (possibly empty after
    # `jg clear`).  Trust it exclusively so shells stay independent and a cleared shell
    # never accidentally reads another shell's ticket from STATE_FILE.
    env = os.environ.get("JG_TICKET")
    if env is not None:
        return env.strip() or None
    # No hook — fall back to the persisted file (single-shell / no-hook setups).
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip() or None
    return None


def validate_ticket_project(ticket: str) -> None:
    """Raise ValueError if *ticket* doesn't match a configured project."""
    projects = get_projects()
    if not projects:
        return  # no projects configured — allow anything
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*)-\d+$", ticket)
    if not m:
        raise ValueError(f"Invalid ticket format: {ticket}")
    prefix = m.group(1).upper()
    if prefix not in (p.upper() for p in projects):
        allowed = ", ".join(projects)
        raise ValueError(
            f"Ticket {ticket} does not match configured projects ({allowed})"
        )


def save_ticket(ticket: str) -> None:
    validate_ticket_project(ticket)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(ticket)


def clear_ticket() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


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
