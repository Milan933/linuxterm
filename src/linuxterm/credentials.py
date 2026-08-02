"""Credential Manager boundary for encrypted SSH password storage."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .vault import CredentialKeyStore, CredentialVault


class CredentialManager:
    """Owns vault access; callers receive IDs, never plaintext persistence."""

    def __init__(self, vault_path: Path, key_path: Path) -> None:
        self.vault_path = vault_path
        self.key_store = CredentialKeyStore(key_path)

    def create_ssh_password(self, name: str, username: str | None, password: str) -> str:
        if not password:
            raise ValueError("password must not be empty")
        credential_id = str(uuid4())
        vault = CredentialVault(self.vault_path, self.key_store.private_key_path)
        try:
            vault.put(credential_id, {"name": name, "type": "ssh_password", "username": username or "", "password": password})
        finally:
            vault.close()
        return credential_id

    def get_credential(self, credential_id: str) -> dict[str, str]:
        vault = CredentialVault(self.vault_path, self.key_store.private_key_path)
        try:
            return vault.get(credential_id)
        finally:
            vault.close()
