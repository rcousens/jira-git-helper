"""Git helper utilities for jira-git-helper."""

import subprocess

import click


def get_file_statuses() -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (staged, modified, deleted, untracked) as lists of (status_code, filepath)."""
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException("Not a git repository or git not available.")
    staged, modified, deleted, untracked = [], [], [], []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        if x == "?" and y == "?":
            untracked.append(("?", path))
        else:
            if x not in (" ", "?"):
                staged.append((x, path))
            if y not in (" ", "?"):
                if y == "D":
                    deleted.append(("D", path))
                else:
                    modified.append((y, path))
    return staged, modified, deleted, untracked


def get_current_branch() -> str | None:
    """Return the current git branch name, or None if not on a branch."""
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def check_not_main_branch() -> None:
    """Abort with an error if the current branch is main or master."""
    branch = get_current_branch()
    if branch in ("main", "master"):
        raise click.ClickException(
            f"You are on '{branch}', which is branch-protected. "
            "Create a feature branch first (e.g. jg branch <name>)."
        )


def get_default_branch() -> str:
    """Return the default branch name by asking origin, falling back to main/master."""
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        # e.g. "refs/remotes/origin/main\n"
        return result.stdout.strip().split("/")[-1]
    # Fallback: look for main or master in local branches
    branches_out = subprocess.run(
        ["git", "branch"], capture_output=True, text=True,
    ).stdout
    local = {b.lstrip("* ").strip() for b in branches_out.splitlines()}
    for name in ("main", "master"):
        if name in local:
            return name
    return "main"



def get_ticket_branches(ticket: str) -> list[dict]:
    """Return local + remote branches matching ticket.

    Each dict: {name, is_current, tracking, status}.
    - tracking: "local", "remote", or "tracked"
    - status: "never pushed", "remote only", "remote deleted", or "" (healthy)
    """
    current = get_current_branch()
    ticket_lower = ticket.lower()

    # Local branches with upstream and tracking state
    result = subprocess.run(
        ["git", "for-each-ref",
         "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)",
         "refs/heads/"],
        capture_output=True, text=True,
    )
    # name -> (tracking, status)
    local_branches: dict[str, tuple[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split("\t")
        if not parts or not parts[0]:
            continue
        branch = parts[0]
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        if ticket_lower not in branch.lower():
            continue
        if not upstream:
            local_branches[branch] = ("local", "never pushed")
        elif "[gone]" in track:
            local_branches[branch] = ("local", "remote deleted")
        else:
            local_branches[branch] = ("tracked", "")

    # Remote branches
    result = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"],
        capture_output=True, text=True,
    )
    remote_only: list[str] = []
    for line in result.stdout.splitlines():
        ref = line.strip()
        if not ref or ref == "origin/HEAD":
            continue
        short = ref.removeprefix("origin/")
        if ticket_lower in short.lower() and short not in local_branches:
            remote_only.append(short)

    branches: list[dict] = []
    for name, (tracking, status) in local_branches.items():
        branches.append({"name": name, "is_current": name == current,
                         "tracking": tracking, "status": status})
    for name in remote_only:
        branches.append({"name": name, "is_current": False,
                         "tracking": "remote", "status": "remote only"})

    branches.sort(key=lambda b: (not b["is_current"], b["name"].lower()))
    return branches


def create_branch(name: str, base: str | None = None) -> None:
    """Create and switch to *name*, optionally branching from *base*."""
    cmd = ["git", "switch", "-C", name]
    if base:
        cmd.append(base)
        click.echo(f"Creating branch: {name} (from {base})")
    else:
        click.echo(f"Creating branch: {name}")
    subprocess.run(cmd, check=True)


def switch_branch(name: str) -> None:
    """Switch to *name*, raising ClickException on failure."""
    result = subprocess.run(["git", "switch", name], capture_output=True, text=True)
    if result.returncode == 0:
        click.echo(f"Switched to branch: {name}")
    else:
        raise click.ClickException(f"Failed to switch to '{name}':\n{result.stderr.strip()}")


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            result = subprocess.run(cmd, input=text.encode(), capture_output=True)
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False
