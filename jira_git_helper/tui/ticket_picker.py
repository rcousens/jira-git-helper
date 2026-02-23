"""Ticket picker TUI: JiraListApp and its supporting modals."""

from __future__ import annotations

import sys
import webbrowser

import click
from jira import JIRAError
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static, Tree

from ..config import (
    get_config,
    set_config,
    get_ticket,
    save_ticket,
    get_projects,
    get_fields_for_project,
    get_filters_for_project,
    set_filters_for_project,
    get_active_filter_name,
    set_active_filter_name,
    get_effective_filter_name,
    _session_active_filters,
)
from ..git import copy_to_clipboard
from ..jira_api import (
    get_jira_server,
    get_jira_client,
    get_jira_field_name,
    fetch_issues_for_projects,
    STATUS_STYLES,
    PRIORITY_STYLES,
)
from .theme import (
    SCREEN_CSS, CONTEXT_BAR_CSS, DATATABLE_CSS, FILTER_BAR_CSS, FOOTER_CSS,
    context_bar_text, preview_raw_value, build_ticket_info,
    cursor_row_key, FilterBarMixin,
)
from .modals import TextInputModal, ConfirmModal


def ensure_ticket() -> str:
    """Return the current ticket. If none is set, show the interactive picker."""
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


class JiraListApp(FilterBarMixin, App):
    CSS = SCREEN_CSS + CONTEXT_BAR_CSS + DATATABLE_CSS + FILTER_BAR_CSS + FOOTER_CSS + """
    Tree { height: 1fr; background: #0a0e0a; display: none; padding: 0 1; color: #b8d4b8; }
    Tree > .tree--cursor { background: #003d00; text-style: bold; }
    Tree > .tree--guides       { color: #1a3a1a; }
    Tree > .tree--guides-hover { color: #2a5a2a; }
    #filter-status { height: 1; background: #0d1a0d; color: #ffb300; padding: 0 1; display: none; }
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
        yield Static(context_bar_text(), classes="context-bar")
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
        """Group all_issues into (roots, children_dict) using fields.parent."""
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
        if len(self.screen_stack) > 1:
            return

        focused = self.focused
        filter_bar = self.query_one("#filter-bar", Input)

        # Custom Input handling for tree/table dual mode
        if isinstance(focused, Input) and focused.id == "filter-bar":
            if event.key == "escape":
                focused.value = ""
                focused.display = False
                self._reset_filter()
                (self.query_one(Tree) if self._tree_mode else self.query_one(DataTable)).focus()
                event.prevent_default()
                return
            if event.key == "enter":
                (self.query_one(Tree) if self._tree_mode else self.query_one(DataTable)).focus()
                event.prevent_default()
                return
            return

        if self._tree_mode:
            if event.key == "escape" and filter_bar.display:
                filter_bar.value = ""
                filter_bar.display = False
                self._reset_filter()
                event.prevent_default()
            return

        # Delegate DataTable-mode keys to mixin
        if self._handle_filter_keys(event):
            return
        if event.key == "enter":
            self.action_select_ticket()
            event.prevent_default()

    def _reset_filter(self) -> None:
        if self._tree_mode:
            self._populate_tree()
        else:
            self._populate_table(self.all_issues)

    def _cursor_key(self) -> str | None:
        return cursor_row_key(self.query_one(DataTable))

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
        if copy_to_clipboard(url):
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
            fname = get_jira_field_name(fid)
            fval = preview_raw_value(raw_val)
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


class FieldPickerModal(ModalScreen):
    CSS = DATATABLE_CSS + FOOTER_CSS + """
    FieldPickerModal { align: center middle; background: #0a0e0a 85%; }
    #fp-dialog { width: 95%; height: 90%; border: thick #00ff41; background: #0d1a0d; }
    #fp-title { text-style: bold; padding: 0 1; background: #152015; color: #00ff41; }
    #fp-table { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Save & close", show=True),
        Binding("space", "toggle_field", "Toggle", show=True),
    ]

    def __init__(
        self,
        fields: list[tuple[str, str, str]],
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
            fid = cursor_row_key(table)
            if fid is None:
                return
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


class FilterListModal(ModalScreen):
    """Manage named JQL filters for a single project."""

    CSS = DATATABLE_CSS + FOOTER_CSS + """
    FilterListModal { align: center middle; background: #0a0e0a 85%; }
    #fl-dialog { width: 95%; height: 90%; border: thick #00ff41; background: #0d1a0d; }
    #fl-header { text-style: bold; padding: 0 1; background: #152015; color: #00ff41; }
    #fl-table { height: 1fr; }
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
        key = cursor_row_key(self.query_one("#fl-table", DataTable))
        return int(key) if key is not None else None

    def on_key(self, event) -> None:
        if event.key == "enter":
            self._do_activate()
            event.prevent_default()
        elif event.key == "space":
            self._do_set_default()
            event.prevent_default()

    def _do_activate(self) -> None:
        idx = self._cursor_idx()
        if idx is None:
            return
        name = self._filters[idx]["name"]
        current = get_effective_filter_name(self.project)
        if current == name:
            _session_active_filters.pop(self.project, None)
        else:
            _session_active_filters[self.project] = name
        self._changed = True
        self.dismiss(True)

    def _do_set_default(self) -> None:
        idx = self._cursor_idx()
        if idx is None:
            return
        name = self._filters[idx]["name"]
        current_default = get_active_filter_name(self.project)
        if current_default == name:
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
            return
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
        if get_active_filter_name(self.project) == name:
            set_active_filter_name(self.project, None)
        if _session_active_filters.get(self.project) == name:
            _session_active_filters.pop(self.project, None)
        self._changed = True
        self._refresh_table()

    def action_close_modal(self) -> None:
        self.dismiss(self._changed)


class TicketInfoModal(ModalScreen):
    CSS = FOOTER_CSS + """
    TicketInfoModal { align: center middle; background: #0a0e0a 85%; }
    #ti-container { width: 90%; height: 90%; border: thick #00ff41; background: #0d1a0d; }
    #ti-title { text-style: bold; padding: 0 1; background: #152015; color: #00ff41; }
    #ti-scroll { height: 1fr; }
    #ti-content { padding: 1 2; color: #b8d4b8; }
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
        try:
            issue = self._jira_client.issue(
                self._key,
                fields=["summary", "status", "assignee", "reporter", "priority", "labels", "description", "issuetype"],
            )
        except Exception as e:
            self.app.call_from_thread(self._update_content, f"[red]Error: {e}[/red]")
            return

        content = build_ticket_info(issue, get_jira_server())
        self.app.call_from_thread(self._update_content, content)

    def _update_content(self, content) -> None:
        self.query_one("#ti-content", Static).update(content)
