"""Branch-related TUI screens: BranchPromptApp, BranchPickerApp, BranchDiffModal."""

from __future__ import annotations

import shutil
import subprocess

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.containers import ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from ..jira_api import get_jira_server
from .theme import context_bar_text, build_ticket_info


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
        try:
            issue = self._jira_client.issue(
                self._ticket,
                fields=["summary", "status", "assignee", "reporter", "priority", "labels", "description", "issuetype"],
            )
        except Exception as e:
            self.call_from_thread(self._update_content, f"[red]Error loading ticket info: {e}[/red]")
            return

        content = build_ticket_info(issue, get_jira_server())
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
        yield Static(context_bar_text(), classes="context-bar")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Input(id="filter-bar", placeholder="Filter…")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", width=2)
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
