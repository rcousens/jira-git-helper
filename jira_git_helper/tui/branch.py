"""Branch-related TUI screens: BranchPromptApp, BranchPickerApp, BranchDiffModal."""

from __future__ import annotations

import shutil
import subprocess

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from ..jira_api import get_jira_server
from .theme import (
    SCREEN_CSS, CONTEXT_BAR_CSS, DATATABLE_CSS, FILTER_BAR_CSS, FOOTER_CSS,
    context_bar_text, build_ticket_info,
    cursor_row_key, FilterBarMixin,
)


class BranchPromptApp(App):
    """Standalone TUI: shows ticket info and prompts for a new branch suffix.

    After app.run(), inspect app.branch_suffix — non-None means the user confirmed.
    The caller is responsible for creating the branch.
    """

    CSS = SCREEN_CSS + FOOTER_CSS + """
    #bp-title { height: 1; padding: 0 1; background: #152015; color: #00ff41; text-style: bold; }
    #bp-scroll { height: 1fr; }
    #bp-content { padding: 1 2; color: #b8d4b8; }
    #bp-prompt-label { height: 1; padding: 0 1; color: #ffb300; }
    #bp-input { border: tall #00ff41; background: #0a0e0a; color: #00ff41; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Show ticket branches"),
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


class BranchPickerApp(FilterBarMixin, App):
    CSS = SCREEN_CSS + CONTEXT_BAR_CSS + DATATABLE_CSS + FILTER_BAR_CSS + FOOTER_CSS

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("enter", "select_branch", "Select", show=True, priority=True),
        Binding("n", "new_branch", "New branch", show=True),
        Binding("slash", "activate_filter", "Filter", show=True),
    ]

    TRACKING_STYLES = {
        "tracked": "#00ff41",
        "local": "#ffb300",
        "remote": "#00e5ff",
    }
    STATUS_STYLES = {
        "never pushed": "#ffb300",
        "remote deleted": "#ff5555",
        "remote only": "#00e5ff",
    }

    def __init__(self, branches: list[dict]) -> None:
        super().__init__()
        self.all_branches = branches
        self.selected_branch: str | None = None
        self.create_new: bool = False

    def compose(self) -> ComposeResult:
        yield Static(context_bar_text(), classes="context-bar")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Input(id="filter-bar", placeholder="Filter…")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", width=2)
        table.add_column("Branch")
        table.add_column("Tracking", width=10)
        table.add_column("Status", width=16)
        self._populate_table(self.all_branches)
        table.focus()

    def _populate_table(self, branches: list[dict]) -> None:
        from rich.text import Text as RichText

        table = self.query_one(DataTable)
        table.clear()
        for b in branches:
            name, is_current = b["name"], b["is_current"]
            tracking, status = b["tracking"], b["status"]
            marker = RichText("*", style="bold #00ff41") if is_current else RichText("")
            label = RichText(name, style="bold #00e5ff") if is_current else RichText(name, style="#b8d4b8")
            tracking_text = RichText(tracking, style=self.TRACKING_STYLES.get(tracking, "#b8d4b8"))
            status_text = RichText(status, style=self.STATUS_STYLES.get(status, "#b8d4b8")) if status else RichText("")
            table.add_row(marker, label, tracking_text, status_text, key=name)

    def on_input_changed(self, event: Input.Changed) -> None:
        search = event.value.lower()
        filtered = [
            b for b in self.all_branches
            if not search or search in b["name"].lower()
            or search in b["tracking"].lower() or search in b["status"].lower()
        ]
        self._populate_table(filtered)

    def on_key(self, event) -> None:
        if self._handle_filter_keys(event):
            return

    def _reset_filter(self) -> None:
        self._populate_table(self.all_branches)

    def action_select_branch(self) -> None:
        fb = self.query_one("#filter-bar", Input)
        if fb.styles.display != "none":
            self.query_one(DataTable).focus()
            return
        key = cursor_row_key(self.query_one(DataTable))
        if key:
            self.selected_branch = key
            self.exit()

    def action_new_branch(self) -> None:
        # Don't trigger when typing in filter bar
        fb = self.query_one("#filter-bar", Input)
        if fb.styles.display != "none":
            return
        self.create_new = True
        self.exit()

    def action_quit(self) -> None:
        self.exit()


class BranchDiffModal(ModalScreen):
    """Full-screen diff of a branch against a base branch."""

    CSS = FOOTER_CSS + """
    BranchDiffModal { background: #0a0e0a 92%; }
    #bdiff-scroll { width: 100%; height: 1fr; border: thick #00ff41; }
    #bdiff-content { padding: 0; }
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
