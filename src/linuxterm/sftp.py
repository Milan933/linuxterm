"""Non-blocking OpenSSH SFTP browser bound to one runtime session."""

from __future__ import annotations

import os
import posixpath
import shlex
import subprocess
import threading
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from .sessions import Session, SessionStore


class SftpBrowser(Gtk.Box):
    def __init__(self, store: SessionStore) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.store = store
        self.session_id: str | None = None
        self.session: Session | None = None
        self.terminal = None
        self.path = "."
        self._request_number = 0
        self.title = Gtk.Label(xalign=0)
        self.status = Gtk.Label(xalign=0)
        self.pack_start(self.title, False, False, 8)
        self.pack_start(self.status, False, False, 4)
        self.follow_checkbox = Gtk.CheckButton(label="Follow terminal directory")
        self.follow_checkbox.connect("toggled", self._follow_toggled)
        self.pack_start(self.follow_checkbox, False, False, 4)
        controls = Gtk.Box(spacing=4)
        self.up_button = Gtk.Button(label="Up")
        self.refresh_button = Gtk.Button(label="Refresh")
        self.up_button.connect("clicked", self._go_up)
        self.refresh_button.connect("clicked", lambda _button: self._refresh())
        controls.pack_start(self.up_button, False, False, 0)
        controls.pack_start(self.refresh_button, False, False, 0)
        self.pack_start(controls, False, False, 0)
        self.model = Gtk.ListStore(str, str)
        self.tree = Gtk.TreeView(model=self.model)
        self.tree.append_column(Gtk.TreeViewColumn("Name", Gtk.CellRendererText(), text=0))
        self.tree.append_column(Gtk.TreeViewColumn("Type", Gtk.CellRendererText(), text=1))
        self.tree.connect("row-activated", self._activate)
        self.tree.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK,
            [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
            Gdk.DragAction.COPY,
        )
        self.tree.connect("drag-data-get", self._drag_data_get)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.tree)
        self.pack_start(scroll, True, True, 0)
        self.show_all()
        self._poll_source = GLib.timeout_add(500, self._poll_terminal_directory)

    @staticmethod
    def build_command(session: Session) -> list[str]:
        if not session.hostname:
            raise ValueError("SSH session has no hostname")
        target = f"{session.username}@{session.hostname}" if session.username else session.hostname
        return ["sftp", "-q", "-P", str(session.port or 22), target]

    @staticmethod
    def parse_listing(output: str) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line == "total" or line.startswith("total "):
                continue
            parts = line.split(None, 8)
            if len(parts) >= 9 and parts[0] and parts[0][0] in "-dl":
                mode, name = parts[0], parts[8]
                if " -> " in name:
                    name = name.split(" -> ", 1)[0]
                entries.append((posixpath.basename(name), "directory" if mode[0] == "d" else "file"))
            else:
                name = posixpath.basename(line)
                if name not in {".", ".."}:
                    entries.append((name, "remote entry"))
        return entries

    def set_session(self, session: Session, terminal=None) -> None:
        self.session = session
        self.terminal = terminal
        self.session_id = session.id
        self.path = self.store.sftp_path(session.id)
        self._update_title()
        self._refresh()

    def _display_path(self) -> str:
        return "~" if self.path in {"", "."} else self.path

    def _update_title(self) -> None:
        if self.session is None:
            self.title.set_text("SFTP")
        else:
            user_host = f"{self.session.username}@" if self.session.username else ""
            self.title.set_text(f"SFTP · {user_host}{self.session.hostname or ''} · {self._display_path()}")

    def _refresh(self) -> None:
        if self.session is None:
            return
        self._request_number += 1
        request = self._request_number
        session, path = self.session, self.path
        self.status.set_text("Loading remote directory…")
        self.refresh_button.set_sensitive(False)
        threading.Thread(target=self._load_directory, args=(request, session, path), daemon=True).start()

    def _load_directory(self, request: int, session: Session, path: str) -> None:
        try:
            completed = subprocess.run(
                self.build_command(session),
                input=f"ls -la {shlex.quote(path)}\nbye\n",
                text=True,
                capture_output=True,
                timeout=20,
                env=os.environ.copy(),
                check=False,
            )
            if completed.returncode != 0:
                result = (request, [], completed.stderr.strip() or "SFTP command failed")
            else:
                result = (request, self.parse_listing(completed.stdout), None)
        except (OSError, subprocess.SubprocessError) as error:
            result = (request, [], str(error))
        GLib.idle_add(self._apply_listing, *result)

    def _remote_entry_path(self, name: str) -> str:
        """Build an SFTP path while keeping the home directory relative."""

        return posixpath.join(self.path, name) if self.path not in {"", "."} else name

    def _download_for_drag(self, name: str, kind: str) -> Path | None:
        if self.session is None or name in {"", ".", ".."} or kind not in {"file", "directory", "remote entry"}:
            return None
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "linuxterm" / "sftp-drops"
        cache_root.mkdir(parents=True, exist_ok=True)
        drop_dir = Path(tempfile.mkdtemp(prefix="drop-", dir=cache_root))
        local_path = drop_dir / Path(name).name
        remote_path = self._remote_entry_path(name)
        recursive = " -r" if kind == "directory" else ""
        self.status.set_text(f"Preparing download: {name}")
        try:
            completed = subprocess.run(
                self.build_command(self.session),
                input=f"get{recursive} {shlex.quote(remote_path)} {shlex.quote(str(local_path))}\nbye\n",
                text=True,
                capture_output=True,
                timeout=120,
                env=os.environ.copy(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.status.set_text(f"Download error: {error}")
            return None
        if completed.returncode != 0 or not local_path.exists():
            message = completed.stderr.strip() or completed.stdout.strip() or "SFTP download failed"
            self.status.set_text(f"Download error: {message}")
            return None
        self.status.set_text(f"Ready to drop locally: {name}")
        return local_path

    def _drag_data_get(self, _tree, _context, selection_data, _info, _time) -> None:
        model, tree_iter = self.tree.get_selection().get_selected()
        if tree_iter is None:
            return
        name, kind = model[tree_iter]
        local_path = self._download_for_drag(name, kind)
        if local_path is not None:
            selection_data.set_uris([GLib.filename_to_uri(str(local_path), None)])

    def _apply_listing(self, request: int, entries: list[tuple[str, str]], error: str | None) -> bool:
        if request != self._request_number:
            return False
        self.model.clear()
        self.model.append(("..", "directory"))
        if error:
            self.status.set_text(f"SFTP error: {error}")
        else:
            for name, kind in entries:
                self.model.append((name, kind))
            self.status.set_text(f"{len(entries)} remote entries")
        self.refresh_button.set_sensitive(True)
        self._update_title()
        if self.session_id:
            self.store.save_sftp_state(self.session_id, self.path)
        return False

    def _follow_toggled(self, _checkbox) -> None:
        if self.follow_checkbox.get_active():
            self._poll_terminal_directory()

    def _poll_terminal_directory(self) -> bool:
        if not self.follow_checkbox.get_active() or self.terminal is None:
            return True
        uri = self.terminal.get_current_directory_uri()
        if uri:
            parsed = urlparse(uri)
            remote_path = unquote(parsed.path or "/")
            if remote_path and remote_path != self.path:
                self.path = remote_path
                self._update_title()
                self._refresh()
        return True

    def _go_up(self, _button) -> None:
        if self.path not in {"", ".", "/"}:
            parent = posixpath.dirname(self.path.rstrip("/"))
            self.path = parent or ("/" if self.path.startswith("/") else ".")
            self._update_title()
            self._refresh()

    def _activate(self, _tree, path, _column) -> None:
        name, kind = self.model[path]
        if name == "..":
            self._go_up(None)
        elif kind == "directory":
            self.path = posixpath.join(self.path, name) if self.path not in {"", "."} else name
            self._update_title()
            self._refresh()
