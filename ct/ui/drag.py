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

    _ZONE = 22   # px from the viewport edge that triggers auto-scroll

    def __init__(self, host: MainWindow):
        self.host         = host
        self.dragging_rid = None
        self.last_row     = -1
        self._last_pos    = None   # last cursor position, for autoscroll ticks
        self._scroll_dir  = 0      # -1 up, +1 down, 0 idle
        self._autoscroll  = QTimer(host)
        self._autoscroll.setInterval(110)
        self._autoscroll.timeout.connect(self._on_autoscroll_tick)
        self.group_rids   = None   # set of child rowids when dragging collapsed group
        self.hidden_rids  = None   # snapshot of hidden rids during separator drag
        self.visible_rids = None   # snapshot of visible rids at drag start

    @property
    def active(self):
        return self.dragging_rid is not None

    def start(self, rowid):
        """Begin drag-reordering a row."""
        h = self.host
        self.dragging_rid = rowid

        row = next(r for r in h._state.rows if r["rowid"] == rowid)
        if row["type"] == "separator" and rowid in h._state.collapsed_groups:
            children        = h._group_children(rowid)
            self.group_rids = set(children)
        else:
            self.group_rids = None

        if row["type"] == "separator":
            self.hidden_rids = self._hidden_rids_snapshot()
        else:
            self.hidden_rids = None

        self.visible_rids = set(h._visible_rowids)
        # For undo. A reorder can't change any elapsed time, so keeping the
        # whole list is safe here in a way a full-state rollback is not.
        self._rows_before     = [dict(r) for r in h._state.rows]
        self._collapsed_before = set(h._state.collapsed_groups)

        h.setFixedSize(h.size())
        QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        QApplication.instance().installEventFilter(h)
        h._rebuild_rows()
        self.last_row = h._visible_rowids.index(rowid)

    def end(self):
        """Finish drag-reordering and persist the new order."""
        h = self.host
        drag_rid         = self.dragging_rid
        was_group_drag   = self.group_rids is not None
        visible_snapshot = self.visible_rids

        self._stop_autoscroll()
        self._last_pos    = None
        self.dragging_rid = None
        self.last_row     = -1
        self.group_rids   = None
        self.hidden_rids  = None
        self.visible_rids = None

        if was_group_drag and drag_rid is not None:
            h._state.collapsed_groups.discard(drag_rid)

        if visible_snapshot:
            for row in h._state.rows:
                if (row["type"] == "separator"
                        and row["rowid"] in h._state.collapsed_groups):
                    for cid in h._group_children(row["rowid"]):
                        if cid in visible_snapshot:
                            h._state.collapsed_groups.discard(row["rowid"])
                            break

        # Only worth an undo entry if the drag actually moved something.
        before = getattr(self, "_rows_before", None)
        self._rows_before = None
        if before is not None:
            order_now = [r["rowid"] for r in h._state.rows]
            if order_now != [r["rowid"] for r in before]:
                from ct.core.undo import ReorderRows
                h._undo.push(ReorderRows("that reorder", before,
                                         self._collapsed_before))

        h._save_state()
        h._try_snapshot(reason="layout_change", priority="medium")
        QApplication.restoreOverrideCursor()
        QApplication.instance().removeEventFilter(h)
        h.setMinimumSize(0, 0)
        h.setMaximumSize(16777215, 16777215)
        h._rebuild_rows()
        h._shrink_to_fit()

    def handle_event(self, obj, event):
        """Handle a QEvent during an active drag. Returns True if consumed."""
        if event.type() == QEvent.MouseMove:
            self._on_mouse_move(event)
            return True
        if event.type() == QEvent.MouseButtonRelease:
            self.end()
            return True
        return False

    def _on_mouse_move(self, event):
        self._last_pos = event.globalPosition().toPoint()
        self._update_autoscroll(self._last_pos)
        self._update_drag_position(self._last_pos)

    # -- Auto-scroll while dragging against the edge of the viewport -- #

    def _update_autoscroll(self, global_pos):
        """Start/stop edge scrolling based on where the cursor is."""
        h  = self.host
        sa = h._scroll_area
        if sa is None or sa.verticalScrollBar().maximum() == 0:
            self._stop_autoscroll()
            return
        bar   = sa.verticalScrollBar()
        vp    = sa.viewport()
        y     = vp.mapFromGlobal(global_pos).y()
        zone  = max(12, self._ZONE)
        if y < zone and bar.value() > bar.minimum():
            direction = -1
        elif y > vp.height() - zone and bar.value() < bar.maximum():
            direction = 1
        else:
            direction = 0
        self._scroll_dir = direction
        if direction:
            if not self._autoscroll.isActive():
                self._autoscroll.start()
        else:
            self._autoscroll.stop()

    def _stop_autoscroll(self):
        self._scroll_dir = 0
        self._autoscroll.stop()

    def _on_autoscroll_tick(self):
        """Advance one row, then re-run the hit test at the held cursor.

        The cursor hasn't moved, but the rows have — so the row under it is a
        different one, and the dragged row needs to swap with it.
        """
        h = self.host
        if not self.active or not self._scroll_dir or h._scroll_area is None:
            self._stop_autoscroll()
            return
        bar    = h._scroll_area.verticalScrollBar()
        before = bar.value()
        h._scroll_by_rows(self._scroll_dir)
        if bar.value() == before:       # reached the end of the list
            self._stop_autoscroll()
            return
        if self._last_pos is not None:
            self._update_drag_position(self._last_pos)

    def _update_drag_position(self, global_pos):
        h          = self.host
        local_pos  = h._grid_widget.mapFromGlobal(global_pos)
        target_vis = self._row_at_y(local_pos.y())
        if target_vis is None or target_vis == self.last_row:
            return

        drag_rid   = self.dragging_rid
        target_rid = h._visible_rowids[target_vis]

        # Separator overshoot prevention
        if target_vis > self.last_row and self.hidden_rids is not None:
            tgt_row = next(
                (r for r in h._state.rows if r["rowid"] == target_rid), None)
            if tgt_row and tgt_row["type"] == "separator":
                nxt = target_vis + 1
                if nxt < len(h._visible_rowids):
                    nxt_rid = h._visible_rowids[nxt]
                    nxt_row = next(
                        (r for r in h._state.rows if r["rowid"] == nxt_rid), None)
                    if nxt_row and nxt_row["type"] != "separator":
                        return  # wait

        if self.group_rids is not None:
            # Group drag
            block = [r for r in h._state.rows
                     if r["rowid"] == drag_rid
                     or r["rowid"] in self.group_rids]
            h._state.rows = [r for r in h._state.rows
                             if r["rowid"] != drag_rid
                             and r["rowid"] not in self.group_rids]
            target_idx = next(
                (i for i, r in enumerate(h._state.rows)
                 if r["rowid"] == target_rid), len(h._state.rows))
            if target_vis > self.last_row:
                target_idx += 1
                if (target_idx > 0
                        and h._state.rows[target_idx - 1]["type"] == "separator"):
                    while (target_idx < len(h._state.rows)
                           and h._state.rows[target_idx]["type"] != "separator"):
                        target_idx += 1
            for j, br in enumerate(block):
                h._state.rows.insert(target_idx + j, br)
        else:
            # Single row drag
            drag_row = next(r for r in h._state.rows if r["rowid"] == drag_rid)
            h._state.rows.remove(drag_row)
            target_idx = next(
                i for i, r in enumerate(h._state.rows)
                if r["rowid"] == target_rid)
            if target_vis > self.last_row:
                insert_idx = target_idx + 1
                if (self.hidden_rids is not None
                        and h._state.rows[target_idx]["type"] == "separator"):
                    while (insert_idx < len(h._state.rows)
                           and h._state.rows[insert_idx]["type"] != "separator"):
                        insert_idx += 1
                h._state.rows.insert(insert_idx, drag_row)
            else:
                h._state.rows.insert(target_idx, drag_row)

        # Pre-expand collapsed group that would swallow a single timer
        drag_row_obj = next(
            (r for r in h._state.rows if r["rowid"] == drag_rid), None)
        if (drag_row_obj and drag_row_obj["type"] == "timer"
                and self.group_rids is None):
            parent = h._parent_group(drag_rid)
            if parent is not None and parent in h._state.collapsed_groups:
                h._state.collapsed_groups.discard(parent)

        self._reorder_visual()
        if drag_rid in h._visible_rowids:
            self.last_row = h._visible_rowids.index(drag_rid)

    def _reorder_visual(self):
        """Lightweight reorder of existing row containers during drag."""
        h = self.host

        from ct.ui.theme import THEMES, SIZES
        ss = h._state.settings
        t  = THEMES.get(ss.theme, THEMES["E-Ink (Default)"])
        s  = SIZES.get(ss.size, SIZES["Regular"])

        current_group_rid = None
        visible_entries   = []
        dragging_group    = (self.group_rids is not None)

        for row in h._state.rows:
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
                        and current_group_rid in h._state.collapsed_groups
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

        bold_label = QFont(ss.font, s["label"])
        bold_label.setBold(True)
        indent_px        = QFontMetrics(bold_label).horizontalAdvance("  ")
        group_bg  = t["group_bg"]

        h._grid_widget.setUpdatesEnabled(False)

        for rid in list(h._widgets.keys()):
            container = h._widgets[rid].get("container")
            if container:
                h._grid.removeWidget(container)
                container.hide()

        for insert_idx, (row, is_child) in enumerate(visible_entries):
            rid       = row["rowid"]
            container = h._widgets[rid]["container"]

            if row["type"] == "separator":
                row_bg = row.get("bg") or group_bg
            else:
                row_bg = row.get("bg") or t["app_bg"]

            if self.dragging_rid == rid:
                row_bg = t["row_drag_bg"]

            margin_css = (f"margin-left: {indent_px - 3}px;"
                          if row["type"] == "timer" and is_child else "")

            needs_sep = (ss.client_separators
                         and insert_idx < len(visible_entries) - 1
                         and row["type"] == "timer"
                         and visible_entries[insert_idx + 1][0]["type"] == "timer")
            # No merged footer line: the thick rule lives below the scroll
            # viewport now, so rows never carry it.
            border_css = (f"border-bottom: 1px solid {t['row_line']};"
                          if needs_sep else "")

            if row["type"] == "separator":
                group_line = t["group_line"]
                container.setStyleSheet(
                    f"#rowBg {{ background-color: {row_bg}; {margin_css}"
                    f" border: 2px solid {group_line}; }}")
            else:
                container.setStyleSheet(
                    f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}")

            container.show()
            h._grid.insertWidget(insert_idx, container)

        h._grid_widget.setUpdatesEnabled(True)
        h._grid.activate()
        # Every row's stylesheet was just rewritten, restoring the separator
        # on whichever row was hiding it. Re-pick the bottom-most one.
        h._update_bottom_line()

    def _row_at_y(self, y):
        """Return the visible row index whose vertical center is closest to y."""
        h         = self.host
        best_row  = None
        best_dist = float("inf")
        for vis_idx, rid in enumerate(h._visible_rowids):
            if rid in h._widgets and "container" in h._widgets[rid]:
                rect = h._widgets[rid]["container"].geometry()
                dist = abs(y - rect.center().y())
                if dist < best_dist:
                    best_dist = dist
                    best_row  = vis_idx
        return best_row

    def _hidden_rids_snapshot(self):
        """Return set of timer rowids currently hidden under collapsed groups."""
        h      = self.host
        hidden = set()
        parent = None
        for row in h._state.rows:
            if row["type"] == "separator":
                parent = row["rowid"]
            elif parent is not None and parent in h._state.collapsed_groups:
                hidden.add(row["rowid"])
        return hidden

    def rid_for_container(self, widget):
        """Map a container widget back to its rowid."""
        for rid, w in self.host._widgets.items():
            if w.get("container") is widget:
                return rid
        return None
