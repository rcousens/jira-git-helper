# jira-git-helper — architecture reference

## Overview

Python CLI tool invoked as `jg`. Split into a multi-module package under `jira_git_helper/`.
Version lives in `pyproject.toml`. Install/reinstall: `uv tool install --reinstall .`

**Stack**: Click (CLI), Textual 8.x (TUI), Rich (rendering), JIRA Python SDK, requests.

---

## Package structure

```
jira_git_helper/
├── __init__.py            # __version__ from package metadata
├── cli.py                 # Click group + all command functions (entry point)
├── config.py              # Config file + ticket state management
├── git.py                 # Git subprocess wrappers
├── jira_api.py            # JIRA client, field cache, issue/PR fetching
├── formatters.py          # Formatter config, execution, eof fixer
└── tui/
    ├── __init__.py
    ├── theme.py           # Shared CSS blocks, colour constants, helpers
    ├── modals.py          # TextInputModal, ConfirmModal, FmtModal
    ├── ticket_picker.py   # JiraListApp, FieldPickerModal, FilterListModal, TicketInfoModal
    ├── branch.py          # BranchPromptApp, BranchPickerApp, BranchDiffModal
    ├── file_picker.py     # FilePickerApp, CommitModal
    ├── pr_picker.py       # PrPickerApp, DiffModal
    └── prune.py           # PruneApp
```

### Entry point

`pyproject.toml` defines `jg = "jira_git_helper.cli:main"`. The `main` function is the
Click group in `cli.py`.

### Dependency graph

```
cli.py  ──→  config, git, jira_api, formatters  (core modules)
        ──→  tui/*  (lazy imports inside command functions)

tui/theme.py       ──→  config, git
tui/modals.py      ──→  formatters
tui/ticket_picker.py ──→  config, git, jira_api, tui/theme, tui/modals
tui/branch.py      ──→  jira_api, tui/theme
tui/file_picker.py ──→  git, formatters, tui/theme, tui/modals
tui/pr_picker.py   ──→  jira_api, tui/theme
tui/prune.py       ──→  git, tui/theme, tui/modals, tui/branch

config.py          ──→  (stdlib only)
git.py             ──→  (stdlib + click)
jira_api.py        ──→  config
formatters.py      ──→  config, git
```

Core modules (`config`, `git`, `jira_api`, `formatters`) have no TUI dependencies.
TUI modules are imported lazily inside CLI command functions to keep `jg --help` fast
and avoid circular imports.

---

## Module details

### `config.py` — config and ticket state

All config file and ticket state management. No JIRA or git imports.

**Key exports**: `get_ticket`, `save_ticket`, `clear_ticket`, `get_config`, `set_config`,
`get_projects`, `get_fields_for_project`, `get_filters_for_project`,
`set_filters_for_project`, `get_active_filter_name`, `set_active_filter_name`,
`get_formatters`, `set_formatters`, `get_effective_filter_name`, `get_jql_for_project`

**Module state**: `STATE_FILE`, `CONFIG_FILE`, `_FALLBACK_JQL`, `_session_active_filters`

### `git.py` — git subprocess wrappers

Pure git helpers. No JIRA or TUI imports.

**Key exports**: `get_file_statuses`, `get_current_branch`, `check_not_main_branch`,
`get_default_branch`, `get_local_branches`, `create_branch`, `copy_to_clipboard`

### `jira_api.py` — JIRA client and API helpers

JIRA client setup, field caching, issue/PR fetching. Depends on `config.py`.

**Key exports**: `get_jira_server`, `get_jira_client`, `ensure_fields_cached`,
`get_jira_field_id`, `get_jira_field_name`, `fetch_issues_for_projects`, `get_prs`,
`get_default_jql`

**Style dicts**: `STATUS_STYLES`, `PRIORITY_STYLES`, `PR_STATUS_STYLES`

### `formatters.py` — file formatters

Formatter execution logic. Depends on `config.py` and `git.py`.

**Key exports**: `FILE_STATUS_LABELS`, `get_binary_paths`, `fix_eof`, `build_fmt_table`,
`run_formatters`

### `tui/theme.py` — shared TUI CSS and helpers

Shared CSS constants and rendering helpers used across all TUI apps.

**CSS blocks** (compose these in each App's `CSS`):
`SCREEN_CSS`, `CONTEXT_BAR_CSS`, `DATATABLE_CSS`, `FOOTER_CSS`, `FILTER_BAR_CSS`, `MODAL_CSS`

**Colour constants**: `COL_GREEN`, `COL_CYAN`, `COL_PALE`, `COL_AMBER`, `COL_PURPLE`,
`COL_RED`, `COL_BG`, `COL_SURFACE`, `COL_DARK`

**Helpers**: `context_bar_text()` (active ticket + branch header),
`build_ticket_info(issue, jira_server)` (Rich renderable for ticket detail),
`preview_raw_value(value)` (format arbitrary JIRA field values for display),
`cursor_row_key(table)` (row key at DataTable cursor, or None if empty)

**Mixin**: `FilterBarMixin` — shared `#filter-bar` + DataTable key handling (see below)

### `tui/modals.py` — generic reusable modals

No JIRA or git imports. Used by multiple TUI apps.

- `TextInputModal` — single-field text input with title/label
- `ConfirmModal` — yes/no confirmation dialog
- `FmtModal` — full-screen formatter results display

### `tui/ticket_picker.py` — `jg set` screen

The main ticket picker and its supporting modals.

- `JiraListApp` — DataTable + Tree view, filter bar, field picker, filter manager
- `FieldPickerModal` — toggle JIRA fields as columns
- `FilterListModal` — manage named JQL filters
- `TicketInfoModal` — inline ticket detail panel
- `ensure_ticket()` — helper that ensures a ticket is active (shows picker if needed)

### `tui/branch.py` — branch TUI apps

- `BranchPromptApp` — standalone; shows ticket info + branch suffix input (used when on main)
- `BranchPickerApp` — DataTable of local branches with filter bar
- `BranchDiffModal` — full-screen diff of branch vs base

### `tui/file_picker.py` — `jg add` staging screen

- `FilePickerApp` — 4-section staging UI (staged, modified, deleted, untracked)
- `CommitModal` — commit message input with ticket prefix

### `tui/pr_picker.py` — `jg prs` screen

- `PrPickerApp` — DataTable of PRs with filter bar, open in browser, switch branch
- `DiffModal` — full-screen PR diff viewer with search, file navigation, delta support

### `tui/prune.py` — `jg prune` screen

- `PruneApp` — DataTable of stale branches, select/delete, diff viewer, switch branch

### `cli.py` — all CLI commands

All Click commands live here. TUI modules are imported lazily inside each command function.

**Commands**: `set`, `clear`, `version`, `branch`, `add`, `commit`, `push`, `reset`,
`sync`, `prune`, `prs`, `info`, `debug`, `open`, `hook`, `setup`

**Command groups**: `fmt` (`add`, `list`, `delete`), `config` (`get`, `set`, `list`)

---

## Architecture patterns

### CLI → TUI handoff

Every interactive command follows the same pattern:

```python
app = SomeApp(data, ...)
app.run()
result = app.some_result_attribute   # None if cancelled
```

Apps store results as instance attributes (e.g. `selected_ticket`, `branch_suffix`).
Never use `app.exit()` return value — it's unreliable. Always read attributes after `run()`.

### ModalScreens

Dismiss with a value: `self.dismiss(value)` or `self.dismiss(None)` to cancel.
Push and receive result: `self.push_screen(Modal(...), callback)`.

### Standalone App vs ModalScreen

Use a **standalone App** when the screen must run before another App (e.g. `BranchPromptApp`
runs before `FilePickerApp` or `BranchPickerApp`). Use a **ModalScreen** when overlaying
an already-running App (e.g. `TicketInfoModal`, `ConfirmModal`).

### Lazy imports in cli.py

TUI modules are imported inside command functions, not at the top of `cli.py`:

```python
@main.command("add")
def cmd_add():
    from .tui.file_picker import FilePickerApp
    # ...
```

This keeps `jg --help` fast and avoids importing Textual for non-interactive commands.

---

## Textual-specific rules (hard-won fixes)

### Filter bars — NEVER use `dock: bottom`

`dock: bottom` on an `Input` filter bar conflicts with `Footer`'s own dock and cuts off
1 line. Always put filter bars in the normal vertical flow (no dock), between the DataTable
and Footer. Textual's `1fr` height on the DataTable correctly shrinks when the filter bar
becomes visible via `display: none → block`.

```css
/* CORRECT — solid border, explicit :focus override, amber to stand out */
#filter-bar { display: none; border: solid #ffb300; background: #0d1a0d; color: #ffb300; height: 3; }
#filter-bar:focus { border: solid #ffb300; }

/* WRONG — tall border + no :focus override = green top / blue bottom */
#filter-bar { border: tall #00ff41; ... }

/* WRONG — causes 1-line cutoff */
#filter-bar { dock: bottom; ... }
```

**Why `border: solid` + `:focus` override?** Textual's built-in `Input:focus` CSS sets
`border: tall $accent` (blue). Without an explicit `:focus` rule, the focus state overrides
the bottom half of the border with blue, and when focus leaves (e.g. pressing Enter), the
focus border disappears entirely. Using `border: solid` with an explicit `:focus` override
keeps the border consistent in all states.

### DataTable cell colours — use Rich Text, not CSS

The CSS rules `datatable--odd-row` / `datatable--even-row` set a blanket fallback colour.
Per-cell colours require passing `rich.text.Text` objects with explicit styles:

```python
from rich.text import Text
table.add_row(Text("SWY-123", style="bold #00e5ff"), Text("In Progress", style="#ffb300"))
```

CSS colour on a DataTable row will override if no inline style is set.

### Tree widget label colours — remove `.tree--label` CSS

The `.tree--label { color: ... }` rule overrides ALL inline span colours from `Rich.Text`
node labels. Remove it entirely. Set fallback colour on the `Tree` widget itself instead:

```css
Tree { color: #b8d4b8; }           /* fallback for unstyled text */
/* Tree > .tree--label { ... }     ← DO NOT ADD — kills inline Rich styles */
```

### Textual 8.x — `show_root` removed from Tree

`Tree("label", show_root=False)` raises `TypeError`. The `show_root` parameter was removed.
Style the root label dimly in `_populate_tree` instead:
```python
tree.root.label = Text("issues", style="#1a3a1a")
```

### Workers and thread safety

Fetch JIRA data in a thread worker. Update UI via `call_from_thread`:

```python
def on_mount(self):
    self.run_worker(self._fetch, thread=True)

def _fetch(self):          # runs in thread — no UI calls
    data = jira.issue(...)
    self.call_from_thread(self._update, data)

def _update(self, data):   # back on main thread — safe to update widgets
    self.query_one(Static).update(data)
```

### Shared CSS via theme.py

TUI apps compose their CSS from shared blocks rather than duplicating styles:

```python
from .theme import SCREEN_CSS, CONTEXT_BAR_CSS, DATATABLE_CSS, FOOTER_CSS

class MyApp(App):
    CSS = SCREEN_CSS + CONTEXT_BAR_CSS + DATATABLE_CSS + FOOTER_CSS + """
        /* app-specific overrides */
    """
```

### Reducing duplication with helpers and mixins

Keep line count low by extracting shared logic into `theme.py` helpers and mixins, and
`git.py` helpers, rather than copy-pasting across TUI apps or CLI commands. Three patterns
to follow:

**1. `cursor_row_key(table)`** — use this wherever you need the row key at the DataTable
cursor. Never inline the 4-line `coordinate_to_cell_key` pattern:

```python
from .theme import cursor_row_key

key = cursor_row_key(self.query_one(DataTable))
if key is None:
    return
```

**2. `FilterBarMixin`** — for any App with a `#filter-bar` Input + DataTable, inherit from
`FilterBarMixin` and implement `_reset_filter()`. The mixin provides `action_activate_filter()`
and `_handle_filter_keys()` which handles escape/enter in the Input, arrow keys for DataTable
navigation, and escape-to-clear-filter. Call `_handle_filter_keys(event)` from `on_key()`:

```python
from .theme import FilterBarMixin

class MyPickerApp(FilterBarMixin, App):
    def on_key(self, event) -> None:
        if self._handle_filter_keys(event):
            return
        # app-specific keys here

    def _reset_filter(self) -> None:
        self._populate_table(self.all_data)
```

**Note**: `FilePickerApp` does NOT use `FilterBarMixin` — its per-section filter bars are
architecturally different (multiple Inputs, each scoped to a section DataTable). `JiraListApp`
uses it partially because its tree mode requires custom Input handling.

**3. `create_branch(name, base=None)`** in `git.py` — use this in `cli.py` whenever
creating a branch with `git switch -C`. It handles the echo and subprocess call:

```python
from .git import create_branch
create_branch(branch_name, get_default_branch())  # with base
create_branch(branch_name)                          # from HEAD
```

**When to extract**: if you find the same 3+ line pattern in 3+ places, extract it into
`theme.py` (TUI patterns), `git.py` (git operations), or the appropriate core module.
Prefer a plain function for stateless logic; use a mixin when the pattern involves widget
queries and event handling.

---

## Colour palette

| Role | Hex | Used for |
|---|---|---|
| Matrix green | `#00ff41` | Active markers, staged files, filter border, context bar |
| Cyan | `#00e5ff` | Ticket keys, branch names, column headers |
| Pale green | `#b8d4b8` | Summaries, titles, body text |
| Amber | `#ffb300` | Status, repo, modified files, prompt labels |
| Purple | `#b39ddb` | Assignee, author, person fields |
| Red | `#ff5555` | Deleted, untracked files |
| Background | `#0a0e0a` | Screen / DataTable background |
| Surface | `#0d1a0d` | Headers, filter bar, footer background |
| Dark surface | `#152015` | Footer key chips, title bars |

---

## JIRA API notes

### Fields must be explicitly requested

`jira.search_issues(jql, fields=[...])` only returns the listed fields. Missing fields
come back as `None`. Required fields for the ticket picker:

```python
["summary", "status", "assignee", "priority", "parent", "issuetype"]
```

`parent` and `issuetype` are needed for tree view. Without `parent`, every issue is a
root node and the hierarchy is flat.

### Parent–child resolution

Next-gen Jira: `issue.fields.parent.key` — works for epic→task and task→subtask.
Classic Jira: `issue.fields.customfield_10014` (Epic Link) — string key, not an object.
`_build_issue_tree` handles both.

---

## Debugging

### `jg debug <ticket>`

Dumps all non-null raw JIRA fields for a ticket as syntax-highlighted JSON. Use this to
inspect the exact API shape, discover custom field IDs, or diagnose tree hierarchy issues.

### Common issues

| Symptom | Cause | Fix |
|---|---|---|
| Tree shows flat (no hierarchy) | `parent` field not fetched | Add `"parent"` to `fields=` list |
| Filter bar cut off by 1 line | `dock: bottom` on filter input | Remove `dock: bottom` |
| Tree labels all one colour | `.tree--label { color: ... }` in CSS | Delete that rule |
| `TypeError: show_root` | Textual 8.x removed the param | Remove `show_root=False` |
| Cell colours ignored | CSS fallback overriding | Pass `Rich.Text` with explicit style |

---

## Common commands

```sh
# Reinstall after changes
uv tool install --reinstall .

# Run a command
jg set
jg add
jg branch
jg prs

# Inspect JIRA API shape for a ticket
jg debug SWY-1234

# Check version
jg version
```

---

## Release checklist

1. Update `CHANGELOG.md` — new version section at top
2. Bump `version` in `pyproject.toml`
3. Update `README.md` if commands or behaviour changed
4. `uv tool install --reinstall .` to verify it builds
