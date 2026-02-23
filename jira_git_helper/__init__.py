from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("jira-git-helper")
except PackageNotFoundError:
    __version__ = "unknown"

# cli.main is imported lazily — the entry point in pyproject.toml
# points directly to jira_git_helper.cli:main

__all__ = ["__version__"]
