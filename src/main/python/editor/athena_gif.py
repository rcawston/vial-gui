import os
import threading

from PIL import Image, ImageSequence
from qt_compat.QtCore import Qt, QRectF, QPointF, QSizeF, QTimer, pyqtSignal
from qt_compat.QtGui import QPainter, QPen, QPixmap, QColor, QImage
from qt_compat.QtWidgets import QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QToolButton, QFileDialog, \
    QDialog, QSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QProgressBar, QMessageBox, QColorDialog, QFrame, QWidget, QSizePolicy, QMenu

from athena_qgf import encode_qgf, encode_uf2, decode_qgf_first_frame, decode_qgf_frames, qgf_first_frame_region_length, parse_qgf_header
from autorefresh.autorefresh import Autorefresh
from editor.basic_editor import BasicEditor
from util import tr, hid_send
from vial_device import VialKeyboard


ATHENA_VENDOR_ID = 0x9D5B
ATHENA_PRODUCT_ID = 0x4100
ATHENA_SLOT_ADDR = [
    0x10400000,
    0x10500000,
    0x10600000,
    0x10800000,
    0x10A00000,
    0x10C00000,
]
ATHENA_SLOT_LIMITS = [31, 31, 63, 63, 63, 63]

ATHENA_HID_PREFIX = 0xFD
ATHENA_GIF_GET_INFO = 0xA0
ATHENA_GIF_BEGIN_UPLOAD = 0xA1
ATHENA_GIF_WRITE_CHUNK = 0xA2
ATHENA_GIF_FINISH_UPLOAD = 0xA3
ATHENA_GIF_GET_SLOT_INFO = 0xA5
ATHENA_GIF_READ_CHUNK = 0xA6

ATHENA_SLOT_NAMES = ["GIF0 CapsLock", "GIF1 Typing", "GIF2", "GIF3", "GIF4", "GIF5"]

ATHENA_STATUS_TEXT = {
    0: "OK",
    1: "Bad command",
    2: "Bad slot",
    3: "Bad size",
    4: "No active upload",
    5: "Bad offset",
    6: "Bad chunk length",
    7: "Flash error",
    8: "Invalid QGF",
}


def athena_send(dev, payload, retries=3, timeout_ms=2000):
    return hid_send(dev, payload, retries=retries, timeout_ms=timeout_ms)


def athena_check_status(resp):
    status = resp[2]
    if status != 0:
        raise RuntimeError(ATHENA_STATUS_TEXT.get(status, "Unknown error"))
    return resp


def athena_upload(dev, slot, qgf_bytes, activate, progress_cb, log_cb):
    resp = athena_send(dev, bytes([ATHENA_HID_PREFIX, ATHENA_GIF_GET_INFO]), retries=3, timeout_ms=2000)
    athena_check_status(resp)
    flash_size = int.from_bytes(resp[6:10], byteorder="little")
    log_cb("Keyboard flash size: {} MB".format(flash_size // (1024 * 1024)))

    begin = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_BEGIN_UPLOAD, slot]) + len(qgf_bytes).to_bytes(4, byteorder="little")
    athena_check_status(athena_send(dev, begin, retries=1, timeout_ms=3000))

    chunk_size = 25
    total = len(qgf_bytes)
    for offset in range(0, total, chunk_size):
        chunk = qgf_bytes[offset:offset + chunk_size]
        msg = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_WRITE_CHUNK]) + offset.to_bytes(4, byteorder="little") + bytes([len(chunk)]) + chunk
        athena_check_status(athena_send(dev, msg, retries=1, timeout_ms=3000))
        progress_cb((offset + len(chunk)) / total)

    finish = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_FINISH_UPLOAD, 1 if activate else 0])
    athena_check_status(athena_send(dev, finish, retries=1, timeout_ms=3000))


def athena_get_slot_info(dev, slot):
    resp = athena_send(dev, bytes([ATHENA_HID_PREFIX, ATHENA_GIF_GET_SLOT_INFO, slot]), retries=2, timeout_ms=2000)
    athena_check_status(resp)
    return {
        "slot": resp[3],
        "valid": bool(resp[4]),
        "active": bool(resp[5]),
        "slot_size": int.from_bytes(resp[6:10], byteorder="little"),
        "total_size": int.from_bytes(resp[10:14], byteorder="little"),
        "width": int.from_bytes(resp[14:16], byteorder="little"),
        "height": int.from_bytes(resp[16:18], byteorder="little"),
        "frame_count": int.from_bytes(resp[18:20], byteorder="little"),
    }


def athena_read_slot_bytes(dev, slot, offset, length, progress_cb=None):
    data = bytearray()
    while len(data) < length:
        request_len = min(24, length - len(data))
        payload = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_READ_CHUNK, slot]) + (offset + len(data)).to_bytes(4, byteorder="little") + bytes([request_len])
        resp = athena_send(dev, payload, retries=2, timeout_ms=2000)
        athena_check_status(resp)
        actual_len = resp[3]
        if actual_len == 0:
            raise RuntimeError("Empty readback chunk")
        data.extend(resp[4:4 + actual_len])
        if progress_cb is not None and length > 0:
            progress_cb(len(data) / length)
    return bytes(data[:length])


def athena_read_slot_preview(dev, slot):
    data = athena_read_slot_bytes(dev, slot, 0, 64)
    while True:
        try:
            required = qgf_first_frame_region_length(data)
            break
        except ValueError as exc:
            if "truncated" not in str(exc):
                raise
            next_len = len(data) + 256
            data = athena_read_slot_bytes(dev, slot, 0, next_len)
    if required > len(data):
        data = athena_read_slot_bytes(dev, slot, 0, required)
    image, header = decode_qgf_first_frame(data[:required])
    return image, header


def pil_to_qpixmap(image):
    image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)


class CropPreviewLabel(QLabel):
    selectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self._image = None
        self._pixmap = None
        self._selection = None
        self._drag_origin = None
        self._image_rect = QRectF()

    def set_image(self, image, preserve_selection=False):
        existing_selection = self._selection
        self._image = image
        self._pixmap = pil_to_qpixmap(image) if image is not None else None
        if image is None:
            self._selection = None
        elif preserve_selection and existing_selection is not None:
            rect = existing_selection.normalized()
            side = min(rect.width(), rect.height(), image.width, image.height)
            x = max(0.0, min(image.width - side, rect.left()))
            y = max(0.0, min(image.height - side, rect.top()))
            self._selection = QRectF(x, y, side, side)
        else:
            side = max(1, min(image.width, image.height) - 2)
            x = max(0, (image.width - side) // 2)
            y = max(0, (image.height - side) // 2)
            self._selection = QRectF(x, y, side, side)
        self.update()
        self.selectionChanged.emit()

    def crop_box(self):
        if self._image is None or self._selection is None:
            return None
        rect = self._selection.normalized()
        return (
            int(rect.left()),
            int(rect.top()),
            int(rect.left() + rect.width()),
            int(rect.top() + rect.height()),
        )

    def _display_rect(self):
        if self._pixmap is None:
            return QRectF()
        target = QRectF(self.rect())
        source = QRectF(self._pixmap.rect())
        scaled_size = QSizeF(source.size())
        scaled_size.scale(target.size(), Qt.KeepAspectRatio)
        scaled = QRectF(QPointF(0, 0), scaled_size)
        scaled.moveCenter(target.center())
        return scaled

    def _widget_to_image(self, point):
        display = self._display_rect()
        if display.isNull() or self._image is None:
            return QPointF()
        x = (point.x() - display.left()) * self._image.width / display.width()
        y = (point.y() - display.top()) * self._image.height / display.height()
        x = max(0.0, min(self._image.width - 1, x))
        y = max(0.0, min(self._image.height - 1, y))
        return QPointF(x, y)

    def mousePressEvent(self, event):
        if self._image is None or event.button() != Qt.LeftButton:
            return
        self._drag_origin = self._widget_to_image(event.pos())
        self._selection = QRectF(self._drag_origin, self._drag_origin)
        self.update()

    def mouseMoveEvent(self, event):
        if self._image is None or self._drag_origin is None:
            return
        current = self._widget_to_image(event.pos())
        dx = current.x() - self._drag_origin.x()
        dy = current.y() - self._drag_origin.y()
        side = max(abs(dx), abs(dy))
        sx = self._drag_origin.x() - side if dx < 0 else self._drag_origin.x()
        sy = self._drag_origin.y() - side if dy < 0 else self._drag_origin.y()
        rect = QRectF(sx, sy, side, side)
        rect = rect.intersected(QRectF(0, 0, self._image.width, self._image.height))
        side = min(rect.width(), rect.height())
        rect.setWidth(side)
        rect.setHeight(side)
        self._selection = rect
        self.update()
        self.selectionChanged.emit()

    def mouseReleaseEvent(self, event):
        if self._drag_origin is None:
            return
        self._drag_origin = None
        self.selectionChanged.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap is None:
            return

        painter = QPainter(self)
        display = self._display_rect()
        self._image_rect = display
        painter.drawPixmap(display, self._pixmap, QRectF(self._pixmap.rect()))

        if self._selection is not None and self._image is not None:
            sx = display.left() + (self._selection.left() * display.width() / self._image.width)
            sy = display.top() + (self._selection.top() * display.height() / self._image.height)
            sw = self._selection.width() * display.width() / self._image.width
            sh = self._selection.height() * display.height() / self._image.height
            selection_rect = QRectF(sx, sy, sw, sh)
            overlay = QColor(0, 0, 0, 110)
            painter.fillRect(QRectF(display.left(), display.top(), display.width(), max(0.0, selection_rect.top() - display.top())), overlay)
            painter.fillRect(QRectF(display.left(), selection_rect.bottom(), display.width(), max(0.0, display.bottom() - selection_rect.bottom())), overlay)
            painter.fillRect(QRectF(display.left(), selection_rect.top(), max(0.0, selection_rect.left() - display.left()), selection_rect.height()), overlay)
            painter.fillRect(QRectF(selection_rect.right(), selection_rect.top(), max(0.0, display.right() - selection_rect.right()), selection_rect.height()), overlay)
            painter.setPen(QPen(QColor("#ff6a00"), 2))
            painter.drawRect(selection_rect)


class AthenaGifEditor(BasicEditor):
    log_signal = pyqtSignal(object)
    progress_signal = pyqtSignal(object)
    done_signal = pyqtSignal(object)
    error_signal = pyqtSignal(object)
    slot_result_signal = pyqtSignal(object, object, object)
    slot_refresh_state_signal = pyqtSignal(object)
    save_done_signal = pyqtSignal(object)

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.frames = []
        self.durations = []
        self.source_path = ""
        self.raw_qgf = None
        self.raw_qgf_header = None
        self.raw_qgf_delays = []
        self.preview_frames = []
        self.preview_durations = []
        self.preview_index = 0
        self.preview_timer = QTimer()
        self.preview_timer.timeout.connect(self._advance_preview_frame)
        self.background_color = QColor(0, 0, 0)
        self.slot_cards = []
        self.slot_refresh_active = False
        self.transfer_active = False

        self.log_signal.connect(self._on_log)
        self.progress_signal.connect(self._on_progress)
        self.done_signal.connect(self._on_done)
        self.error_signal.connect(self._on_error)
        self.slot_result_signal.connect(self._on_slot_result)
        self.slot_refresh_state_signal.connect(self._on_slot_refresh_state)
        self.save_done_signal.connect(self._on_save_done)

        gallery_panel, gallery_layout = self._make_panel("Keyboard slots", with_header_row=True)
        gallery_header = gallery_layout.itemAt(0).layout()
        gallery_header.addStretch(1)
        self.btn_refresh_slots = QToolButton()
        self.btn_refresh_slots.setText("Refresh slots")
        self.btn_refresh_slots.clicked.connect(self.refresh_slots_async)
        self.btn_refresh_slots.setProperty("role", "secondary")
        gallery_header.addWidget(self.btn_refresh_slots)

        self.slot_grid = QGridLayout()
        self.slot_grid.setHorizontalSpacing(12)
        self.slot_grid.setVerticalSpacing(12)
        for idx, name in enumerate(ATHENA_SLOT_NAMES):
            card = self._create_slot_card(idx, name)
            self.slot_cards.append(card)
            self.slot_grid.addWidget(card["frame"], idx // 3, idx % 3)
        gallery_layout.addLayout(self.slot_grid)
        self.addWidget(gallery_panel)

        content_row = QHBoxLayout()

        left_panel, left_layout = self._make_panel("Create and upload")

        file_row = QHBoxLayout()
        self.txt_path = QLineEdit()
        self.txt_path.setReadOnly(True)
        file_row.addWidget(self.txt_path)
        self.btn_select = QToolButton()
        self.btn_select.setText(tr("AthenaGif", "Select GIF/QGF..."))
        self.btn_select.clicked.connect(self.on_select_file)
        self.btn_select.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_select.setProperty("role", "primary")
        file_row.addWidget(self.btn_select)
        left_layout.addLayout(file_row)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        controls.addWidget(QLabel("Slot"), 0, 0)
        self.combo_slot = QComboBox()
        for idx, name in enumerate(["GIF0 CapsLock", "GIF1 Typing", "GIF2", "GIF3", "GIF4", "GIF5"]):
            self.combo_slot.addItem(name, idx)
        self.combo_slot.currentIndexChanged.connect(self.update_preview)
        controls.addWidget(self.combo_slot, 0, 1)

        controls.addWidget(QLabel("Start frame"), 0, 2)
        self.spin_start = QSpinBox()
        self.spin_start.setMinimum(1)
        self.spin_start.valueChanged.connect(self.update_preview)
        controls.addWidget(self.spin_start, 0, 3)

        controls.addWidget(QLabel("End frame"), 0, 4)
        self.spin_end = QSpinBox()
        self.spin_end.setMinimum(1)
        self.spin_end.valueChanged.connect(self.update_preview)
        controls.addWidget(self.spin_end, 0, 5)

        self.chk_half = QCheckBox("Half frame rate")
        self.chk_half.stateChanged.connect(self.update_preview)
        controls.addWidget(self.chk_half, 1, 0, 1, 2)

        self.chk_activate = QCheckBox("Activate after upload")
        self.chk_activate.setChecked(True)
        controls.addWidget(self.chk_activate, 1, 2, 1, 2)

        controls.addWidget(QLabel("Background"), 1, 4)
        self.btn_background = QToolButton()
        self.btn_background.clicked.connect(self.on_select_background)
        controls.addWidget(self.btn_background, 1, 5)
        self._update_background_button()

        self.lbl_summary = QLabel("No GIF loaded")
        controls.addWidget(self.lbl_summary, 2, 0, 1, 6)
        left_layout.addLayout(controls)

        crop_row = QHBoxLayout()
        crop_col = QVBoxLayout()
        crop_col.addWidget(QLabel("Crop area"))
        self.cropper = CropPreviewLabel()
        self.cropper.selectionChanged.connect(self.update_preview)
        crop_col.addWidget(self.cropper)
        crop_row.addLayout(crop_col, 3)

        preview_col = QVBoxLayout()
        preview_col.addWidget(QLabel("Device preview"))
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(220, 220)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet("QLabel { background: #111418; border: 1px solid #2f343d; border-radius: 8px; }")
        preview_col.addWidget(self.preview_label, 1)

        preview_note = QLabel("128 x 128 processed output")
        preview_note.setProperty("muted", True)
        preview_col.addWidget(preview_note)
        crop_row.addLayout(preview_col, 2)
        left_layout.addLayout(crop_row, 1)

        buttons = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        buttons.addWidget(self.progress, 1)
        self.btn_save = QToolButton()
        self.btn_save.setText("Create UF2")
        self.btn_save.clicked.connect(self.on_save_uf2)
        self.btn_save.setProperty("role", "secondary")
        buttons.addWidget(self.btn_save)
        self.btn_upload = QToolButton()
        self.btn_upload.setText("Upload")
        self.btn_upload.clicked.connect(self.on_upload)
        self.btn_upload.setProperty("role", "primary")
        buttons.addWidget(self.btn_upload)
        left_layout.addLayout(buttons)

        right_panel, right_layout = self._make_panel("Transfer log")
        help_text = QLabel("Use the slot gallery to inspect what is on the keyboard now. Upload writes the selected slot directly over HID.")
        help_text.setWordWrap(True)
        help_text.setProperty("muted", True)
        right_layout.addWidget(help_text)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        right_layout.addWidget(self.log_box, 1)

        content_row.addWidget(left_panel, 3)
        content_row.addWidget(right_panel, 2)
        self.addLayout(content_row)

        self._apply_styles([
            gallery_panel,
            left_panel,
            right_panel,
            self.cropper,
            self.preview_label,
            self.log_box,
            self.txt_path,
            self.combo_slot,
            self.spin_start,
            self.spin_end,
            self.progress,
        ])

    def _apply_styles(self, widgets):
        stylesheet = """
            QFrame[panel="true"] {
                background: #2c2f34;
                border: 1px solid #454b55;
                border-radius: 10px;
            }
            QLabel[muted="true"] {
                color: #b6bcc7;
            }
            QToolButton[role="primary"] {
                background: #2f7df6;
                color: white;
                border: 1px solid #4d91f8;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QToolButton[role="secondary"] {
                background: #3a3f46;
                color: #eef2f7;
                border: 1px solid #575e69;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                background: #1f2329;
                border: 1px solid #4a505a;
                border-radius: 6px;
                padding: 4px 6px;
            }
            QProgressBar {
                background: #1f2329;
                border: 1px solid #4a505a;
                border-radius: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #2f7df6;
                border-radius: 6px;
            }
        """
        for widget in widgets:
            widget.setStyleSheet(stylesheet)

    def _make_panel(self, title, with_header_row=False):
        frame = QFrame()
        frame.setProperty("panel", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        label = QLabel(title)
        label.setStyleSheet("font-weight: 600; font-size: 13px;")
        if with_header_row:
            header = QHBoxLayout()
            header.addWidget(label)
            layout.addLayout(header)
        else:
            layout.addWidget(label)
        return frame, layout

    def _create_slot_card(self, idx, name):
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("""
            QFrame {
                background: #262a30;
                border: 1px solid #4a505a;
                border-radius: 8px;
                padding: 6px;
            }
        """)

        layout = QVBoxLayout()
        title = QLabel(name)
        title.setStyleSheet("QLabel { font-weight: bold; }")
        layout.addWidget(title)

        thumb = QLabel("No preview")
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setMinimumSize(120, 120)
        thumb.setStyleSheet("QLabel { background: #14171b; border: 1px solid #3b414b; border-radius: 6px; }")
        layout.addWidget(thumb)

        actions = QHBoxLayout()

        use_button = QToolButton()
        use_button.setText("Use slot")
        use_button.setProperty("role", "secondary")
        use_button.clicked.connect(lambda _checked=False, x=idx: self.combo_slot.setCurrentIndex(x))
        actions.addWidget(use_button)

        save_qgf_button = QToolButton()
        save_qgf_button.setText("Save QGF")
        save_qgf_button.setProperty("role", "secondary")
        save_qgf_button.clicked.connect(lambda _checked=False, x=idx: self.on_save_slot_qgf(x))
        actions.addWidget(save_qgf_button)

        save_gif_button = QToolButton()
        save_gif_button.setText("Save GIF")
        save_gif_button.setProperty("role", "secondary")
        save_gif_button.clicked.connect(lambda _checked=False, x=idx: self.on_save_slot_gif(x))
        actions.addWidget(save_gif_button)

        save_uf2_button = QToolButton()
        save_uf2_button.setText("Save UF2")
        save_uf2_button.setProperty("role", "secondary")
        save_uf2_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        uf2_menu = QMenu(save_uf2_button)
        for target_slot in range(len(ATHENA_SLOT_NAMES)):
            action = uf2_menu.addAction("Save for GIF{}".format(target_slot))
            action.triggered.connect(lambda _checked=False, source=idx, target=target_slot: self.on_save_slot_uf2(source, target))
        save_uf2_button.setMenu(uf2_menu)
        actions.addWidget(save_uf2_button)

        layout.addLayout(actions)

        frame.setLayout(layout)
        return {"frame": frame, "title": title, "thumb": thumb, "use_button": use_button,
                "save_qgf_button": save_qgf_button, "save_gif_button": save_gif_button, "save_uf2_button": save_uf2_button}

    def _set_slot_card(self, idx, info, image=None):
        card = self.slot_cards[idx]
        title = ATHENA_SLOT_NAMES[idx]
        if info.get("valid"):
            title += " - {}x{} - {}f - {} KB".format(
                info["width"],
                info["height"],
                info["frame_count"],
                max(1, info["total_size"] // 1024),
            )
        else:
            title += " - Empty / invalid"
        card["title"].setText(title)
        card["frame"].setStyleSheet("""
            QFrame {
                background: #262a30;
                border: 1px solid #4a505a;
                border-radius: 8px;
                padding: 6px;
            }
        """)
        if image is not None:
            pixmap = pil_to_qpixmap(image)
            card["thumb"].setPixmap(pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            card["thumb"].setText("")
        else:
            card["thumb"].clear()
            card["thumb"].setText("Empty")

    def refresh_slots(self):
        if not self.valid():
            for idx in range(len(self.slot_cards)):
                self._set_slot_card(idx, {"valid": False, "active": False})
            return

        for idx in range(len(self.slot_cards)):
            try:
                info = athena_get_slot_info(self.device.dev, idx)
                image = None
                if info["valid"]:
                    image, _header = athena_read_slot_preview(self.device.dev, idx)
                self._set_slot_card(idx, info, image=image)
            except Exception as exc:
                self._set_slot_card(idx, {"valid": False, "active": False})
                self.log("Slot {} refresh failed: {}".format(idx, exc))

    def refresh_slots_async(self):
        if self.slot_refresh_active:
            return
        if not self.valid():
            self.refresh_slots()
            return

        self.slot_refresh_state_signal.emit(True)
        threading.Thread(target=self._refresh_slots_worker, daemon=True).start()

    def _refresh_slots_worker(self):
        try:
            with Autorefresh.lock():
                for idx in range(len(self.slot_cards)):
                    info = athena_get_slot_info(self.device.dev, idx)
                    image = None
                    if info["valid"]:
                        try:
                            image, _header = athena_read_slot_preview(self.device.dev, idx)
                        except Exception as exc:
                            self.log_signal.emit("GIF{} preview unavailable: {}".format(idx, exc))
                    self.slot_result_signal.emit(idx, info, image)
        except Exception as exc:
            self.log_signal.emit("Slot refresh failed: {}".format(exc))
        finally:
            self.slot_refresh_state_signal.emit(False)

    def valid(self):
        return isinstance(self.device, VialKeyboard) and \
            self.device.desc["vendor_id"] == ATHENA_VENDOR_ID and \
            self.device.desc["product_id"] == ATHENA_PRODUCT_ID

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return
        self.refresh_slots_async()

    def on_select_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self.btn_select.window(),
            tr("AthenaGif", "Select GIF/QGF"),
            "",
            "Animation files (*.gif *.qgf *.qcf);;GIF files (*.gif);;QGF files (*.qgf *.qcf)",
        )
        if not filename:
            return
        self.source_path = filename
        self.txt_path.setText(self.source_path)
        try:
            self._load_source(self.source_path)
        except Exception as exc:
            QMessageBox.warning(self.btn_select.window(), "", str(exc))

    def _load_source(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in [".qgf", ".qcf"]:
            self._load_qgf(path)
        else:
            self._load_gif(path)

    def _load_gif(self, path):
        self.raw_qgf = None
        self.raw_qgf_header = None
        self.raw_qgf_delays = []
        image = Image.open(path)
        self.frames = []
        self.durations = []
        for frame in ImageSequence.Iterator(image):
            copy = frame.copy().convert("RGBA")
            self.frames.append(copy)
            self.durations.append(int(frame.info.get("duration", image.info.get("duration", 100))))

        if not self.frames:
            raise RuntimeError("GIF contains no frames")

        self.spin_start.setMaximum(len(self.frames))
        self.spin_end.setMaximum(len(self.frames))
        self.spin_start.setValue(1)
        self.spin_end.setValue(len(self.frames))
        self.cropper.set_image(self._flatten_frame(self.frames[0]))
        self.log("Loaded GIF with {} frames".format(len(self.frames)))
        self.update_preview()

    def _load_qgf(self, path):
        with open(path, "rb") as inf:
            self.raw_qgf = inf.read()
        frames, delays, self.raw_qgf_header = decode_qgf_frames(self.raw_qgf)
        self.raw_qgf_delays = delays
        self.frames = []
        self.durations = []
        self.cropper.set_image(frames[0])
        self.preview_frames = [pil_to_qpixmap(frame) for frame in frames]
        self.preview_durations = [max(20, int(delay)) for delay in delays]
        self.preview_index = 0
        self._render_preview_frame()
        if len(self.preview_frames) > 1:
            self.preview_timer.start(self.preview_durations[0])
        else:
            self.preview_timer.stop()
        self.lbl_summary.setText("QGF/QCF loaded: {}x{} • {}f • {} KB".format(
            self.raw_qgf_header["width"],
            self.raw_qgf_header["height"],
            self.raw_qgf_header["frame_count"],
            max(1, self.raw_qgf_header["total_size"] // 1024),
        ))
        self.log("Loaded QGF/QCF payload with {} frames".format(self.raw_qgf_header["frame_count"]))

    def _update_background_button(self):
        self.btn_background.setText(self.background_color.name().upper())
        self.btn_background.setStyleSheet(
            "QToolButton { background-color: %s; color: %s; }" % (
                self.background_color.name(),
                "#000000" if self.background_color.lightness() > 127 else "#ffffff",
            )
        )

    def on_select_background(self):
        color = QColorDialog.getColor(self.background_color, self.btn_background.window(), tr("AthenaGif", "Select Background Color"))
        if not color.isValid():
            return
        self.background_color = color
        self._update_background_button()
        if self.frames:
            self.cropper.set_image(self._flatten_frame(self.frames[0]), preserve_selection=True)
            self.update_preview()

    def _flatten_frame(self, frame):
        background = Image.new("RGBA", frame.size, (
            self.background_color.red(),
            self.background_color.green(),
            self.background_color.blue(),
            255,
        ))
        background.alpha_composite(frame.convert("RGBA"))
        return background.convert("RGB")

    def _selected_slot(self):
        return self.combo_slot.currentData()

    def _frame_limit(self):
        return ATHENA_SLOT_LIMITS[self._selected_slot()]

    def _build_processed_frames(self):
        if self.raw_qgf is not None:
            raise RuntimeError("Crop and frame editing are only available for GIF sources")
        if not self.frames:
            raise RuntimeError("No GIF loaded")

        start = self.spin_start.value() - 1
        end = self.spin_end.value()
        if end <= start:
            raise RuntimeError("End frame must be after start frame")

        box = self.cropper.crop_box()
        if box is None:
            raise RuntimeError("No crop selected")

        frames = self.frames[start:end]
        durations = self.durations[start:end]

        if self.chk_half.isChecked():
            half_frames = []
            half_durations = []
            for idx in range(0, len(frames), 2):
                half_frames.append(frames[idx])
                delay = durations[idx]
                if idx + 1 < len(durations):
                    delay += durations[idx + 1]
                else:
                    delay *= 2
                half_durations.append(delay)
            frames = half_frames
            durations = half_durations

        if len(frames) > self._frame_limit():
            raise RuntimeError("Selected slot supports at most {} frames".format(self._frame_limit()))

        out_frames = []
        for frame in frames:
            flattened = self._flatten_frame(frame)
            cropped = flattened.crop(box)
            resized = cropped.resize((128, 128), Image.LANCZOS).convert("RGB")
            out_frames.append(resized)
        return out_frames, durations

    def update_preview(self):
        if self.raw_qgf is not None:
            return
        if not self.frames:
            return
        try:
            frames, durations = self._build_processed_frames()
        except Exception as exc:
            self.preview_timer.stop()
            self.preview_frames = []
            self.preview_durations = []
            self.preview_label.clear()
            self.lbl_summary.setText(str(exc))
            return

        self.preview_frames = [pil_to_qpixmap(frame) for frame in frames]
        self.preview_durations = [max(20, int(delay)) for delay in durations]
        self.preview_index = 0
        self._render_preview_frame()
        self.lbl_summary.setText("{} frames, {:.1f}s".format(len(frames), sum(durations) / 1000.0))
        if len(self.preview_frames) > 1:
            self.preview_timer.start(self.preview_durations[0])
        else:
            self.preview_timer.stop()

    def _render_preview_frame(self):
        if not self.preview_frames:
            self.preview_label.clear()
            return
        preview = self.preview_frames[self.preview_index]
        self.preview_label.setPixmap(preview.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _advance_preview_frame(self):
        if not self.preview_frames:
            self.preview_timer.stop()
            return
        self.preview_index = (self.preview_index + 1) % len(self.preview_frames)
        self._render_preview_frame()
        self.preview_timer.start(self.preview_durations[self.preview_index])

    def _build_qgf(self):
        if self.raw_qgf is not None:
            return self.raw_qgf
        frames, durations = self._build_processed_frames()
        return encode_qgf(frames, durations, use_rle=True, use_deltas=True)

    def on_save_uf2(self):
        if not self.frames:
            return
        try:
            qgf = self._build_qgf()
        except Exception as exc:
            QMessageBox.warning(None, "", str(exc))
            return

        slot = self._selected_slot()
        uf2 = encode_uf2(qgf, ATHENA_SLOT_ADDR[slot])
        base = os.path.splitext(os.path.basename(self.source_path or "athena"))[0]
        filename, _ = QFileDialog.getSaveFileName(
            self.btn_save.window(),
            tr("AthenaGif", "Save UF2"),
            "{}_gif{}.uf2".format(base, slot),
            "UF2 files (*.uf2)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".uf2"):
            filename += ".uf2"
        with open(filename, "wb") as outf:
            outf.write(uf2)
        self.log("Saved UF2 for slot GIF{}".format(slot))

    def _download_slot_qgf(self, slot, progress_cb=None):
        with Autorefresh.lock():
            info = athena_get_slot_info(self.device.dev, slot)
            if not info["valid"]:
                raise RuntimeError("GIF{} is empty or invalid".format(slot))
            return athena_read_slot_bytes(self.device.dev, slot, 0, info["total_size"], progress_cb=progress_cb)

    def _start_slot_save_worker(self, source_slot, filename, mode, target_slot):
        if self.transfer_active:
            return
        self.transfer_active = True
        self.main.lock_ui()
        self.progress.setValue(0)
        if mode == "qgf":
            self.log("Downloading GIF{} as QGF...".format(source_slot))
        else:
            self.log("Downloading GIF{} and saving UF2 for GIF{}...".format(source_slot, target_slot))
        threading.Thread(
            target=lambda: self._save_slot_worker(source_slot, filename, mode, target_slot),
            daemon=True,
        ).start()

    def _save_slot_worker(self, source_slot, filename, mode, target_slot):
        try:
            payload = self._download_slot_qgf(source_slot, progress_cb=self.on_progress)
            if mode == "uf2":
                payload = encode_uf2(payload, ATHENA_SLOT_ADDR[target_slot])
            elif mode == "gif":
                frames, delays, _header = decode_qgf_frames(payload)
                frames[0].save(
                    filename,
                    save_all=True,
                    append_images=frames[1:],
                    duration=delays,
                    loop=0,
                    optimize=False,
                )
                payload = None
            if payload is not None:
                with open(filename, "wb") as outf:
                    outf.write(payload)
        except Exception as exc:
            self.on_error(str(exc))
            return

        if mode == "qgf":
            self.save_done_signal.emit("Saved GIF{} as QGF".format(source_slot))
        elif mode == "gif":
            self.save_done_signal.emit("Saved GIF{} as animated GIF".format(source_slot))
        else:
            self.save_done_signal.emit("Saved GIF{} as UF2 targeting GIF{}".format(source_slot, target_slot))

    def on_save_slot_qgf(self, slot):
        if not self.valid():
            return

        filename, _ = QFileDialog.getSaveFileName(
            self.btn_select.window(),
            "Save GIF{} as QGF".format(slot),
            "gif{}_download.qgf".format(slot),
            "QGF files (*.qgf)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".qgf"):
            filename += ".qgf"
        self._start_slot_save_worker(slot, filename, "qgf", slot)

    def on_save_slot_uf2(self, source_slot, target_slot):
        if not self.valid():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self.btn_select.window(),
            "Save GIF{} as UF2 for GIF{}".format(source_slot, target_slot),
            "gif{}_to_gif{}.uf2".format(source_slot, target_slot),
            "UF2 files (*.uf2)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".uf2"):
            filename += ".uf2"
        self._start_slot_save_worker(source_slot, filename, "uf2", target_slot)

    def on_save_slot_gif(self, slot):
        if not self.valid():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self.btn_select.window(),
            "Save GIF{} as GIF".format(slot),
            "gif{}_download.gif".format(slot),
            "GIF files (*.gif)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".gif"):
            filename += ".gif"
        self._start_slot_save_worker(slot, filename, "gif", slot)

    def on_upload(self):
        if not self.valid():
            return
        try:
            qgf = self._build_qgf()
        except Exception as exc:
            QMessageBox.warning(None, "", str(exc))
            return

        self.main.lock_ui()
        self.progress.setValue(0)
        slot = self._selected_slot()
        activate = self.chk_activate.isChecked() and slot > 0
        self.log("Uploading {} bytes to GIF{}...".format(len(qgf), slot))

        threading.Thread(target=lambda: self._upload_worker(qgf, slot, activate)).start()

    def _upload_worker(self, qgf, slot, activate):
        try:
            with Autorefresh.lock():
                athena_upload(self.device.dev, slot, qgf, activate, self.on_progress, self.on_log)
        except Exception as exc:
            self.on_error(str(exc))
            return
        self.done_signal.emit("Upload complete")

    def on_log(self, msg):
        self.log_signal.emit(msg)

    def on_progress(self, progress):
        self.progress_signal.emit(progress)

    def on_error(self, msg):
        self.error_signal.emit(msg)

    def log(self, msg):
        self.log_box.appendPlainText(msg)

    def _on_log(self, msg):
        self.log(msg)

    def _on_progress(self, progress):
        self.progress.setValue(int(progress * 100))

    def _on_done(self, msg):
        self.progress.setValue(100)
        self.log(msg)
        self.refresh_slots_async()
        self.main.unlock_ui()
        self.transfer_active = False

    def _on_error(self, msg):
        self.log("Error: {}".format(msg))
        self.main.unlock_ui()
        self.transfer_active = False

    def _on_slot_result(self, idx, info, image):
        self._set_slot_card(idx, info, image=image)

    def _on_slot_refresh_state(self, active):
        self.slot_refresh_active = bool(active)
        self.btn_refresh_slots.setEnabled(not self.slot_refresh_active)
        self.btn_refresh_slots.setText("Refreshing..." if self.slot_refresh_active else "Refresh slots")
        if self.slot_refresh_active:
            self.log("Refreshing keyboard slots...")
        else:
            self.log("Slot refresh complete")

    def _on_save_done(self, msg):
        self.progress.setValue(100)
        self.log(msg)
        self.main.unlock_ui()
        self.transfer_active = False
