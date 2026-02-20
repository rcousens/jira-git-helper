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

**2. Set up the shell hook** (so `jg set`/`jg clear` update your current terminal)

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

**3. (Optional) Scope tickets to your projects**

```sh
jg config set projects SWY
# or multiple:
jg config set projects SWY,DOPS
```

**4. Pick a ticket and start working**

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

The hook wraps the `jg` command in your shell so that `jg set` and `jg clear`
update the `JG_TICKET` environment variable in your **current terminal session**.
Without the hook, ticket state is shared across all terminals via a file.

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

## Tide prompt integration (fish)

Display the active ticket in your [Tide](https://github.com/IlanCosman/tide) prompt:

```sh
jg setup
```

Then follow the printed instructions to add `jg` to your Tide prompt items.

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
| Type anything | Filter the list by key, summary, assignee, or status |
| `Enter` | Select the highlighted ticket |
| `Escape` | Cancel |

---

### `jg clear`

Clear the active ticket for the current session.

```sh
jg clear
```

---

### `jg info [TICKET]`

Show a detailed summary of a ticket: status, priority, assignee, reporter, labels,
description, and a direct URL.

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

---

### `jg add`

An interactive TUI for staging files and committing — all in one step.

```sh
jg add
```

The screen is split into three sections (staged, modified, untracked). Use `Space`
to toggle files between staged/unstaged, then `Enter` to open the commit message
prompt. The commit message is automatically prefixed with the active ticket key.

**Controls:**

| Key | Action |
|---|---|
| `↑` / `↓` | Move between files |
| `Space` | Stage or unstage the highlighted file |
| Type anything | Filter files within the focused section |
| `Enter` | Open commit message prompt |
| `Escape` | Cancel / close filter |

> **Note:** `jg add` refuses to run on `main` or `master` to prevent accidental commits.

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

> **Note:** Like `jg add`, this refuses to run on `main` or `master`.

---

### `jg push`

Push the current branch to origin and open the linked PR in your browser.

```sh
jg push
```

After pushing, `jg push` looks up the active ticket in JIRA to find any linked open
PR. If found, it opens that PR. If not found but GitHub printed a "Create a pull
request" URL during the push, it opens that instead.

---

### `jg diff [TICKET]`

Show a diff of an open or draft PR linked to the ticket, using the `gh` CLI.

```sh
jg diff            # uses the active ticket
jg diff SWY-5678   # diff PRs for any ticket
```

If multiple PRs are found, an interactive picker lets you choose one.

| Flag | Description |
|---|---|
| `--all` | Include merged and declined PRs, not just open/draft ones |

> **Requires:** [`gh` CLI](https://cli.github.com) installed and authenticated.

---

### `jg prs [TICKET]`

List all GitHub PRs linked to a ticket.

```sh
jg prs             # uses the active ticket
jg prs SWY-5678    # list PRs for any ticket
```

PRs are sorted with open ones first, then by last-updated date. Status is
colour-coded: green (open), yellow (draft), blue (merged), red (declined).

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

---

### `jg setup`

Configure Tide prompt integration. Creates
`~/.config/fish/functions/_tide_item_jg.fish` and prints the follow-up commands
needed to activate it.

```sh
jg setup
```

---

## Requirements

- Python 3.10+
- `gh` CLI — only required for `jg diff` — https://cli.github.com

---

## License

MIT
