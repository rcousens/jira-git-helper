# jira-git-helper — architecture reference

## Overview

Single-file Python CLI (`jira_git_helper.py`, ~3700 lines). Invoked as `jg`.
Version lives in `pyproject.toml`. Install/reinstall: `uv tool install --reinstall .`

**Stack**: Click (CLI), Textual 8.x (TUI), Rich (rendering), JIRA Python SDK, requests.

---

## Key files

| File | Purpose |
|---|---|
| `jira_git_helper.py` | Everything — config, JIRA helpers, all TUI apps, all CLI commands |
| `pyproject.toml` | Version, dependencies |
| `CHANGELOG.md` | Per-version release notes |
| `README.md` | User-facing docs |

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

---

## Textual-specific rules (hard-won fixes)

### Filter bars — NEVER use `dock: bottom`

`dock: bottom` on an `Input` filter bar conflicts with `Footer`'s own dock and cuts off
1 line. Always put filter bars in the normal vertical flow (no dock), between the DataTable
and Footer. Textual's `1fr` height on the DataTable correctly shrinks when the filter bar
becomes visible via `display: none → block`.

```css
/* CORRECT */
#filter-bar { display: none; border: tall #00ff41; background: #0d1a0d; color: #00ff41; }

/* WRONG — causes 1-line cutoff */
#filter-bar { dock: bottom; ... }
```

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
