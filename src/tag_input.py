"""
Tako Reader — tag input widget.
A flow-layout tag bar with pill/chip display, × removal,
inline text input, and autocomplete from existing library tags.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QEvent, QTimer
from PyQt6.QtGui import QFont, QKeyEvent

import theme


class TagPill(QWidget):
    """A single tag pill with name and × button."""
    removed = pyqtSignal(str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.tag_name = name
        self.setFixedHeight(24)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(4)

        label = QLabel(name)
        label.setFont(QFont("", 9))
        lay.addWidget(label)

        x_btn = QPushButton("×")
        x_btn.setFixedSize(16, 16)
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(lambda: self.removed.emit(self.tag_name))
        lay.addWidget(x_btn)

        self._apply_style()

    def _apply_style(self):
        bg = theme._active["border_light"]
        text = theme._active["text"]
        text_muted = theme._active["text_muted"]
        hover = theme._active["hover_bg"]

        self.setStyleSheet(
            f"TagPill {{"
            f"  background: {bg}; border-radius: 10px;"
            f"}}"
            f" QLabel {{"
            f"  color: {text}; background: transparent; padding: 0;"
            f"}}"
            f" QPushButton {{"
            f"  background: transparent; border: none;"
            f"  color: {text_muted}; font-size: 12px; font-weight: bold;"
            f"  padding: 0; margin: 0;"
            f"}}"
            f" QPushButton:hover {{"
            f"  color: {text};"
            f"}}"
        )


class FlowLayout(QVBoxLayout):
    """Simplified flow layout that wraps pills into rows."""
    pass


class TagInputWidget(QWidget):
    """
    Tag input bar: shows existing tags as pills, with inline text input
    and autocomplete dropdown. Emits tags_changed when tags are added/removed.
    """
    tags_changed = pyqtSignal(list)  # emits current tag list

    def __init__(self, available_tags: list[str] = None, parent=None):
        super().__init__(parent)
        self._tags: list[str] = []
        self._available_tags: list[str] = available_tags or []
        self._pills: dict[str, TagPill] = {}

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Flow container for pills + input
        self._flow_container = QWidget()
        self._flow_layout = _FlowLayoutHelper(self._flow_container)
        self._flow_layout.setContentsMargins(0, 0, 0, 0)
        self._flow_layout.setSpacing(4)
        root.addWidget(self._flow_container)

        # Text input (added as last item in flow)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Add tag…")
        self._input.setFixedHeight(24)
        self._input.setMinimumWidth(80)
        self._input.setMaximumWidth(200)
        self._input.setFont(QFont("", 9))
        self._input.setStyleSheet(
            f"QLineEdit {{"
            f"  background: transparent;"
            f"  color: {theme._active['text']};"
            f"  border: none; padding: 2px 4px;"
            f"}}"
        )
        self._input.textChanged.connect(self._on_text_changed)
        self._input.installEventFilter(self)
        self._flow_layout.addWidget(self._input)

        # Autocomplete dropdown
        self._dropdown = QListWidget()
        self._dropdown.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self._dropdown.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dropdown.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating, True
        )
        self._dropdown.setMaximumHeight(150)
        self._dropdown.setStyleSheet(
            f"QListWidget {{"
            f"  background: {theme._active['menu_bg']};"
            f"  color: {theme._active['text']};"
            f"  border: 1px solid {theme._active['border']};"
            f"  border-radius: 4px; padding: 2px;"
            f"  font-size: 9pt;"
            f"}}"
            f" QListWidget::item {{"
            f"  padding: 4px 8px; border-radius: 3px;"
            f"}}"
            f" QListWidget::item:selected {{"
            f"  background: {theme.ACCENT}; color: #fff;"
            f"}}"
        )
        self._dropdown.itemClicked.connect(self._on_dropdown_clicked)
        self._dropdown.hide()

        # Container styling
        self._flow_container.setStyleSheet(
            f"QWidget {{"
            f"  background: {theme._active['input_bg']};"
            f"  border: 1px solid {theme._active['border_light']};"
            f"  border-radius: 6px; padding: 4px;"
            f"}}"
        )

    # ── Public API ────────────────────────────────────────────────────────

    def set_tags(self, tags: list[str]):
        """Set the current tags (replaces all)."""
        # Clear existing
        for pill in list(self._pills.values()):
            self._flow_layout.removeWidget(pill)
            pill.deleteLater()
        self._pills.clear()
        self._tags.clear()

        for tag in tags:
            tag = tag.strip()
            if tag and tag not in self._tags:
                self._add_pill(tag)

    def get_tags(self) -> list[str]:
        """Return current tag list."""
        return list(self._tags)

    def set_available_tags(self, tags: list[str]):
        """Update the autocomplete suggestions."""
        self._available_tags = tags

    # ── Internal ──────────────────────────────────────────────────────────

    def _add_pill(self, tag: str):
        """Add a tag pill to the bar."""
        if tag in self._tags:
            return
        self._tags.append(tag)

        pill = TagPill(tag)
        pill.removed.connect(self._remove_tag)
        self._pills[tag] = pill

        # Insert before the input
        idx = self._flow_layout.count() - 1  # before input
        self._flow_layout.insertWidget(idx, pill)

        self.tags_changed.emit(self._tags)

    def _remove_tag(self, tag: str):
        """Remove a tag pill."""
        if tag not in self._tags:
            return
        self._tags.remove(tag)
        pill = self._pills.pop(tag, None)
        if pill:
            self._flow_layout.removeWidget(pill)
            pill.deleteLater()
        self.tags_changed.emit(self._tags)

    def _commit_input(self):
        """Commit the current text input as a tag."""
        text = self._input.text().strip().rstrip(",")
        if text and text not in self._tags:
            self._add_pill(text)
        self._input.clear()
        self._dropdown.hide()

    def _on_text_changed(self, text: str):
        """Handle text input changes — show/update autocomplete."""
        # Check for comma (commit trigger)
        if "," in text:
            parts = text.split(",")
            for part in parts[:-1]:
                part = part.strip()
                if part and part not in self._tags:
                    self._add_pill(part)
            self._input.setText(parts[-1].strip())
            return

        text = text.strip().lower()
        if not text:
            self._dropdown.hide()
            return

        # Filter available tags
        matches = [
            t for t in self._available_tags
            if text in t.lower() and t not in self._tags
        ]

        if not matches:
            self._dropdown.hide()
            return

        self._dropdown.clear()
        for m in matches[:10]:
            self._dropdown.addItem(m)
        self._dropdown.setCurrentRow(0)

        # Position below input
        pos = self._input.mapToGlobal(self._input.rect().bottomLeft())
        self._dropdown.move(pos)
        self._dropdown.setFixedWidth(max(self._input.width(), 150))
        self._dropdown.show()
        self._input.setFocus()

    def _on_dropdown_clicked(self, item: QListWidgetItem):
        """Handle autocomplete item click."""
        tag = item.text()
        self._input.clear()
        self._dropdown.hide()
        self._add_pill(tag)
        self._input.setFocus()

    def eventFilter(self, obj, event):
        """Handle key events on the text input."""
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if (self._dropdown.isVisible()
                        and self._dropdown.currentItem()):
                    tag = self._dropdown.currentItem().text()
                    self._input.clear()
                    self._dropdown.hide()
                    self._add_pill(tag)
                else:
                    self._commit_input()
                return True

            if key == Qt.Key.Key_Backspace and not self._input.text():
                # Remove last pill
                if self._tags:
                    self._remove_tag(self._tags[-1])
                return True

            if key == Qt.Key.Key_Down and self._dropdown.isVisible():
                row = self._dropdown.currentRow()
                if row < self._dropdown.count() - 1:
                    self._dropdown.setCurrentRow(row + 1)
                return True

            if key == Qt.Key.Key_Up and self._dropdown.isVisible():
                row = self._dropdown.currentRow()
                if row > 0:
                    self._dropdown.setCurrentRow(row - 1)
                return True

            if key == Qt.Key.Key_Escape and self._dropdown.isVisible():
                self._dropdown.hide()
                return True

            if key == Qt.Key.Key_Tab and self._dropdown.isVisible():
                if self._dropdown.currentItem():
                    tag = self._dropdown.currentItem().text()
                    self._input.clear()
                    self._dropdown.hide()
                    self._add_pill(tag)
                return True

        return super().eventFilter(obj, event)

    def hideEvent(self, event):
        self._dropdown.hide()
        super().hideEvent(event)


class _FlowLayoutHelper(QHBoxLayout):
    """
    A simple horizontal layout that wraps.
    For our use case, QHBoxLayout is sufficient since tag bars
    typically fit in a single row, and the container will scroll
    or expand as needed.
    """
    pass
