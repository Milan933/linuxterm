"""Left-side saved-resource explorer."""

from __future__ import annotations

import sqlite3

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from .credentials import CredentialManager
from .sessions import ExplorerState, Folder, Session, SessionStore


TARGET = Gtk.TargetEntry.new("application/x-linuxterm-resource", Gtk.TargetFlags.SAME_APP, 0)


class SessionExplorer(Gtk.Box):
    """Saved folder/session tree; all persistence is delegated to ``SessionStore``."""

    def __init__(self, store: SessionStore, on_open_session, credentials: CredentialManager | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.store = store
        self.on_open_session = on_open_session
        self.credentials = credentials
        self._expanded: set[str] = set(store.load_explorer_state().expanded_folder_ids)
        self._selected: str | None = store.load_explorer_state().selected_resource_id
        self._reloading = False

        toolbar = Gtk.Box(spacing=4)
        new_folder = Gtk.Button(label="New Folder")
        new_session = Gtk.Button(label="New SSH Session")
        new_folder.connect("clicked", self._new_folder)
        new_session.connect("clicked", self._new_session)
        toolbar.pack_start(new_folder, True, True, 0)
        toolbar.pack_start(new_session, True, True, 0)
        self.pack_start(toolbar, False, False, 4)

        self.model = Gtk.TreeStore(str, str, str)
        self.tree = Gtk.TreeView(model=self.model)
        self.tree.set_headers_visible(False)
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("Saved resources", renderer, text=2)
        self.tree.append_column(column)
        self.tree.connect("row-activated", self._row_activated)
        self.tree.connect("row-expanded", self._row_expanded)
        self.tree.connect("row-collapsed", self._row_collapsed)
        self.tree.get_selection().connect("changed", self._selection_changed)
        self.tree.connect("button-press-event", self._button_press)
        self.tree.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, [TARGET], Gdk.DragAction.MOVE)
        self.tree.enable_model_drag_dest([TARGET], Gdk.DragAction.MOVE)
        self.tree.connect("drag-data-get", self._drag_data_get)
        self.tree.connect("drag-data-received", self._drag_data_received)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.tree)
        self.pack_start(scroll, True, True, 0)
        self.show_all()
        self.reload()

    def _selected_folder(self) -> str | None:
        selection = self.tree.get_selection()
        _model, iterator = selection.get_selected()
        if iterator is None:
            return None
        resource_id, resource_type, _name = self.model[iterator]
        return resource_id if resource_type == "folder" else None

    def _new_folder(self, _button) -> None:
        parent = self._selected_folder()
        dialog = Gtk.Dialog(title="New Folder", transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Create", Gtk.ResponseType.OK)
        entry = Gtk.Entry(); entry.set_placeholder_text("Folder name")
        dialog.get_content_area().pack_start(entry, True, True, 8); dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try: self.store.add_folder(Folder(entry.get_text(), parent_id=parent)); self.reload()
            except (ValueError, sqlite3.IntegrityError) as error: self._error(str(error))
        dialog.destroy()

    def _new_session(self, _button) -> None:
        parent = self._selected_folder()
        dialog = Gtk.Dialog(title="New SSH Session", transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Create", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=10)
        fields = {}
        for row, (key, label, value) in enumerate((("name", "Name", "SSH Session"), ("host", "Host", ""), ("port", "Port", "22"), ("user", "Username", ""), ("credential", "Credential ID", ""), ("password", "Password (optional)", ""))):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            entry = Gtk.Entry(); entry.set_text(value)
            if key == "password": entry.set_visibility(False)
            fields[key] = entry; grid.attach(entry, 1, row, 1, 1)
        dialog.get_content_area().pack_start(grid, True, True, 0); dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                credential = fields["credential"].get_text().strip() or None
                password = fields["password"].get_text()
                if password:
                    if self.credentials is None: raise ValueError("credential manager is unavailable")
                    credential = self.credentials.create_ssh_password(fields["name"].get_text(), fields["user"].get_text().strip() or None, password)
                session = Session(fields["name"].get_text(), "ssh", fields["host"].get_text().strip() or None, int(fields["port"].get_text()), fields["user"].get_text().strip() or None, credential, parent)
                self.store.add_session(session); self.reload()
            except (ValueError, sqlite3.IntegrityError) as error: self._error(str(error))
        dialog.destroy()

    def _error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=message)
        dialog.run(); dialog.destroy()

    def reload(self) -> None:
        self._reloading = True
        self.model.clear()
        def add_children(parent_iter, parent_id):
            for resource_id, resource_type, name in self.store.children(parent_id):
                iterator = self.model.append(parent_iter, (resource_id, resource_type, name))
                if resource_type == "folder": add_children(iterator, resource_id)
        add_children(None, None)
        self._reloading = False
        self.show_all()
        self._restore_view_state()

    def _restore_view_state(self) -> None:
        def walk(iterator):
            while iterator:
                resource_id, resource_type, _name = self.model[iterator]
                path = self.model.get_path(iterator)
                if resource_type == "folder" and resource_id in self._expanded: self.tree.expand_row(path, False)
                if resource_id == self._selected:
                    self.tree.get_selection().select_path(path); self.tree.scroll_to_cell(path, None, False, 0, 0)
                if self.model.iter_has_child(iterator): walk(self.model.iter_children(iterator))
                iterator = self.model.iter_next(iterator)
        first = self.model.get_iter_first()
        if first: walk(first)

    def _row_activated(self, _tree, path, _column) -> None:
        iterator = self.model.get_iter(path)
        resource_id, resource_type, _name = self.model[iterator]
        if resource_type == "ssh_session": self.on_open_session(self.store.get_session(resource_id))

    def _row_expanded(self, _tree, iterator, _path) -> None:
        self._set_expanded(iterator, True)

    def _row_collapsed(self, _tree, iterator, _path) -> None:
        self._set_expanded(iterator, False)

    def _set_expanded(self, iterator, expanded: bool) -> None:
        resource_id, _type, _name = self.model[iterator]
        if expanded: self._expanded.add(resource_id)
        else: self._expanded.discard(resource_id)
        if not self._reloading: self._save_state()

    def _selection_changed(self, selection) -> None:
        _model, iterator = selection.get_selected()
        self._selected = self.model[iterator][0] if iterator else None
        self._save_state()

    def _save_state(self) -> None:
        self.store.save_explorer_state(ExplorerState(selected_resource_id=self._selected, expanded_folder_ids=tuple(sorted(self._expanded))))

    def _button_press(self, _tree, event) -> bool:
        if event.button == 3 and event.state & Gdk.ModifierType.SHIFT_MASK:
            menu = Gtk.Menu()
            folder = Gtk.MenuItem(label="New Folder"); session = Gtk.MenuItem(label="New SSH Session")
            folder.connect("activate", self._new_folder); session.connect("activate", self._new_session)
            menu.append(folder); menu.append(session); menu.show_all(); menu.popup_at_pointer(event); return True
        return False

    def _drag_data_get(self, _tree, _context, selection_data, _info, _time) -> None:
        selected = self.tree.get_selection(); _model, iterator = selected.get_selected()
        if iterator: selection_data.set_text(self.model[iterator][0], -1)

    def _drag_data_received(self, _tree, context, x, y, selection_data, _info, time) -> None:
        resource_id = selection_data.get_text()
        destination = None
        hit = self.tree.get_dest_row_at_pos(x, y)
        if hit:
            path, _position = hit; iterator = self.model.get_iter(path)
            target_id, target_type, _name = self.model[iterator]
            destination = target_id if target_type == "folder" else self._parent_id(path)
        try:
            self.store.move_resource(resource_id, destination); self.reload(); context.finish(True, False, time)
        except (ValueError, KeyError, sqlite3.IntegrityError) as error:
            context.finish(False, False, time); self._error(str(error))

    def _parent_id(self, path) -> str | None:
        parent = path[:-1]
        if not parent: return None
        iterator = self.model.get_iter(parent)
        return self.model[iterator][0]
