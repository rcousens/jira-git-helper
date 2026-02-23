"""File picker TUI for staging/unstaging files: FilePickerApp and CommitModal."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from ..config import get_config, get_ticket
from ..git import get_file_statuses
from ..formatters import FILE_STATUS_LABELS
from .theme import context_bar_text
from .modals import FmtModal


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
        self.orig_staged   = [(s, p, f"staged:{p}")    for s, p in staged]
        self.orig_modified = [(s, p, f"modified:{p}")  for s, p in modified]
        self.orig_deleted  = [(s, p, f"deleted:{p}")   for s, p in deleted]
        self.orig_untracked= [(s, p, f"untracked:{p}") for s, p in untracked]

        self.file_info: dict[str, tuple[str, str]] = {}
        for status, path, ik in self.orig_staged:
            self.file_info[ik] = (status, "staged")
        for status, path, ik in self.orig_modified:
            self.file_info[ik] = (status, "modified")
        for status, path, ik in self.orig_deleted:
            self.file_info[ik] = (status, "deleted")
        for status, path, ik in self.orig_untracked:
            self.file_info[ik] = (status, "untracked")

        self._staged_paths: list[str] = [ik for _, _, ik in self.orig_staged]

        self.to_stage: set[str] = set()
        self.to_unstage: set[str] = set()
        self.aborted: bool = False
        self.commit_message: str | None = None
        self._section_filters: dict[str, str] = {}

    # --- data helpers ---

    def _files_for_section(self, section_id: str) -> list[tuple[str, str, str]]:
        staged_set = set(self._staged_paths)
        if section_id == "staged":
            return sorted(
                [(self.file_info[ik][0], ik.split(":", 1)[1], ik) for ik in self._staged_paths],
                key=lambda x: x[1],
            )
        if section_id == "modified":
            files = [(s, p, ik) for s, p, ik in self.orig_modified if ik not in staged_set]
            files += [(s, p, ik) for s, p, ik in self.orig_staged if ik not in staged_set and s not in ("A", "?", "D")]
            return sorted(files, key=lambda x: x[1])
        if section_id == "deleted":
            files = [(s, p, ik) for s, p, ik in self.orig_deleted if ik not in staged_set]
            files += [(s, p, ik) for s, p, ik in self.orig_staged if ik not in staged_set and s == "D"]
            return sorted(files, key=lambda x: x[1])
        if section_id == "untracked":
            files = [(s, p, ik) for s, p, ik in self.orig_untracked if ik not in staged_set]
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
        yield Static(context_bar_text(), classes="context-bar")
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

        if section_id == "staged":
            status_style = "bold #00ff41"
        elif is_untracked_type:
            status_style = "bold #ff5555"
        elif status == "D":
            status_style = "bold #ff5555"
        else:
            status_style = "bold #ffb300"

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
            return

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
        table.move_cursor(row=min(cursor, max(0, table.row_count - 1)))

    def action_confirm(self) -> None:
        if not self._staged_paths:
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
        staged, modified, deleted, untracked = get_file_statuses()
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
        self._staged_paths = [ik for ik in self._staged_paths if ik in self.file_info]
        self._update_section_visibility()
        self._refresh_all()
