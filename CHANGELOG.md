# Changelog

## v0.14.0

### New command: `jg fmt`

Run formatters against all modified, staged, and untracked files in the repo.

- **`jg fmt`** — for each dirty file, runs the built-in `eof` formatter and any user-configured formatters whose glob matches the filename. Results are displayed as a Rich table with status (`✓` / `✗` / `—`), file path, formatter name, exit code, and a note column for errors or skip reasons.
- **Built-in `eof` formatter** — always runs on every text file. Ensures each file ends with exactly one newline, stripping any extra trailing newlines.
- **Binary file detection** — before running any formatter, `git ls-files --eol` is used to identify binary files (`w/-text`). Binary files are shown in the table as `— skipped (binary)` and skipped entirely.
- **`jg fmt add [name]`** — interactively add a user formatter (prompts for file glob and command). The command uses `{}` as a placeholder for the absolute file path, similar to `find -exec`.
- **`jg fmt list`** — list all configured user formatters.
- **`jg fmt delete <name>`** — remove a user formatter by name.

Formatters are stored in config under the `fmt` key and shown in `jg config list`.

### `jg add` — format from the staging screen

- **`f` — run formatters**: press `f` in the `jg add` file picker to open a full-screen format results modal. Formatters run in the background; the same Rich table used by `jg fmt` is shown inline. Press any key to close the modal. Git status is automatically reloaded afterwards so the file lists reflect any changes made by the formatters.

---

## v0.13.0

### `jg branch <name>` — always forks from the default branch

- Creating a new branch with `jg branch <name>` now always forks from the default branch (`main`/`master`, auto-detected from `origin/HEAD`) rather than the current branch. The output confirms which branch is being forked from, e.g. `Creating branch: SWY-1234-fix (from main)`.

### `jg add` — commit prompt fix

- Fixed a `MountError` crash caused by two widgets sharing the `id="hint"` in the commit message prompt.

---

## v0.12.0

### `jg add` — commit prompt clarification

- Pressing `Escape` on the commit message prompt now stages the selected files without committing, then exits to the shell. This was already the behaviour; it is now clearly communicated via a hint label in the prompt and a renamed footer binding ("Stage without commit").

---

## v0.11.0

### `jg add` — improvements and bug fixes

- **Deleted files section**: working-tree deletions (files removed from disk but not yet staged) now appear in a dedicated **Deleted** section. Press `Space` to stage the deletion, just like modified or untracked files.
- **Conditional sections**: the Modified, Deleted, and Untracked sections are only shown when they have files. The Staged section is always visible.
- **Key collision fix**: when the same path appeared as both a staged deletion and an untracked file (e.g. after deleting and recreating a file), the two entries now show independently — once in Staged and once in Untracked — and both can be staged. Previously the untracked entry would silently overwrite the staged deletion in the internal lookup, causing incorrect behaviour.

---

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

## v0.9.0

- Per-project JQL via `jql.<PROJECT>` config key
- Multi-project merged ticket list
- `jg prs` — interactive PR browser with filter bar
- `jg branch --all` — browse all project branches and update the active ticket

## v0.7.0

- Interactive `jg add` TUI (stage/unstage files, commit message prompt)
- Shell hook for per-terminal ticket isolation (`jg hook`)
- Fish/Tide prompt integration (`jg setup`)

## v0.6.0

- `jg info` command
- `jg open` command
- `jg push` opens linked PR after pushing

## v0.5.0

- Initial release: `jg set`, `jg clear`, `jg branch`, `jg commit`, `jg push`
- Config system (`jg config set/get/list`)
