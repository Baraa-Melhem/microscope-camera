"""User-space driver for the HDCE-10A microscope camera (eMPIA EM2750 bridge + Micron MT9M001 sensor).

The vendor's kernel driver is 32-bit only, so it cannot load on 64-bit Windows. Instead the camera is bound to
libusb-win32 and driven from here: the sensor is configured over the bridge's I2C bus, and raw 8-bit Bayer frames
are reassembled from the isochronous stream on endpoint 0x82.

The bridge applies its own colour levels even to raw output, and they power up as junk (red gain 0), so they
are reset to unity as the Linux em28xx driver does.

libusb-win32 only reports the total bytes of an isochronous transfer, not per-packet lengths, and leaves each
packet at a fixed slot offset. Each buffer is therefore pre-filled with a marker byte; a packet's length is the
position after its last non-marker byte, rounded up to a multiple of 4 (the bridge always sends multiples of 4),
and the sum is checked against the reported total.
"""
import ctypes as C
import threading
import time

import numpy as np

import lusb0 as U
from em28xx import EM28xx

SENSOR = 0x5D            # MT9M001 7-bit I2C address
WIDTH, HEIGHT = 1280, 1024
FRAME_BYTES = WIDTH * HEIGHT
ALT, PKT = 7, 3072       # highest-bandwidth alt setting: 3 x 1024 bytes per microframe
NPKT = 32                # packets per transfer (libusb-win32 rejects much larger transfers)
NXFER = 16               # transfers kept in flight
MARK = 0xEE

# EM28xx R0F XCLK codes -> MHz (sensor master clock; the sensor's pixel clock equals it)
XCLK_MHZ = {0x00: 30, 0x01: 15, 0x02: 10, 0x03: 7.5, 0x04: 6, 0x05: 5, 0x06: 4.3,
            0x07: 12, 0x08: 20, 0x0A: 48, 0x0B: 24}

# MT9M001 registers
R_ROW_START, R_COL_START, R_ROW_SIZE, R_COL_SIZE = 0x01, 0x02, 0x03, 0x04
R_HBLANK, R_VBLANK, R_SHUTTER, R_RESET = 0x05, 0x06, 0x09, 0x0D
R_GAIN_G1, R_GAIN_B, R_GAIN_R, R_GAIN_G2, R_GAIN_ALL = 0x2B, 0x2C, 0x2D, 0x2E, 0x35


def gain_to_reg(g):
    """MT9M001 gain register: bits 0-5 = gain*8 (1x..7.875x), bit 6 doubles it (up to 15.75x)."""
    g = float(min(max(g, 1.0), 15.75))
    return int(round(g * 8)) if g <= 7.875 else 0x40 | int(round(g * 4))


def reg_to_gain(v):
    return (v & 0x3F) / 8.0 * (2 if v & 0x40 else 1)


class MicroscopeCamera:
    def __init__(self, xclk=0x0B):
        self.cam = EM28xx()
        self._lock = threading.Condition()
        self._frame, self._frame_id = None, 0
        self._thread, self._running, self.error = None, False, None
        self.stats = {"fps": 0.0, "mbps": 0.0, "dropped": 0, "bad_xfers": 0}
        self._init_sensor()
        self._init_bridge(xclk)

    # ---------------------------------------------------------------- setup
    def _sensor_write(self, reg, val):
        if not self.cam.micron_write(SENSOR, reg, val):
            raise U.USBError(f"sensor did not ACK write to reg 0x{reg:02x}")

    def sensor_read(self, reg):
        return self.cam.micron_read(SENSOR, reg)

    def _init_sensor(self):
        chip = self.sensor_read(0x00)
        if chip not in (0x8411, 0x8421, 0x8431):
            raise U.USBError(f"unexpected sensor id {chip!r} (expected MT9M001)")
        self._sensor_write(R_RESET, 1)
        self._sensor_write(R_RESET, 0)
        self._sensor_write(R_ROW_START, 0x000C)
        self._sensor_write(R_COL_START, 0x0014)
        # Window one row/column larger than the bridge's capture area, as Linux em28xx does; with the
        # datasheet's size-1 values the bridge delivers short frames.
        self._sensor_write(R_ROW_SIZE, HEIGHT)
        self._sensor_write(R_COL_SIZE, WIDTH)
        self._sensor_write(R_HBLANK, 0x0009)
        self._sensor_write(R_VBLANK, 0x0019)
        self.set_gains(r=1.0, g=1.0, b=1.0)
        self.set_exposure_rows(800)

    def _init_bridge(self, xclk):
        c = self.cam
        c.wr(0x12, 0x27)                         # capture off while configuring
        c.wr(0x0F, xclk)                         # sensor master clock
        # Colour levels as Linux em28xx_colorlevels_set(): the power-on values are junk (red gain = 0
        # pins the red channel to its offset), and they apply even to raw Bayer output.
        c.wr(0x14, 0x20)                         # gamma
        c.write(0x15, [0x20, 0x20, 0x20])        # R, G, B gain (unity)
        c.write(0x18, [0x00, 0x00, 0x00])        # R, G, B offset
        c.write(0x20, [0x10, 0x00, 0x10, 0x00, 0x00, 0x00])  # contrast, brightness, saturation, U/V balance, sharpness
        c.wr(0x27, 0x20 | 0x08)                  # OUTFMT: raw 8-bit Bayer
        c.wr(0x10, 0x0C)                         # VINMODE: RGB8 BGGR, as Linux uses for MT9M001
        c.wr(0x11, 0x00)                         # VINCTRL
        c.write(0x1C, [0, 0, (WIDTH >> 2) & 0xFF, (HEIGHT >> 2) & 0xFF])   # HSTART VSTART CWIDTH CHEIGHT
        c.wr(0x1B, ((HEIGHT >> 9) & 0x02) | ((WIDTH >> 10) & 0x01))        # size overflow bits
        c.write(0x30, [0, 0, 0, 0])              # no scaling
        c.wr(0x26, 0x00)
        c.wr(0x13, 0x0C)
        c.wr(0x48, 0x00)
        self.xclk = xclk

    # ------------------------------------------------------------- controls
    def set_gains(self, r=None, g=None, b=None):
        for reg, val in ((R_GAIN_R, r), (R_GAIN_G1, g), (R_GAIN_G2, g), (R_GAIN_B, b)):
            if val is not None:
                self._sensor_write(reg, gain_to_reg(val))

    def gains(self):
        return {k: reg_to_gain(self.sensor_read(r)) for k, r in (("r", R_GAIN_R), ("g", R_GAIN_G1), ("b", R_GAIN_B))}

    def set_exposure_rows(self, rows):
        self._sensor_write(R_SHUTTER, int(min(max(rows, 1), 0x3FFF)))

    def exposure_rows(self):
        return self.sensor_read(R_SHUTTER)

    def row_time_s(self):
        # MT9M001 row time = (R0x04 + 1 + 244 + R0x05 - 19) master clocks
        return (WIDTH + 1 + 244 + 9 - 19) / (XCLK_MHZ.get(self.xclk, 12) * 1e6)

    def frame_rows(self):
        """Rows per frame at the default blanking; exposures longer than this lower the frame rate."""
        return HEIGHT + 1 + 0x19

    # ------------------------------------------------------------ streaming
    def start(self):
        if self._running:
            return
        self.cam.wr(0x12, 0x67)                  # VINENABLE: capture on
        time.sleep(0.01)
        U.check(U.usb_set_altinterface(self.cam.h, ALT), "set_altinterface")
        self._running = True
        self._thread = threading.Thread(target=self._stream, name="cam-stream", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(3)
            self._thread = None
        try:
            self.cam.wr(0x12, 0x27)
            U.usb_set_altinterface(self.cam.h, 0)
        except OSError:
            pass

    def close(self):
        self.stop()
        self.cam.close()

    def wait_frame(self, after_id=0, timeout=2.0):
        """Return (frame_id, bayer HxW uint8 array) newer than after_id, or (after_id, None) on timeout."""
        with self._lock:
            self._lock.wait_for(lambda: self._frame_id > after_id or self.error or not self._running, timeout)
            if self.error:
                raise self.error
            if self._frame_id > after_id:
                return self._frame_id, self._frame
            return after_id, None

    def _stream(self):
        B = PKT * NPKT
        ctxs, bufs = [], []
        try:
            for _ in range(NXFER):
                ctx = C.c_void_p()
                U.check(U.usb_isochronous_setup_async(self.cam.h, C.byref(ctx), 0x82, PKT), "iso setup")
                buf = (C.c_ubyte * B)()
                C.memset(buf, MARK, B)
                U.check(U.usb_submit_async(ctx, C.cast(buf, C.c_char_p), B), "iso submit")
                ctxs.append(ctx)
                bufs.append(buf)
            views = [np.ctypeslib.as_array(b).reshape(NPKT, PKT) for b in bufs]
            frame = np.empty(FRAME_BYTES * 2 + 4096, np.uint8)
            fill, in_frame = 0, False
            t_last, nbytes, nframes = time.perf_counter(), 0, 0
            k = 0
            while self._running:
                n = U.usb_reap_async_nocancel(ctxs[k], 1000)
                if n < 0 and n != -116:          # -116 = timeout, anything else is fatal (e.g. unplugged)
                    raise U.USBError(f"isochronous read failed ({n}): {U.usb_strerror().decode(errors='replace')}")
                if n > 0:
                    s = views[k]
                    nz = s != MARK
                    has = nz.any(1)
                    L = np.where(has, PKT - np.argmax(nz[:, ::-1], 1), 0)
                    L = (L + 3) & ~3
                    if int(L.sum()) != n:
                        self.stats["bad_xfers"] += 1
                        in_frame = False          # can't trust this transfer: resync on next frame start
                    else:
                        nbytes += n
                        for i in np.flatnonzero(L):
                            p = s[i, :L[i]]
                            if p[0] == 0x22 and p[1] == 0x5A:
                                if in_frame:
                                    self.stats["last_fill"] = fill
                                if in_frame and fill >= FRAME_BYTES:
                                    self._publish(frame[:FRAME_BYTES].reshape(HEIGHT, WIDTH).copy())
                                    nframes += 1
                                elif in_frame:
                                    self.stats["dropped"] += 1
                                in_frame, fill = True, 0
                            elif not in_frame:
                                continue
                            d = p[4:]
                            m = min(len(d), len(frame) - fill)
                            frame[fill:fill + m] = d[:m]
                            fill += m
                    s[:] = MARK
                U.check(U.usb_submit_async(ctxs[k], C.cast(bufs[k], C.c_char_p), B), "iso resubmit")
                k = (k + 1) % NXFER
                now = time.perf_counter()
                if now - t_last >= 1.0:
                    self.stats.update(fps=nframes / (now - t_last), mbps=nbytes / (now - t_last) / 1e6)
                    t_last, nbytes, nframes = now, 0, 0
        except Exception as e:                    # surface to the consumer thread
            self.error = e
        finally:
            for ctx in ctxs:
                U.usb_cancel_async(ctx)
                U.usb_free_async(C.byref(ctx))
            self._running = False
            with self._lock:
                self._lock.notify_all()

    def _publish(self, bayer):
        with self._lock:
            self._frame = bayer
            self._frame_id += 1
            self._lock.notify_all()


def demosaic(bayer, rgb=False):
    """RGGB layout as delivered with VINMODE 0x0C (row 0: R G, row 1: G B) -> BGR (or RGB) image.

    OpenCV names Bayer codes after the second row, so RGGB is its "BG" pattern."""
    import cv2
    return cv2.cvtColor(bayer, cv2.COLOR_BayerBG2RGB if rgb else cv2.COLOR_BayerBG2BGR)
