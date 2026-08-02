# LinuXterm

The repository now contains the first runnable milestone of the LinuXterm clone.

## Run

This milestone targets the locally available GTK 3 + VTE 2.91 stack. On a Debian/Ubuntu system,
install the system packages `python3-gi`, `gir1.2-gtk-3.0`, and `gir1.2-vte-2.91`, then run:

```text
PYTHONPATH=src python3 -m linuxterm.app
```

It opens a local shell in a tab. Selecting text copies it, ordinary right-click pastes, and
`Shift + Right Click` opens the small optional context menu. Configuration is written as TOML
under `$XDG_CONFIG_HOME/linuxterm/config.toml` (or the platform data fallback), while
sessions are stored in SQLite.
Local and SSH tabs share the same ANSI 16-color palette and advertise `TERM=xterm-256color` and
`COLORTERM=truecolor` (SSH uses `SetEnv`), so colored Bash output, scripts, `nano`, and other
terminal applications are rendered consistently when the server permits the environment value.

For a connected SSH tab, the SFTP sidebar uses the system `sftp` client asynchronously to load the
remote directory listing. SSH host-key verification is left enabled; connection or authentication
errors are shown in the SFTP panel.
The browser starts in the authenticated user's remote home directory, lists only entry names, and
offers a `Follow terminal directory` checkbox that follows shell directory changes when the shell
reports its current directory (OSC 7).
Remote files and directories can be dragged from the SFTP list to a local file manager folder;
the application downloads them first and supplies the local file URI to the desktop drop target.

SSH passwords entered while creating a saved session are stored only in the separate encrypted
vault at `$XDG_DATA_HOME/linuxterm/vault.sqlite` (or the platform data fallback). The session
database stores only the generated credential ID. The vault is protected by the user-local RSA
key `$XDG_DATA_HOME/linuxterm/credential-vault-private.pem`; first launch offers key creation.
The offer is shown again on later launches until the key exists. An older master-password vault
is preserved as a legacy file instead of being overwritten.

The left sidebar shows the persisted saved-resource tree. Use **New Folder** and **New SSH
Session** to create resources; selecting a folder before creation places the new resource inside
it. Double-clicking an SSH session starts an OpenSSH terminal tab. Once that tab is running, the
sidebar switches to its runtime-bound SFTP browser and returns to the saved tree when another tab
is selected.

If the configured XDG directory is unavailable, startup falls back to
`$TMPDIR/linuxterm` and reports the fallback on stderr. This keeps the application runnable
in restricted environments; normal desktop installations use the standard XDG locations.

## Architecture decision

The specifications pair PySide6/Qt6 with `libvte`, which is a GTK terminal widget. This is not a
clean supported embedding boundary, and PySide6/VTE are not installed in the target environment.
The milestone therefore uses GTK 3 + PyGObject + VTE 2.91. VTE supplies PTY management and mature
terminal emulation; the application owns the input and clipboard policy. A later GTK4 migration can
be isolated to the UI adapter if VTE4 becomes the target platform baseline.

## Verification

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
