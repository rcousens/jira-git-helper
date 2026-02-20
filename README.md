# jira-git-helper

A terminal-based JIRA ticket context manager for git workflows, invoked as `jg`.

`jg` keeps track of which JIRA ticket you're working on so that branch names,
commit messages, and PR lookups are automatically prefixed — without you having
to type the ticket key every time.

## How it works

`jg` maintains an **active ticket** for each terminal session (e.g. `SWY-1234`).
Once set, commands like `jg commit`, `jg branch`, and `jg push` automatically use
that ticket — you never have to copy-paste it again.

```
$ jg set          # pick a ticket interactively
$ jg branch fix   # creates SWY-1234-fix and switches to it
$ jg add          # stage files and commit — ticket prefix added automatically
$ jg push         # pushes branch and opens the linked PR
```

Each terminal window can track a different ticket independently.

---

## Installation

```sh
uv tool install jira-git-helper
```

Or with pipx:

```sh
pipx install jira-git-helper
```

---

## Quick start

**1. Connect to JIRA**

```sh
jg config set server https://yourcompany.atlassian.net
jg config set email  you@yourcompany.com
jg config set token  <your-jira-api-token>
```

Generate a token at: https://id.atlassian.com/manage-profile/security/api-tokens

**2. Set up the shell hook**

The hook lets each terminal track its own ticket independently. See [Shell hook](#shell-hook) for full details and shell-specific instructions.

**3. (Optional) Show the active ticket in your prompt**

See [Prompt integration](#prompt-integration) for fish/Tide, bash, and zsh instructions.

**4. (Optional) Scope tickets to your projects**

```sh
jg config set projects SWY
# or multiple:
jg config set projects SWY,DOPS
```

**5. Pick a ticket and start working**

```sh
jg set        # opens an interactive picker
jg            # shows the active ticket at any time
```

---

## Configuration

Config is stored in `~/.config/jira-git-helper/config`. Use `jg config set/get/list` to manage it.

### Required

| Key | Description |
|---|---|
| `server` | Your JIRA instance URL, e.g. `https://yourcompany.atlassian.net` |
| `email` | Your JIRA account email |
| `token` | Your JIRA API token |

### Optional

| Key | Description |
|---|---|
| `projects` | Comma-separated project keys to scope the ticket picker, e.g. `SWY` or `SWY,DOPS` |
| `jql.<PROJECT>` | Custom JQL for a specific project (see below) |

### Project scoping

Without `projects` set, `jg set` shows all tickets assigned to you across JIRA.
With `projects` set, results are scoped to just those projects:

```sh
# Single project
jg config set projects SWY

# Multiple projects — results from all projects are merged into one list
jg config set projects SWY,DOPS
```

### Per-project JQL

By default each project uses:
```
project = <KEY> AND assignee = currentUser() ORDER BY updated DESC
```

Override this for any project with a `jql.<PROJECT>` key:

```sh
jg config set jql.SWY "project = SWY AND sprint in openSprints() AND assignee = currentUser()"
jg config set jql.DOPS "project = DOPS AND status != Done AND assignee = currentUser()"
```

**JQL resolution order** (for a given project key):
1. `jql.<PROJECT>` — if set, this wins
2. `project = PROJECT AND assignee = currentUser() ORDER BY updated DESC` — default

When multiple projects are configured and none have custom JQL, a single combined
JIRA query is used. If any project has custom JQL, one query per project is run and
results are merged.

### View your current config

```sh
jg config list
```

This shows all standard keys plus any `jql.<PROJECT>` keys you've set.

---

## Shell hook

The hook does three things:

1. **Seeds `JG_TICKET`** from the last-used ticket when a new shell opens (so you
   don't start from scratch every time).
2. **Keeps terminals isolated** — `jg set` in one terminal updates only that
   terminal's `JG_TICKET`. Other open terminals are unaffected.
3. **Updates `JG_TICKET`** after `jg set` or `jg branch --all`, and clears it after
   `jg clear`.

Without the hook, all terminals share the same ticket via the state file.

Fish — add to `~/.config/fish/config.fish`:
```fish
eval (jg hook)
```

Bash — add to `~/.bashrc`:
```sh
eval "$(jg hook --shell bash)"
```

Zsh — add to `~/.zshrc`:
```sh
eval "$(jg hook --shell zsh)"
```

---

## Prompt integration

### Fish / Tide

Display the active ticket in your [Tide](https://github.com/IlanCosman/tide) prompt.
Run once to install the prompt item:

```sh
jg setup
```

Then follow the printed instructions to add `jg` to your Tide prompt items.

> The `jg setup` command writes `~/.config/fish/functions/_tide_item_jg.fish`, which
> reads the shell-local `$JG_TICKET` variable — so each terminal shows its own ticket.

### Bash

The hook defines a `__jg_ps1` helper. Splice it into your `PS1` in `~/.bashrc`
(after the `eval` line):

```sh
PS1='$(__jg_ps1)\$ '
```

Or anywhere inside an existing prompt string, e.g.:

```sh
PS1='\u@\h $(__jg_ps1)\$ '
```

### Zsh

Same helper, different variable. Add to `~/.zshrc` (after the `eval` line):

```sh
PROMPT='$(__jg_ps1)%% '
```

`__jg_ps1` prints the active ticket followed by a space, or nothing if no ticket is set.

---

## Commands

### `jg`

Show the active ticket for the current session.

```sh
$ jg
SWY-1234
```

---

### `jg set [TICKET]`

Set the active ticket. With no argument, opens an interactive picker that fetches
tickets from JIRA based on your configured projects and JQL.

```sh
jg set             # interactive picker
jg set SWY-1234    # set directly without opening the picker
```

**Flags:**

| Flag | Description |
|---|---|
| `--jql "..."` | Use a raw JQL query instead of configured project JQL. Useful for one-off searches without changing your config. |
| `--max N` | Maximum number of tickets to fetch (default: `200`) |

**Examples:**

```sh
# Show only high-priority tickets, one-off
jg set --jql "project = SWY AND priority = Highest ORDER BY created DESC"

# Fetch more results than the default
jg set --max 500
```

**Interactive picker controls:**

| Key | Action |
|---|---|
| `↑` / `↓` | Move between tickets |
| `/` | Open filter bar — type to narrow by key, summary, assignee, or status |
| `Enter` | Select the highlighted ticket (or confirm filter and return to list) |
| `Escape` | Close filter / cancel |

---

### `jg clear`

Clear the active ticket for the current session.

```sh
jg clear
```

---

### `jg info [TICKET]`

Show a rich summary panel for a ticket, including: summary, status, priority,
assignee, reporter, labels, URL, and a description excerpt (truncated at 800 chars).

```sh
jg info            # uses the active ticket
jg info SWY-5678   # look up any ticket by key
```

---

### `jg open [TICKET]`

Open a ticket in your browser.

```sh
jg open            # opens the active ticket
jg open SWY-5678   # open any ticket by key
```

---

### `jg branch [name]`

Work with git branches scoped to the active ticket.

**With no arguments** — opens an interactive branch picker showing all local branches
that match the active ticket key. Selecting one switches to it.

```sh
jg branch
```

**With a name** — creates a new branch named `TICKET-branch-name` (using `git switch -C`)
and switches to it.

```sh
jg branch my-feature    # creates SWY-1234-my-feature
```

**With `--all`** — shows all local branches matching any of your configured projects,
regardless of the active ticket. Selecting a branch also sets the active ticket to
match the ticket key embedded in the branch name.

```sh
jg branch --all    # requires `projects` to be configured
```

| Flag | Description |
|---|---|
| `--all` | Browse all project branches and update the active ticket to match |

> **Note:** `--all` requires `projects` to be configured.
> Branch names are expected to follow the `PROJECT-1234-description` convention.

**Interactive picker controls:**

| Key | Action |
|---|---|
| `↑` / `↓` | Move between branches |
| `/` | Open filter bar — type to narrow by branch name |
| `Enter` | Switch to the highlighted branch (or confirm filter and return to list) |
| `Escape` | Close filter / cancel |

---

### `jg add`

An interactive TUI for staging files and committing — all in one step.

```sh
jg add
```

The screen is split into up to three sections (staged, modified, untracked). Use `Space`
to toggle files between staged/unstaged, then `Enter` to open the commit message
prompt. The commit message is automatically prefixed with the active ticket key.

**Controls:**

| Key | Action |
|---|---|
| `↑` / `↓` | Move between files |
| `Space` | Stage or unstage the highlighted file |
| `/` | Open filter bar for the focused section |
| `Enter` | Open commit message prompt (or confirm filter and return to list) |
| `Escape` | Close filter / cancel |

> **Note:** If no ticket is set, `jg add` will prompt you to pick one interactively before proceeding.

---

### `jg commit <message>`

Commit with the active ticket key automatically prepended to the message.

```sh
jg commit "fix login redirect"
# runs: git commit -m "SWY-1234 fix login redirect"
```

Any extra arguments after the message are passed through to `git commit`:

```sh
jg commit "fix login redirect" --no-verify
jg commit "fix login redirect" --amend
```

> **Note:** Refuses to run on `main` or `master`. Use `jg branch <name>` to create a feature branch first.

---

### `jg push`

Push the current branch to origin (`git push -u origin HEAD`) and open the linked
PR in your browser.

```sh
jg push
```

After pushing, `jg push` looks up the active ticket in JIRA to find any linked open
PR. If found, it opens that PR. If not found but GitHub printed a "Create a pull
request" URL during the push, it opens that instead.

---

### `jg prs [TICKET]`

Browse all GitHub PRs linked to a ticket in an interactive TUI, with inline diff viewing.

```sh
jg prs             # uses the active ticket
jg prs SWY-5678    # browse PRs for any ticket
```

Columns shown: Status, Author, Repo, Source branch, Title. PRs are sorted with open
ones first, then by last-updated date. Status is colour-coded: green (open), yellow
(draft), blue (merged), red (declined).

**Controls:**

| Key | Action |
|---|---|
| `↑` / `↓` | Move between PRs |
| `/` | Open filter bar — searches status, author, repo, branch, and title |
| `o` | Open the highlighted PR in your browser |
| `d` | View the PR diff inline |
| `Escape` | Close filter / quit |

**Diff viewer:**

Press `d` on any PR (open or merged) to open a full-screen diff viewer. If
[`delta`](https://github.com/dandavison/delta) is installed it is used for
syntax-aware colouring; otherwise Rich syntax highlighting is applied.

| Key | Action |
|---|---|
| `↑` / `↓` | Scroll the diff |
| `/` | Open search bar — type a term and press `Enter` to commit the search |
| `Enter` | Jump to the next match (cycles through all matches) |
| `n` | Jump to the next file in the diff |
| `p` | Jump to the previous file in the diff |
| `Escape` | Clear active search, or close the diff viewer |

An active search is shown in an amber status bar above the footer, displaying the
search term and current position (e.g. `Search: foo  3/7 matches — Enter next  Esc clear`).
All matches are highlighted inline. Press `Escape` once to clear the search, and
again to close the diff viewer.

> **Requires:** [`gh` CLI](https://cli.github.com) installed and authenticated.

---

### `jg config get <key>`

Print a single config value.

```sh
jg config get server
jg config get jql.SWY
```

Exits with a non-zero status if the key is not set.

---

### `jg config set <key> <value>`

Set a config value. Standard keys are `server`, `email`, `token`, and `projects`.
Use `jql.<PROJECT>` to set per-project JQL:

```sh
jg config set server   https://yourcompany.atlassian.net
jg config set email    you@yourcompany.com
jg config set token    <api-token>
jg config set projects SWY,DOPS

jg config set jql.SWY "project = SWY AND sprint in openSprints() AND assignee = currentUser()"
```

---

### `jg config list`

List all configured values. Masks the `token` value for safety. Automatically
shows any `jql.<PROJECT>` keys you have set.

```sh
jg config list
```

---

### `jg hook [--shell fish|bash|zsh]`

Print the shell hook function to stdout. Intended to be evaluated in your shell
startup file (see [Shell hook](#shell-hook) above).

```sh
jg hook                  # fish (default)
jg hook --shell bash
jg hook --shell zsh
```

| Flag | Description |
|---|---|
| `--shell fish\|bash\|zsh` | Shell to emit the hook for (default: `fish`) |

The bash/zsh hook also defines `__jg_ps1` for prompt integration (see
[Prompt integration](#prompt-integration)).

---

### `jg setup`

Configure fish/Tide prompt integration. Creates
`~/.config/fish/functions/_tide_item_jg.fish` and prints the follow-up `set -U`
commands needed to activate and style the prompt item.

```sh
jg setup
```

> Fish/Tide only. For bash/zsh prompt integration, see [Prompt integration](#prompt-integration).

---

### `jg version`

Print the installed version.

```sh
jg version
# or
jg --version
```

---

## Requirements

- Python 3.10+
- `gh` CLI — required for PR diff viewing in `jg prs` — https://cli.github.com

---

## License

MIT
