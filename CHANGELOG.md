# Changelog

## v0.22.0

### `jg push` — `open_on_push` config flag and branch-aware PR matching

The post-push PR-opening behaviour is now opt-in via `open_on_push`:

```sh
jg config set open_on_push true
```

When enabled, `jg push` looks up open PRs linked to the active ticket and opens only the one matching the current branch. Previously it opened the first open PR found — which could be an unrelated PR against the same ticket from a different branch.

If no matching PR exists, the "Create a pull request" URL from the git push output is opened instead.

### `jg add` — formatter staging drift detection

When `fmt_on_add` is enabled, formatters now run on every commit attempt. If a formatter modifies a staged file (dropping it back to modified), the commit is paused with a warning: "Formatter modified files — review staging before committing". The staging screen stays open so you can re-stage the affected files and try again. On the second attempt, idempotent formatters produce no changes and the commit proceeds normally.

### `jg add` — `r` to refresh git status

Press `r` in the file picker to reload git status and refresh all sections at any time.

### Config flag rename: `fmt.on_commit` → `fmt_on_add`

The auto-formatter config key has been renamed for consistency with the underscore convention used by other flags. Update your config:

```sh
jg config set fmt_on_add true
```

### `jg config list` — shows all flags

`jg config list` now displays all boolean flags (`fmt_on_add`, `open_on_push`) with their current value and a description, so they are discoverable without reading docs.

---

## v0.21.0

### Shared helpers to reduce TUI and CLI duplication

Three new helpers consolidate repeated patterns across TUI apps and CLI commands.

- **`cursor_row_key(table)`** in `tui/theme.py` — extracts the row key at the DataTable cursor, replacing a 4-line pattern that was duplicated across 7 call sites in `ticket_picker.py`, `branch.py`, `file_picker.py`, `pr_picker.py`, and `prune.py`.

- **`FilterBarMixin`** in `tui/theme.py` — provides `action_activate_filter()` and `_handle_filter_keys()` for apps with a `#filter-bar` Input + DataTable. Subclasses implement `_reset_filter()` to repopulate their view. Applied to:
  - `BranchPickerApp` — `on_key()` reduced from ~25 lines to 4.
  - `PrPickerApp` — `on_key()` reduced from ~25 lines to 4.
  - `JiraListApp` — partial use (custom Input handling for tree/table dual mode; mixin handles DataTable-mode navigation and escape-when-filter-visible).

- **`create_branch(name, base=None)`** in `git.py` — consolidates `git switch -C` + echo logic used at 4 sites in `cli.py` (`cmd_branch` ×2, `cmd_add` ×2).

No functionality changes — pure deduplication refactor.

### Shell hook: fix cross-shell ticket bleed after `jg clear`

Fixed a bug where clearing a ticket in one shell (`jg clear`) and then setting a ticket in a second shell would cause the first shell to silently pick up the second shell's ticket — despite the prompt showing no active ticket.

**Root cause:** `jg clear` deleted the state file and unset `JG_TICKET` entirely. `get_ticket()` could not distinguish "hook active, ticket cleared" from "no hook installed", so it fell through to reading the state file — which another shell had since written to.

**Fix:** The hook now sets `JG_TICKET=""` (empty string) on clear instead of unsetting it. `get_ticket()` treats any defined `JG_TICKET` (including empty) as authoritative and never falls back to the state file. The state file fallback only applies in shells without the hook installed.

**Breaking change for existing hook users:** You must re-source the hook in all open shells for the fix to take effect:

```sh
# fish
source (jg hook | psub)

# bash / zsh
eval "$(jg hook --shell bash)"   # or zsh
```

Or simply restart your terminal.

### `jg prs` — fix branch switching to use remote tracking

Pressing `s` in `jg prs` to switch to a PR's source branch now uses `git switch` directly, which handles both cases correctly:

- **Branch exists locally** — switches to it.
- **Branch only exists on a remote** — creates a local tracking branch from the remote (equivalent to `git checkout --track origin/<branch>`).

Previously, when the branch didn't exist locally, `jg prs` would create it from the default branch (`main`/`master`) — discarding the remote branch's history and not setting up tracking.

---

## v0.20.0

### Multi-module package refactor

The single-file monolith (`jira_git_helper.py`, ~3,700 lines) has been split into a proper Python package with 14 focused modules. No functionality changes — pure structural refactor.

```
jira_git_helper/
├── __init__.py           # package version
├── cli.py                # all Click commands (879 lines)
├── config.py             # config file + ticket state management (150 lines)
├── formatters.py         # formatter execution, eof fixer (153 lines)
├── git.py                # git subprocess wrappers (97 lines)
├── jira_api.py           # JIRA client, field cache, issue/PR fetching (172 lines)
└── tui/
    ├── __init__.py
    ├── theme.py           # shared CSS, colour constants, helpers (172 lines)
    ├── modals.py          # TextInputModal, ConfirmModal, FmtModal (143 lines)
    ├── ticket_picker.py   # JiraListApp + supporting modals (829 lines)
    ├── branch.py          # BranchPromptApp, BranchPickerApp, BranchDiffModal (274 lines)
    ├── file_picker.py     # FilePickerApp, CommitModal (418 lines)
    ├── pr_picker.py       # PrPickerApp, DiffModal (379 lines)
    └── prune.py           # PruneApp (168 lines)
```

**Key improvements:**
- **CSS deduplication**: shared CSS blocks (`SCREEN_CSS`, `DATATABLE_CSS`, `FOOTER_CSS`, etc.) in `tui/theme.py` replace 5+ inline copies across TUI apps.
- **Shared ticket info renderer**: `build_ticket_info()` in `tui/theme.py` eliminates duplicate fetch-and-render logic between `TicketInfoModal` and `BranchPromptApp`.
- **Lazy TUI imports**: CLI commands import TUI modules inside their functions, keeping `jg --help` and non-interactive commands fast.
- **Clean dependency graph**: core modules (`config`, `git`, `jira_api`, `formatters`) have no TUI dependencies; TUI modules import from core; `cli.py` orchestrates both.

---

## v0.19.0

### Branch prompt on main/master

A new `BranchPromptApp` TUI screen is shown whenever `jg branch` or `jg add` is run while on `main` or `master`. It displays the active ticket's full info (summary, status, priority, assignee, description) and a branch suffix input at the bottom — the same ticket info shown by `jg info`, loaded asynchronously while you type.

- **`jg branch`** (no arguments): if on main/master, shows the branch prompt TUI. On confirm, creates `TICKET-<suffix>` from the default branch, switches to it, then falls through to the interactive branch picker showing all local branches for the ticket (including the newly created one).
- **`jg add`**: if on main/master, shows the branch prompt TUI before the file picker. On confirm, creates the branch and switches to it, then continues to the staging screen on the new branch.
- **Escape** in the branch prompt cancels entirely.

### Consistent colour scheme across all TUI screens

All interactive DataTable and tree views now share the same colour palette:

| Field type | Colour |
|---|---|
| Key / identifier / branch name | cyan `#00e5ff` |
| Summary / title / description | pale-green `#b8d4b8` |
| Status / category | amber `#ffb300` |
| Person (assignee / author) | purple `#b39ddb` |
| Staged / active / selected | matrix green `#00ff41` |
| Modified files | amber `#ffb300` |
| Deleted / untracked files | red `#ff5555` |

**`jg prs`**: Author → purple, Repo → amber, Source branch → cyan, Title → pale-green.

**`jg branch`**: current branch marker and name → bold cyan; other branches → pale-green.

**`jg prune`**: branch name column → cyan.

**`jg add`**: staged status labels → matrix green; modified labels and paths → amber; untracked and deleted labels and paths → red.

### Filter bar colour consistency

The per-section filter inputs in `jg add` now use the same bright green border (`#00ff41`) as filter bars in all other screens. Previously they used a dim dark-green border.

### Filter bar layout fix (all screens)

The filter bars in `jg prs` and `jg branch` had a `dock: bottom` + `margin-bottom: 1` workaround that caused a one-line layout gap. They now match `jg set`'s approach — no dock, sitting in the normal vertical flow above the footer.

---

## v0.18.0

### `jg set` — tree view fixes and visual improvements

- **Hierarchy fixed**: parent–child relationships now render correctly. The `parent` and `issuetype` fields are now explicitly requested from JIRA, enabling `_build_issue_tree` to resolve epic → task → subtask relationships (previously every issue appeared as a root node).
- **Wider summaries**: node labels now show up to 80 characters of the ticket summary (was 55).
- **Colour scheme**: tree node labels now use the full colour palette — **cyan** key · pale-green summary · **amber** `[status]` · **purple** assignee. Previously a blanket `.tree--label` CSS rule was overriding all inline colours with a flat dark green.

### `jg set` — data table colour scheme

- The flat DataTable now uses the same colour palette as the tree view: **cyan** key, **amber** status, **purple** assignee, pale-green summary — replacing the previous uniform pale-green across all columns.

### `jg set` — filter bar layout fix

- The filter bar (activated with `/`) was cut off by one line in both table and tree view. Removed the conflicting `dock: bottom` declaration so the filter bar appears correctly in the layout above the footer.

### New command: `jg debug <ticket>`

- Dumps every non-null raw JIRA field returned by the API for a ticket, formatted as syntax-highlighted JSON. Useful for inspecting API shape, discovering custom field IDs, and diagnosing tree hierarchy issues.

  ```sh
  jg debug SWY-1234
  ```

---

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
