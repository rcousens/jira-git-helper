# Changelog

## v0.17.0

### Removed: `jg diff`

- `jg diff` has been removed. Its functionality is fully covered by `jg prs` — open the PR browser, then press `d` to view the diff inline with search, file navigation, and delta support. `jg prs` also shows all PR statuses (open, merged, declined) without needing an `--all` flag.

---

## v0.16.0

### `jg prune` — interactive TUI

`jg prune` is now a full interactive DataTable instead of a confirm-and-delete prompt.

- **Two categories of prunable branches** are shown: `remote deleted` (upstream existed and was deleted, typically after a merged PR) and `never pushed` (local-only branch that was never pushed to remote). Previously only "remote deleted" branches were caught; "never pushed" branches are now included.
- **`Space`** — toggle a branch for deletion.
- **`a`** — select / deselect all branches.
- **`d`** — view a diff of the highlighted branch against the default branch (`main`/`master`) in a full-screen viewer. Uses [`delta`](https://github.com/dandavison/delta) if installed, otherwise Rich syntax highlighting.
- **`x`** — delete all selected branches (confirmation prompt before deleting).
- **`s`** — switch to the highlighted branch and exit.
- **`Escape`** — quit without changes.

### `jg prs` — switch to PR branch

- **`s` — switch branch**: press `s` on any PR to switch to its source branch. If the branch exists locally, `git switch <branch>` is run immediately. If not, a warning is printed and the branch is created from the default branch (`git switch -c <branch> <default>`).

### Context bar in all TUI screens

All interactive screens now show a one-line header displaying the active ticket and current git branch:

```
ticket: SWY-1234   branch: SWY-1234-my-feature
```

This appears at the top of `jg set`, `jg add`, `jg branch`, and `jg prs`.

### Visual theme overhaul

All TUI screens and modals now use a consistent dark hacker-style colour palette — near-black backgrounds, matrix green accents, cyan for headers and key chips, amber for status and warnings. Every `$theme-variable` reference has been replaced with explicit hex colours for full visual control.

### `jg add` — auto-fmt crash fix

- Fixed a crash when `fmt.on_commit` is enabled and the auto-formatter clears all staged files (e.g. the formatter rewrites a file that was the only staged change). The commit modal no longer opens in this case — instead, `jg add` exits cleanly.

---

## v0.15.0

### New command: `jg prune`

- Runs `git fetch --prune` to refresh remote-tracking refs, then lists every local branch whose upstream is gone (i.e. the remote branch was deleted — typically after a PR was merged). Shows the list with a confirmation prompt before deleting. Uses `git branch -D` so squash-merged and rebase-merged branches are handled correctly. Skips the currently checked-out branch and reports any failures individually.

### New command: `jg sync`

- Fetches from origin and rebases the current feature branch onto `origin/<default-branch>` (auto-detected). Keeps you on your branch — unlike `jg reset` which switches away to the default branch. If a rebase conflict occurs, git's standard conflict resolution flow applies (`git rebase --continue` / `--abort`).

### `jg set` — copy ticket URL

- **`c` — copy URL**: press `c` on any ticket to copy its JIRA URL to the system clipboard. A toast notification confirms the URL. Supports `pbcopy` (macOS), `wl-copy` (Wayland), and `xclip` (X11).

### `jg add` — auto-format on commit

- New config flag `fmt.on_commit`: when set to `true`, the format modal runs automatically before the commit message prompt every time you press `Enter` to commit in `jg add`. Git status is reloaded after formatting before the prompt opens.

  ```sh
  jg config set fmt.on_commit true
  ```

---

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
