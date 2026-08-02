"""Canonical persistent resource, session, and explorer state model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import UUID, uuid4


def new_id() -> str:
    """Return a stable UUIDv4 identifier (the documented fallback)."""

    return str(uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass
class Folder:
    name: str
    parent_id: str | None = None
    id: str = field(default_factory=new_id)
    sort_order: int = 0


@dataclass
class Session:
    name: str
    kind: str = "local"
    hostname: str | None = None
    port: int | None = 22
    username: str | None = None
    credential_id: str | None = None
    folder_id: str | None = None
    working_directory: str | None = None
    startup_command: str | None = None
    shell_command: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    notes: str | None = None
    id: str = field(default_factory=new_id)
    sort_order: int = 0


@dataclass(frozen=True)
class ExplorerState:
    explorer_id: str = "sessions"
    selected_resource_id: str | None = None
    expanded_folder_ids: tuple[str, ...] = ()
    scroll_position: int = 0
    search_query: str | None = None
    sort_mode: str = "manual"


@dataclass(frozen=True)
class RuntimeSession:
    runtime_id: str
    saved_session_id: str | None
    session_type: str
    terminal_tab_id: str
    status: str = "created"
    remote_hostname: str | None = None
    remote_username: str | None = None


class SessionStore:
    """SQLite repository for the canonical resource hierarchy.

    The credential vault remains a separate security boundary. This repository stores only
    credential IDs and never accepts secret payloads.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)")
        old_sessions = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
        if old_sessions:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(sessions)")}
            if "resource_id" not in columns:
                self.db.execute("ALTER TABLE sessions RENAME TO sessions_legacy")
        old_folders = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='folders'").fetchone()
        if old_folders:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(folders)")}
            if "resource_id" not in columns:
                self.db.execute("ALTER TABLE folders RENAME TO folders_legacy")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, name TEXT NOT NULL,
                parent_folder_id TEXT REFERENCES resources(id) ON DELETE RESTRICT,
                sort_order INTEGER NOT NULL DEFAULT 1000, icon_name TEXT, custom_color TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0, is_hidden INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                CHECK (parent_folder_id IS NULL OR parent_folder_id <> id)
            );
            CREATE INDEX IF NOT EXISTS resources_parent_order ON resources(parent_folder_id, sort_order, name);
            CREATE TABLE IF NOT EXISTS folders (
                resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
                folder_kind TEXT NOT NULL DEFAULT 'normal', description TEXT,
                default_session_type TEXT, default_credential_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
                session_type TEXT NOT NULL, hostname TEXT, port INTEGER, username TEXT,
                credential_id TEXT, terminal_profile_id TEXT, working_directory TEXT,
                startup_command TEXT, shell_command TEXT, environment TEXT NOT NULL DEFAULT '{}',
                notes TEXT, connect_timeout_seconds INTEGER NOT NULL DEFAULT 30,
                keepalive_interval_seconds INTEGER, auto_reconnect INTEGER NOT NULL DEFAULT 0,
                close_behavior TEXT NOT NULL DEFAULT 'close', ssh_config_host TEXT,
                proxy_jump_session_ids TEXT NOT NULL DEFAULT '[]', host_key_policy TEXT NOT NULL DEFAULT 'strict',
                known_hosts_file TEXT, compression_enabled INTEGER NOT NULL DEFAULT 0,
                agent_forwarding_enabled INTEGER NOT NULL DEFAULT 0, x11_forwarding_enabled INTEGER NOT NULL DEFAULT 0,
                remote_working_directory TEXT, tunnel_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS explorer_state (
                explorer_id TEXT PRIMARY KEY, workspace_id TEXT, selected_resource_id TEXT,
                expanded_folder_ids TEXT NOT NULL DEFAULT '[]', scroll_position INTEGER NOT NULL DEFAULT 0,
                search_query TEXT, active_filter TEXT, sort_mode TEXT NOT NULL DEFAULT 'manual', updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sftp_browser_state (
                session_id TEXT PRIMARY KEY, current_remote_path TEXT NOT NULL DEFAULT '.',
                history TEXT NOT NULL DEFAULT '[]', show_hidden_files INTEGER NOT NULL DEFAULT 0,
                sort_column TEXT NOT NULL DEFAULT 'name', sort_direction TEXT NOT NULL DEFAULT 'ascending',
                view_mode TEXT NOT NULL DEFAULT 'list', updated_at TEXT NOT NULL
            );
            """
        )
        migration = self.db.execute("SELECT version FROM schema_migrations WHERE version = 1").fetchone()
        if migration is None:
            self._migrate_legacy_data()
            self.db.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?)", (1, "canonical-resource-model", utc_now(), "mvp-canonical-v1"))
        self.db.commit()

    def _migrate_legacy_data(self) -> None:
        legacy_folders = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='folders_legacy'").fetchone()
        if legacy_folders:
            legacy_rows = self.db.execute("SELECT id, name, parent_id FROM folders_legacy").fetchall()
            for row in legacy_rows:
                created = utc_now()
                self.db.execute("INSERT INTO resources VALUES (?, 'folder', ?, NULL, 1000, NULL, NULL, 0, 0, ?, ?, NULL, '{}')", (row[0], row[1], created, created))
                self.db.execute("INSERT INTO folders VALUES (?, 'normal', NULL, NULL, NULL, ?, ?)", (row[0], created, created))
            for row in legacy_rows:
                self.db.execute("UPDATE resources SET parent_folder_id = ? WHERE id = ?", (row[2], row[0]))
        legacy_sessions = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions_legacy'").fetchone()
        if legacy_sessions:
            for row in self.db.execute("SELECT id, name, kind, hostname, port, username, working_directory, shell_command, credential_id, folder_id FROM sessions_legacy").fetchall():
                created = utc_now()
                self.db.execute("INSERT INTO resources VALUES (?, ?, ?, ?, 1000, NULL, NULL, 0, 0, ?, ?, NULL, '{}')", (row[0], f"{'ssh' if row[2] == 'ssh' else 'local'}_session", row[1], row[9], created, created))
                self.db.execute("INSERT INTO sessions (resource_id, session_type, hostname, port, username, credential_id, working_directory, shell_command, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (row[0], row[2], row[3], row[4], row[5], row[8], row[6], row[7], created, created))

    @staticmethod
    def _validate_id(identifier: str) -> None:
        UUID(identifier)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name.strip():
            raise ValueError("resource name must not be empty")

    def _next_sort_order(self, parent_id: str | None) -> int:
        row = self.db.execute("SELECT COALESCE(MAX(sort_order), 0) FROM resources WHERE parent_folder_id IS ?", (parent_id,)).fetchone()
        return int(row[0]) + 1000

    def _assert_folder_target(self, folder_id: str | None) -> None:
        if folder_id is not None:
            self._validate_id(folder_id)
            row = self.db.execute("SELECT resource_type FROM resources WHERE id = ?", (folder_id,)).fetchone()
            if row is None or row[0] != "folder":
                raise ValueError("parent folder does not exist")

    def add_folder(self, folder: Folder) -> None:
        self._validate_id(folder.id); self._validate_name(folder.name); self._assert_folder_target(folder.parent_id)
        now = utc_now()
        with self.db:
            self.db.execute("INSERT INTO resources VALUES (?, 'folder', ?, ?, ?, NULL, NULL, 0, 0, ?, ?, NULL, '{}')", (folder.id, folder.name, folder.parent_id, folder.sort_order or self._next_sort_order(folder.parent_id), now, now))
            self.db.execute("INSERT INTO folders VALUES (?, 'normal', NULL, NULL, NULL, ?, ?)", (folder.id, now, now))

    def add_session(self, session: Session) -> None:
        self._validate_id(session.id); self._validate_name(session.name); self._assert_folder_target(session.folder_id)
        if session.kind not in {"local", "ssh"}:
            raise ValueError("unsupported session type")
        if session.credential_id is not None:
            self._validate_id(session.credential_id)
        now = utc_now()
        resource_type = f"{session.kind}_session"
        with self.db:
            self.db.execute("INSERT INTO resources VALUES (?, ?, ?, ?, ?, NULL, NULL, 0, 0, ?, ?, NULL, ?)", (session.id, resource_type, session.name, session.folder_id, session.sort_order or self._next_sort_order(session.folder_id), now, now, json.dumps({})))
            self.db.execute("INSERT INTO sessions (resource_id, session_type, hostname, port, username, credential_id, working_directory, startup_command, shell_command, environment, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (session.id, session.kind, session.hostname, session.port, session.username, session.credential_id, session.working_directory, session.startup_command, session.shell_command, json.dumps(session.environment), session.notes, now, now))

    def list_folders(self) -> list[Folder]:
        rows = self.db.execute("SELECT id, name, parent_folder_id, sort_order FROM resources WHERE resource_type = 'folder' ORDER BY parent_folder_id, sort_order, name").fetchall()
        return [Folder(row[1], row[2], row[0], row[3]) for row in rows]

    def list_sessions(self) -> list[Session]:
        rows = self.db.execute("SELECT r.id, r.name, s.session_type, s.hostname, s.port, s.username, s.credential_id, r.parent_folder_id, s.working_directory, s.startup_command, s.shell_command, s.environment, s.notes, r.sort_order FROM resources r JOIN sessions s ON s.resource_id = r.id ORDER BY r.parent_folder_id, r.sort_order, r.name").fetchall()
        return [Session(row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], json.loads(row[11] or "{}"), row[12], row[0], row[13]) for row in rows]

    def get_session(self, resource_id: str) -> Session:
        for session in self.list_sessions():
            if session.id == resource_id:
                return session
        raise KeyError(resource_id)

    def get_resource_type(self, resource_id: str) -> str:
        row = self.db.execute("SELECT resource_type FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if row is None:
            raise KeyError(resource_id)
        return str(row[0])

    def children(self, parent_id: str | None) -> list[tuple[str, str, str]]:
        rows = self.db.execute("SELECT id, resource_type, name FROM resources WHERE parent_folder_id IS ? ORDER BY sort_order, name", (parent_id,)).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def move_resource(self, resource_id: str, parent_id: str | None) -> None:
        self._validate_id(resource_id); self._assert_folder_target(parent_id)
        if resource_id == parent_id:
            raise ValueError("resource cannot be its own parent")
        if self.get_resource_type(resource_id) == "folder":
            descendants: set[str] = set()
            pending = [resource_id]
            while pending:
                current = pending.pop()
                children = [item[0] for item in self.children(current)]
                descendants.update(children); pending.extend(children)
            if parent_id in descendants:
                raise ValueError("folder cannot be moved into its descendant")
        with self.db:
            self.db.execute("UPDATE resources SET parent_folder_id = ?, sort_order = ?, updated_at = ? WHERE id = ?", (parent_id, self._next_sort_order(parent_id), utc_now(), resource_id))

    def save_explorer_state(self, state: ExplorerState) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO explorer_state (explorer_id, selected_resource_id, expanded_folder_ids, scroll_position, sort_mode, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(explorer_id) DO UPDATE SET selected_resource_id=excluded.selected_resource_id, expanded_folder_ids=excluded.expanded_folder_ids, scroll_position=excluded.scroll_position, sort_mode=excluded.sort_mode, updated_at=excluded.updated_at",
                (state.explorer_id, state.selected_resource_id, json.dumps(list(state.expanded_folder_ids)), state.scroll_position, state.sort_mode, utc_now()),
            )

    def load_explorer_state(self) -> ExplorerState:
        row = self.db.execute("SELECT explorer_id, selected_resource_id, expanded_folder_ids, scroll_position, search_query, sort_mode FROM explorer_state WHERE explorer_id = 'sessions'").fetchone()
        if row is None:
            return ExplorerState()
        valid = []
        for identifier in json.loads(row[2] or "[]"):
            try:
                if self.get_resource_type(identifier) == "folder":
                    valid.append(identifier)
            except (KeyError, ValueError):
                continue
        selected = row[1]
        if selected:
            try: self.get_resource_type(selected)
            except KeyError: selected = None
        return ExplorerState(row[0], selected, tuple(valid), row[3], row[4], row[5])

    def save_sftp_state(self, session_id: str, path: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO sftp_browser_state(session_id, current_remote_path, updated_at) VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET current_remote_path=excluded.current_remote_path, updated_at=excluded.updated_at", (session_id, path, utc_now()))

    def sftp_path(self, session_id: str) -> str:
        row = self.db.execute("SELECT current_remote_path FROM sftp_browser_state WHERE session_id = ?", (session_id,)).fetchone()
        return "." if row is None else str(row[0])

    def close(self) -> None:
        self.db.close()
