"""Opt-in GTK integration checks for the sidebar/runtime transition."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

if os.environ.get("LINUXTERM_GUI_TEST") != "1":
    raise unittest.SkipTest("set LINUXTERM_GUI_TEST=1 to run display-backed GTK integration tests")

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from linuxterm.app import MainWindow, LinuXtermApplication
from linuxterm.config import AppConfig
from linuxterm.sessions import Folder, Session, SessionStore


class SidebarIntegrationTests(unittest.TestCase):
    def test_saved_tree_and_connected_sftp_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.sqlite")
            folder = Folder("Integration")
            store.add_folder(folder)
            session = Session("Local SSH Probe", "ssh", "127.0.0.1", 22, "nobody", None, folder.id)
            store.add_session(session)
            application = LinuXtermApplication(AppConfig(), store)
            window_holder = {}
            result = {"mode": None, "error": None}

            def activate(app):
                window = MainWindow(app, AppConfig(), store)
                window_holder["window"] = window
                window.present()
                window.explorer.reload()
                self.assertEqual(window.sidebar.get_visible_child_name(), "saved")
                local_page = window.notebook.get_nth_page(0)
                local_header = window.notebook.get_tab_label(local_page)
                self.assertEqual(local_header.get_children()[0].get_children()[0].get_text(), "Local Shell")
                self.assertTrue(local_header.get_visible())
                self.assertTrue(local_header.get_children()[0].get_children()[0].get_visible())
                window._open_ssh_session(session)
                window.notebook.set_current_page(1)

            def check_mode():
                try:
                    window = window_holder.get("window")
                    if window is None:
                        return True
                    page = window.notebook.get_nth_page(window.notebook.get_current_page())
                    if getattr(page, "connected", False):
                        result["mode"] = window.sidebar.get_visible_child_name()
                        window.destroy()
                        application.quit()
                        return False
                    return True
                except Exception as error:  # pragma: no cover - diagnostic path
                    result["error"] = error
                    application.quit()
                    return False

            GLib.timeout_add(100, check_mode)
            application.connect("activate", activate)
            GLib.timeout_add(3000, application.quit)
            application.run([])
            self.assertIsNone(result["error"])
            self.assertEqual(result["mode"], "sftp")
            store.close()


if __name__ == "__main__":
    unittest.main()
