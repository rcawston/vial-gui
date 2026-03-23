import os
import threading

from PIL import Image, ImageSequence
from PIL.ImageQt import ImageQt
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QPixmap, QColor
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QToolButton, QFileDialog, \
    QDialog, QSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QProgressBar, QMessageBox

from athena_qgf import encode_qgf, encode_uf2
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


def athena_send(dev, payload):
    return hid_send(dev, payload, retries=20)


def athena_check_status(resp):
    status = resp[2]
    if status != 0:
        raise RuntimeError(ATHENA_STATUS_TEXT.get(status, "Unknown error"))
    return resp


def athena_upload(dev, slot, qgf_bytes, activate, progress_cb, log_cb):
    resp = athena_send(dev, bytes([ATHENA_HID_PREFIX, ATHENA_GIF_GET_INFO]))
    athena_check_status(resp)
    flash_size = int.from_bytes(resp[6:10], byteorder="little")
    log_cb("Keyboard flash size: {} MB".format(flash_size // (1024 * 1024)))

    begin = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_BEGIN_UPLOAD, slot]) + len(qgf_bytes).to_bytes(4, byteorder="little")
    athena_check_status(athena_send(dev, begin))

    chunk_size = 25
    total = len(qgf_bytes)
    for offset in range(0, total, chunk_size):
        chunk = qgf_bytes[offset:offset + chunk_size]
        msg = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_WRITE_CHUNK]) + offset.to_bytes(4, byteorder="little") + bytes([len(chunk)]) + chunk
        athena_check_status(athena_send(dev, msg))
        progress_cb((offset + len(chunk)) / total)

    finish = bytes([ATHENA_HID_PREFIX, ATHENA_GIF_FINISH_UPLOAD, 1 if activate else 0])
    athena_check_status(athena_send(dev, finish))


class CropPreviewLabel(QLabel):
    selectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setAlignment(Qt.AlignCenter)
        self._image = None
        self._pixmap = None
        self._selection = None
        self._drag_origin = None
        self._image_rect = QRectF()

    def set_image(self, image):
        self._image = image
        self._pixmap = QPixmap.fromImage(ImageQt(image))
        if image is None:
            self._selection = None
        else:
            side = min(image.width, image.height)
            x = (image.width - side) // 2
            y = (image.height - side) // 2
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
            int(rect.right()),
            int(rect.bottom()),
        )

    def _display_rect(self):
        if self._pixmap is None:
            return QRectF()
        target = QRectF(self.rect())
        source = QRectF(self._pixmap.rect())
        scaled = source
        scaled.scale(target.size(), Qt.KeepAspectRatio)
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
            painter.setPen(QPen(QColor("#ff6a00"), 2))
            painter.drawRect(QRectF(sx, sy, sw, sh))


class AthenaGifEditor(BasicEditor):
    log_signal = pyqtSignal(object)
    progress_signal = pyqtSignal(object)
    done_signal = pyqtSignal(object)
    error_signal = pyqtSignal(object)

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.frames = []
        self.durations = []
        self.source_path = ""

        self.log_signal.connect(self._on_log)
        self.progress_signal.connect(self._on_progress)
        self.done_signal.connect(self._on_done)
        self.error_signal.connect(self._on_error)

        file_row = QHBoxLayout()
        self.txt_path = QLineEdit()
        self.txt_path.setReadOnly(True)
        file_row.addWidget(self.txt_path)
        self.btn_select = QToolButton()
        self.btn_select.setText(tr("AthenaGif", "Select GIF..."))
        self.btn_select.clicked.connect(self.on_select_file)
        file_row.addWidget(self.btn_select)
        self.addLayout(file_row)

        controls = QGridLayout()
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

        self.lbl_summary = QLabel("No GIF loaded")
        controls.addWidget(self.lbl_summary, 1, 4, 1, 2)
        self.addLayout(controls)

        previews = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Crop"))
        self.cropper = CropPreviewLabel()
        self.cropper.selectionChanged.connect(self.update_preview)
        left.addWidget(self.cropper)
        previews.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("128x128 preview"))
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(200, 200)
        right.addWidget(self.preview_label)
        previews.addLayout(right, 1)
        self.addLayout(previews)

        buttons = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        buttons.addWidget(self.progress, 1)
        self.btn_save = QToolButton()
        self.btn_save.setText("Save UF2")
        self.btn_save.clicked.connect(self.on_save_uf2)
        buttons.addWidget(self.btn_save)
        self.btn_upload = QToolButton()
        self.btn_upload.setText("Upload")
        self.btn_upload.clicked.connect(self.on_upload)
        buttons.addWidget(self.btn_upload)
        self.addLayout(buttons)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.addWidget(self.log_box)

    def valid(self):
        return isinstance(self.device, VialKeyboard) and \
            self.device.desc["vendor_id"] == ATHENA_VENDOR_ID and \
            self.device.desc["product_id"] == ATHENA_PRODUCT_ID

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return

    def on_select_file(self):
        dialog = QFileDialog()
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setNameFilters(["GIF files (*.gif)"])
        if dialog.exec_() != QDialog.Accepted:
            return
        self.source_path = dialog.selectedFiles()[0]
        self.txt_path.setText(self.source_path)
        self._load_gif(self.source_path)

    def _load_gif(self, path):
        image = Image.open(path)
        self.frames = []
        self.durations = []
        for frame in ImageSequence.Iterator(image):
            copy = frame.copy().convert("RGB")
            self.frames.append(copy)
            self.durations.append(int(frame.info.get("duration", image.info.get("duration", 100))))

        if not self.frames:
            raise RuntimeError("GIF contains no frames")

        self.spin_start.setMaximum(len(self.frames))
        self.spin_end.setMaximum(len(self.frames))
        self.spin_start.setValue(1)
        self.spin_end.setValue(len(self.frames))
        self.cropper.set_image(self.frames[0])
        self.log("Loaded GIF with {} frames".format(len(self.frames)))
        self.update_preview()

    def _selected_slot(self):
        return self.combo_slot.currentData()

    def _frame_limit(self):
        return ATHENA_SLOT_LIMITS[self._selected_slot()]

    def _build_processed_frames(self):
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
            cropped = frame.crop(box)
            resized = cropped.resize((128, 128), Image.LANCZOS).convert("RGB")
            out_frames.append(resized)
        return out_frames, durations

    def update_preview(self):
        if not self.frames:
            return
        try:
            frames, durations = self._build_processed_frames()
        except Exception as exc:
            self.lbl_summary.setText(str(exc))
            return

        preview = QPixmap.fromImage(ImageQt(frames[0]))
        self.preview_label.setPixmap(preview.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.lbl_summary.setText("{} frames, {:.1f}s".format(len(frames), sum(durations) / 1000.0))

    def _build_qgf(self):
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
        dialog = QFileDialog()
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setDefaultSuffix("uf2")
        base = os.path.splitext(os.path.basename(self.source_path or "athena"))[0]
        dialog.selectFile("{}_gif{}.uf2".format(base, slot))
        dialog.setNameFilters(["UF2 files (*.uf2)"])
        if dialog.exec_() != QDialog.Accepted:
            return
        with open(dialog.selectedFiles()[0], "wb") as outf:
            outf.write(uf2)
        self.log("Saved UF2 for slot GIF{}".format(slot))

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
        self.main.unlock_ui()

    def _on_error(self, msg):
        self.log("Error: {}".format(msg))
        self.main.unlock_ui()
