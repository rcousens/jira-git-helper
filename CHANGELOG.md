# Changelog

## v0.10.0

### `jg set` — ticket picker enhancements

- **`i` — inline ticket info**: press `i` on any ticket to open a detail panel showing summary, status, priority, assignee, reporter, labels, URL, and description — same content as `jg info` but without leaving the picker.
- **`o` — open in browser**: press `o` to open the highlighted ticket directly in your browser.
- **`d` — field picker**: press `d` to open a modal listing every field on the ticket. Space to toggle fields on/off; Enter to save. Selected fields are stored in `fields.<PROJECT>` config and added as columns to the picker table.
- **`f` — filter manager**: press `f` to open a per-project named filter list.
  - `n` creates a new filter (prompts for name then JQL).
  - `e` edits the JQL of the selected filter.
  - `d` deletes a filter (with confirmation).
  - **Enter** activates a filter for the current session only (not persisted).
  - **Space** sets a filter as the persisted default (saved to config, used on next open).
  - A status bar above the footer shows the active filter per project (`*` = built-in default).
- **`r` — refresh**: press `r` to re-query JIRA and reload the ticket list with the latest data.
- **Custom columns**: configure `fields.<PROJECT>` (via the field picker or `jg config set`) to display extra JIRA fields as columns. Column values are also included in filter-bar searches.
- **Labels removed from default columns**: labels are no longer shown by default; add them via the field picker if desired.
- **`jql.<PROJECT>` config key removed**: per-project JQL is now managed entirely through named filters. Existing `jql.<PROJECT>` values are automatically migrated to a filter named "Default" on first run.

### `jg prs` — inline diff viewer

- **`d` — diff viewer**: press `d` on any PR (open or merged) to open a full-screen diff viewer without leaving the terminal.
  - Uses [`delta`](https://github.com/dandavison/delta) for syntax-aware colouring if installed; otherwise falls back to Rich syntax highlighting.
  - **`/`** — open search bar; type a term and press `Enter` to commit it.
  - **`Enter`** — jump to the next match (cycles).
  - **`n` / `p`** — jump to the next / previous file in the diff.
  - **`Escape`** — clear the active search (first press), then close the viewer (second press).
  - An amber status bar shows the active search term and match position (`Search: foo  3/7 matches`).
- **`o` — open in browser**: press `o` to open the highlighted PR in your browser (previously Enter).

### New command: `jg reset`

- Switches to the default branch (auto-detected from `origin/HEAD`, falling back to `main`/`master`) and pulls the latest from origin.
- If uncommitted changes would block the branch switch, offers to stash them and continue.
- After a successful pull, offers to restore the stash.

### `jg add` — bug fixes

- Fixed a path-doubling bug when staging files from a subdirectory.
- If not already on a branch prefixed with the active ticket, prompts for a branch suffix and creates the branch automatically before committing.

---

## 0.9.0

- Per-project JQL via `jql.<PROJECT>` config key
- Multi-project merged ticket list
- `jg prs` — interactive PR browser with filter bar
- `jg branch --all` — browse all project branches and update the active ticket

## 0.7.0

- Interactive `jg add` TUI (stage/unstage files, commit message prompt)
- Shell hook for per-terminal ticket isolation (`jg hook`)
- Fish/Tide prompt integration (`jg setup`)

## 0.6.0

- `jg info` command
- `jg open` command
- `jg push` opens linked PR after pushing

## 0.5.0

- Initial release: `jg set`, `jg clear`, `jg branch`, `jg commit`, `jg push`
- Config system (`jg config set/get/list`)
