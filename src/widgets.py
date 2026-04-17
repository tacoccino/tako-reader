"""
Tako Reader — reusable UI widgets.
PageView, OCR panel and cards, bookmark popup, image adjustments,
marquee overlay, and thumbnail strip.
"""

import webbrowser
from urllib.parse import quote as url_quote

try:
    from PyQt6 import sip as _sip
    def _is_deleted(obj) -> bool:
        return _sip.isdeleted(obj)
except ImportError:
    def _is_deleted(obj) -> bool:  # type: ignore[misc]
        try:
            obj.objectName()
            return False
        except RuntimeError:
            return True

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QRubberBand,
    QTextBrowser, QListWidget, QListWidgetItem, QLineEdit,
    QSlider, QMenu,
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QPoint, QThread, pyqtSignal, QSettings, QTimer,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QAction, QFont, QColor, QCursor, QIcon,
    QGuiApplication, QPainter, QBrush, QPen,
)

from utils import load_icon
import theme
from ocr import segment_japanese
from dictionary import DictPopup

# ─── Page View ───────────────────────────────────────────────────────────────

class PageView(QLabel):
    """Single manga page: zoom, Shift+drag pan, OCR rubber-band selection."""

    ocr_requested  = pyqtSignal(QImage, QRect)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background-color: {theme._active['window_bg']};")

        self._pixmap_orig: QPixmap | None = None
        self._scale    = 1.0
        self._fit_mode = "fit_width"
        self._ocr_mode = False
        self.setMouseTracking(True)

        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._sel_origin  = QPoint()

        self._panning      = False
        self._pan_start    = QPoint()
        self._scroll_start = QPoint()

        # OCR highlight overlay
        self._highlight_rect: QRect | None = None  # source-image coords
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self.clear_highlight)

        # OCR region hover (highlight rect on page when mouse over it)
        self._ocr_regions: list[tuple[QRect, object]] = []  # (display-coord rect, card)
        self._hovered_region: QRect | None = None
        self._hovered_card_ref = None

    def set_highlight(self, source_rect: QRect, duration_ms: int = 0):
        """Show a highlight overlay at the given source-image rect.
        If duration_ms > 0, auto-clear after that time."""
        self._highlight_rect = source_rect
        self.update()
        if duration_ms > 0:
            self._highlight_timer.start(duration_ms)

    def clear_highlight(self):
        self._highlight_rect = None
        self._highlight_timer.stop()
        self.update()

    def set_ocr_regions(self, regions: list[tuple[QRect, object]]):
        """Set the list of (display_rect, card_ref) for hover hit-testing."""
        self._ocr_regions = regions
        self._hovered_region = None

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._highlight_rect or not self._pixmap_orig:
            return
        pm = self.pixmap()
        if not pm:
            return
        dpr = pm.devicePixelRatio()
        pm_lw = pm.width() / dpr
        pm_lh = pm.height() / dpr
        off_x = (self.width() - pm_lw) / 2
        off_y = (self.height() - pm_lh) / 2

        r = self._highlight_rect
        screen_rect = QRect(
            int(r.x() * self._scale + off_x),
            int(r.y() * self._scale + off_y),
            int(r.width() * self._scale),
            int(r.height() * self._scale),
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(screen_rect, QColor(53, 132, 228, 50))
        painter.setPen(QPen(QColor(53, 132, 228), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(screen_rect)
        painter.end()

    def set_pixmap(self, px: QPixmap):
        self._pixmap_orig = px
        self._apply_fit()

    def set_scale(self, scale: float):
        self._scale    = max(0.1, min(scale, 8.0))
        self._fit_mode = "custom"
        self._render()

    def set_fit_mode(self, mode: str):
        self._fit_mode = mode
        self._apply_fit()

    def set_ocr_mode(self, enabled: bool):
        self._ocr_mode = enabled
        self._update_cursor()

    def _update_cursor(self):
        if self._panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._ocr_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _apply_fit(self):
        if not self._pixmap_orig:
            return
        pw, ph = self._pixmap_orig.width(), self._pixmap_orig.height()
        # Measure the viewport, not self — self resizes to fit the zoomed image
        sa = self._scroll_area()
        if sa:
            vw = sa.viewport().width()
            vh = sa.viewport().height()
        else:
            vw, vh = self.width(), self.height()
        if self._fit_mode == "fit_width":
            self._scale = vw / pw if pw else 1.0
        elif self._fit_mode == "fit_page":
            self._scale = min(vw / pw, vh / ph) if pw and ph else 1.0
        self._render()

    def _render(self):
        if not self._pixmap_orig:
            return
        # Get device pixel ratio for HiDPI / Retina sharpness
        dpr = self.devicePixelRatio()
        w = int(self._pixmap_orig.width()  * self._scale)
        h = int(self._pixmap_orig.height() * self._scale)
        # Scale to physical pixels so the image is never upsampled by the OS
        scaled = self._pixmap_orig.scaled(
            int(w * dpr), int(h * dpr),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.setPixmap(scaled)
        # Resize so the scroll area has real range at high zoom,
        # but never shrink below the viewport size (keeps image centred at low zoom)
        sa = self._scroll_area()
        if sa:
            vw = sa.viewport().width()
            vh = sa.viewport().height()
            self.setFixedSize(max(w, vw), max(h, vh))
        else:
            self.setFixedSize(w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-render on resize so widget size stays in sync with viewport
        if self._fit_mode != "custom":
            self._apply_fit()
        else:
            self._render()

    def _scroll_area(self):
        p = self.parent()
        while p:
            if isinstance(p, QScrollArea):
                return p
            p = p.parent()
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._panning      = True
                self._pan_start    = event.globalPosition().toPoint()
                sa = self._scroll_area()
                if sa:
                    self._scroll_start = QPoint(
                        sa.horizontalScrollBar().value(),
                        sa.verticalScrollBar().value(),
                    )
                self._update_cursor()
                event.accept()
                return
            if self._ocr_mode:
                self._sel_origin = event.pos()
                self._rubber_band.setGeometry(QRect(self._sel_origin, QSize()))
                self._rubber_band.show()

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.globalPosition().toPoint() - self._pan_start
            sa = self._scroll_area()
            if sa:
                sa.horizontalScrollBar().setValue(self._scroll_start.x() - delta.x())
                sa.verticalScrollBar().setValue(self._scroll_start.y() - delta.y())
            event.accept()
            return
        if self._ocr_mode and not self._sel_origin.isNull():
            self._rubber_band.setGeometry(
                QRect(self._sel_origin, event.pos()).normalized()
            )
            return
        # Region hover hit-test (only in OCR mode, not while drawing)
        if self._ocr_mode and self._ocr_regions and self._pixmap_orig:
            self._hit_test_regions(event.pos())

    def _hit_test_regions(self, pos: QPoint):
        """Check if cursor is over any stored OCR region and highlight it."""
        pm = self.pixmap()
        if not pm:
            return
        dpr = pm.devicePixelRatio()
        pm_lw = pm.width() / dpr
        pm_lh = pm.height() / dpr
        off_x = (self.width() - pm_lw) / 2
        off_y = (self.height() - pm_lh) / 2

        # Convert mouse pos to source-image coords
        src_x = (pos.x() - off_x) / self._scale
        src_y = (pos.y() - off_y) / self._scale

        live_regions = []
        for region, card_ref in self._ocr_regions:
            if _is_deleted(card_ref):
                continue  # prune stale refs
            live_regions.append((region, card_ref))
            if region.contains(int(src_x), int(src_y)):
                if self._hovered_region is not region:
                    self._hovered_region = region
                    self.set_highlight(region)
                    # Show overlay on the card
                    if not hasattr(card_ref, '_highlight_overlay') or card_ref._highlight_overlay is None:
                        ov = QWidget(card_ref)
                        ov.setStyleSheet(
                            f"background: rgba(53, 132, 228, 40);"
                            f" border: 2px solid {theme.ACCENT};"
                            f" border-radius: 6px;"
                        )
                        ov.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                        card_ref._highlight_overlay = ov
                    card_ref._highlight_overlay.setGeometry(card_ref.rect())
                    card_ref._highlight_overlay.show()
                    card_ref._highlight_overlay.raise_()
                    self._hovered_card_ref = card_ref
                self._ocr_regions = live_regions
                return
        self._ocr_regions = live_regions

        # No hit — clear if we were hovering a region
        if self._hovered_region is not None:
            self._hovered_region = None
            self.clear_highlight()
            if hasattr(self, '_hovered_card_ref') and self._hovered_card_ref:
                if (not _is_deleted(self._hovered_card_ref)
                        and hasattr(self._hovered_card_ref, '_highlight_overlay')
                        and self._hovered_card_ref._highlight_overlay):
                    self._hovered_card_ref._highlight_overlay.hide()
                self._hovered_card_ref = None

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._panning:
                self._panning = False
                self._update_cursor()
                event.accept()
                return
            if self._ocr_mode:
                self._rubber_band.hide()
                sel = QRect(self._sel_origin, event.pos()).normalized()
                self._sel_origin = QPoint()
                if sel.width() > 5 and sel.height() > 5 and self._pixmap_orig:
                    pm = self.pixmap()
                    if pm:
                        dpr = pm.devicePixelRatio()
                        # pm dimensions are physical pixels; widget/sel are logical
                        pm_logical_w = pm.width()  / dpr
                        pm_logical_h = pm.height() / dpr
                        offset_x = (self.width()  - pm_logical_w) / 2
                        offset_y = (self.height() - pm_logical_h) / 2
                        img_rect = QRect(
                            int((sel.x() - offset_x) / self._scale),
                            int((sel.y() - offset_y) / self._scale),
                            int(sel.width()  / self._scale),
                            int(sel.height() / self._scale),
                        ).intersected(
                            QRect(0, 0, self._pixmap_orig.width(), self._pixmap_orig.height())
                        )
                        if img_rect.isValid():
                            self.ocr_requested.emit(self._pixmap_orig.toImage(), img_rect)


# ─── OCR Sidebar ─────────────────────────────────────────────────────────────

class HoverTextBrowser(QTextBrowser):
    """QTextBrowser that tracks which anchor the cursor is currently over."""

    hovered_anchor_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_anchor = ""
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        anchor = self.anchorAt(event.pos())
        if anchor != self._current_anchor:
            self._current_anchor = anchor
            self.hovered_anchor_changed.emit(anchor)
            self.viewport().setCursor(
                Qt.CursorShape.PointingHandCursor if anchor
                else Qt.CursorShape.IBeamCursor
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._current_anchor:
            self._current_anchor = ""
            self.hovered_anchor_changed.emit("")
        super().leaveEvent(event)


class OCRCard(QWidget):
    """
    A single OCR result card. Each rubber-band selection produces one card.
    Newest cards are inserted at the top of the panel's scroll area.
    """
    word_clicked      = pyqtSignal(str, str)   # word, own raw_text (as sentence)
    merge_requested   = pyqtSignal(object)     # emits self
    dismiss_requested = pyqtSignal(object)     # emits self
    jump_requested    = pyqtSignal(object)     # emits self
    highlight_requested = pyqtSignal(object)   # emits self (on hover enter)
    highlight_cleared   = pyqtSignal()         # on hover leave

    def __init__(self, raw_text: str, segmentation_on: bool,
                 dict_popup, page_index: int = 0,
                 source_rect: QRect | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("OCRCard")
        self.setStyleSheet(theme.CARD_STYLE)
        self._raw_text       = raw_text
        self._segmentation_on = segmentation_on
        self._hovered_word   = ""
        self._last_hovered   = ""
        self._dict_popup     = dict_popup
        self._page_index     = page_index
        self._source_rect    = source_rect

        # Use a plain layout — buttons float over the browser as an overlay
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Text browser fills the card ──
        self.browser = HoverTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setFont(QFont("Noto Serif JP, serif", 16))
        self.browser.setStyleSheet(theme.ocr_browser_stylesheet())
        self.browser.anchorClicked.connect(self._on_word_clicked)
        self.browser.hovered_anchor_changed.connect(self._on_hover_changed)
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self._show_context_menu)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.browser)

        # ── Button row overlaid at bottom-right of the browser ──
        self._btn_bar = QWidget(self)
        self._btn_bar.setStyleSheet("background: transparent;")
        btn_lay = QHBoxLayout(self._btn_bar)
        btn_lay.setContentsMargins(0, 0, 4, 2)
        btn_lay.setSpacing(2)
        btn_lay.addStretch()

        ocr_act_btn_style = f"""
            QToolTip {{ 
                background-color: {theme._active['popup_bg']}; color: {theme._active['text']}; 
                border: black solid 1px; border-radius: 4px;
            }}
        """

        self._merge_btn = QPushButton()
        self._merge_btn.setToolTip("Merge with card above")
        self._merge_btn.setFixedSize(22, 18)
        self._merge_btn.setStyleSheet(ocr_act_btn_style)
        ic_merge = load_icon("merge")
        if not ic_merge.isNull():
            self._merge_btn.setIcon(ic_merge)
            self._merge_btn.setIconSize(QSize(12, 12))
        else:
            self._merge_btn.setText("↕")
        self._merge_btn.clicked.connect(lambda: self.merge_requested.emit(self))
        btn_lay.addWidget(self._merge_btn)

        edit_btn = QPushButton()
        edit_btn.setToolTip("Edit OCR text")
        edit_btn.setFixedSize(22, 18)
        edit_btn.setStyleSheet(ocr_act_btn_style)
        ic_edit = load_icon("edit")
        if not ic_edit.isNull():
            edit_btn.setIcon(ic_edit)
            edit_btn.setIconSize(QSize(12, 12))
        else:
            edit_btn.setText("✎")
        edit_btn.clicked.connect(self._start_edit)
        btn_lay.addWidget(edit_btn)

        copy_btn = QPushButton()
        copy_btn.setToolTip("Copy text")
        copy_btn.setFixedSize(22, 18)
        copy_btn.setStyleSheet(ocr_act_btn_style)
        ic_copy = load_icon("copy")
        if not ic_copy.isNull():
            copy_btn.setIcon(ic_copy)
            copy_btn.setIconSize(QSize(12, 12))
        else:
            copy_btn.setText("C")
        copy_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self._raw_text)
        )
        btn_lay.addWidget(copy_btn)

        dismiss_btn = QPushButton()
        dismiss_btn.setToolTip("Dismiss")
        dismiss_btn.setFixedSize(22, 18)
        dismiss_btn.setStyleSheet(ocr_act_btn_style)
        ic_dismiss = load_icon("remove")
        if not ic_dismiss.isNull():
            dismiss_btn.setIcon(ic_dismiss)
            dismiss_btn.setIconSize(QSize(12, 12))
        else:
            dismiss_btn.setText("✕")
        dismiss_btn.clicked.connect(lambda: self.dismiss_requested.emit(self))
        btn_lay.addWidget(dismiss_btn)

        self._jump_btn = QPushButton()
        self._jump_btn.setToolTip("Jump to source page")
        self._jump_btn.setFixedSize(22, 18)
        self._jump_btn.setStyleSheet(ocr_act_btn_style)
        ic_jump = load_icon("jump")
        if not ic_jump.isNull():
            self._jump_btn.setIcon(ic_jump)
            self._jump_btn.setIconSize(QSize(12, 12))
        else:
            self._jump_btn.setText("⤴")
        self._jump_btn.clicked.connect(lambda: self.jump_requested.emit(self))
        self._jump_btn.setVisible(False)
        btn_lay.addWidget(self._jump_btn)

        # documentSizeChanged fires after layout is complete — reliable for initial size
        self.browser.document().documentLayout().documentSizeChanged.connect(
            lambda _: self._fit_browser_height()
        )
        self._render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def set_segmentation(self, on: bool):
        self._segmentation_on = on
        self._hovered_word = ""
        self._render()

    def refresh_theme(self):
        """Re-apply theme styles and re-render text with current colours."""
        self.setStyleSheet(theme.CARD_STYLE)
        self.browser.setStyleSheet(theme.ocr_browser_stylesheet())
        self._render()

    def _render(self):
        raw = self._raw_text
        if self._segmentation_on:
            words = segment_japanese(raw)
            parts = []
            for word in words:
                esc = (word.replace("&","&amp;").replace("<","&lt;")
                           .replace(">","&gt;").replace('"',"&quot;"))
                if esc == self._hovered_word:
                    parts.append(
                        f'<a href="{esc}" style="color:{theme.WORD_HOVER};'
                        f'background-color:{theme.WORD_HOVER_BG};'
                        f'border-radius:3px;padding:0 2px;'
                        f'text-decoration:none;">{esc}</a>'
                    )
                else:
                    parts.append(
                        f'<a href="{esc}" style="color:{theme.WORD_COLOUR};'
                        f'text-decoration:none;">{esc}</a>'
                    )
            body = "".join(parts)
        else:
            esc = (raw.replace("&","&amp;").replace("<","&lt;")
                      .replace(">","&gt;"))
            body = f'<span style="color:{theme.TEXT_COLOUR};">{esc}</span>'

        font_style = "font-family:'Noto Serif JP',serif;font-size:18px;"
        html = f'<div style="{font_style}">{body}</div>'
        self.browser.setHtml(html)

    def _fit_browser_height(self):
        """Resize browser to content and position the button overlay at the bottom."""
        doc_h = int(self.browser.document().size().height())
        # Extra 24px bottom padding makes room for the button bar overlay
        h = max(doc_h + 28, 48)
        self.browser.setFixedHeight(h)
        self.setFixedHeight(h)
        # Position btn_bar at bottom-right of the card
        bw = self._btn_bar.sizeHint().width()
        self._btn_bar.setGeometry(0, h - 22, self.width(), 22)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_hover_changed(self, anchor: str):
        if not self._segmentation_on:
            return
        self._hovered_word = anchor
        self._last_hovered = anchor
        self._render()

    def _on_word_clicked(self, url):
        word = url.toString()
        if word:
            self.word_clicked.emit(word, self._raw_text)

    def _show_context_menu(self, pos):
        menu = self.browser.createStandardContextMenu()
        menu.setStyleSheet(f"""
            QMenu {{
                background: {theme._active['popup_bg']}; color: {theme._active['text']};
                border: 1px solid {theme._active['popup_border']};
            }}
            QMenu::item:selected {{ background: {theme.ACCENT}; color: #fff; }}
            QMenu::separator {{ background: {theme._active['popup_border']}; height: 1px; margin: 2px 8px; }}
        """)
        menu.addSeparator()
        if self._segmentation_on:
            lookup_word = self._last_hovered
        else:
            lookup_word = self.browser.textCursor().selectedText().strip()
        dict_act = QAction("📚  Look Up in Dictionary", self)
        dict_act.triggered.connect(
            lambda: self._do_lookup(lookup_word)
        )
        dict_act.setEnabled(bool(lookup_word))
        menu.addAction(dict_act)
        menu.addSeparator()
        for label, url_tpl in [
            ("🔍  Search Jisho",    "https://jisho.org/search/{}"),
            ("🐙  Search Takoboto", "https://takoboto.jp/?q={}"),
        ]:
            text = lookup_word or self._raw_text
            act  = QAction(label, self)
            act.triggered.connect(
                lambda _, u=url_tpl, t=text: webbrowser.open(u.format(url_quote(t)))
            )
            act.setEnabled(bool(text))
            menu.addAction(act)
        menu.exec(self.browser.viewport().mapToGlobal(pos))

    def _do_lookup(self, word: str):
        if word and self._dict_popup:
            self._dict_popup.show_word(word, QCursor.pos(),
                                       sentence=self._raw_text)

    # ── Merge ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self.height()
        if h > 0:
            self._btn_bar.setGeometry(0, h - 22, self.width(), 22)

    def absorb(self, other: "OCRCard"):
        """Append other's text to this card (merge down into self)."""
        self._raw_text = self._raw_text + " " + other._raw_text
        self._render()

    def set_merge_visible(self, visible: bool):
        self._merge_btn.setVisible(visible)

    def _start_edit(self):
        """Replace the browser with a plain text editor for correction."""
        if hasattr(self, "_edit_widget") and self._edit_widget.isVisible():
            return  # already editing

        from PyQt6.QtWidgets import QTextEdit as _QTextEdit
        self._edit_widget = _QTextEdit()
        self._edit_widget.setPlainText(self._raw_text)
        self._edit_widget.setFont(QFont("Noto Serif JP, serif", 14))
        self._edit_widget.setStyleSheet(
            f"background: {theme._active['input_bg']}; color: {theme._active['text']};"
            f" border: 2px solid {theme.ACCENT}; border-radius: 4px;"
            f" padding: 6px; font-size: 12pt;"
        )
        # Size it to roughly match the browser
        self._edit_widget.setFixedHeight(max(self.browser.height(), 60))

        # Done / Cancel buttons
        self._edit_btns = QWidget()
        edit_btn_lay = QHBoxLayout(self._edit_btns)
        edit_btn_lay.setContentsMargins(0, 2, 2, 4)
        edit_btn_lay.setSpacing(4)
        edit_btn_lay.addStretch()
        done_btn = QPushButton("Done")
        done_btn.setFixedWidth(54)
        done_btn.clicked.connect(self._commit_edit)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(54)
        cancel_btn.clicked.connect(self._cancel_edit)
        edit_btn_lay.addWidget(done_btn)
        edit_btn_lay.addWidget(cancel_btn)

        # Swap browser for editor
        layout = self.layout()
        self.browser.hide()
        self._btn_bar.hide()
        layout.addWidget(self._edit_widget)
        layout.addWidget(self._edit_btns)
        self._edit_widget.setFocus()

    def _commit_edit(self):
        """Apply the edited text and return to normal display."""
        new_text = self._edit_widget.toPlainText().strip()
        if new_text:
            self._raw_text = new_text
        self._teardown_edit()
        self._render()

    def _cancel_edit(self):
        """Discard edits and return to normal display."""
        self._teardown_edit()

    def _teardown_edit(self):
        """Remove the edit widgets and restore the browser."""
        if hasattr(self, "_edit_widget"):
            self._edit_widget.deleteLater()
            del self._edit_widget
        if hasattr(self, "_edit_btns"):
            self._edit_btns.deleteLater()
            del self._edit_btns
        self.browser.show()
        self._btn_bar.show()

    @property
    def raw_text(self) -> str:
        return self._raw_text

    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def source_rect(self) -> QRect | None:
        return self._source_rect

    def update_jump_visible(self, visible_pages: set[int]):
        """Show jump button only when this card's page is not currently visible."""
        self._jump_btn.setVisible(self._page_index not in visible_pages)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._source_rect:
            self.highlight_requested.emit(self)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.highlight_cleared.emit()


class OCRPanel(QWidget):
    jump_to_page      = pyqtSignal(int, QRect)  # page_index, source_rect
    highlight_on_page = pyqtSignal(int, QRect)  # page_index, source_rect
    highlight_clear   = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(280)
        self._segmentation_on = False
        self._dict_popup      = None
        self._app_settings    = None
        self._cards: list[OCRCard] = []
        self._current_page    = 0
        self._visible_pages: set[int] = {0}
        self._filter_mode     = "page"  # "page" | "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Header ──
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self.ocr_indicator = QLabel("⬤")
        self.ocr_indicator.setToolTip("OCR status: idle")
        self.ocr_indicator.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 8pt;")
        self.ocr_indicator.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self.ocr_indicator)

        self._title = QLabel("OCR / Text")
        self._title.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 9pt;")
        self._title.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self._title, stretch=1)

        # Filter toggle: Page / All
        self._filter_btn = QPushButton("Page")
        self._filter_btn.setCheckable(True)
        self._filter_btn.setChecked(False)
        self._filter_btn.setToolTip("Show OCR results for current page only, or all pages")
        self._filter_btn.setStyleSheet(theme.segment_btn_stylesheet())
        self._filter_btn.clicked.connect(self._toggle_filter)
        header_row.addWidget(self._filter_btn)

        self.seg_check = QPushButton("Segment")
        self.seg_check.setCheckable(True)
        self.seg_check.setChecked(False)
        self.seg_check.setToolTip("Tokenise text into words.\nClick a word to look it up.")
        self.seg_check.setStyleSheet(theme.segment_btn_stylesheet())
        self.seg_check.clicked.connect(self._on_seg_toggled)
        header_row.addWidget(self.seg_check)
        layout.addLayout(header_row)

        # ── Page count label ──
        self._page_count_lbl = QLabel("")
        self._page_count_lbl.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 8pt;")
        self._page_count_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._page_count_lbl)

        # ── Scroll area containing cards ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._card_container = QWidget()
        self._card_container.setStyleSheet("background: transparent;")
        self._card_lay = QVBoxLayout(self._card_container)
        self._card_lay.setContentsMargins(0, 0, 0, 0)
        self._card_lay.setSpacing(6)
        self._card_lay.addStretch()   # pushes cards toward the top

        self._scroll.setWidget(self._card_container)
        layout.addWidget(self._scroll, stretch=1)

        # ── Bottom bar ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {theme._active['text_muted']};
                border: 1px solid {theme._active['popup_border']}; border-radius: 4px;
                padding: 4px 8px; font-size: 9pt;
            }}
            QPushButton:hover {{
                background: {theme._active['hover_bg']}; color: {theme._active['text']};
                border-color: {theme.ACCENT};
            }}
        """)
        self.clear_btn.clicked.connect(self.clear_all)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # ── Settings wiring ───────────────────────────────────────────────────────

    def set_settings(self, app_settings: QSettings, main_window=None):
        self._app_settings = app_settings
        self._main_window  = main_window
        self._dict_popup   = DictPopup(app_settings, main_window=main_window)
        for card in self._cards:
            card._dict_popup = self._dict_popup

    # ── Filter toggle ────────────────────────────────────────────────────────

    def _toggle_filter(self):
        if self._filter_btn.isChecked():
            self._filter_mode = "all"
            self._filter_btn.setText("All")
        else:
            self._filter_mode = "page"
            self._filter_btn.setText("Page")
        self._apply_filter()

    def _apply_filter(self):
        """Show/hide cards based on filter mode and visible pages."""
        for card in self._cards:
            if self._filter_mode == "all":
                card.setVisible(True)
            else:
                card.setVisible(card.page_index in self._visible_pages)
        self._update_merge_buttons()
        self._update_page_count_label()

    def set_current_page(self, page_index: int, visible_pages: set | None = None):
        """Called by TakoReader when the page changes.
        visible_pages: set of page indices currently on screen (e.g. {4, 5} for a spread).
        Falls back to {page_index} if not provided."""
        self._current_page = page_index
        self._visible_pages = visible_pages or {page_index}
        if self._filter_mode == "page":
            self._apply_filter()
        self._update_page_count_label()
        for card in self._cards:
            card.update_jump_visible(self._visible_pages)

    def _update_page_count_label(self):
        """Update the label showing how many results exist for visible pages."""
        page_count = sum(1 for c in self._cards if c.page_index in self._visible_pages)
        total = len(self._cards)
        if total == 0:
            self._page_count_lbl.setText("")
        elif self._filter_mode == "page":
            self._page_count_lbl.setText(
                f"{page_count} result{'s' if page_count != 1 else ''} on this page"
                f" \u00b7 {total} total"
            )
        else:
            self._page_count_lbl.setText(
                f"{total} result{'s' if total != 1 else ''}"
            )

    # ── Segmentation ─────────────────────────────────────────────────────────

    def _on_seg_toggled(self):
        self._segmentation_on = self.seg_check.isChecked()
        for card in self._cards:
            card.set_segmentation(self._segmentation_on)

    # ── Card management ───────────────────────────────────────────────────────

    def _add_card(self, raw_text: str, page_index: int = 0,
                  source_rect: QRect | None = None):
        card = OCRCard(raw_text, self._segmentation_on,
                       self._dict_popup, page_index=page_index,
                       source_rect=source_rect,
                       parent=self._card_container)
        card.word_clicked.connect(self._on_card_word_clicked)
        card.merge_requested.connect(self._on_merge_requested)
        card.dismiss_requested.connect(self._on_dismiss_requested)
        card.jump_requested.connect(self._on_jump_requested)
        card.highlight_requested.connect(self._on_highlight_requested)
        card.highlight_cleared.connect(self._on_highlight_cleared)
        card.update_jump_visible(self._visible_pages)
        self._card_lay.insertWidget(0, card)
        self._cards.insert(0, card)
        self._apply_filter()
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _update_merge_buttons(self):
        """Only show merge button on visible cards that have a visible card above them."""
        visible = [c for c in self._cards if c.isVisible()]
        for card in self._cards:
            if card in visible:
                card.set_merge_visible(visible.index(card) > 0)
            else:
                card.set_merge_visible(False)

    def _on_card_word_clicked(self, word: str, sentence: str):
        if self._dict_popup:
            self._dict_popup.show_word(word, QCursor.pos(), sentence=sentence)
        self.status.setText(f"Looking up: {word}")

    def _on_merge_requested(self, card: OCRCard):
        visible = [c for c in self._cards if c.isVisible()]
        idx = visible.index(card) if card in visible else -1
        if idx <= 0:
            return
        above = visible[idx - 1]
        above.absorb(card)
        self._remove_card(card)

    def _on_dismiss_requested(self, card: OCRCard):
        self._remove_card(card)

    def _on_jump_requested(self, card: OCRCard):
        """Jump to the card's source page and flash the highlight."""
        if card.source_rect:
            self.jump_to_page.emit(card.page_index, card.source_rect)

    def _on_highlight_requested(self, card: OCRCard):
        """Highlight the card's source rect on the page (hover enter)."""
        if card.source_rect and card.page_index in self._visible_pages:
            self.highlight_on_page.emit(card.page_index, card.source_rect)

    def _on_highlight_cleared(self):
        """Clear highlight (hover leave)."""
        self.highlight_clear.emit()

    def _remove_card(self, card: OCRCard):
        if card in self._cards:
            self._cards.remove(card)
        self._card_lay.removeWidget(card)
        card.deleteLater()
        self._update_merge_buttons()
        self._update_page_count_label()

    def clear_all(self):
        for card in list(self._cards):
            self._card_lay.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self.status.setText("")
        self._update_page_count_label()

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_results(self, file_key: str):
        """Persist all OCR card data to QSettings under the given file key."""
        if not self._app_settings or not file_key:
            return
        import json as _json
        entries = []
        for card in reversed(self._cards):
            entry = {
                "page": card.page_index,
                "text": card.raw_text,
            }
            if card.source_rect:
                r = card.source_rect
                entry["rect"] = [r.x(), r.y(), r.width(), r.height()]
            entries.append(entry)
        self._app_settings.setValue(f"ocr_results/{file_key}",
                                    _json.dumps(entries, ensure_ascii=False))

    def load_results(self, file_key: str):
        """Restore OCR cards from QSettings for the given file key."""
        if not self._app_settings or not file_key:
            return
        import json as _json
        raw = self._app_settings.value(f"ocr_results/{file_key}", "")
        if not raw:
            return
        try:
            entries = _json.loads(raw)
        except Exception:
            return
        for entry in entries:
            rect = None
            if "rect" in entry:
                r = entry["rect"]
                rect = QRect(r[0], r[1], r[2], r[3])
            self._add_card(entry["text"],
                           page_index=entry.get("page", 0),
                           source_rect=rect)

    # ── Public API (called by TakoReader) ─────────────────────────────────────

    def set_text(self, text: str, page_index: int = 0,
                 source_rect: QRect | None = None):
        self._add_card(text, page_index=page_index, source_rect=source_rect)
        self.status.setText("\u2713 OCR complete")

    def set_ocr_state(self, state: str):
        styles = {
            "idle":    ("\u2b24", "#444",    "OCR status: idle"),
            "loading": ("\u2b24", "#e6a817", "OCR model loading\u2026"),
            "ready":   ("\u2b24", "#2ecc71", "OCR model ready"),
            "error":   ("\u2b24", "#e74c3c", "OCR failed to load"),
        }
        dot, colour, tip = styles.get(state, styles["idle"])
        self.ocr_indicator.setText(dot)
        self.ocr_indicator.setStyleSheet(f"color: {colour}; font-size: 8pt;")
        self.ocr_indicator.setToolTip(tip)

    def set_status(self, msg: str):
        self.status.setText(msg)

    def refresh_theme(self):
        self._title.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 9pt;")
        self._page_count_lbl.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 8pt;")
        self.seg_check.setStyleSheet(theme.segment_btn_stylesheet())
        self._filter_btn.setStyleSheet(theme.segment_btn_stylesheet())
        for card in self._cards:
            card.refresh_theme()
        if self._app_settings:
            self._dict_popup = DictPopup(self._app_settings,
                                         main_window=getattr(self, '_main_window', None))

    def lookup_shortcut(self):
        """Ctrl+D: look up last hovered word across all cards."""
        if self._segmentation_on:
            for card in self._cards:
                if card._last_hovered:
                    self._on_card_word_clicked(card._last_hovered, card.raw_text)
                    return
        else:
            for card in self._cards:
                sel = card.browser.textCursor().selectedText().strip()
                if sel:
                    self._on_card_word_clicked(sel, card.raw_text)
                    return

    # ── Reverse highlight (page hover → panel) ────────────────────────────

    def get_visible_regions(self) -> list[tuple[QRect, "OCRCard"]]:
        """Return (source_rect, card) for all cards on visible pages that have a rect."""
        regions = []
        for card in self._cards:
            if card.source_rect and card.page_index in self._visible_pages:
                regions.append((card.source_rect, card))
        return regions




# ─── Page preload worker ──────────────────────────────────────────────────────

class PagePreloadWorker(QThread):
    """Loads a list of page pixmaps (already in memory) through _apply_adjustments
    on a background thread so they are cache-warm by the time the user turns the page."""
    done = pyqtSignal()

    def __init__(self, indices: list, pages: list, get_display_fn):
        super().__init__()
        self._indices       = indices
        self._pages         = pages
        self._get_display   = get_display_fn

    def run(self):
        for i in self._indices:
            if 0 <= i < len(self._pages):
                try:
                    self._get_display(i)
                except Exception:
                    pass
        self.done.emit()


# ─── Bookmark Popup ──────────────────────────────────────────────

class BookmarkPopup(QWidget):
    navigate         = pyqtSignal(int)
    rename_requested = pyqtSignal(int, str)
    remove_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)
        self.setMaximumHeight(480)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.POPUP_STYLESHEET)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QWidget()
        self._header.setStyleSheet(theme.popup_header_stylesheet())
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(14, 10, 14, 10)
        self._title = QLabel("Bookmarks")
        self._title.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {theme._active['text']}; background: transparent; border: none;")
        hl.addWidget(self._title)
        outer.addWidget(self._header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent; border: none;")
        self._list_lay = QVBoxLayout(self._content)
        self._list_lay.setContentsMargins(12, 10, 12, 10)
        self._list_lay.setSpacing(6)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, stretch=1)

    def show_at(self, global_pos, bookmarks: list, current_page: int):
        self._populate(bookmarks, current_page)
        self._reposition(global_pos)
        self.show()

    def _populate(self, bookmarks: list, current_page: int):
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not bookmarks:
            empty = QLabel("No bookmarks yet.\nUse the bookmark button to add one.")
            empty.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 9pt; background: transparent; border: none;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_lay.addWidget(empty)
        else:
            for bm in sorted(bookmarks, key=lambda b: b["page"]):
                self._list_lay.addWidget(self._make_row(bm, current_page))
        self._list_lay.addStretch()
        self._resize()

    def _make_row(self, bm: dict, current_page: int) -> QWidget:
        page = bm["page"]
        name = bm.get("name", f"Page {page + 1}")
        card = QWidget()
        t = theme._active
        a = theme.ACCENT
        card.setStyleSheet(
            f"background: {t['hover_bg']}; border: 1px solid {a}; border-radius: 6px;"
            if page == current_page else
            f"background: {t['card_bg']}; border: 1px solid {t['popup_border']}; border-radius: 6px;"
        )
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(8)

        page_lbl = QLabel(f"p.{page + 1}")
        page_lbl.setFixedWidth(36)
        page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_lbl.setStyleSheet(
            f"color: {a}; font-size: 8pt; font-weight: bold;"
            f" background: transparent; border: none;"
        )
        cl.addWidget(page_lbl)

        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("Bookmark name…")
        name_edit.editingFinished.connect(
            lambda p=page, e=name_edit: self.rename_requested.emit(p, e.text())
        )
        cl.addWidget(name_edit, stretch=1)

        go_btn = QPushButton("Go")
        go_btn.setFixedWidth(36)
        go_btn.clicked.connect(lambda _, p=page: self._go(p))
        cl.addWidget(go_btn)

        del_btn = QPushButton("✕")
        del_btn.setFixedWidth(28)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {t['text_muted']};
                border: none; font-size: 10pt; padding: 0;
            }}
            QPushButton:hover {{ color: #e74c3c; background: transparent; }}
        """)
        del_btn.clicked.connect(lambda _, p=page: self.remove_requested.emit(p))
        cl.addWidget(del_btn)
        return card

    def _go(self, page: int):
        self.navigate.emit(page)
        self.hide()

    def _resize(self):
        self._content.adjustSize()
        h = min(self._content.sizeHint().height() + 56, self.maximumHeight())
        self.resize(self.width(), max(h, 120))

    def _reposition(self, pos):
        screen = QGuiApplication.screenAt(pos)
        sg = screen.availableGeometry() if screen \
             else QGuiApplication.primaryScreen().availableGeometry()
        x = pos.x() - self.width() // 2
        y = pos.y() + 8
        if x + self.width()  > sg.right():  x = sg.right()  - self.width()  - 4
        if x < sg.left():                   x = sg.left()   + 4
        if y + self.height() > sg.bottom(): y = pos.y() - self.height() - 8
        self.move(x, y)


# ─── Image Adjustments Popup ─────────────────────────────────────────────────

class ImageAdjustPopup(QWidget):
    """
    Floating popup with sliders for brightness, contrast, saturation, sharpness.
    Dismisses on click outside.
    """
    changed = pyqtSignal()  # emitted whenever any slider moves

    # (label, icon_name, attr, default, min, max, step)
    _CONTROLS = [
        ("Brightness", "adj-brightness", "brightness", 100, 0,   200, 1),
        ("Contrast",   "adj-contrast",   "contrast",   100, 0,   200, 1),
        ("Saturation", "adj-saturation", "saturation", 100, 0,   200, 1),
        ("Sharpness",  "adj-sharpness",  "sharpness",  100, 0,   200, 1),
        ("Warmth",     "warmth",        "warmth",       0, 0,   100, 1),
    ]

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setMinimumWidth(300)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            theme.POPUP_STYLESHEET +
            theme.slider_popup_stylesheet()
        )

        # Default values
        self.brightness = 100
        self.contrast   = 100
        self.saturation = 100
        self.sharpness  = 100
        self.warmth     = 0

        self._sliders: dict[str, QSlider] = {}
        self._val_labels: dict[str, QLabel] = {}
        self._icon_labels: list[tuple[QLabel, str]] = []  # (label, icon_name)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet(theme.popup_header_stylesheet())
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 10, 10, 10)
        title = QLabel("Image Adjustments")
        title.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {theme._active['text']}; background: transparent; border: none;")
        hl.addWidget(title, stretch=1)
        outer.addWidget(header)

        # Sliders
        body = QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 8)
        bl.setSpacing(12)

        from PyQt6.QtWidgets import QSlider
        for label, icon_name, attr, default, mn, mx, step in self._CONTROLS:
            row = QHBoxLayout()
            row.setSpacing(8)

            # Icon
            ic = load_icon(icon_name)
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(16, 16)
            if not ic.isNull():
                icon_lbl.setPixmap(ic.pixmap(16, 16))
            self._icon_labels.append((icon_lbl, icon_name))
            row.addWidget(icon_lbl)

            # Label
            name_lbl = QLabel(label)
            name_lbl.setFixedWidth(72)
            name_lbl.setStyleSheet(f"color: {theme._active['text_secondary']}; font-size: 9pt; background: transparent; border: none;")
            row.addWidget(name_lbl)

            # Slider
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(mn, mx)
            slider.setValue(default)
            slider.setSingleStep(step)
            slider.setPageStep(10)

            val_lbl = QLabel(f"{default}%")
            val_lbl.setFixedWidth(38)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 8pt; background: transparent; border: none;")

            def _on_change(v, a=attr, lbl=val_lbl):
                setattr(self, a, v)
                lbl.setText(f"{v}%")
                self.changed.emit()

            def _on_double_click(event, s=slider, d=default, a=attr, lbl=val_lbl):
                s.setValue(d)

            def _on_context_menu(pos, s=slider, d=default, a=attr, lbl=val_lbl):
                from PyQt6.QtWidgets import QMenu
                menu = QMenu(s)
                menu.setStyleSheet(f"""
                    QMenu {{ background: {theme._active['menu_bg']}; color: {theme._active['text']}; border: 1px solid {theme._active['border']}; }}
                    QMenu::item:selected {{ background: {theme.ACCENT}; }}
                """)
                reset_act = menu.addAction("Reset")
                if menu.exec(s.mapToGlobal(pos)) == reset_act:
                    s.setValue(d)

            slider.valueChanged.connect(_on_change)
            slider.mouseDoubleClickEvent = _on_double_click
            slider.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            slider.customContextMenuRequested.connect(_on_context_menu)
            self._sliders[attr]    = slider
            self._val_labels[attr] = val_lbl

            row.addWidget(slider, stretch=1)
            row.addWidget(val_lbl)
            bl.addLayout(row)

        # Reset button
        self._reset_btn = QPushButton()
        self._reset_btn.setToolTip("Reset all adjustments")
        ic_reset = load_icon("adj-reset")
        if not ic_reset.isNull():
            self._reset_btn.setIcon(ic_reset)
            self._reset_btn.setIconSize(QSize(14, 14))
            self._reset_btn.setText("")
            self._reset_btn.setFixedSize(28, 24)
        else:
            self._reset_btn.setText("Reset")
        self._reset_btn.clicked.connect(self.reset)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._reset_btn)
        bl.addLayout(btn_row)

        outer.addWidget(body)

    def reset(self):
        for _, _, attr, default, *_ in self._CONTROLS:
            self._sliders[attr].setValue(default)

    def load_values(self, brightness: int, contrast: int,
                    saturation: int, sharpness: int, warmth: int = 0):
        self._sliders["brightness"].setValue(brightness)
        self._sliders["contrast"].setValue(contrast)
        self._sliders["saturation"].setValue(saturation)
        self._sliders["sharpness"].setValue(sharpness)
        self._sliders["warmth"].setValue(warmth)

    def get_values(self) -> dict:
        return {
            "brightness": self.brightness,
            "contrast":   self.contrast,
            "saturation": self.saturation,
            "sharpness":  self.sharpness,
            "warmth":     self.warmth,
        }

    def show_at(self, global_pos):
        self.adjustSize()
        screen = QGuiApplication.screenAt(global_pos)
        sg = screen.availableGeometry() if screen              else QGuiApplication.primaryScreen().availableGeometry()
        x = global_pos.x() - self.width() // 2
        y = global_pos.y() + 6
        if x + self.width()  > sg.right():  x = sg.right()  - self.width()  - 4
        if x < sg.left():                   x = sg.left()   + 4
        if y + self.height() > sg.bottom(): y = global_pos.y() - self.height() - 6
        self.move(x, y)
        self.show()

    def refresh_theme(self):
        """Re-apply theme styles and reload icons."""
        self.setStyleSheet(theme.POPUP_STYLESHEET + theme.slider_popup_stylesheet())
        for icon_lbl, icon_name in self._icon_labels:
            ic = load_icon(icon_name)
            if not ic.isNull():
                icon_lbl.setPixmap(ic.pixmap(16, 16))
        ic_reset = load_icon("adj-reset")
        if not ic_reset.isNull():
            self._reset_btn.setIcon(ic_reset)


# ─── Marquee selection overlay ───────────────────────────────────────────────

class MarqueeOverlay(QWidget):
    """
    Transparent overlay over the page view for drawing a selection rectangle.
    Supports draw, move, and edge-resize. Shows confirm/cancel buttons below rect.
    Emits confirmed(QRect) with coordinates in overlay space, or cancelled().
    """
    confirmed  = pyqtSignal(QRect)
    cancelled  = pyqtSignal()

    _HANDLE   = 8    # handle size px
    _MIN_SIZE = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._rect      : QRect | None = None
        self._drawing   = False
        self._drag_start: QPoint | None = None
        self._drag_rect : QRect  | None = None
        self._resize_edge = None   # "tl","tr","bl","br","t","b","l","r" or None

        # Confirm / cancel buttons — hidden until a rect is drawn
        self._confirm_btn = QPushButton("✓", self)
        self._cancel_btn  = QPushButton("✕", self)
        for btn, bg, hover in [
            (self._confirm_btn, "#2ecc71", "#27ae60"),
            (self._cancel_btn,  "#e74c3c", "#c0392b"),
        ]:
            btn.setFixedSize(28, 24)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg}; color: #fff;
                    border: none; border-radius: 4px; font-size: 11pt;
                }}
                QPushButton:hover {{ background: {hover}; }}
            """)
            btn.hide()
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._cancel_btn.clicked.connect(self._on_cancel)

    # ── Public ────────────────────────────────────────────────────────────────

    def activate(self, cover_widget: "QWidget | None" = None):
        """Resize to cover parent viewport and raise to top."""
        target = cover_widget or self.parent()
        if target:
            self.setParent(target)
            self.resize(target.size())
            self.move(0, 0)
        self._rect = None
        self._confirm_btn.hide()
        self._cancel_btn.hide()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.show()
        self.raise_()
        self.setFocus()

    def deactivate(self):
        self.hide()
        self._rect = None
        self._confirm_btn.hide()
        self._cancel_btn.hide()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QRegion
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )

        if self._rect and abs(self._rect.width()) > 2 and abs(self._rect.height()) > 2:
            r = self._rect.normalized()
            # Paint vignette as 4 rects around the selection (never over it)
            full = self.rect()
            for vr in [
                QRect(full.left(),  full.top(),    full.width(),     r.top() - full.top()),
                QRect(full.left(),  r.bottom(),    full.width(),     full.bottom() - r.bottom()),
                QRect(full.left(),  r.top(),       r.left() - full.left(), r.height()),
                QRect(r.right(),    r.top(),       full.right() - r.right(), r.height()),
            ]:
                if vr.isValid():
                    painter.fillRect(vr, QColor(0, 0, 0, 100))
            # Blue semi-transparent fill inside selection
            painter.fillRect(r, QColor(53, 132, 228, 60))
            # Border
            painter.setPen(QPen(QColor(53, 132, 228), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)
            # Resize handles
            painter.setBrush(QBrush(QColor(53, 132, 228)))
            painter.setPen(Qt.PenStyle.NoPen)
            for hx, hy in self._handle_centers(r):
                h = self._HANDLE
                painter.drawRect(hx - h//2, hy - h//2, h, h)
        else:
            # No selection yet — light vignette over whole area
            painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

    def _handle_centers(self, r: QRect):
        cx, cy = r.center().x(), r.center().y()
        return [
            (r.left(),  r.top()),    (cx, r.top()),    (r.right(), r.top()),
            (r.left(),  cy),                            (r.right(), cy),
            (r.left(),  r.bottom()), (cx, r.bottom()), (r.right(), r.bottom()),
        ]

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def _hit_handle(self, pos: QPoint) -> str | None:
        if not self._rect:
            return None
        r = self._rect.normalized()
        H = self._HANDLE + 4
        cx, cy = r.center().x(), r.center().y()
        handles = {
            "tl": (r.left(), r.top()),    "t": (cx, r.top()),    "tr": (r.right(), r.top()),
            "l":  (r.left(), cy),                                  "r":  (r.right(), cy),
            "bl": (r.left(), r.bottom()), "b": (cx, r.bottom()), "br": (r.right(), r.bottom()),
        }
        for name, (hx, hy) in handles.items():
            if abs(pos.x() - hx) <= H and abs(pos.y() - hy) <= H:
                return name
        return None

    def _cursor_for_edge(self, edge: str | None):
        cursors = {
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t":  Qt.CursorShape.SizeVerCursor,
            "b":  Qt.CursorShape.SizeVerCursor,
            "l":  Qt.CursorShape.SizeHorCursor,
            "r":  Qt.CursorShape.SizeHorCursor,
        }
        return cursors.get(edge, Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        edge = self._hit_handle(pos)
        if edge:
            self._resize_edge  = edge
            self._drag_start   = pos
            self._drag_rect    = QRect(self._rect.normalized())
            return
        if self._rect and self._rect.normalized().contains(pos):
            self._drag_start = pos
            self._drag_rect  = QRect(self._rect.normalized())
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        # Start new rect
        self._drawing   = True
        self._rect      = QRect(pos, pos)
        self._drag_start = None
        self._confirm_btn.hide()
        self._cancel_btn.hide()
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._resize_edge and self._drag_start and self._drag_rect:
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            r  = QRect(self._drag_rect)
            e  = self._resize_edge
            if "l" in e: r.setLeft(r.left()   + dx)
            if "r" in e: r.setRight(r.right()  + dx)
            if "t" in e: r.setTop(r.top()     + dy)
            if "b" in e: r.setBottom(r.bottom() + dy)
            if r.width() >= self._MIN_SIZE and r.height() >= self._MIN_SIZE:
                self._rect = r
            self.update()
            return
        if self._drag_start and self._drag_rect and not self._drawing:
            delta = pos - self._drag_start
            self._rect = self._drag_rect.translated(delta)
            self.update()
            return
        if self._drawing and self._rect:
            self._rect.setBottomRight(pos)
            self.update()
            return
        # Hover cursor
        edge = self._hit_handle(pos)
        if edge:
            self.setCursor(self._cursor_for_edge(edge))
        elif self._rect and self._rect.normalized().contains(pos):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drawing    = False
        self._drag_start = None
        self._drag_rect  = None
        self._resize_edge = None
        if self._rect and abs(self._rect.width()) > self._MIN_SIZE                       and abs(self._rect.height()) > self._MIN_SIZE:
            self._position_buttons()
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
        elif event.key() == Qt.Key.Key_Return:
            self._on_confirm()

    # ── Button positioning ────────────────────────────────────────────────────

    def _position_buttons(self):
        if not self._rect:
            return
        r   = self._rect.normalized()
        gap = 6
        bw  = self._confirm_btn.width() + self._cancel_btn.width() + gap
        bx  = r.center().x() - bw // 2
        by  = min(r.bottom() + gap, self.height() - 30)
        self._confirm_btn.move(bx, by)
        self._cancel_btn.move(bx + self._confirm_btn.width() + gap, by)
        self._confirm_btn.show()
        self._cancel_btn.show()
        self._confirm_btn.raise_()
        self._cancel_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._rect:
            self._position_buttons()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_confirm(self):
        if self._rect:
            self.confirmed.emit(self._rect.normalized())
        self.deactivate()

    def _on_cancel(self):
        self.deactivate()
        self.cancelled.emit()


# ─── Thumbnail Strip ──────────────────────────────────────────────────────────

class ThumbnailList(QListWidget):
    page_selected = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(150)
        self.setIconSize(QSize(90, 120))
        self.setSpacing(4)
        self.setStyleSheet(theme.THUMBNAIL_SCROLLBAR_STYLESHEET)
        self.itemClicked.connect(lambda item: self.page_selected.emit(self.row(item)))

    def load_pages(self, pixmaps: list[QPixmap]):
        self.clear()
        for i, px in enumerate(pixmaps):
            thumb = px.scaled(90, 120, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            self.addItem(QListWidgetItem(QIcon(thumb), f"  {i+1}"))

    def select_page(self, index: int):
        self.setCurrentRow(index)

    def refresh_theme(self):
        self.setStyleSheet(theme.THUMBNAIL_SCROLLBAR_STYLESHEET)


