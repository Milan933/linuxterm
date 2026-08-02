"""Human-readable application configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import tomllib


@dataclass
class TerminalConfig:
    font: str = "Monospace 11"
    foreground: str = "#eeeeec"
    background: str = "#1e1e1e"
    scrollback_lines: int = 10_000
    ansi_palette: list[str] = field(default_factory=lambda: [
        "#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
        "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec",
    ])


@dataclass
class ClipboardConfig:
    auto_copy_selection: bool = True
    right_click_paste: bool = True
    middle_click_paste_primary: bool = True
    shift_right_click_context_menu: bool = True


@dataclass
class AppConfig:
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            return cls()
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
        return cls(
            terminal=TerminalConfig(**raw.get("terminal", {})),
            clipboard=ClipboardConfig(**raw.get("clipboard", {})),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[terminal]\n"
            f'font = "{self.terminal.font}"\n'
            f'foreground = "{self.terminal.foreground}"\n'
            f'background = "{self.terminal.background}"\n'
            f"scrollback_lines = {self.terminal.scrollback_lines}\n\n"
            f"ansi_palette = {json.dumps(self.terminal.ansi_palette)}\n\n"
            "[clipboard]\n"
            f"auto_copy_selection = {str(self.clipboard.auto_copy_selection).lower()}\n"
            f"right_click_paste = {str(self.clipboard.right_click_paste).lower()}\n"
            f"middle_click_paste_primary = {str(self.clipboard.middle_click_paste_primary).lower()}\n"
            f"shift_right_click_context_menu = {str(self.clipboard.shift_right_click_context_menu).lower()}\n",
            encoding="utf-8",
        )
