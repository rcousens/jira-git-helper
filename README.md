# jira-git-helper

A terminal-based JIRA ticket context manager for git workflows, invoked as `jg`.

Keeps the current JIRA ticket in your shell environment so that branch names,
commit messages, and PR lookups are automatically prefixed — without you having
to type the ticket key every time.

## Installation

```sh
uv tool install jira-git-helper
```

Or with pipx:

```sh
pipx install jira-git-helper
```

## Configuration

Run these once after installation:

```sh
jg config set server https://yourcompany.atlassian.net
jg config set email  you@yourcompany.com
jg config set token  <your-jira-api-token>
```

Optionally set a default JQL filter for the ticket picker (defaults to your assigned tickets):

```sh
jg config set jql "project = MYPROJECT AND assignee = currentUser() ORDER BY updated DESC"
```

Generate a JIRA API token at: https://id.atlassian.com/manage-profile/security/api-tokens

### Shell hook

The hook lets `jg set` / `jg clear` update `SJ_TICKET` in your current shell,
so each terminal can track a different ticket independently.

**Fish:**
```fish
# Add to ~/.config/fish/config.fish
eval (jg hook)
```

**Bash / Zsh:**
```sh
# Add to ~/.bashrc or ~/.zshrc
eval "$(jg hook --shell bash)"   # or --shell zsh
```

### Tide prompt (fish)

```sh
jg setup
```

Then follow the printed instructions to add `jg` to your Tide prompt items.

## Commands

| Command | Description |
|---|---|
| `jg` | Show the current ticket |
| `jg set [TICKET]` | Set ticket (interactive picker if no argument) |
| `jg clear` | Clear the current ticket |
| `jg info [TICKET]` | Show ticket details |
| `jg open [TICKET]` | Open ticket in browser |
| `jg branch [name]` | Switch to a ticket branch, or create one |
| `jg add` | Interactive staging UI with inline commit |
| `jg commit <message>` | Commit with ticket prefix |
| `jg push` | Push branch and open linked PR |
| `jg diff [TICKET]` | Diff an open/draft PR with `gh` |
| `jg prs [TICKET]` | List all linked PRs |
| `jg config get <key>` | Get a config value |
| `jg config set <key> <value>` | Set a config value |
| `jg config list` | List all config values |
| `jg hook [--shell fish\|bash\|zsh]` | Print shell hook |
| `jg setup` | Configure Tide prompt integration |

## Requirements

- Python 3.10+
- `gh` CLI (only required for `jg diff`) — https://cli.github.com

## License

MIT
