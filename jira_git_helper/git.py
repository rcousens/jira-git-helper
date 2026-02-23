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


def get_local_branches() -> list[tuple[str, bool]]:
    """Return (branch_name, is_current) for all local branches."""
    result = subprocess.run(["git", "branch"], capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException("Not a git repository or git not available.")
    branches = []
    for line in result.stdout.splitlines():
        is_current = line.startswith("*")
        branch = line.lstrip("* ").strip()
        if branch:
            branches.append((branch, is_current))
    return branches


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
