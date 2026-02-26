"""Reusable modal screens: TextInputModal, ConfirmModal, FmtModal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, Static

from ..formatters import build_fmt_table
from .theme import FOOTER_CSS


class TextInputModal(ModalScreen):
    """Generic single-line text prompt. Dismisses with the entered string, or None on cancel."""

    CSS = FOOTER_CSS + """
    TextInputModal { align: center middle; background: #0a0e0a 80%; }
    #tip-dialog {
        width: 80%;
        height: auto;
        padding: 1 2;
        border: thick #00ff41;
        background: #0d1a0d;
    }
    #tip-title { text-style: bold; padding-bottom: 1; color: #00ff41; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="tip-dialog"):
            yield Label(self._title, id="tip-title")
            yield Input(value=self._initial, placeholder=self._placeholder)
            yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if val:
            self.dismiss(val)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Yes/No confirmation dialog. Dismisses with True (yes) or False (no)."""

    CSS = FOOTER_CSS + """
    ConfirmModal { align: center middle; background: #0a0e0a 80%; }
    #confirm-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick #ffb300;
        background: #0d1a0d;
    }
    #confirm-message { padding-bottom: 1; color: #b8d4b8; }
    #confirm-hint { color: #ffb300; }
    """

    BINDINGS = [
        Binding("y", "confirm_yes", "Yes", show=True),
        Binding("n", "confirm_no", "No", show=True),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._message, id="confirm-message")
            yield Label("y — yes    n / Escape — no", id="confirm-hint")
            yield Footer()

    def on_key(self, event) -> None:
        if event.key in ("y", "enter"):
            self.dismiss(True)
            event.prevent_default()
        elif event.key in ("n", "escape"):
            self.dismiss(False)
            event.prevent_default()

    def action_confirm_yes(self) -> None:
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        self.dismiss(False)


class FmtModal(ModalScreen):
    CSS = """
    FmtModal { background: #0a0e0a 85%; }
    #fmt-outer {
        width: 90%;
        height: 80%;
        border: thick #00ff41;
        background: #0d1a0d;
        padding: 1 2;
    }
    #fmt-title  { text-style: bold; padding-bottom: 1; color: #00ff41; }
    #fmt-scroll { height: 1fr; }
    #fmt-hint   { color: #4d8a4d; padding-top: 1; }
    """

    BINDINGS = [
        Binding("enter", "close", show=False, priority=True),
        Binding("escape", "close", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="fmt-outer"):
            yield Label("Format results", id="fmt-title")
            with ScrollableContainer(id="fmt-scroll"):
                yield Static("Running formatters…", id="fmt-content")
            yield Label("Press any key to close", id="fmt-hint")

    def on_mount(self) -> None:
        self.run_worker(self._run, thread=True)

    def _run(self) -> None:
        msg, table = build_fmt_table()
        if msg == "clean":
            content = "Nothing to format — working tree clean."
        else:
            content = table
        self.app.call_from_thread(self._update, content)

    def _update(self, content) -> None:
        self.query_one("#fmt-content", Static).update(content)

    def action_close(self) -> None:
        self.dismiss()

    def on_key(self, event) -> None:
        self.dismiss()
        event.stop()
