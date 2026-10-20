"""Microscope Camera viewer for the HDCE-10A (EM2750 + MT9M001) on 64-bit Windows.

Live view, auto/manual exposure and gain, white balance, snapshots and video recording.
Run with Anaconda's pythonw.exe; the camera must be bound to the libusb-win32 driver.
"""
import math
import os
import queue
import sys
import threading
import time
import traceback

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera import MicroscopeCamera, WIDTH, HEIGHT, XCLK_MHZ, demosaic  # noqa: E402

APP = "Microscope Camera"
VERSION = "1.0.0"
MAX_ROWS = 0x3FFF
MAX_GAIN = 15.75


# ============================================================== camera worker
class CameraWorker(QtCore.QThread):
    """Owns the camera: streaming, demosaicing, auto exposure/white balance, saving and recording."""
    frameReady = QtCore.Signal(object)          # RGB uint8 HxWx3
    status = QtCore.Signal(dict)                # fps, mbps, exposure_ms, gain, wb, ...
    message = QtCore.Signal(str, bool)          # text, is_error
    connected = QtCore.Signal(bool)

    def __init__(self, settings):
        super().__init__()
        self.s = settings                       # plain dict shared with the GUI (GUI writes, worker reads)
        self.cmds = queue.Queue()
        self._stop = False
        self.cam = None
        self.recorder = None
        self._settle = 0
        self._wb_steps = 0

    def stop(self):
        self._stop = True

    def post(self, fn, *args):
        self.cmds.put((fn, args))

    # ---- life cycle
    def run(self):
        while not self._stop:
            try:
                self._open()
                self._loop()
            except Exception as e:              # unplugged, USB error, ...
                self.connected.emit(False)
                self.message.emit(f"Camera error: {e}. Retrying…", True)
                traceback.print_exc()
            finally:
                self._close()
            for _ in range(20):
                if self._stop:
                    break
                time.sleep(0.1)

    def _open(self):
        try:
            self.cam = MicroscopeCamera(xclk=self.s["xclk"])
        except Exception as e:
            self.connected.emit(False)
            raise RuntimeError(f"camera not found — is it plugged in? ({e})") from None
        self._apply_exposure()
        self._apply_gains()
        self.cam.start()
        self.connected.emit(True)
        self.message.emit(f"Camera connected — {WIDTH}×{HEIGHT}, sensor clock {XCLK_MHZ[self.s['xclk']]} MHz", False)

    def _close(self):
        self._stop_recording()
        if self.cam:
            try:
                self.cam.close()
            except Exception:
                pass
            self.cam = None

    def _loop(self):
        fid, last_status, last_frame = 0, 0.0, time.time()
        while not self._stop:
            while not self.cmds.empty():
                fn, args = self.cmds.get_nowait()
                fn(*args)
            if self.cam is None:
                return                          # a command asked for a reconnect
            nid, bayer = self.cam.wait_frame(fid, 1.0)
            if bayer is None:
                if time.time() - last_frame > 5 + self.s["exposure_ms"] / 1000:
                    raise RuntimeError("no frames from the camera")
                continue
            fid, last_frame = nid, time.time()
            if self._settle:
                self._settle -= 1
            elif self.s["auto_exposure"]:
                self._auto_exposure(bayer)
            if self._wb_steps and not self._settle:
                self._white_balance_step(bayer)
            rgb = demosaic(bayer, rgb=True)
            if self.s["flip_h"] or self.s["flip_v"]:
                code = -1 if (self.s["flip_h"] and self.s["flip_v"]) else (1 if self.s["flip_h"] else 0)
                rgb = cv2.flip(rgb, code)
            self.frameReady.emit(rgb)
            if self.recorder:
                self.recorder.push(rgb)
            now = time.time()
            if now - last_status > 0.5:
                last_status = now
                st = self.cam.stats
                self.status.emit({"fps": st["fps"], "mbps": st["mbps"], "exposure_ms": self.s["exposure_ms"],
                                  "gain": self.s["gain"], "wb": tuple(self.s["wb"]),
                                  "recording": self.recorder.info() if self.recorder else None})

    # ---- hardware settings
    def _rows_for_ms(self, ms):
        return int(min(max(round(ms / 1000.0 / self.cam.row_time_s()), 1), MAX_ROWS))

    def _apply_exposure(self):
        self.cam.set_exposure_rows(self._rows_for_ms(self.s["exposure_ms"]))
        self._settle = 2

    def _apply_gains(self):
        g, (wr, wg, wb) = self.s["gain"], self.s["wb"]
        self.cam.set_gains(r=min(g * wr, MAX_GAIN), g=min(g * wg, MAX_GAIN), b=min(g * wb, MAX_GAIN))
        self._settle = 2

    def set_exposure(self, ms):
        self.s["exposure_ms"] = ms
        if self.cam:
            self._apply_exposure()

    def set_gain(self, g):
        self.s["gain"] = g
        if self.cam:
            self._apply_gains()

    def set_wb(self, wb):
        self.s["wb"] = list(wb)
        if self.cam:
            self._apply_gains()

    def set_clock(self, code):
        self.s["xclk"] = code
        self._close()                           # _loop returns, run() reopens with the new clock

    def start_white_balance(self):
        self._wb_steps = 4

    # ---- automatic control
    def _auto_exposure(self, bayer):
        sample = bayer[200:-200:4, 200:-200:4]
        green = sample[0::2, 1::2]
        mean = float(green.mean())
        clipped = float((sample >= 250).mean())
        target = float(self.s["ae_target"])
        ratio = target / max(mean, 1.0)
        if clipped > 0.02:
            ratio = min(ratio, 0.6)
        if abs(math.log(ratio)) < 0.08:
            return
        ratio = ratio ** 0.7
        frame_ms = self.cam.frame_rows() * self.cam.row_time_s() * 1000
        exp, gain = self.s["exposure_ms"], self.s["gain"]
        total = exp * gain * ratio
        # Prefer exposure up to one frame time (keeps full frame rate), then gain up to 8x, then longer exposure.
        min_ms = self.cam.row_time_s() * 1000
        new_exp = min(max(total, min_ms), frame_ms)
        new_gain = min(max(total / new_exp, 1.0), 8.0)
        new_exp = min(max(total / new_gain, min_ms), MAX_ROWS * min_ms)
        if abs(new_exp - exp) / exp > 0.02:
            self.s["exposure_ms"] = new_exp
            self._apply_exposure()
        if abs(new_gain - gain) / gain > 0.02:
            self.s["gain"] = new_gain
            self._apply_gains()

    def _white_balance_step(self, bayer):
        h, w = bayer.shape
        r = bayer[h // 4: 3 * h // 4: 2, w // 4: 3 * w // 4: 2].astype(np.float32)
        g = bayer[h // 4: 3 * h // 4: 2, w // 4 + 1: 3 * w // 4: 2].astype(np.float32)
        b = bayer[h // 4 + 1: 3 * h // 4: 2, w // 4 + 1: 3 * w // 4: 2].astype(np.float32)
        ok = (r < 245) & (g < 245) & (b < 245)
        if ok.mean() < 0.2 or g[ok].mean() < 8:
            self._wb_steps = 0
            self.message.emit("White balance needs a lit, not over-exposed, empty field — adjust light/exposure and retry.", True)
            return
        means = np.array([r[ok].mean(), g[ok].mean(), b[ok].mean()], np.float64)
        wb = np.array(self.s["wb"], np.float64) * (means[1] / np.maximum(means, 1e-3))
        wb /= wb.min()                          # hardware gains can't go below 1x
        self.set_wb([float(x) for x in np.clip(wb, 1.0, 8.0)])
        self._wb_steps -= 1
        if not self._wb_steps:
            self.message.emit("White balance set (R {:.2f}, G {:.2f}, B {:.2f})".format(*self.s["wb"]), False)

    # ---- saving
    def snapshot(self, rgb, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ext = os.path.splitext(path)[1].lower()
            params = [cv2.IMWRITE_JPEG_QUALITY, 95] if ext in (".jpg", ".jpeg") else []
            ok, buf = cv2.imencode(ext, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), params)
            if not ok:
                raise OSError("encoding failed")
            with open(path, "wb") as f:      # imwrite can't handle non-ASCII paths on Windows
                f.write(buf.tobytes())
            self.message.emit(f"Saved {path}", False)
        except OSError as e:
            self.message.emit(f"Could not save {path}: {e}. If the folder is Pictures/Documents, Windows "
                              "Controlled Folder Access may be blocking Python — choose another folder.", True)

    def start_recording(self, path, fps):
        self._stop_recording()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.recorder = Recorder(path, fps)
            self.message.emit(f"Recording to {path}", False)
        except OSError as e:
            self.recorder = None
            self.message.emit(f"Could not start recording: {e}", True)

    def _stop_recording(self):
        if self.recorder:
            info = self.recorder.close()
            self.message.emit(f"Recording saved: {info}", False)
            self.recorder = None

    def stop_recording(self):
        self._stop_recording()


class Recorder:
    """Encodes frames on its own thread so recording doesn't slow the live view."""

    def __init__(self, path, fps):
        self.path, self.frames, self.t0 = path, 0, time.time()
        self.q = queue.Queue(maxsize=60)
        self.w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (WIDTH, HEIGHT))
        if not self.w.isOpened():
            raise OSError(f"cannot open video file {path}")
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def push(self, rgb):
        try:
            self.q.put_nowait(rgb)
        except queue.Full:
            pass

    def _run(self):
        while True:
            f = self.q.get()
            if f is None:
                break
            self.w.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
            self.frames += 1

    def info(self):
        return f"{time.time() - self.t0:.0f}s, {self.frames} frames"

    def close(self):
        self.q.put(None)
        self.th.join(10)
        self.w.release()
        return f"{self.path} ({self.info()})"


# ================================================================== video view
class VideoView(QtWidgets.QWidget):
    """Draws the live image fitted to the widget; wheel zooms around the cursor, drag pans, double-click resets."""

    def __init__(self):
        super().__init__()
        self.img = None
        self.zoom, self.center = 1.0, QtCore.QPointF(0.5, 0.5)
        self.crosshair = False
        self.placeholder = "Waiting for camera…"
        self._drag = None
        self.setMinimumSize(320, 256)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setCursor(QtCore.Qt.OpenHandCursor)

    def set_frame(self, rgb):
        h, w, _ = rgb.shape
        self.img = QtGui.QImage(rgb.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
        self.update()

    def _geometry(self):
        """Source rect (image px) and target rect (widget px)."""
        iw, ih = self.img.width(), self.img.height()
        scale = min(self.width() / iw, self.height() / ih) * self.zoom
        sw, sh = min(iw, self.width() / scale), min(ih, self.height() / scale)
        cx = min(max(self.center.x() * iw, sw / 2), iw - sw / 2)
        cy = min(max(self.center.y() * ih, sh / 2), ih - sh / 2)
        src = QtCore.QRectF(cx - sw / 2, cy - sh / 2, sw, sh)
        tw, th = sw * scale, sh * scale
        dst = QtCore.QRectF((self.width() - tw) / 2, (self.height() - th) / 2, tw, th)
        return src, dst, scale

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(18, 18, 18))
        if self.img is None:
            p.setPen(QtGui.QColor(170, 170, 170))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, self.placeholder)
            return
        src, dst, _ = self._geometry()
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, self.zoom < 2)
        p.drawImage(dst, self.img, src)
        if self.crosshair:
            p.setPen(QtGui.QPen(QtGui.QColor(0, 255, 120, 200), 1))
            c = dst.center()
            p.drawLine(QtCore.QPointF(dst.left(), c.y()), QtCore.QPointF(dst.right(), c.y()))
            p.drawLine(QtCore.QPointF(c.x(), dst.top()), QtCore.QPointF(c.x(), dst.bottom()))
            p.drawEllipse(c, 40, 40)
        if self.zoom > 1.001:
            p.setPen(QtGui.QColor(255, 255, 255))
            p.drawText(dst.adjusted(8, 6, 0, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, f"{self.zoom:.1f}×")

    def wheelEvent(self, e):
        if self.img is None:
            return
        src, dst, _ = self._geometry()
        pos = e.position()
        # image point under the cursor stays put
        fx = src.left() + (pos.x() - dst.left()) / dst.width() * src.width()
        fy = src.top() + (pos.y() - dst.top()) / dst.height() * src.height()
        old = self.zoom
        self.zoom = min(max(self.zoom * (1.25 if e.angleDelta().y() > 0 else 0.8), 1.0), 16.0)
        k = old / self.zoom
        iw, ih = self.img.width(), self.img.height()
        cx = fx - (fx - src.center().x()) * k
        cy = fy - (fy - src.center().y()) * k
        self.center = QtCore.QPointF(cx / iw, cy / ih)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag = (e.position(), QtCore.QPointF(self.center))
            self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag and self.img is not None:
            start, c0 = self._drag
            _, dst, scale = self._geometry()
            d = e.position() - start
            self.center = QtCore.QPointF(c0.x() - d.x() / scale / self.img.width(),
                                         c0.y() - d.y() / scale / self.img.height())
            self.update()

    def mouseReleaseEvent(self, _):
        self._drag = None
        self.setCursor(QtCore.Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, _):
        self.zoom, self.center = 1.0, QtCore.QPointF(0.5, 0.5)
        self.update()


# ================================================================ main window
def log_slider(minv, maxv, steps=1000):
    s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    s.setRange(0, steps)
    s._lo, s._hi, s._steps = math.log(minv), math.log(maxv), steps
    return s


def log_get(s):
    return math.exp(s._lo + (s._hi - s._lo) * s.value() / s._steps)


def log_set(s, v):
    s.blockSignals(True)
    s.setValue(int(round((math.log(max(v, 1e-9)) - s._lo) / (s._hi - s._lo) * s._steps)))
    s.blockSignals(False)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP} {VERSION}")
        self.qs = QtCore.QSettings("MicroscopeCam", "Viewer")
        self.cfg = {
            "xclk": int(self.qs.value("xclk", 0x0B)),
            "exposure_ms": float(self.qs.value("exposure_ms", 40.0)),
            "gain": float(self.qs.value("gain", 1.0)),
            "wb": [float(x) for x in (self.qs.value("wb", None) or [1.0, 1.0, 1.6])],
            "auto_exposure": self.qs.value("auto_exposure", "true") in (True, "true"),
            "ae_target": int(self.qs.value("ae_target", 110)),
            "flip_h": self.qs.value("flip_h", "false") in (True, "true"),
            "flip_v": self.qs.value("flip_v", "false") in (True, "true"),
        }
        desktop = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DesktopLocation)
        self.save_dir = self.qs.value("save_dir", os.path.join(os.path.normpath(desktop), "Microscope Captures"))
        self.fmt = self.qs.value("format", "PNG")
        self.last_rgb = None
        self.fps = 0.0
        self.recording = False

        self.view = VideoView()
        self.view.crosshair = self.qs.value("crosshair", "false") in (True, "true")
        self.setCentralWidget(self.view)
        self._build_panel()
        self._build_status()
        self.resize(int(self.qs.value("w", 1280)), int(self.qs.value("h", 820)))

        self.worker = CameraWorker(self.cfg)
        self.worker.frameReady.connect(self._on_frame)
        self.worker.status.connect(self._on_status)
        self.worker.message.connect(self._on_message)
        self.worker.connected.connect(self._on_connected)
        self.worker.start()

        for key, fn in [("Space", self.snapshot), ("R", self.toggle_record), ("F11", self.toggle_fullscreen),
                        ("Escape", lambda: self.isFullScreen() and self.toggle_fullscreen()),
                        ("W", self.worker_wb)]:
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=fn)

    # ---- panel
    def _build_panel(self):
        dock = QtWidgets.QDockWidget("Controls", self)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        # Exposure
        g = QtWidgets.QGroupBox("Exposure")
        f = QtWidgets.QFormLayout(g)
        self.ae = QtWidgets.QCheckBox("Automatic")
        self.ae.setChecked(self.cfg["auto_exposure"])
        self.ae.toggled.connect(self._on_ae)
        f.addRow(self.ae)
        self.target = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.target.setRange(30, 220)
        self.target.setValue(self.cfg["ae_target"])
        self.target.valueChanged.connect(lambda v: self.cfg.__setitem__("ae_target", v))
        f.addRow("Brightness", self.target)
        self.exp = log_slider(0.07, 1000)
        self.exp.valueChanged.connect(lambda _: self.worker.post(self.worker.set_exposure, log_get(self.exp)))
        self.exp_lbl = QtWidgets.QLabel()
        f.addRow("Time", self.exp)
        f.addRow("", self.exp_lbl)
        self.gain = log_slider(1.0, MAX_GAIN)
        self.gain.valueChanged.connect(lambda _: self.worker.post(self.worker.set_gain, log_get(self.gain)))
        self.gain_lbl = QtWidgets.QLabel()
        f.addRow("Gain", self.gain)
        f.addRow("", self.gain_lbl)
        lay.addWidget(g)

        # White balance
        g = QtWidgets.QGroupBox("Colour")
        f = QtWidgets.QFormLayout(g)
        b = QtWidgets.QPushButton("Auto white balance  (W)")
        b.setToolTip("Point at an empty, evenly lit field (no specimen), then click.")
        b.clicked.connect(self.worker_wb)
        f.addRow(b)
        self.wb_r = log_slider(1.0, 8.0)
        self.wb_b = log_slider(1.0, 8.0)
        self.wb_r.valueChanged.connect(self._on_wb_slider)
        self.wb_b.valueChanged.connect(self._on_wb_slider)
        f.addRow("Red", self.wb_r)
        f.addRow("Blue", self.wb_b)
        lay.addWidget(g)

        # Image
        g = QtWidgets.QGroupBox("View")
        f = QtWidgets.QFormLayout(g)
        self.fh = QtWidgets.QCheckBox("Mirror")
        self.fv = QtWidgets.QCheckBox("Flip")
        self.fh.setChecked(self.cfg["flip_h"])
        self.fv.setChecked(self.cfg["flip_v"])
        self.fh.toggled.connect(lambda v: self.cfg.__setitem__("flip_h", v))
        self.fv.toggled.connect(lambda v: self.cfg.__setitem__("flip_v", v))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.fh)
        row.addWidget(self.fv)
        f.addRow(row)
        self.cross = QtWidgets.QCheckBox("Crosshair")
        self.cross.setChecked(self.view.crosshair)
        self.cross.toggled.connect(self._on_cross)
        f.addRow(self.cross)
        self.clock = QtWidgets.QComboBox()
        self.clock.addItem("Fast — 15 fps (24 MHz)", 0x0B)
        self.clock.addItem("Safe — 8 fps (12 MHz)", 0x07)
        self.clock.setCurrentIndex(max(0, self.clock.findData(self.cfg["xclk"])))
        self.clock.setToolTip("Use Safe if the picture breaks up on a slow USB port or hub.")
        self.clock.currentIndexChanged.connect(lambda _: self.worker.post(self.worker.set_clock, self.clock.currentData()))
        f.addRow("Speed", self.clock)
        hint = QtWidgets.QLabel("Wheel: zoom · drag: pan · double-click: fit")
        hint.setStyleSheet("color: gray")
        f.addRow(hint)
        lay.addWidget(g)

        # Capture
        g = QtWidgets.QGroupBox("Capture")
        v = QtWidgets.QVBoxLayout(g)
        self.snap_btn = QtWidgets.QPushButton("📷  Snapshot  (Space)")
        self.snap_btn.clicked.connect(self.snapshot)
        self.rec_btn = QtWidgets.QPushButton("⏺  Record video  (R)")
        self.rec_btn.setCheckable(True)
        self.rec_btn.clicked.connect(self.toggle_record)
        self.fmt_box = QtWidgets.QComboBox()
        self.fmt_box.addItems(["PNG", "TIFF", "JPG"])
        self.fmt_box.setCurrentText(self.fmt)
        self.fmt_box.currentTextChanged.connect(lambda t: setattr(self, "fmt", t))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Format"))
        row.addWidget(self.fmt_box, 1)
        self.dir_lbl = QtWidgets.QLabel()
        self.dir_lbl.setWordWrap(True)
        self.dir_lbl.setStyleSheet("color: gray")
        dirs = QtWidgets.QHBoxLayout()
        cb = QtWidgets.QPushButton("Change folder…")
        cb.clicked.connect(self._choose_dir)
        ob = QtWidgets.QPushButton("Open folder")
        ob.clicked.connect(lambda: (os.makedirs(self.save_dir, exist_ok=True), os.startfile(self.save_dir)))
        dirs.addWidget(cb)
        dirs.addWidget(ob)
        for x in (self.snap_btn, self.rec_btn):
            v.addWidget(x)
        v.addLayout(row)
        v.addLayout(dirs)
        v.addWidget(self.dir_lbl)
        lay.addWidget(g)
        lay.addStretch(1)

        dock.setWidget(w)
        dock.setMinimumWidth(270)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        self._sync_controls()
        self._update_dir_label()

    def _build_status(self):
        sb = self.statusBar()
        self.conn_lbl = QtWidgets.QLabel("● Connecting…")
        self.rate_lbl = QtWidgets.QLabel()
        sb.addPermanentWidget(self.rate_lbl)
        sb.addPermanentWidget(self.conn_lbl)

    def _sync_controls(self):
        log_set(self.exp, self.cfg["exposure_ms"])
        log_set(self.gain, self.cfg["gain"])
        r, g, b = self.cfg["wb"]
        log_set(self.wb_r, r / g)
        log_set(self.wb_b, b / g)
        self.exp_lbl.setText(f"{self.cfg['exposure_ms']:.1f} ms")
        self.gain_lbl.setText(f"{self.cfg['gain']:.2f}×")
        manual = not self.cfg["auto_exposure"]
        self.exp.setEnabled(manual)
        self.gain.setEnabled(manual)
        self.target.setEnabled(not manual)

    def _update_dir_label(self):
        self.dir_lbl.setText(f"Saving to: {self.save_dir}")

    # ---- handlers
    def _on_frame(self, rgb):
        self.last_rgb = rgb
        self.view.set_frame(rgb)

    def _on_status(self, st):
        self.fps = st["fps"]
        rec = f"   ⏺ REC {st['recording']}" if st["recording"] else ""
        self.rate_lbl.setText(f"{st['fps']:.1f} fps   {st['mbps']:.1f} MB/s   {WIDTH}×{HEIGHT}{rec}")
        if not self.exp.isSliderDown() and not self.gain.isSliderDown():
            self._sync_controls()

    def _on_message(self, text, err):
        self.statusBar().showMessage(text, 10000 if not err else 15000)
        if err and self.view.img is None:
            self.view.placeholder = text + "\n\nCheck the USB cable. The app keeps retrying."
            self.view.update()

    def _on_connected(self, ok):
        self.conn_lbl.setText("● Connected" if ok else "● Disconnected")
        self.conn_lbl.setStyleSheet("color: #2a2" if ok else "color: #c33")
        if not ok:
            self.view.img = None
            self.view.update()

    def _on_ae(self, on):
        self.cfg["auto_exposure"] = on
        self._sync_controls()

    def _on_wb_slider(self, _):
        self.worker.post(self.worker.set_wb, [log_get(self.wb_r), 1.0, log_get(self.wb_b)])

    def _on_cross(self, on):
        self.view.crosshair = on
        self.view.update()

    def worker_wb(self):
        self.statusBar().showMessage("Balancing white…")
        self.worker.post(self.worker.start_white_balance)

    def _choose_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Save captures to", self.save_dir)
        if d:
            self.save_dir = os.path.normpath(d)
            self._update_dir_label()

    def _stamp(self):
        return time.strftime("%Y-%m-%d_%H-%M-%S")

    def snapshot(self):
        if self.last_rgb is None:
            return
        ext = {"PNG": ".png", "TIFF": ".tif", "JPG": ".jpg"}[self.fmt]
        path = os.path.join(self.save_dir, f"microscope_{self._stamp()}{ext}")
        self.worker.post(self.worker.snapshot, self.last_rgb, path)
        QtWidgets.QApplication.beep()

    def toggle_record(self):
        if not self.recording and self.last_rgb is not None:
            self.recording = True
            path = os.path.join(self.save_dir, f"microscope_{self._stamp()}.mp4")
            self.worker.post(self.worker.start_recording, path, round(self.fps) or 15)
            self.rec_btn.setChecked(True)
            self.rec_btn.setText("⏹  Stop recording  (R)")
        elif self.recording:
            self.recording = False
            self.worker.post(self.worker.stop_recording)
            self.rec_btn.setChecked(False)
            self.rec_btn.setText("⏺  Record video  (R)")

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, e):
        for k in ("xclk", "exposure_ms", "gain", "wb", "auto_exposure", "ae_target", "flip_h", "flip_v"):
            self.qs.setValue(k, self.cfg[k])
        self.qs.setValue("save_dir", self.save_dir)
        self.qs.setValue("format", self.fmt)
        self.qs.setValue("crosshair", self.view.crosshair)
        self.qs.setValue("w", self.width())
        self.qs.setValue("h", self.height())
        self.worker.stop()
        self.worker.wait(5000)
        e.accept()


def main():
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP)
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for icon in (os.path.join(here, "icon.ico"), os.path.join(here, "packaging", "icon.ico")):
        if os.path.exists(icon):
            app.setWindowIcon(QtGui.QIcon(icon))
            break

    win = MainWindow()
    win.show()
    if "--selftest" in sys.argv:
        # Exercise the app unattended: AE settles, white balance, snapshot, 3 s of video, window screenshot.
        out = os.path.join(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.TempLocation), "microscope_selftest")
        os.makedirs(out, exist_ok=True)
        win.save_dir = out
        T = QtCore.QTimer.singleShot
        T(5000, win.worker_wb)
        T(8000, win.snapshot)
        T(9000, win.toggle_record)
        T(12000, win.toggle_record)
        T(14000, lambda: win._update_dir_label() or win.grab().save(os.path.join(out, "window.png")))
        T(14500, win.close)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
