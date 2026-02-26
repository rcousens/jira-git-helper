"""CLI entry point: all Click commands for jg."""

from __future__ import annotations

import json
import subprocess
import sys
import webbrowser
from pathlib import Path

import click
import requests
from jira import JIRAError

from .config import (
    STATE_FILE,
    get_config,
    set_config,
    get_ticket,
    save_ticket,
    clear_ticket,
    get_projects,
    get_fields_for_project,
    get_filters_for_project,
    set_filters_for_project,
    get_active_filter_name,
    set_active_filter_name,
    get_formatters,
    set_formatters,
    get_effective_filter_name,
    _read_config,
    _write_config,
    _session_active_filters,
)
from .git import (
    get_file_statuses,
    get_current_branch,
    check_not_main_branch,
    get_default_branch,
    get_ticket_branches,
    create_branch,
    switch_branch,
)
from .jira_api import (
    get_jira_server,
    get_jira_client,
    ensure_fields_cached,
    get_jira_field_name,
    fetch_issues_for_projects,
    get_prs,
    get_gh_prs,
    STATUS_STYLES,
    PRIORITY_STYLES,
)
from .formatters import run_formatters
from . import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="jg")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Manage JIRA ticket context for git workflows."""
    if ctx.invoked_subcommand is None:
        ticket = get_ticket()
        if ticket:
            click.echo(ticket)
        else:
            click.echo("No ticket set. Use 'jg set TICKET-123' to set one.", err=True)
            sys.exit(1)


@main.command("set")
@click.argument("ticket", required=False)
@click.option("--jql", default=None, help="Raw JQL override — bypasses all filters and project config for this run")
@click.option("--max", "max_results", default=200, show_default=True, help="Max results to fetch")
def cmd_set(ticket: str | None, jql: str | None, max_results: int) -> None:
    """Set the current JIRA ticket, or browse interactively if no ticket given."""
    if ticket:
        try:
            save_ticket(ticket)
        except ValueError as e:
            raise click.ClickException(str(e))
        click.echo(f"Ticket set to {ticket}")
        return

    from .tui.ticket_picker import JiraListApp

    jira = get_jira_client()
    ensure_fields_cached(jira)
    projects = get_projects()

    # Migrate any legacy jql.<PROJECT> config keys to named filters
    for proj in projects:
        legacy_jql = get_config(f"jql.{proj}")
        if legacy_jql:
            if not get_filters_for_project(proj):
                set_filters_for_project(proj, [{"name": "Default", "jql": legacy_jql}])
                set_active_filter_name(proj, "Default")
                _session_active_filters[proj] = "Default"
                click.echo(f"Migrated jql.{proj} to a named filter 'Default'.", err=True)
            cfg = _read_config()
            cfg.pop(f"jql.{proj}", None)
            _write_config(cfg)

    def _collect_extra_fields() -> tuple[list[str], dict[str, str]]:
        seen: set[str] = set()
        ordered: list[str] = []
        for proj in projects:
            for fid in get_fields_for_project(proj):
                if fid not in seen:
                    seen.add(fid)
                    ordered.append(fid)
        names = {fid: get_jira_field_name(fid) for fid in ordered}
        return ordered, names

    extra_field_ids, field_names = _collect_extra_fields()

    click.echo("Fetching tickets…", err=True)
    try:
        if jql:
            issues = list(jira.search_issues(
                jql, maxResults=max_results,
                fields=["summary", "status", "assignee", "priority", "parent", "issuetype"] + extra_field_ids,
            ))
        else:
            issues = fetch_issues_for_projects(jira, projects, max_results, extra_field_ids)
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    if not issues:
        click.echo("No issues found.")
        return

    while True:
        app = JiraListApp(
            issues,
            jira_client=jira,
            extra_field_ids=extra_field_ids,
            field_names=field_names,
            projects=projects,
        )
        app.run()

        if app.reload_needed:
            extra_field_ids, field_names = _collect_extra_fields()
            click.echo("Reloading with updated fields…", err=True)
            try:
                issues = fetch_issues_for_projects(jira, projects, max_results, extra_field_ids)
            except JIRAError as e:
                raise click.ClickException(f"JIRA API error: {e.text}") from e
            continue

        if app.selected_ticket:
            save_ticket(app.selected_ticket)
            click.echo(f"Ticket set to {app.selected_ticket}")
        else:
            click.echo("No ticket selected.", err=True)
        break


@main.command("clear")
def cmd_clear() -> None:
    """Clear the current JIRA ticket."""
    clear_ticket()
    click.echo("Ticket cleared")


@main.command("version")
def cmd_version() -> None:
    """Show the jg version."""
    click.echo(f"jg {__version__}")


@main.command("branch")
@click.argument("name", required=False)
def cmd_branch(name: str | None) -> None:
    """Switch to a ticket branch interactively, or create one with the given name."""
    from .tui.branch import BranchPromptApp, BranchPickerApp
    from .tui.ticket_picker import ensure_ticket

    ticket = ensure_ticket()

    if name:
        branch_name = f"{ticket}-{name}"
        create_branch(branch_name, get_default_branch())
        return

    click.echo("Fetching branches…", err=True)
    subprocess.run(["git", "fetch", "--prune"], capture_output=True, text=True)
    branches = get_ticket_branches(ticket)

    if not branches:
        jira_client = get_jira_client()
        prompt_app = BranchPromptApp(ticket, jira_client)
        prompt_app.run()
        if prompt_app.branch_suffix:
            create_branch(f"{ticket}-{prompt_app.branch_suffix}", get_default_branch())
        return

    app = BranchPickerApp(branches)
    app.run()

    if app.create_new:
        jira_client = get_jira_client()
        prompt_app = BranchPromptApp(ticket, jira_client)
        prompt_app.run()
        if prompt_app.branch_suffix:
            create_branch(f"{ticket}-{prompt_app.branch_suffix}", get_default_branch())
        return

    if app.selected_branch:
        switch_branch(app.selected_branch)


@main.command("add")
def cmd_add() -> None:
    """Interactively stage and unstage files."""
    from .tui.branch import BranchPromptApp
    from .tui.file_picker import FilePickerApp
    from .tui.ticket_picker import ensure_ticket

    ticket = ensure_ticket()

    if get_current_branch() in ("main", "master"):
        jira_client = get_jira_client()
        prompt_app = BranchPromptApp(ticket, jira_client)
        prompt_app.run()
        if not prompt_app.branch_suffix:
            click.echo("Cancelled.", err=True)
            return
        branch_name = f"{ticket}-{prompt_app.branch_suffix}"
        create_branch(branch_name, get_default_branch())

    staged, modified, deleted, untracked = get_file_statuses()

    if not staged and not modified and not deleted and not untracked:
        click.echo("Nothing to do — working tree clean.")
        return

    app = FilePickerApp(staged, modified, deleted, untracked)
    app.run()

    if app.aborted:
        click.echo("Aborted.", err=True)
        return

    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    if app.to_stage:
        subprocess.run(["git", "add", "--", *app.to_stage], check=True, cwd=git_root)
        click.echo(f"Staged {len(app.to_stage)} file(s):")
        for f in sorted(app.to_stage):
            click.echo(f"  + {f}")

    if app.to_unstage:
        subprocess.run(["git", "restore", "--staged", "--", *app.to_unstage], check=True, cwd=git_root)
        click.echo(f"Unstaged {len(app.to_unstage)} file(s):")
        for f in sorted(app.to_unstage):
            click.echo(f"  - {f}")

    if app.commit_message:
        branch = get_current_branch()
        if branch is None or not branch.lower().startswith(ticket.lower()):
            suffix = click.prompt(
                f"Not on a {ticket} branch. Branch suffix (will create {ticket}-<suffix>)"
            )
            branch_name = f"{ticket}-{suffix}"
            create_branch(branch_name)
        full_msg = f"{ticket} {app.commit_message}"
        subprocess.run(["git", "commit", "--no-verify", "-m", full_msg], check=True)
    elif not app.to_stage and not app.to_unstage:
        click.echo("No changes made.", err=True)


# ---------------------------------------------------------------------------
# jg fmt
# ---------------------------------------------------------------------------

@main.group("fmt", invoke_without_command=True)
@click.pass_context
def cmd_fmt(ctx: click.Context) -> None:
    """Run configured formatters against modified files, or manage formatter config."""
    if ctx.invoked_subcommand is None:
        run_formatters()


@cmd_fmt.command("add")
@click.argument("name", required=False)
def cmd_fmt_add(name: str | None) -> None:
    """Add a new formatter (prompts for glob and command)."""
    formatters = get_formatters()
    if not name:
        name = click.prompt("Formatter name")
    if any(f["name"] == name for f in formatters):
        raise click.ClickException(
            f"A formatter named '{name}' already exists. "
            f"Delete it first with: jg fmt delete {name}"
        )
    glob_pattern = click.prompt("File glob (e.g. *.hcl, *.tf)")
    cmd = click.prompt("Command (use {} for the filename, e.g. terragrunt hcl fmt {})")
    formatters.append({"name": name, "glob": glob_pattern, "cmd": cmd})
    set_formatters(formatters)
    click.echo(f"Added formatter '{name}'.")


@cmd_fmt.command("list")
def cmd_fmt_list() -> None:
    """List all configured formatters."""
    formatters = get_formatters()
    if not formatters:
        click.echo("No formatters configured.")
        return
    for fmt in formatters:
        click.echo(fmt["name"])
        click.echo(f"  glob:    {fmt['glob']}")
        click.echo(f"  command: {fmt['cmd']}")


@cmd_fmt.command("edit")
@click.argument("name")
def cmd_fmt_edit(name: str) -> None:
    """Edit an existing formatter's glob and command."""
    formatters = get_formatters()
    fmt = next((f for f in formatters if f["name"] == name), None)
    if fmt is None:
        raise click.ClickException(f"No formatter named '{name}'.")
    glob_pattern = click.prompt("File glob", default=fmt["glob"])
    cmd = click.prompt("Command", default=fmt["cmd"])
    fmt["glob"] = glob_pattern
    fmt["cmd"] = cmd
    set_formatters(formatters)
    click.echo(f"Updated formatter '{name}'.")


@cmd_fmt.command("delete")
@click.argument("name")
def cmd_fmt_delete(name: str) -> None:
    """Delete a formatter by name."""
    formatters = get_formatters()
    new_formatters = [f for f in formatters if f["name"] != name]
    if len(new_formatters) == len(formatters):
        raise click.ClickException(f"No formatter named '{name}'.")
    set_formatters(new_formatters)
    click.echo(f"Deleted formatter '{name}'.")


@cmd_fmt.command("diff")
def cmd_fmt_diff() -> None:
    """Run formatters over all files changed between the current branch and the default branch."""
    from rich.console import Console
    from .formatters import build_fmt_table

    current = get_current_branch()
    default_branch = get_default_branch()
    if current in (None, default_branch, "main", "master"):
        click.echo(
            f"On {current or 'detached HEAD'} — nothing to format. "
            "Switch to a feature branch first.",
            err=True,
        )
        return

    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", f"{default_branch}...HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"Failed to get diff against {default_branch}:\n{result.stderr.strip()}"
        )

    paths = [p for p in result.stdout.splitlines() if p.strip()]
    if not paths:
        click.echo(f"No files changed between {default_branch} and {current}.")
        return

    click.echo(f"Formatting {len(paths)} file(s) changed since {default_branch}…")
    msg, table = build_fmt_table(paths)
    if msg == "clean":
        click.echo("Nothing to format.")
        return
    Console().print(table)


@main.command("push")
def cmd_push() -> None:
    """Push the current branch and open any linked open PR in the browser."""
    from .tui.ticket_picker import ensure_ticket

    ticket = ensure_ticket()

    result = subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        sys.exit(result.returncode)

    if get_config("open_on_push") != "true":
        return

    push_url: str | None = None
    for line in result.stderr.splitlines():
        for word in line.split():
            if word.startswith("https://") and "/pull/new/" in word:
                push_url = word
                break
        if push_url:
            break

    current_branch = get_current_branch()
    try:
        jira = get_jira_client()
        issue = jira.issue(ticket, fields=["summary"])
        prs = get_prs(issue.id)
        open_prs = [p for p in prs if p.get("status") == "OPEN"]
        # Only open a PR that matches the branch we just pushed
        for pr in open_prs:
            if pr.get("source", {}).get("branch") == current_branch:
                url = pr["url"]
                click.echo(f"Opening PR: {url}")
                webbrowser.open(url)
                return
    except Exception:
        pass

    if push_url:
        click.echo(f"Opening: {push_url}")
        webbrowser.open(push_url)


@main.command("reset")
def cmd_reset() -> None:
    """Switch to the main branch and pull latest from origin."""
    default_branch = get_default_branch()
    current_branch = get_current_branch()
    stashed = False

    if current_branch != default_branch:
        click.echo(f"Switching to {default_branch}…")
        result = subprocess.run(
            ["git", "switch", default_branch],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            click.echo(err, err=True)
            is_dirty = "overwritten" in err or "commit your changes or stash" in err
            if is_dirty and click.confirm(
                "Stash your local changes and continue?", default=False
            ):
                subprocess.run(
                    ["git", "stash", "--include-untracked"], check=True
                )
                stashed = True
                switch_retry = subprocess.run(
                    ["git", "switch", default_branch],
                    capture_output=True, text=True,
                )
                if switch_retry.returncode != 0:
                    raise click.ClickException(
                        f"Still could not switch to {default_branch}:\n"
                        + switch_retry.stderr.strip()
                    )
            else:
                raise click.Abort()

    click.echo(f"Pulling latest from origin/{default_branch}…")
    pull_result = subprocess.run(["git", "pull", "origin", default_branch])
    if pull_result.returncode != 0:
        if stashed:
            click.echo(
                "\nYour changes are safely stashed. "
                "Run 'git stash pop' to restore them.",
                err=True,
            )
        raise click.Abort()

    if stashed:
        if click.confirm("Restore your stashed changes?", default=True):
            pop = subprocess.run(
                ["git", "stash", "pop"], capture_output=True, text=True
            )
            if pop.returncode != 0:
                click.echo(pop.stdout.strip(), err=True)
                click.echo(
                    "Stash pop had conflicts — resolve them, then run 'git stash drop'.",
                    err=True,
                )
            else:
                click.echo("Stashed changes restored.")


@main.command("sync")
def cmd_sync() -> None:
    """Rebase the current branch onto the latest default branch from origin."""
    current_branch = get_current_branch()
    if current_branch is None:
        raise click.ClickException("Not on a branch (detached HEAD).")

    default_branch = get_default_branch()
    if current_branch == default_branch:
        raise click.ClickException(
            f"Already on {default_branch}. Use 'jg reset' to pull the latest."
        )

    click.echo(f"Fetching origin…")
    fetch = subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True)
    if fetch.returncode != 0:
        raise click.ClickException(f"Fetch failed:\n{fetch.stderr.strip()}")

    click.echo(f"Rebasing {current_branch} onto origin/{default_branch}…")
    rebase = subprocess.run(["git", "rebase", f"origin/{default_branch}"])
    if rebase.returncode != 0:
        click.echo(
            "\nRebase conflict detected. Resolve the conflicts then run:\n"
            "  git rebase --continue\n"
            "Or to cancel:\n"
            "  git rebase --abort",
            err=True,
        )
        raise click.Abort()

    click.echo(f"Done. {current_branch} is up to date with origin/{default_branch}.")


@main.command("prune")
def cmd_prune() -> None:
    """Interactively prune local branches with no remote."""
    from .tui.prune import PruneApp

    click.echo("Fetching and pruning remote refs…")
    fetch = subprocess.run(["git", "fetch", "--prune"], capture_output=True, text=True)
    if fetch.returncode != 0:
        raise click.ClickException(f"Fetch failed:\n{fetch.stderr.strip()}")

    result = subprocess.run(["git", "branch", "-vv"], capture_output=True, text=True, check=True)

    current = get_current_branch()
    default_branch = get_default_branch()

    branches: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.lstrip("* ").split()
        if not parts:
            continue
        branch = parts[0]
        if branch in (current, default_branch):
            continue
        if ": gone]" in line:
            branches.append({"name": branch, "status": "remote deleted"})
        elif "[origin/" not in line:
            branches.append({"name": branch, "status": "never pushed"})

    if not branches:
        click.echo("No prunable local branches found.")
        return

    app = PruneApp(branches)
    app.run()

    if app.deleted:
        click.echo(f"Deleted {len(app.deleted)} branch(es): {', '.join(app.deleted)}")

    if app.branch_to_switch:
        switch_branch(app.branch_to_switch)


@main.command("commit", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("message")
@click.argument("git_args", nargs=-1, type=click.UNPROCESSED)
def cmd_commit(message: str, git_args: tuple[str, ...]) -> None:
    """Commit with message prefixed by the current ticket (TICKET-123 <message>)."""
    check_not_main_branch()
    ticket = get_ticket()
    if not ticket:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)
    commit_msg = f"{ticket} {message}"
    subprocess.run(["git", "commit", "-m", commit_msg, *git_args], check=True)


@main.command("debug")
@click.argument("ticket")
def cmd_debug(ticket: str) -> None:
    """Dump every raw JIRA field for a ticket — useful for inspecting API shape."""
    from rich.console import Console
    from rich.syntax import Syntax

    jira = get_jira_client()
    try:
        issue = jira.issue(ticket)
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    console = Console()
    console.print(f"\n[bold #00e5ff]{issue.key}[/]  [#b8d4b8]{issue.fields.summary}[/]\n")

    raw = issue.raw.get("fields", {})
    filtered = {k: v for k, v in raw.items() if v not in (None, [], "", {})}
    console.print(Syntax(json.dumps(filtered, indent=2, default=str), "json", theme="monokai"))


@main.command("info")
@click.argument("ticket", required=False)
def cmd_info(ticket: str | None) -> None:
    """Show details for the current (or given) ticket."""
    from rich.console import Console
    from rich.panel import Panel

    from .tui.theme import build_ticket_info

    key = ticket or get_ticket()
    if not key:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)

    jira = get_jira_client()
    try:
        issue = jira.issue(
            key,
            fields=["summary", "status", "assignee", "reporter", "priority", "labels", "description", "issuetype"],
        )
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    content = build_ticket_info(issue, get_jira_server())
    Console().print(Panel(content, title=f"[bold bright_blue]{issue.key}[/bold bright_blue]", border_style="bright_blue", padding=(1, 2)))


@main.command("open")
@click.argument("ticket", required=False)
def cmd_open(ticket: str | None) -> None:
    """Open the current (or given) ticket in the browser."""
    key = ticket or get_ticket()
    if not key:
        click.echo("No ticket set. Use 'jg set TICKET-123' first.", err=True)
        sys.exit(1)
    url = f"{get_jira_server()}/browse/{key}"
    click.echo(f"Opening {url}")
    webbrowser.open(url)


@main.command("prs")
@click.argument("ticket", required=False)
def cmd_prs(ticket: str | None) -> None:
    """Browse PRs linked to the current (or given) ticket."""
    from .tui.pr_picker import PrPickerApp
    from .tui.ticket_picker import ensure_ticket

    key = ticket or ensure_ticket()

    jira = get_jira_client()
    try:
        issue = jira.issue(key, fields=["summary"])
    except JIRAError as e:
        raise click.ClickException(f"JIRA API error: {e.text}") from e

    click.echo(f"Fetching PRs for {key}…", err=True)
    try:
        prs = get_prs(issue.id)
    except requests.HTTPError as e:
        raise click.ClickException(f"Failed to fetch PRs: {e}") from e

    for pr in prs:
        pr["_source"] = "jira"

    # Supplement with GitHub CLI data (silently skipped if gh is unavailable)
    gh_prs = get_gh_prs(key)
    for gh_pr in gh_prs:
        gh_pr["_source"] = "github"
        prs.append(gh_pr)

    if not prs:
        click.echo(f"No PRs linked to {key}.")
        return

    _src = {"github": 0, "jira": 1}
    _sts = {"OPEN": 0, "MERGED": 1, "DECLINED": 2}
    # Sort by lastUpdate desc first (stable sort preserves this within groups)
    sorted_prs = sorted(prs, key=lambda p: p.get("lastUpdate", ""), reverse=True)
    sorted_prs.sort(key=lambda p: (
        _src.get(p.get("_source", "jira"), 9),
        _sts.get(p.get("status", ""), 9),
    ))
    app = PrPickerApp(sorted_prs, open_on_enter=True)
    app.run()

    if app.branch_to_switch:
        switch_branch(app.branch_to_switch)


@main.group("config")
def cmd_config() -> None:
    """Get and set configuration values."""


@cmd_config.command("get")
@click.argument("key")
def config_get(key: str) -> None:
    """Get a config value."""
    value = get_config(key)
    if value is None:
        click.echo(f"{key} is not set", err=True)
        sys.exit(1)
    click.echo(value)


@cmd_config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config value."""
    set_config(key, value)
    click.echo(f"{key} = {value}")


@cmd_config.command("list")
def config_list() -> None:
    """List all config values and configured named filters."""
    known = [
        ("server",  "JIRA server URL, e.g. https://yourcompany.atlassian.net",              False),
        ("email",   "JIRA account email",                                                    False),
        ("token",   "JIRA API token",                                                        True),
        ("projects", "Project key(s) for the ticket picker, e.g. SWY or SWY,ABC (optional)", False),
    ]
    flags = [
        ("fmt_on_add",    "Run formatters automatically before commit in jg add (true/false)"),
        ("open_on_push",  "Open PR in browser after jg push (true/false)"),
    ]
    config = _read_config()
    for key, description, secret in known:
        value = config.get(key)
        if value:
            display = "****" if secret else value
            click.echo(f"{key} = {display}")
        else:
            click.echo(f"{key} = (not set)  # {description}")

    click.echo()
    for key, description in flags:
        value = config.get(key, "false")
        click.echo(f"{key} = {value}  # {description}")

    filter_projects = sorted({
        k[len("filters."):] for k in config
        if k.startswith("filters.") and "." not in k[len("filters."):]
    })
    if filter_projects:
        click.echo()
        for proj in filter_projects:
            filters = get_filters_for_project(proj)
            default = get_active_filter_name(proj)
            for f in filters:
                marker = " (default)" if f["name"] == default else ""
                click.echo(f"filters.{proj}  {f['name']}{marker}")
                click.echo(f"  jql: {f['jql']}")

    formatters = get_formatters()
    if formatters:
        click.echo()
        for fmt in formatters:
            click.echo(f"fmt  {fmt['name']}")
            click.echo(f"  glob:    {fmt['glob']}")
            click.echo(f"  command: {fmt['cmd']}")


@main.command("hook")
@click.option(
    "--shell", "shell",
    default="fish",
    type=click.Choice(["fish", "bash", "zsh"]),
    show_default=True,
    help="Shell to emit hook for",
)
def cmd_hook(shell: str) -> None:
    """Print the shell hook to set JG_TICKET in the current shell."""
    if shell == "fish":
        click.echo(f"""\
# Seed JG_TICKET from the persisted default when this shell starts.
# Always set the variable (even to "") so get_ticket() knows the hook is
# active and won't fall back to the state file from another shell.
if not set -q JG_TICKET
    set -gx JG_TICKET (cat {STATE_FILE} 2>/dev/null)
end

function jg
    command jg $argv
    set -l _jg_exit $status
    switch "$argv[1]"
        case set
            set -l _jg_ticket (cat {STATE_FILE} 2>/dev/null)
            if test -n "$_jg_ticket"
                set -gx JG_TICKET $_jg_ticket
            end
        case clear
            set -gx JG_TICKET ""
    end
    return $_jg_exit
end""")
    else:
        click.echo(f"""\
# Seed JG_TICKET from the persisted default when this shell starts.
# Always set the variable (even to "") so get_ticket() knows the hook is
# active and won't fall back to the state file from another shell.
if [ -z "${{JG_TICKET+x}}" ]; then
    export JG_TICKET="$(cat {STATE_FILE} 2>/dev/null)"
fi

# Splice into your prompt:
#   bash: PS1='$(__jg_ps1)\\$ '
#   zsh:  PROMPT='$(__jg_ps1)%% '
__jg_ps1() {{
    [ -n "${{JG_TICKET:-}}" ] && printf '%s ' "$JG_TICKET"
}}

jg() {{
    command jg "$@"
    local _jg_exit=$?
    case "$1" in
        set)
            local _jg_ticket
            _jg_ticket=$(cat {STATE_FILE} 2>/dev/null)
            if [ -n "$_jg_ticket" ]; then
                export JG_TICKET="$_jg_ticket"
            fi
            ;;
        clear)
            export JG_TICKET=""
            ;;
    esac
    return $_jg_exit
}}""")


@main.command("setup")
def cmd_setup() -> None:
    """Configure fish/tide prompt integration."""
    tide_fn_file = Path.home() / ".config" / "fish" / "functions" / "_tide_item_jg.fish"
    fish_fn = """\
function _tide_item_jg
    if set -q JG_TICKET; and test -n "$JG_TICKET"
        _tide_print_item jg $tide_jg_icon' ' $JG_TICKET
    end
end
"""

    if tide_fn_file.exists():
        click.confirm(
            f"{tide_fn_file} already exists. Overwrite?", abort=True
        )
    else:
        click.confirm(f"Create {tide_fn_file}?", abort=True)

    tide_fn_file.parent.mkdir(parents=True, exist_ok=True)
    tide_fn_file.write_text(fish_fn)
    click.echo(f"Wrote {tide_fn_file}")
    click.echo()
    click.echo("To finish setup, run these in fish:")
    click.echo("  set -U tide_right_prompt_items $tide_right_prompt_items jg")
    click.echo("  set -U tide_jg_icon '󰔖'")
    click.echo("  set -U tide_jg_bg_color blue")
    click.echo("  set -U tide_jg_color white")
