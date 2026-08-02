"""VTE terminal widget and the application-owned mouse/clipboard policy."""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte

from .config import AppConfig
from .clipboard import ClipboardController


class TerminalView(Gtk.Box):
    def __init__(self, config: AppConfig, command: list[str] | None = None, cwd: str | None = None, kind: str = "local", on_status=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.config = config
        self.kind = kind
        self.on_status = on_status
        self.connected = False
        self.clipboard = ClipboardController(config.clipboard)
        self.terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        self.terminal.set_scrollback_lines(config.terminal.scrollback_lines)
        self.terminal.set_font(Pango.FontDescription(config.terminal.font))
        foreground = Gdk.RGBA(); foreground.parse(config.terminal.foreground)
        background = Gdk.RGBA(); background.parse(config.terminal.background)
        palette = []
        for color in config.terminal.ansi_palette:
            rgba = Gdk.RGBA()
            if rgba.parse(color):
                palette.append(rgba)
        self.terminal.set_colors(foreground, background, palette or None)
        self.terminal.set_enable_bidi(True)
        self.terminal.connect("selection-changed", self._selection_changed)
        self.terminal.connect("button-press-event", self._button_press)
        self.terminal.connect("child-exited", self._child_exited)
        self.pack_start(self.terminal, True, True, 0)
        self.show_all()
        self._spawn(command or [os.environ.get("SHELL", "/bin/sh")], cwd or str(Path.home()))

    def _spawn(self, command: list[str], cwd: str) -> None:
        if self.on_status: self.on_status(self, "starting", None)
        env = [f"{key}={value}" for key, value in self.build_environment().items()]
        self.terminal.spawn_async(
            pty_flags=Vte.PtyFlags.DEFAULT,
            working_directory=cwd,
            argv=command,
            envv=env,
            spawn_flags=GLib.SpawnFlags.DEFAULT,
            child_setup=None,
            timeout=-1,
            cancellable=Gio.Cancellable(),
            callback=self._spawn_finished,
            user_data=None,
        )

    @staticmethod
    def build_environment() -> dict[str, str]:
        """Give local shells and SSH the same modern color capabilities."""

        environment = os.environ.copy()
        environment["TERM"] = "xterm-256color"
        environment["COLORTERM"] = "truecolor"
        return environment

    def _spawn_finished(self, _terminal: Vte.Terminal, _pid: int, _error: object, _user_data: object = None) -> None:
        """Required callback for PyGObject versions that reject a null callback."""
        if _error is None:
            self.connected = True
            if self.on_status: self.on_status(self, "connected", None)
        else:
            if self.on_status: self.on_status(self, "failed", str(_error))

    def _selection_changed(self, _terminal: Vte.Terminal) -> None:
        if self.clipboard.selection_changed(self.terminal.get_has_selection()):
            self.terminal.copy_clipboard()

    def _button_press(self, _terminal: Vte.Terminal, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            action = self.clipboard.button(3, bool(event.state & Gdk.ModifierType.SHIFT_MASK))
            if action and action.name == "context-menu":
                self._show_context_menu(event)
            elif action and action.name == "paste-clipboard":
                self.terminal.paste_clipboard()
            return True
        action = self.clipboard.button(event.button)
        if action and action.name == "paste-primary":
            self.terminal.paste_primary()
            return True
        return False

    def _show_context_menu(self, event: Gdk.EventButton) -> None:
        menu = Gtk.Menu()
        copy_item = Gtk.MenuItem(label="Copy")
        paste_item = Gtk.MenuItem(label="Paste")
        select_item = Gtk.MenuItem(label="Select All")
        copy_item.connect("activate", lambda _item: self.terminal.copy_clipboard())
        paste_item.connect("activate", lambda _item: self.terminal.paste_clipboard())
        select_item.connect("activate", lambda _item: self.terminal.select_all())
        for item in (copy_item, paste_item, select_item):
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _child_exited(self, _terminal: Vte.Terminal, _status: int) -> None:
        self.connected = False
        try:
            exit_code = os.waitstatus_to_exitcode(_status)
        except ValueError:
            exit_code = _status
        if self.on_status: self.on_status(self, "exited", exit_code)
        self.terminal.set_sensitive(False)

    def close_process(self) -> None:
        """Close the PTY; VTE owns the child process and releases it idempotently."""

        pty = self.terminal.get_pty()
        if pty is not None:
            pty.close()
        self.terminal.set_input_enabled(False)

    def get_current_directory_uri(self) -> str | None:
        """Return the shell-reported directory URI used by SFTP follow mode."""

        return self.terminal.get_current_directory_uri()
