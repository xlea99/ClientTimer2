"""Drag-and-drop reordering controller for Client Timer rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from ct.ui.app import MainWindow


class DragController:
    """Manages all drag-reorder state and logic.

    Holds a reference to the host MainWindow for access to rows, widgets,
    collapsed groups, and rebuild methods.
    """

    def __init__(self, host: MainWindow):
        self.host = host
        self.dragging_rid = None
        self.last_row = -1
        self.group_rids = None       # set of child rowids when dragging collapsed group
        self.hidden_rids = None      # snapshot of hidden rids during separator drag
        self.visible_rids = None     # snapshot of visible rids at drag start

    @property
    def active(self):
        return self.dragging_rid is not None

    def start(self, rowid):
        """Begin drag-reordering a row."""
        h = self.host
        self.dragging_rid = rowid

        row = next(r for r in h.rows if r["rowid"] == rowid)
        if row["type"] == "separator" and rowid in h._collapsed_groups:
            children = h._group_children(rowid)
            self.group_rids = set(children)
        else:
            self.group_rids = None

        if row["type"] == "separator":
            self.hidden_rids = self._hidden_rids_snapshot()
        else:
            self.hidden_rids = None

        self.visible_rids = set(h._visible_rowids)

        h.setFixedSize(h.size())
        QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        QApplication.instance().installEventFilter(h)
        h._rebuild_rows()
        self.last_row = h._visible_rowids.index(rowid)

    def end(self):
        """Finish drag-reordering and persist the new order."""
        h = self.host
        drag_rid = self.dragging_rid
        was_group_drag = self.group_rids is not None
        visible_snapshot = self.visible_rids

        self.dragging_rid = None
        self.last_row = -1
        self.group_rids = None
        self.hidden_rids = None
        self.visible_rids = None

        if was_group_drag and drag_rid is not None:
            h._collapsed_groups.discard(drag_rid)

        if visible_snapshot:
            for row in h.rows:
                if (row["type"] == "separator"
                        and row["rowid"] in h._collapsed_groups):
                    for cid in h._group_children(row["rowid"]):
                        if cid in visible_snapshot:
                            h._collapsed_groups.discard(row["rowid"])
                            break

        h._save_state()
        h._try_snapshot(reason="layout_change",priority="medium")
        QApplication.restoreOverrideCursor()
        QApplication.instance().removeEventFilter(h)
        h.setMinimumSize(0, 0)
        h.setMaximumSize(16777215, 16777215)
        h._rebuild_rows()
        QTimer.singleShot(0, h.adjustSize)

    def handle_event(self, obj, event):
        """Handle a QEvent during an active drag.  Returns True if consumed."""
        if event.type() == QEvent.MouseMove:
            self._on_mouse_move(event)
            return True
        if event.type() == QEvent.MouseButtonRelease:
            self.end()
            return True
        return False

    def _on_mouse_move(self, event):
        h = self.host
        global_pos = event.globalPosition().toPoint()
        local_pos = h._grid_widget.mapFromGlobal(global_pos)
        target_vis = self._row_at_y(local_pos.y())
        if target_vis is None or target_vis == self.last_row:
            return

        drag_rid = self.dragging_rid
        target_rid = h._visible_rowids[target_vis]

        # Separator overshoot prevention
        if target_vis > self.last_row and self.hidden_rids is not None:
            tgt_row = next(
                (r for r in h.rows if r["rowid"] == target_rid), None)
            if tgt_row and tgt_row["type"] == "separator":
                nxt = target_vis + 1
                if nxt < len(h._visible_rowids):
                    nxt_rid = h._visible_rowids[nxt]
                    nxt_row = next(
                        (r for r in h.rows if r["rowid"] == nxt_rid), None)
                    if nxt_row and nxt_row["type"] != "separator":
                        return  # wait

        if self.group_rids is not None:
            # Group drag
            block = [r for r in h.rows
                     if r["rowid"] == drag_rid
                     or r["rowid"] in self.group_rids]
            h.rows = [r for r in h.rows
                      if r["rowid"] != drag_rid
                      and r["rowid"] not in self.group_rids]
            target_idx = next(
                (i for i, r in enumerate(h.rows)
                 if r["rowid"] == target_rid), len(h.rows))
            if target_vis > self.last_row:
                target_idx += 1
                if (target_idx > 0
                        and h.rows[target_idx - 1]["type"] == "separator"):
                    while (target_idx < len(h.rows)
                           and h.rows[target_idx]["type"] != "separator"):
                        target_idx += 1
            for j, br in enumerate(block):
                h.rows.insert(target_idx + j, br)
        else:
            # Single row drag
            drag_row = next(r for r in h.rows if r["rowid"] == drag_rid)
            h.rows.remove(drag_row)
            target_idx = next(
                i for i, r in enumerate(h.rows)
                if r["rowid"] == target_rid)
            if target_vis > self.last_row:
                insert_idx = target_idx + 1
                if (self.hidden_rids is not None
                        and h.rows[target_idx]["type"] == "separator"):
                    while (insert_idx < len(h.rows)
                           and h.rows[insert_idx]["type"] != "separator"):
                        insert_idx += 1
                h.rows.insert(insert_idx, drag_row)
            else:
                h.rows.insert(target_idx, drag_row)

        # Pre-expand collapsed group that would swallow a single timer
        drag_row_obj = next(
            (r for r in h.rows if r["rowid"] == drag_rid), None)
        if (drag_row_obj and drag_row_obj["type"] == "timer"
                and self.group_rids is None):
            parent = h._parent_group(drag_rid)
            if parent is not None and parent in h._collapsed_groups:
                h._collapsed_groups.discard(parent)

        self._reorder_visual()
        if drag_rid in h._visible_rowids:
            self.last_row = h._visible_rowids.index(drag_rid)

    def _reorder_visual(self):
        """Lightweight reorder of existing row containers during drag."""
        h = self.host
        from ct.ui.theme import THEMES, SIZES
        t = THEMES.get(h.theme, THEMES["Cupertino Light"])
        s = SIZES.get(h.ui_size, SIZES["Regular"])

        current_group_rid = None
        visible_entries = []
        dragging_group = (self.group_rids is not None)

        for row in h.rows:
            rid = row["rowid"]
            if row["type"] == "separator":
                current_group_rid = rid
                visible_entries.append((row, False))
            else:
                if dragging_group and rid in self.group_rids:
                    continue
                if self.hidden_rids is not None and rid in self.hidden_rids:
                    continue
                is_child = current_group_rid is not None
                if (is_child
                        and current_group_rid in h._collapsed_groups
                        and not (dragging_group
                                 and current_group_rid == self.dragging_rid)):
                    if (self.visible_rids is not None
                            and rid in self.visible_rids):
                        pass
                    else:
                        continue
                visible_entries.append((row, is_child))

        new_visible_rids = [r["rowid"] for r, _ in visible_entries]

        for rid in new_visible_rids:
            if rid not in h._widgets:
                h._rebuild_rows()
                return

        h._visible_rowids = new_visible_rids

        bold_label = QFont(h.font_family, s["label"])
        bold_label.setBold(True)
        indent_px = QFontMetrics(bold_label).horizontalAdvance("  ")
        group_header_bg = t.get("group_header_bg", t["bg"])

        h._grid_widget.setUpdatesEnabled(False)

        for rid in list(h._widgets.keys()):
            container = h._widgets[rid].get("container")
            if container:
                h._grid.removeWidget(container)
                container.hide()

        for insert_idx, (row, is_child) in enumerate(visible_entries):
            rid = row["rowid"]
            container = h._widgets[rid]["container"]

            if row["type"] == "separator":
                row_bg = row.get("bg") or group_header_bg
            else:
                row_bg = row.get("bg") or t["bg"]
            if self.dragging_rid == rid:
                row_bg = t["row_dragged"]

            margin_css = (f"margin-left: {indent_px - 3}px;"
                          if row["type"] == "timer" and is_child else "")
            needs_sep = (h.client_separators
                         and insert_idx < len(visible_entries) - 1
                         and row["type"] == "timer"
                         and visible_entries[insert_idx + 1][0]["type"] == "timer")
            border_css = (f"border-bottom: 1px solid {t['row_separator']};"
                          if needs_sep else "")

            container.setStyleSheet(
                f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}")
            container.show()
            h._grid.insertWidget(insert_idx, container)

        h._grid_widget.setUpdatesEnabled(True)
        h._grid.activate()

    def _row_at_y(self, y):
        """Return the visible row index whose vertical center is closest to y."""
        h = self.host
        best_row = None
        best_dist = float("inf")
        for vis_idx, rid in enumerate(h._visible_rowids):
            if rid in h._widgets and "container" in h._widgets[rid]:
                rect = h._widgets[rid]["container"].geometry()
                dist = abs(y - rect.center().y())
                if dist < best_dist:
                    best_dist = dist
                    best_row = vis_idx
        return best_row

    def _hidden_rids_snapshot(self):
        """Return set of timer rowids currently hidden under collapsed groups."""
        h = self.host
        hidden = set()
        parent = None
        for row in h.rows:
            if row["type"] == "separator":
                parent = row["rowid"]
            elif parent is not None and parent in h._collapsed_groups:
                hidden.add(row["rowid"])
        return hidden

    def rid_for_container(self, widget):
        """Map a container widget back to its rowid."""
        for rid, w in self.host._widgets.items():
            if w.get("container") is widget:
                return rid
        return None
