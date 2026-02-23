"""Prune TUI: PruneApp for interactively deleting stale branches."""

from __future__ import annotations

import subprocess

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Static

from ..git import get_default_branch
from .theme import SCREEN_CSS, CONTEXT_BAR_CSS, DATATABLE_CSS, FOOTER_CSS, context_bar_text, cursor_row_key
from .modals import ConfirmModal
from .branch import BranchDiffModal


class PruneApp(App):
    CSS = SCREEN_CSS + CONTEXT_BAR_CSS + DATATABLE_CSS + FOOTER_CSS

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
        self.branches = list(branches)
        self._selected: set[str] = set()
        self.deleted: list[str] = []
        self.branch_to_switch: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(context_bar_text(), classes="context-bar")
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
        return cursor_row_key(self.query_one(DataTable))

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
        base = get_default_branch()
        self.push_screen(BranchDiffModal(name, base))

    def action_switch_branch(self) -> None:
        name = self._cursor_branch()
        if name is None:
            return
        self.branch_to_switch = name
        self.exit()

    def action_quit(self) -> None:
        self.exit()
