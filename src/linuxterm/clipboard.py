"""Explicit, injectable terminal clipboard policy."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ClipboardConfig


@dataclass(frozen=True)
class ClipboardAction:
    """Decision produced from a mouse event; UI code performs the action."""

    name: str
    handled: bool = True


class ClipboardController:
    def __init__(self, config: ClipboardConfig) -> None:
        self.config = config

    def selection_changed(self, has_selection: bool) -> ClipboardAction | None:
        if has_selection and self.config.auto_copy_selection:
            return ClipboardAction("copy")
        return None

    def button(self, button: int, shift: bool = False) -> ClipboardAction | None:
        if button == 3:
            if shift and self.config.shift_right_click_context_menu:
                return ClipboardAction("context-menu")
            if self.config.right_click_paste:
                return ClipboardAction("paste-clipboard")
            return ClipboardAction("consume")
        if button == 2 and self.config.middle_click_paste_primary:
            return ClipboardAction("paste-primary")
        return None

