"""GTK application bootstrap for the first runnable milestone."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from .config import AppConfig
from .credentials import CredentialManager
from .explorer import SessionExplorer
from .sessions import RuntimeSession, Session, SessionStore, new_id
from .sftp import SftpBrowser
from .terminal import TerminalView


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, config: AppConfig, store: SessionStore, credentials: CredentialManager | None = None) -> None:
        super().__init__(application=application, title="LinuXterm", default_width=1100, default_height=700)
        self.config = config
        self.store = store
        self.credentials = credentials
        self._closing = False
        self.runtime_sessions: dict[str, RuntimeSession] = {}
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_tab_pos(Gtk.PositionType.TOP)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        self._build_menu(root)
        content = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.explorer = SessionExplorer(store, self._open_ssh_session, credentials)
        self.sftp = SftpBrowser(store)
        self.sidebar = Gtk.Stack()
        self.sidebar.set_size_request(310, -1)
        self.sidebar.add_titled(self.explorer, "saved", "Saved Sessions")
        self.sidebar.add_titled(self.sftp, "sftp", "SFTP")
        content.pack1(self.sidebar, False, False)
        content.pack2(self.notebook, True, False)
        root.pack_start(content, True, True, 0)
        self.notebook.connect("switch-page", lambda *_args: self._update_sidebar())
        self.connect("key-press-event", self._key_press)
        self.connect("destroy", lambda _window: setattr(self, "_closing", True))
        self._new_local_tab()
        self.show_all()

    def _build_menu(self, root: Gtk.Box) -> None:
        bar = Gtk.MenuBar()
        session_menu = Gtk.Menu()
        session = Gtk.MenuItem(label="Session")
        session.set_submenu(session_menu)
        new_local = Gtk.MenuItem(label="New Local Shell")
        new_local.connect("activate", lambda _item: self._new_local_tab())
        save_local = Gtk.MenuItem(label="Save Current as Local Session")
        save_local.connect("activate", lambda _item: self._save_current("local"))
        save_ssh = Gtk.MenuItem(label="Save SSH Session…")
        save_ssh.connect("activate", lambda _item: self._save_current("ssh"))
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: self.get_application().quit())
        for item in (new_local, save_local, save_ssh, quit_item):
            session_menu.append(item)
        bar.append(session)
        root.pack_start(bar, False, False, 0)

    def _new_local_tab(self) -> None:
        view = TerminalView(self.config, kind="local", on_status=self._terminal_status)
        view.runtime_id = new_id()
        view.saved_session_id = None
        self._append_tab(view, "Local Shell")

    def _append_tab(self, view: TerminalView, title: str) -> None:
        header = Gtk.EventBox()
        header.set_size_request(300, 34)
        row = Gtk.Box(spacing=4)
        label = Gtk.Label(label=title)
        label.set_width_chars(26)
        label.set_xalign(0.0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_tooltip_text(title)
        header.set_tooltip_text(title)
        close = Gtk.Button.new_from_icon_name("window-close", Gtk.IconSize.MENU)
        close.set_size_request(28, 28)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_focus_on_click(False)
        close.connect("clicked", lambda _button: self._close_tab(view))
        header.add(row); row.pack_start(label, True, True, 0); row.pack_start(close, False, False, 0)
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        header.connect("button-press-event", lambda _widget, event: self._tab_header_button(view, event))
        self.notebook.append_page(view, header)
        self.notebook.set_tab_reorderable(view, True)
        self.notebook.set_current_page(-1)
        view.tab_header = header
        view.tab_title = label
        header.show_all()
        view.show_all()

    def _tab_header_button(self, view: TerminalView, event: Gdk.EventButton) -> bool:
        if event.button == 2:
            self._close_tab(view)
            return True
        return False

    def _key_press(self, _window, event: Gdk.EventKey) -> bool:
        if event.keyval in (Gdk.KEY_w, Gdk.KEY_W) and event.state & Gdk.ModifierType.CONTROL_MASK:
            page = self.notebook.get_nth_page(self.notebook.get_current_page())
            if page is not None: self._close_tab(page)
            return True
        return False

    def _open_ssh_session(self, session: Session) -> None:
        if not session.hostname:
            self._show_error("The SSH session has no hostname.")
            return
        target = f"{session.username}@{session.hostname}" if session.username else session.hostname
        command = ["ssh", "-p", str(session.port or 22), "-o", "SetEnv=COLORTERM=truecolor", target]
        if session.startup_command:
            command.extend([session.startup_command])
        view = TerminalView(self.config, command=command, cwd=session.working_directory, kind="ssh", on_status=self._terminal_status)
        view.runtime_id = new_id()
        view.saved_session_id = session.id
        view.saved_session = session
        self.runtime_sessions[view.runtime_id] = RuntimeSession(view.runtime_id, session.id, "ssh", view.runtime_id, "starting", session.hostname, session.username)
        self._append_tab(view, session.name)

    def _terminal_status(self, view: TerminalView, status: str, error: str | None) -> None:
        runtime_id = getattr(view, "runtime_id", None)
        if runtime_id and runtime_id in self.runtime_sessions:
            old = self.runtime_sessions[runtime_id]
            self.runtime_sessions[runtime_id] = RuntimeSession(old.runtime_id, old.saved_session_id, old.session_type, old.terminal_tab_id, status, old.remote_hostname, old.remote_username)
        if status == "exited" and error == 0:
            self._close_tab(view)
            return
        if status == "exited" and getattr(view, "tab_title", None) is not None:
            view.tab_title.set_text(f"{view.tab_title.get_text()} (exited)")
        self._update_sidebar()

    def _close_tab(self, view: TerminalView) -> None:
        page_number = self.notebook.page_num(view)
        if page_number < 0:
            return
        view.close_process()
        self.notebook.remove_page(page_number)
        self.runtime_sessions.pop(getattr(view, "runtime_id", ""), None)
        self._update_sidebar()

    def _update_sidebar(self) -> None:
        if self._closing:
            return
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if page is not None and getattr(page, "kind", "local") == "ssh" and getattr(page, "connected", False):
            self.sidebar.set_visible_child_name("sftp")
            self.sftp.set_session(page.saved_session, page.terminal)
        else:
            self.sidebar.set_visible_child_name("saved")

    def _show_error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=message)
        dialog.run(); dialog.destroy()

    def _save_current(self, kind: str) -> None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if page is None:
            return
        name = "Local Shell" if kind == "local" else "SSH Session"
        self.store.add_session(Session(name=name, kind=kind, shell_command="ssh" if kind == "ssh" else None))


class LinuXtermApplication(Gtk.Application):
    def __init__(self, config: AppConfig, store: SessionStore, credentials: CredentialManager | None = None) -> None:
        super().__init__(application_id="org.linuxterm.LinuXterm")
        self.config = config
        self.store = store
        self.credentials = credentials

    def do_activate(self) -> None:
        if self.credentials is not None and not self.credentials.key_store.exists:
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="Create a credential encryption key?",
                secondary_text="LinuXterm stores saved SSH passwords in an encrypted vault protected by a user-local RSA key.",
            )
            dialog.add_button("Not now", Gtk.ResponseType.CANCEL)
            dialog.add_button("Create key", Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.OK:
                try:
                    self.credentials.key_store.create()
                except OSError as error:
                    error_dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=f"Could not create encryption key: {error}")
                    error_dialog.run(); error_dialog.destroy()
        window = MainWindow(self, self.config, self.store, self.credentials)
        window.present()


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "linuxterm"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "linuxterm"


def fallback_dir() -> Path:
    return Path(os.environ.get("TMPDIR", "/tmp")) / "linuxterm"


def main() -> int:
    directory = data_dir()
    config_path = config_dir() / "config.toml"
    try:
        config = AppConfig.load(config_path)
        config.save(config_path)
    except OSError as error:
        fallback = fallback_dir()
        print(f"warning: using fallback configuration directory {fallback}: {error}", file=sys.stderr)
        config_path = fallback / "config.toml"
        config = AppConfig.load(config_path)
        config.save(config_path)
    try:
        store = SessionStore(directory / "sessions.sqlite")
    except OSError as error:
        fallback = fallback_dir()
        print(f"warning: using fallback data directory {fallback}: {error}", file=sys.stderr)
        store = SessionStore(fallback / "sessions.sqlite")
    application = LinuXtermApplication(config, store, CredentialManager(directory / "vault.sqlite", directory / "credential-vault-private.pem"))
    try:
        return application.run(sys.argv)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
