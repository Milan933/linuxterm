import tempfile
import unittest
from pathlib import Path
import sqlite3

from linuxterm.clipboard import ClipboardController
from linuxterm.config import AppConfig
from linuxterm.credentials import CredentialManager
from linuxterm.sftp import SftpBrowser
from linuxterm.sessions import ExplorerState, Folder, Session, SessionStore, new_id
from linuxterm.terminal import TerminalView
from linuxterm.vault import CredentialKeyStore, CredentialVault


class ClipboardPolicyTests(unittest.TestCase):
    def setUp(self):
        self.controller = ClipboardController(AppConfig().clipboard)

    def test_selection_copies(self):
        self.assertEqual(self.controller.selection_changed(True).name, "copy")

    def test_right_click_pastes_without_context_menu(self):
        self.assertEqual(self.controller.button(3).name, "paste-clipboard")

    def test_shift_right_click_is_the_only_context_menu_path(self):
        self.assertEqual(self.controller.button(3, shift=True).name, "context-menu")

    def test_terminal_advertises_shared_color_capabilities(self):
        environment = TerminalView.build_environment()
        self.assertEqual(environment["TERM"], "xterm-256color")
        self.assertEqual(environment["COLORTERM"], "truecolor")


class SftpTests(unittest.TestCase):
    def test_sftp_command_uses_session_target_without_disabling_host_checks(self):
        session = Session("server", "ssh", "server.example", 2222, "admin")
        self.assertEqual(SftpBrowser.build_command(session), ["sftp", "-q", "-P", "2222", "admin@server.example"])

    def test_sftp_listing_parser_ignores_empty_and_dot_entries(self):
        listing = "total 8\ndrwxr-xr-x 2 admin admin 4096 Jan 1 00:00 folder\n-rw-r--r-- 1 admin admin 12 Jan 1 00:00 file.txt\n"
        self.assertEqual(SftpBrowser.parse_listing(listing), [("folder", "directory"), ("file.txt", "file")])

    def test_sftp_listing_uses_entry_names_for_pathful_listing(self):
        listing = "-rw-r--r-- 1 admin admin 12 Jan 1 00:00 /home/admin/file.txt\n"
        self.assertEqual(SftpBrowser.parse_listing(listing), [("file.txt", "file")])

    def test_sftp_entry_path_is_relative_to_home(self):
        browser = type("BrowserStub", (), {})()
        browser.path = "."
        self.assertEqual(SftpBrowser._remote_entry_path(browser, "file.txt"), "file.txt")
        browser.path = "projects"
        self.assertEqual(SftpBrowser._remote_entry_path(browser, "file.txt"), "projects/file.txt")


class PersistenceTests(unittest.TestCase):
    def test_hierarchical_sessions_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.sqlite")
            folder = Folder("Infrastructure")
            nested = Folder("Production", parent_id=folder.id)
            store.add_folder(folder)
            store.add_folder(nested)
            store.add_session(Session("prod-shell", folder_id=nested.id))
            store.close()
            reopened = SessionStore(Path(directory) / "sessions.sqlite")
            self.assertEqual([f.name for f in reopened.list_folders()], ["Infrastructure", "Production"])
            self.assertEqual(reopened.list_sessions()[0].folder_id, nested.id)
            reopened.close()

    def test_config_is_human_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            config = AppConfig()
            config.save(path)
            self.assertIn("[clipboard]", path.read_text())
            self.assertEqual(AppConfig.load(path).terminal.font, config.terminal.font)
            self.assertEqual(AppConfig.load(path).terminal.ansi_palette, config.terminal.ansi_palette)

    def test_vault_does_not_store_plaintext_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vault.sqlite"
            key_path = Path(directory) / "credential-vault-private.pem"
            key_store = CredentialKeyStore(key_path)
            key_store.create()
            vault = CredentialVault(path, key_path)
            vault.put("ssh-prod", {"password": "super-secret"})
            vault.close()
            self.assertNotIn(b"super-secret", path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            reopened = CredentialVault(path, key_path)
            self.assertEqual(reopened.get("ssh-prod")["password"], "super-secret")
            reopened.close()

    def test_legacy_master_password_vault_is_preserved_for_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vault.sqlite"
            legacy = sqlite3.connect(path)
            legacy.execute("CREATE TABLE vault_meta (salt BLOB NOT NULL)")
            legacy.execute("INSERT INTO vault_meta VALUES (?)", (b"legacy-salt",))
            legacy.commit(); legacy.close()
            key_path = Path(directory) / "credential-vault-private.pem"
            CredentialKeyStore(key_path).create()
            vault = CredentialVault(path, key_path)
            vault.close()
            self.assertTrue(Path(directory, "vault.legacy.sqlite").exists())

    def test_credential_manager_stores_only_an_id_in_the_session_model(self):
        with tempfile.TemporaryDirectory() as directory:
            vault_path = Path(directory) / "vault.sqlite"
            key_path = Path(directory) / "credential-vault-private.pem"
            manager = CredentialManager(vault_path, key_path)
            manager.key_store.create()
            credential_id = manager.create_ssh_password("Production", "admin", "hidden-password")
            store = SessionStore(Path(directory) / "sessions.sqlite")
            session = Session("Production", "ssh", "server.example", 22, "admin", credential_id=credential_id)
            store.add_session(session)
            self.assertEqual(store.get_session(session.id).credential_id, credential_id)
            self.assertNotIn(b"hidden-password", vault_path.read_bytes())
            self.assertNotIn(b"hidden-password", Path(directory, "sessions.sqlite").read_bytes())
            self.assertEqual(manager.get_credential(credential_id)["password"], "hidden-password")
            store.close()

    def test_canonical_resource_model_persists_order_and_explorer_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite"
            store = SessionStore(path)
            root = Folder("Infrastructure")
            nested = Folder("Production", parent_id=root.id)
            store.add_folder(root)
            store.add_folder(nested)
            session = Session("Database", "ssh", "db.example", 22, "operator", None, nested.id)
            store.add_session(session)
            store.save_explorer_state(ExplorerState(selected_resource_id=session.id, expanded_folder_ids=(root.id, nested.id)))
            store.close()

            reopened = SessionStore(path)
            self.assertEqual(reopened.get_resource_type(session.id), "ssh_session")
            self.assertEqual(reopened.get_session(session.id).folder_id, nested.id)
            state = reopened.load_explorer_state()
            self.assertEqual(state.selected_resource_id, session.id)
            self.assertEqual(state.expanded_folder_ids, (root.id, nested.id))
            self.assertEqual([child[2] for child in reopened.children(nested.id)], ["Database"])
            reopened.close()

    def test_folder_move_rejects_cycles_and_preserves_session_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.sqlite")
            first = Folder("First"); second = Folder("Second", parent_id=first.id)
            store.add_folder(first); store.add_folder(second)
            session = Session("Host", "ssh", "host.example", folder_id=second.id)
            store.add_session(session)
            with self.assertRaises(ValueError):
                store.move_resource(first.id, second.id)
            store.move_resource(session.id, first.id)
            self.assertIsNone(store.get_session(session.id).credential_id)
            self.assertEqual(store.get_session(session.id).folder_id, first.id)
            store.close()

    def test_resource_crud_and_duplicate_preserve_credential_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.sqlite")
            folder = Folder("Production")
            store.add_folder(folder)
            credential_id = new_id()
            session = Session("Database", "ssh", "db.example", 22, "operator", credential_id, folder.id)
            store.add_session(session)
            store.rename_resource(session.id, "Primary database")
            self.assertEqual(store.get_resource_name(session.id), "Primary database")
            duplicate_id = store.duplicate_resource(session.id)
            duplicate = store.get_session(duplicate_id)
            self.assertEqual(duplicate.credential_id, credential_id)
            self.assertEqual(duplicate.folder_id, folder.id)
            store.delete_resource(folder.id, recursive=True)
            with self.assertRaises(KeyError):
                store.get_session(session.id)
            with self.assertRaises(KeyError):
                store.get_session(duplicate_id)
            store.close()


if __name__ == "__main__":
    unittest.main()
