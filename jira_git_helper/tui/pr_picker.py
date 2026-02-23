"""PR picker TUI: PrPickerApp and DiffModal."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static

from ..jira_api import PR_STATUS_STYLES
from .theme import (
    SCREEN_CSS, CONTEXT_BAR_CSS, DATATABLE_CSS, FILTER_BAR_CSS, FOOTER_CSS,
    context_bar_text, cursor_row_key, FilterBarMixin,
)


class DiffModal(ModalScreen):
    CSS = FOOTER_CSS + """
    DiffModal { background: #0a0e0a 92%; }
    #diff-scroll { width: 100%; height: 1fr; border: thick #00ff41; }
    #diff-content { padding: 0; }
    #search-bar { display: none; background: #0d1a0d; color: #ffb300; border: solid #ffb300; height: 3; }
    #search-bar:focus { border: solid #ffb300; }
    #search-status { display: none; background: #ffb300; color: #0a0e0a; padding: 0 1; text-style: bold; }
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
        self._file_starts: list[int] = []
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
            bar.display = False
            bar.value = ""
        elif self._search_query:
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


class PrPickerApp(FilterBarMixin, App):
    CSS = SCREEN_CSS + CONTEXT_BAR_CSS + DATATABLE_CSS + FILTER_BAR_CSS + FOOTER_CSS

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
        yield Static(context_bar_text(), classes="context-bar")
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
        if self._handle_filter_keys(event):
            return
        if event.key == "enter" and not self.open_on_enter:
            self.action_select_pr()
            event.prevent_default()

    def _reset_filter(self) -> None:
        self._populate_table(list(enumerate(self.prs)))

    def _selected_pr(self) -> dict | None:
        key = cursor_row_key(self.query_one(DataTable))
        return self.prs[int(key)] if key is not None else None

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
