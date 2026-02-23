"""Formatter-related functions: binary detection, EOF fixing, and formatter execution."""

from __future__ import annotations

import fnmatch
import os
import subprocess

import click

from .config import get_formatters
from .git import get_file_statuses

FILE_STATUS_LABELS: dict[str, str] = {
    "M": "modified",
    "D": "deleted",
    "?": "untracked",
    "R": "renamed",
    "A": "added",
    "C": "copied",
}


def get_binary_paths(paths: list[str], git_root: str) -> set[str]:
    """Return the subset of paths that git identifies as binary (w/-text) via git ls-files --eol."""
    if not paths:
        return set()
    result = subprocess.run(
        ["git", "ls-files", "--eol", "--", *paths],
        capture_output=True, text=True, cwd=git_root,
    )
    binary: set[str] = set()
    for line in result.stdout.splitlines():
        # Format: "i/<eol>\tw/<eol>\tattr/<attrs>\t<path>"
        # Use split(None, 3) to handle varying whitespace between columns.
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        w_eol = parts[1]  # e.g. "w/lf", "w/-text", "w/crlf"
        path = parts[3]
        if w_eol == "w/-text":
            binary.add(path)
    return binary


def fix_eof(abs_path: str) -> tuple[bool, str]:
    """Ensure file ends with exactly one newline. Returns (ok, error_msg)."""
    try:
        with open(abs_path, "rb") as f:
            content = f.read()
        if not content:
            return True, ""
        fixed = content.rstrip(b"\r\n") + b"\n"
        if fixed != content:
            with open(abs_path, "wb") as f:
                f.write(fixed)
        return True, ""
    except OSError as e:
        return False, str(e)


def build_fmt_table() -> "tuple[str, object]":
    """Run all formatters and return (message | None, table | None).

    Returns ("clean", None) if working tree is clean.
    Otherwise returns (None, rich.table.Table) with all results.
    """
    from rich.table import Table
    from rich.text import Text

    user_formatters = get_formatters()

    staged, modified, deleted, untracked = get_file_statuses()
    seen: set[str] = set()
    all_paths: list[str] = []
    for _, path in staged + modified + untracked:
        if path not in seen:
            seen.add(path)
            all_paths.append(path)

    if not all_paths:
        return "clean", None

    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    binary_paths = get_binary_paths(all_paths, git_root)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("", width=1, no_wrap=True)
    table.add_column("File", style="dim")
    table.add_column("Formatter", style="cyan", no_wrap=True)
    table.add_column("Exit", no_wrap=True)
    table.add_column("Note", style="red")

    for path in sorted(all_paths):
        abs_path = os.path.join(git_root, path)
        basename = os.path.basename(path)

        if path in binary_paths:
            table.add_row(
                Text("—", style="dim"),
                Text(path, style="dim"),
                Text("—", style="dim"),
                Text("—", style="dim"),
                Text("skipped (binary)", style="dim"),
            )
            continue

        # Built-in eof formatter — runs on every text file
        ok, err = fix_eof(abs_path)
        if ok:
            table.add_row(Text("✓", style="bold green"), path, "eof", Text("0", style="green"), "")
        else:
            table.add_row(Text("✗", style="bold red"), path, "eof", Text("1", style="red"), err)

        # User-configured formatters
        for fmt in user_formatters:
            if fnmatch.fnmatch(basename, fmt["glob"]):
                cmd = fmt["cmd"].replace("{}", abs_path)
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    table.add_row(
                        Text("✓", style="bold green"),
                        path,
                        fmt["name"],
                        Text("0", style="green"),
                        "",
                    )
                else:
                    error_msg = (result.stderr or result.stdout or "").strip()
                    table.add_row(
                        Text("✗", style="bold red"),
                        path,
                        fmt["name"],
                        Text(str(result.returncode), style="red"),
                        error_msg,
                    )

    return None, table


def run_formatters() -> None:
    from .tui.modals import FmtModal

    msg, table = build_fmt_table()
    if msg == "clean":
        click.echo("Nothing to format — working tree clean.")
        return
    from rich.console import Console
    Console().print(table)
