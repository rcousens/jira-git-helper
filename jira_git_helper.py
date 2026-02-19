import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import requests

import click
from jira import JIRA, JIRAError
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label

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
    """Return the current ticket. If none is set, show the interactive picker."""
    ticket = get_ticket()
    if ticket:
        return ticket

    # Lazy import — JiraListApp is defined later in the file
    from jira import JIRAError  # already imported at top, but kept for clarity

    jira = get_jira_client()
    click.echo("No ticket set — fetching tickets…", err=True)
    try:
        issues = jira.search_issues(
            get_default_jql(),
            maxResults=200,
            fields=["summary", "status", "assignee", "priority"],
        )
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    if not issues:
        raise click.ClickException("No issues found.")

    app = JiraListApp(list(issues))
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


# --- JIRA helpers ---


def get_jira_server() -> str:
    server = get_config("server")
    if not server:
        raise click.ClickException(
            "JIRA server not configured. Run: jg config set server https://yourcompany.atlassian.net"
        )
    return server.rstrip("/")


def get_default_jql() -> str:
    return get_config("jql") or _FALLBACK_JQL


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


# --- TUI ---


class JiraListApp(App):
    CSS = """
    Input {
        dock: top;
        border: tall $accent;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("enter", "select_ticket", "Select", show=True),
    ]

    def __init__(self, issues: list) -> None:
        super().__init__()
        self.all_issues = issues
        self.visible_keys: list[str] = [i.key for i in issues]
        self.selected_ticket: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter tickets…")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Key", width=14)
        table.add_column("Status", width=16)
        table.add_column("Assignee", width=24)
        table.add_column("Summary")
        self._populate_table(self.all_issues)
        self.query_one(Input).focus()

    def _populate_table(self, issues: list) -> None:
        table = self.query_one(DataTable)
        table.clear()
        self.visible_keys = []
        for issue in issues:
            assignee = (
                issue.fields.assignee.displayName
                if issue.fields.assignee
                else "Unassigned"
            )
            table.add_row(
                issue.key,
                issue.fields.status.name,
                assignee,
                issue.fields.summary,
                key=issue.key,
            )
            self.visible_keys.append(issue.key)

    def on_input_changed(self, event: Input.Changed) -> None:
        search = event.value.lower()
        if not search:
            self._populate_table(self.all_issues)
            return
        filtered = [
            i
            for i in self.all_issues
            if search in i.key.lower()
            or search in i.fields.summary.lower()
            or search
            in (
                i.fields.assignee.displayName.lower()
                if i.fields.assignee
                else ""
            )
            or search in i.fields.status.name.lower()
        ]
        self._populate_table(filtered)

    def on_key(self, event) -> None:
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

    def action_select_ticket(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        self.selected_ticket = cell_key.row_key.value
        self.exit()

    def action_quit(self) -> None:
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
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("enter", "select_pr", "Select", show=True),
    ]

    def __init__(self, prs: list[dict]) -> None:
        super().__init__()
        self.prs = prs
        self.selected_pr: dict | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        from rich.text import Text as RichText

        table = self.query_one(DataTable)
        table.add_column("Status", width=10)
        table.add_column("Repo", width=24)
        table.add_column("Source branch", width=32)
        table.add_column("Title")

        for i, pr in enumerate(self.prs):
            status = pr.get("status", "")
            style = PR_STATUS_STYLES.get(status, "white")
            table.add_row(
                RichText(status, style=style),
                pr.get("repositoryName", ""),
                pr.get("source", {}).get("branch", ""),
                pr.get("name", ""),
                key=str(i),
            )
        table.focus()

    def on_key(self, event) -> None:
        table = self.query_one(DataTable)
        if event.key == "down":
            table.move_cursor(row=table.cursor_row + 1)
            event.prevent_default()
        elif event.key == "up":
            table.move_cursor(row=table.cursor_row - 1)
            event.prevent_default()
        elif event.key == "enter":
            self.action_select_pr()
            event.prevent_default()

    def action_select_pr(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        self.selected_pr = self.prs[int(cell_key.row_key.value)]
        self.exit()

    def action_quit(self) -> None:
        self.exit()


# --- CLI ---


@click.group(invoke_without_command=True)
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
@click.option("--jql", default=None, help="JQL filter (defaults to config jql or assigned tickets)")
@click.option("--max", "max_results", default=200, show_default=True, help="Max results to fetch")
def cmd_set(ticket: str | None, jql: str | None, max_results: int) -> None:
    """Set the current JIRA ticket, or browse interactively if no ticket given."""
    if ticket:
        save_ticket(ticket)
        click.echo(f"Ticket set to {ticket}")
        return

    jira = get_jira_client()
    click.echo("Fetching tickets…", err=True)
    try:
        issues = jira.search_issues(
            jql or get_default_jql(),
            maxResults=max_results,
            fields=["summary", "status", "assignee", "priority"],
        )
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    if not issues:
        click.echo("No issues found.")
        return

    app = JiraListApp(list(issues))
    app.run()

    if app.selected_ticket:
        save_ticket(app.selected_ticket)
        click.echo(f"Ticket set to {app.selected_ticket}")
    else:
        click.echo("No ticket selected.", err=True)


@main.command("clear")
def cmd_clear() -> None:
    """Clear the current JIRA ticket."""
    clear_ticket()
    click.echo("Ticket cleared")


def _get_file_statuses() -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (staged, modified, untracked) as lists of (status_code, filepath)."""
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException("Not a git repository or git not available.")
    staged, modified, untracked = [], [], []
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
                modified.append((y, path))
    return staged, modified, untracked


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
    Input {
        dock: top;
        border: tall $accent;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("enter", "select_branch", "Switch", show=True),
    ]

    def __init__(self, branches: list[tuple[str, bool]]) -> None:
        super().__init__()
        self.all_branches = branches
        self.selected_branch: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter branches…")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", width=2)      # current marker
        table.add_column("Branch")
        self._populate_table(self.all_branches)
        self.query_one(Input).focus()

    def _populate_table(self, branches: list[tuple[str, bool]]) -> None:
        from rich.text import Text as RichText

        table = self.query_one(DataTable)
        table.clear()
        for branch, is_current in branches:
            marker = RichText("*", style="bold green") if is_current else RichText("")
            label  = RichText(branch, style="bold green") if is_current else RichText(branch)
            table.add_row(marker, label, key=branch)

    def on_input_changed(self, event: Input.Changed) -> None:
        search = event.value.lower()
        filtered = [
            (b, cur) for b, cur in self.all_branches
            if not search or search in b.lower()
        ]
        self._populate_table(filtered)

    def on_key(self, event) -> None:
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
        background: $background 70%;
    }
    #dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #title { text-style: bold; padding-bottom: 1; }
    #hint  { color: $text-muted; padding-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

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


class FilePickerApp(App):
    CSS = """
    Screen { layout: vertical; }

    .section {
        height: 1fr;
        border: tall $panel;
    }
    .section:focus-within {
        border: tall $accent;
    }
    .section-label {
        padding: 0 1;
        background: $boost;
        color: $text-muted;
        text-style: bold;
    }
    DataTable { height: 1fr; }
    .section-filter {
        display: none;
        border-top: tall $panel;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Cancel"),
        Binding("space", "toggle_select", "Toggle", show=True),
        Binding("enter", "confirm", "Commit", show=True),
    ]

    def __init__(
        self,
        staged: list[tuple[str, str]],
        modified: list[tuple[str, str]],
        untracked: list[tuple[str, str]],
    ) -> None:
        super().__init__()
        self.staged = staged
        self.modified = modified
        self.untracked = untracked
        self.to_unstage: set[str] = set()
        self.to_stage: set[str] = set()
        self.aborted: bool = False
        self.commit_message: str | None = None
        self._section_filters: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        if self.staged:
            with Vertical(classes="section"):
                yield Label("  Staged  (space to unstage)", classes="section-label")
                yield DataTable(id="staged", cursor_type="row", zebra_stripes=True)
                yield Input(id="filter-staged", placeholder="Filter…", classes="section-filter")
        if self.modified:
            with Vertical(classes="section"):
                yield Label("  Modified  (space to stage)", classes="section-label")
                yield DataTable(id="modified", cursor_type="row", zebra_stripes=True)
                yield Input(id="filter-modified", placeholder="Filter…", classes="section-filter")
        if self.untracked:
            with Vertical(classes="section"):
                yield Label("  Untracked  (space to stage)", classes="section-label")
                yield DataTable(id="untracked", cursor_type="row", zebra_stripes=True)
                yield Input(id="filter-untracked", placeholder="Filter…", classes="section-filter")
        yield Footer()

    def on_mount(self) -> None:
        self._populate("staged",    self.staged,    self.to_unstage)
        self._populate("modified",  self.modified,  self.to_stage)
        self._populate("untracked", self.untracked, self.to_stage)
        # Focus the first visible table
        tables = self.query(DataTable)
        if tables:
            tables.first().focus()

    def _checkbox(self, path: str, selected: set[str]):
        from rich.text import Text as RichText
        if path in selected:
            return RichText.from_markup("[bold green]☑[/bold green]")
        return RichText.from_markup("[dim]☐[/dim]")

    def _path_cell(self, path: str, selected: set[str], default_style: str = ""):
        from rich.text import Text as RichText
        if path in selected:
            return RichText.from_markup(f"[bold green]{path}[/bold green]")
        if default_style:
            return RichText.from_markup(f"[{default_style}]{path}[/{default_style}]")
        return RichText(path)

    def _populate(self, table_id: str, files: list[tuple[str, str]], selected: set[str]) -> None:
        from rich.text import Text as RichText
        try:
            table = self.query_one(f"#{table_id}", DataTable)
        except Exception:
            return
        table.add_column("", width=3)
        table.add_column("STATUS", width=12)
        table.add_column("FILE")
        for status, path in files:
            label = FILE_STATUS_LABELS.get(status, status)
            style = FILE_STATUS_STYLES.get(status, "white")
            path_style = FILE_PATH_STYLES.get(status, "")
            table.add_row(self._checkbox(path, selected), RichText(label, style=style), self._path_cell(path, selected, path_style), key=path)

    def _focused_table(self) -> DataTable | None:
        w = self.focused
        return w if isinstance(w, DataTable) else None

    def _selected_set(self, table: DataTable) -> set[str]:
        return self.to_unstage if table.id == "staged" else self.to_stage

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
        else:
            # Any printable character (or /) activates the filter for this section
            char = event.character
            if char and char != " " and char.isprintable():
                filter_id = f"filter-{table.id}"
                try:
                    filter_input = self.query_one(f"#{filter_id}", Input)
                    filter_input.display = True
                    filter_input.value = char
                    filter_input.cursor_position = len(char)
                    filter_input.focus()
                    event.prevent_default()
                except Exception:
                    pass

    def _rebuild_table(self, table: DataTable, files: list[tuple[str, str]], selected: set[str]) -> None:
        from rich.text import Text as RichText
        cursor = table.cursor_row
        table.clear()
        for status, path in files:
            label = FILE_STATUS_LABELS.get(status, status)
            style = FILE_STATUS_STYLES.get(status, "white")
            path_style = FILE_PATH_STYLES.get(status, "")
            table.add_row(self._checkbox(path, selected), RichText(label, style=style), self._path_cell(path, selected, path_style), key=path)
        table.move_cursor(row=min(cursor, max(0, table.row_count - 1)))

    def _refresh_table(self, table_id: str) -> None:
        """Rebuild a section's table applying the current filter."""
        try:
            table = self.query_one(f"#{table_id}", DataTable)
        except Exception:
            return
        all_files = {"staged": self.staged, "modified": self.modified, "untracked": self.untracked}.get(table_id, [])
        selected = self.to_unstage if table_id == "staged" else self.to_stage
        filt = self._section_filters.get(table_id, "").lower()
        files = [(s, p) for s, p in all_files if filt in p.lower()] if filt else all_files
        self._rebuild_table(table, files, selected)

    def action_toggle_select(self) -> None:
        table = self._focused_table()
        if table is None or table.row_count == 0:
            return
        cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        path = cell_key.row_key.value
        selected = self._selected_set(table)
        if path in selected:
            selected.discard(path)
        else:
            selected.add(path)
        cursor = table.cursor_row
        self._refresh_table(table.id)
        table.move_cursor(row=min(cursor + 1, max(0, table.row_count - 1)))

    def action_confirm(self) -> None:
        ticket = get_ticket()
        self.push_screen(CommitModal(ticket), self._on_commit_modal)

    def _on_commit_modal(self, message: str | None) -> None:
        if message is not None:
            self.commit_message = message
        # Always exit — staging changes are kept, commit only if message given
        self.exit()

    def action_quit(self) -> None:
        self.aborted = True
        self.exit()


@main.command("branch")
@click.argument("name", required=False)
def cmd_branch(name: str | None) -> None:
    """Switch to a ticket branch interactively, or create one with the given name."""
    ticket = ensure_ticket()

    if name:
        branch_name = f"{ticket}-{name}"
        click.echo(f"Creating branch: {branch_name}")
        subprocess.run(["git", "switch", "-C", branch_name], check=True)
        return

    all_branches = _get_local_branches()
    matching = [(b, cur) for b, cur in all_branches if ticket.lower() in b.lower()]

    if not matching:
        click.echo(f"No local branches found matching {ticket}.")
        return

    app = BranchPickerApp(matching)
    app.run()

    if app.selected_branch:
        subprocess.run(["git", "switch", app.selected_branch], check=True)
    else:
        click.echo("No branch selected.", err=True)


@main.command("add")
def cmd_add() -> None:
    """Interactively stage and unstage files."""
    ticket = ensure_ticket()
    staged, modified, untracked = _get_file_statuses()

    if not staged and not modified and not untracked:
        click.echo("Nothing to do — working tree clean.")
        return

    app = FilePickerApp(staged, modified, untracked)
    app.run()

    if app.aborted:
        click.echo("Aborted.", err=True)
        return

    if app.to_stage:
        subprocess.run(["git", "add", "--", *app.to_stage], check=True)
        click.echo(f"Staged {len(app.to_stage)} file(s):")
        for f in sorted(app.to_stage):
            click.echo(f"  + {f}")

    if app.to_unstage:
        subprocess.run(["git", "restore", "--staged", "--", *app.to_unstage], check=True)
        click.echo(f"Unstaged {len(app.to_unstage)} file(s):")
        for f in sorted(app.to_unstage):
            click.echo(f"  - {f}")

    if app.commit_message:
        full_msg = f"{ticket} {app.commit_message}"
        subprocess.run(["git", "commit", "--no-verify", "-m", full_msg], check=True)
    elif not app.to_stage and not app.to_unstage:
        click.echo("No changes made.", err=True)


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


@main.command("commit", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("message")
@click.argument("git_args", nargs=-1, type=click.UNPROCESSED)
def cmd_commit(message: str, git_args: tuple[str, ...]) -> None:
    """Commit with message prefixed by the current ticket (TICKET-123 <message>)."""
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


@main.command("diff")
@click.argument("ticket", required=False)
@click.option("--all", "show_all", is_flag=True, help="Include merged and declined PRs")
def cmd_diff(ticket: str | None, show_all: bool) -> None:
    """Diff a linked PR (open or draft) for the current (or given) ticket."""
    if not shutil.which("gh"):
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com"
        )

    key = ticket or get_ticket()
    if not key:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)

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

    if not show_all:
        prs = [p for p in prs if p.get("status") in ("OPEN", "DRAFT")]

    if not prs:
        msg = f"No {'linked' if show_all else 'open or draft'} PRs found for {key}."
        if not show_all:
            msg += " Use --all to include merged/declined PRs."
        raise click.ClickException(msg)

    if len(prs) == 1:
        pr = prs[0]
    else:
        app = PrPickerApp(prs)
        app.run()
        if not app.selected_pr:
            click.echo("No PR selected.", err=True)
            sys.exit(1)
        pr = app.selected_pr

    url    = pr["url"]
    source = pr.get("source", {}).get("branch", "?")
    dest   = pr.get("destination", {}).get("branch", "main")
    status = pr.get("status", "")
    title  = pr.get("name", "")

    click.echo(f"\n  {title}")
    click.echo(f"  {source} → {dest}  [{status}]")
    click.echo(f"  {url}\n")

    subprocess.run(["gh", "pr", "diff", url], check=True)


@main.command("prs")
@click.argument("ticket", required=False)
def cmd_prs(ticket: str | None) -> None:
    """List all PRs linked to the current (or given) ticket."""
    from rich import box
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

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

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold bright_black", padding=(0, 1))
    table.add_column("STATUS", width=10, no_wrap=True)
    table.add_column("REPO",   width=20, no_wrap=True)
    table.add_column("TITLE",  ratio=2)
    table.add_column("URL",    ratio=3)

    for pr in sorted(prs, key=lambda p: (p.get("status") != "OPEN", p.get("lastUpdate", ""))):
        status = pr.get("status", "")
        style  = PR_STATUS_STYLES.get(status, "white")
        url    = pr.get("url", "")
        repo   = pr.get("repositoryName", "")
        title  = pr.get("name", "")

        table.add_row(
            Text(status, style=style),
            repo,
            title,
            Text(url, style=f"link {url} bright_cyan"),
        )

    Console().print(table)


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
    """Set a config value."""
    set_config(key, value)
    click.echo(f"{key} = {value}")


@cmd_config.command("list")
def config_list() -> None:
    """List all config values."""
    known = [
        ("server", "JIRA server URL, e.g. https://yourcompany.atlassian.net", False),
        ("email",  "JIRA account email",                                        False),
        ("token",  "JIRA API token",                                            True),
        ("jql",    "Default JQL for the ticket picker (optional)",              False),
    ]
    config = _read_config()
    for key, description, secret in known:
        value = config.get(key)
        if value:
            display = "****" if secret else value
            click.echo(f"{key} = {display}")
        else:
            click.echo(f"{key} = (not set)  # {description}")



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
    """
    if shell == "fish":
        click.echo(f"""\
function jg
    command jg $argv
    set -l _jg_exit $status
    switch "$argv[1]"
        case set
            set -l _jg_ticket (cat {STATE_FILE} 2>/dev/null)
            if test -n "$_jg_ticket"
                set -gx JG_TICKET $_jg_ticket
            end
        case clear
            set -e JG_TICKET
    end
    return $_jg_exit
end""")
    else:
        # bash and zsh share the same syntax
        click.echo(f"""\
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
    fish_fn = f"""\
function _tide_item_jg
    set -l ticket (cat {STATE_FILE} 2>/dev/null)
    if test -n "$ticket"
        _tide_print_item jg $tide_jg_icon' ' $ticket
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
