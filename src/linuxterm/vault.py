"""RSA-backed local credential vault.

The vault encrypts each credential payload with AES-GCM. A random AES key is
wrapped with a per-user RSA-OAEP private key stored with restrictive
permissions; no vault master password is required or persisted.
"""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import sqlite3

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialKeyStore:
    """Creates and loads the user-local RSA key used by the credential vault."""

    def __init__(self, private_key_path: Path) -> None:
        self.private_key_path = private_key_path

    @property
    def exists(self) -> bool:
        return self.private_key_path.is_file()

    def create(self) -> None:
        if self.exists:
            return
        self.private_key_path.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        encoded = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        temporary = self.private_key_path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.chmod(0o600)
        temporary.replace(self.private_key_path)
        self.private_key_path.chmod(0o600)

    def load(self):
        if not self.exists:
            raise FileNotFoundError(f"credential encryption key is missing: {self.private_key_path}")
        return serialization.load_pem_private_key(self.private_key_path.read_bytes(), password=None)


class CredentialVault:
    def __init__(self, path: Path, key_path: Path) -> None:
        self.path = path
        self.key_store = CredentialKeyStore(key_path)
        if not self.key_store.exists:
            raise FileNotFoundError(f"create the credential encryption key first: {key_path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self._move_legacy_vault_if_needed()
        self.path.chmod(0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS vault_meta (wrapped_key BLOB NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS credentials (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self._private_key = self.key_store.load()
        row = self.db.execute("SELECT wrapped_key FROM vault_meta LIMIT 1").fetchone()
        if row is None:
            aes_key = secrets.token_bytes(32)
            wrapped = self._private_key.public_key().encrypt(
                aes_key,
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
            )
            self.db.execute("INSERT INTO vault_meta VALUES (?)", (wrapped,))
            self.db.commit()
            self._key = aes_key
        else:
            self._key = self._private_key.decrypt(
                row[0],
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
            )

    def _move_legacy_vault_if_needed(self) -> None:
        """Keep pre-RSA vaults recoverable without mixing incompatible formats."""

        table = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vault_meta'").fetchone()
        if table is None:
            return
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(vault_meta)")}
        if "salt" not in columns or "wrapped_key" in columns:
            return
        self.db.close()
        legacy = self.path.with_name(f"{self.path.stem}.legacy{self.path.suffix}")
        counter = 1
        while legacy.exists():
            legacy = self.path.with_name(f"{self.path.stem}.legacy-{counter}{self.path.suffix}")
            counter += 1
        self.path.replace(legacy)
        self.db = sqlite3.connect(self.path)

    def put(self, credential_id: str, values: dict[str, str]) -> None:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(values, separators=(",", ":")).encode()
        encrypted = nonce + AESGCM(self._key).encrypt(nonce, plaintext, credential_id.encode())
        self.db.execute("INSERT OR REPLACE INTO credentials VALUES (?, ?)", (credential_id, encrypted))
        self.db.commit()

    def get(self, credential_id: str) -> dict[str, str]:
        row = self.db.execute("SELECT payload FROM credentials WHERE id = ?", (credential_id,)).fetchone()
        if row is None:
            raise KeyError(credential_id)
        payload = row[0]
        plaintext = AESGCM(self._key).decrypt(payload[:12], payload[12:], credential_id.encode())
        return json.loads(plaintext)

    def close(self) -> None:
        self._key = b"\x00" * len(self._key)
        self.db.close()
