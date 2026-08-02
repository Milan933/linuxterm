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
        self._query = ""

        toolbar = Gtk.Box(spacing=4)
        new_folder = Gtk.Button(label="New Folder")
        new_session = Gtk.Button(label="New SSH Session")
        new_folder.connect("clicked", self._new_folder)
        new_session.connect("clicked", self._new_session)
        toolbar.pack_start(new_folder, True, True, 0)
        toolbar.pack_start(new_session, True, True, 0)
        self.pack_start(toolbar, False, False, 4)
        self.search = Gtk.SearchEntry(placeholder_text="Search sessions and folders")
        self.search.connect("search-changed", self._search_changed)
        self.pack_start(self.search, False, False, 4)

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

    def _selected_resource(self) -> tuple[str, str, str] | None:
        _model, iterator = self.tree.get_selection().get_selected()
        if iterator is None:
            return None
        return tuple(self.model[iterator])

    def _search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text().strip().casefold()
        self.reload()

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
        def branch_matches(resource_id: str, resource_type: str, name: str) -> bool:
            if not self._query or self._query in name.casefold():
                return True
            return resource_type == "folder" and any(
                branch_matches(child_id, child_type, child_name)
                for child_id, child_type, child_name in self.store.children(resource_id)
            )

        def add_children(parent_iter, parent_id):
            for resource_id, resource_type, name in self.store.children(parent_id):
                if branch_matches(resource_id, resource_type, name):
                    iterator = self.model.append(parent_iter, (resource_id, resource_type, name))
                    if resource_type == "folder":
                        add_children(iterator, resource_id)
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
        if event.button == 3:
            hit = self.tree.get_path_at_pos(int(event.x), int(event.y))
            selected = None
            if hit:
                path = hit[0]
                self.tree.get_selection().select_path(path)
                iterator = self.model.get_iter(path)
                selected = tuple(self.model[iterator])
            menu = Gtk.Menu()
            self._append_menu_item(menu, "New Folder", self._new_folder)
            self._append_menu_item(menu, "New SSH Session", self._new_session)
            if selected:
                resource_id, resource_type, _name = selected
                menu.append(Gtk.SeparatorMenuItem())
                if resource_type == "ssh_session":
                    self._append_menu_item(menu, "Connect", lambda _item: self.on_open_session(self.store.get_session(resource_id)))
                self._append_menu_item(menu, "Rename", lambda _item: self._rename_resource(resource_id))
                self._append_menu_item(menu, "Duplicate", lambda _item: self._duplicate_resource(resource_id))
                if resource_type == "folder":
                    self._append_menu_item(menu, "Expand All", lambda _item: self._set_all_expanded(resource_id, True))
                    self._append_menu_item(menu, "Collapse All", lambda _item: self._set_all_expanded(resource_id, False))
                self._append_menu_item(menu, "Delete", lambda _item: self._delete_resource(resource_id, resource_type))
            menu.show_all(); menu.popup_at_pointer(event); return True
        return False

    @staticmethod
    def _append_menu_item(menu: Gtk.Menu, label: str, callback) -> None:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", callback)
        menu.append(item)

    def _text_dialog(self, title: str, initial: str) -> str | None:
        dialog = Gtk.Dialog(title=title, transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=initial)
        dialog.get_content_area().pack_start(entry, False, False, 8)
        dialog.show_all()
        result = entry.get_text().strip() if dialog.run() == Gtk.ResponseType.OK else None
        dialog.destroy()
        return result

    def _rename_resource(self, resource_id: str) -> None:
        try:
            initial = self.store.get_resource_name(resource_id)
        except KeyError:
            return
        name = self._text_dialog("Rename resource", initial)
        if name:
            try: self.store.rename_resource(resource_id, name); self.reload()
            except (ValueError, KeyError) as error: self._error(str(error))

    def _delete_resource(self, resource_id: str, resource_type: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text="Delete this resource?")
        dialog.format_secondary_text("A non-empty folder and all of its contents will be deleted." if resource_type == "folder" else "The saved session will be removed, but its credential remains.")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Delete", Gtk.ResponseType.OK)
        response = dialog.run(); dialog.destroy()
        if response == Gtk.ResponseType.OK:
            try: self.store.delete_resource(resource_id, recursive=True); self._selected = None; self.reload()
            except (ValueError, KeyError) as error: self._error(str(error))

    def _duplicate_resource(self, resource_id: str) -> None:
        try: self.store.duplicate_resource(resource_id); self.reload()
        except (ValueError, KeyError, sqlite3.IntegrityError) as error: self._error(str(error))

    def _set_all_expanded(self, folder_id: str, expanded: bool) -> None:
        pending = [folder_id]
        while pending:
            current = pending.pop()
            if expanded: self._expanded.add(current)
            else: self._expanded.discard(current)
            pending.extend(item[0] for item in self.store.children(current) if item[1] == "folder")
        self._save_state(); self.reload()

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
