"""Shared CSS blocks, colour constants, and rendering helpers for TUI apps."""

from __future__ import annotations

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import get_ticket
from ..git import get_current_branch
from ..jira_api import STATUS_STYLES, PRIORITY_STYLES, get_jira_server


# ---------------------------------------------------------------------------
# Colour constants
# ---------------------------------------------------------------------------

COL_GREEN = "#00ff41"
COL_CYAN = "#00e5ff"
COL_PALE = "#b8d4b8"
COL_AMBER = "#ffb300"
COL_PURPLE = "#b39ddb"
COL_RED = "#ff5555"
COL_BG = "#0a0e0a"
COL_SURFACE = "#0d1a0d"
COL_DARK = "#152015"


# ---------------------------------------------------------------------------
# Shared CSS blocks — compose these in each App's CSS string
# ---------------------------------------------------------------------------

SCREEN_CSS = f"""
    Screen {{ background: {COL_BG}; }}
"""

CONTEXT_BAR_CSS = f"""
    .context-bar {{
        height: 1;
        background: {COL_SURFACE};
        color: {COL_GREEN};
        padding: 0 1;
        text-style: bold;
    }}
"""

DATATABLE_CSS = f"""
    DataTable {{
        height: 1fr;
        background: {COL_BG};
    }}
    DataTable > .datatable--header {{ background: {COL_SURFACE}; color: {COL_CYAN}; text-style: bold; }}
    DataTable > .datatable--cursor {{ background: #003d00; color: {COL_GREEN}; text-style: bold; }}
    DataTable > .datatable--hover  {{ background: #001a00; }}
    DataTable > .datatable--odd-row  {{ background: #080c08; color: {COL_PALE}; }}
    DataTable > .datatable--even-row {{ background: {COL_BG}; color: {COL_PALE}; }}
"""

FOOTER_CSS = f"""
    Footer {{ background: {COL_SURFACE}; color: #4d8a4d; }}
    Footer > .footer--key {{ background: {COL_DARK}; color: {COL_CYAN}; }}
"""

FILTER_BAR_CSS = f"""
    #filter-bar {{
        display: none;
        border: tall {COL_GREEN};
        background: {COL_SURFACE};
        color: {COL_GREEN};
    }}
"""

MODAL_CSS = f"""
    Footer {{ background: {COL_SURFACE}; color: #4d8a4d; }}
    Footer > .footer--key {{ background: {COL_DARK}; color: {COL_CYAN}; }}
"""


# ---------------------------------------------------------------------------
# Shared rendering helpers
# ---------------------------------------------------------------------------

def context_bar_text() -> str:
    """Return a one-line context string showing the active ticket and current branch."""
    ticket = get_ticket() or "—"
    branch = get_current_branch() or "—"
    return f"  ticket: {ticket}   branch: {branch}"


def build_ticket_info(issue, jira_server: str) -> Group:
    """Build a Rich renderable with ticket summary, meta table, URL, and description.

    Used by both TicketInfoModal and BranchPromptApp to avoid duplication.
    The caller fetches the issue and passes it in along with the server URL.
    """
    f = issue.fields
    assignee = f.assignee.displayName if f.assignee else "Unassigned"
    reporter = f.reporter.displayName if f.reporter else "Unknown"
    labels = ", ".join(f.labels) if f.labels else "—"
    priority = f.priority.name if f.priority else "—"
    status = f.status.name
    description = (f.description or "").strip()

    status_style = STATUS_STYLES.get(status.lower(), "white")
    priority_style = PRIORITY_STYLES.get(priority.lower(), "white")
    url = f"{jira_server}/browse/{issue.key}"

    meta = Table.grid(padding=(0, 3), expand=False)
    meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
    meta.add_column(min_width=22)
    meta.add_column(style="bold bright_black", no_wrap=True, min_width=10)
    meta.add_column(min_width=16)
    meta.add_row("STATUS", Text(status, style=status_style),
                 "PRIORITY", Text(priority, style=priority_style))
    meta.add_row("ASSIGNEE", assignee, "REPORTER", reporter)
    meta.add_row("LABELS", Text(labels, style="cyan"), "", "")

    truncated = (
        description[:800] + "\n[dim]…truncated[/dim]"
    ) if len(description) > 800 else (description or "[dim]—[/dim]")

    desc_block = Group(
        Rule(style="bright_black"),
        Text.from_markup("[bold bright_black]DESCRIPTION[/bold bright_black]"),
        Text.from_markup(f"\n{truncated}"),
    )

    url_line = Text.assemble(
        ("URL  ", "bold bright_black"),
        (url, f"link {url} bright_cyan"),
    )

    return Group(
        Text(f.summary, style="bold white"),
        Text(""),
        meta,
        Text(""),
        url_line,
        Text(""),
        desc_block,
    )


def preview_raw_value(val) -> str:
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
