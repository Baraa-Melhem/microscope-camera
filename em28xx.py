"""Register and I2C access to an eMPIA EM27xx/EM28xx bridge over libusb0 (protocol as in Linux em28xx)."""
import ctypes as C
import time
import lusb0 as U


class EM28xx:
    def __init__(self, vid=0x1578, pid=0x800C):
        self.dev = U.find(vid, pid)
        if not self.dev:
            raise U.USBError(f"camera {vid:04x}:{pid:04x} not found (is it plugged in and bound to libusb-win32?)")
        self.h = U.usb_open(self.dev)
        if not self.h:
            raise U.USBError("usb_open failed")
        U.check(U.usb_set_configuration(self.h, self.dev.contents.config[0].bConfigurationValue), "set_configuration")
        U.check(U.usb_claim_interface(self.h, 0), "claim_interface")

    def close(self):
        if self.h:
            try:
                U.usb_set_altinterface(self.h, 0)
            finally:
                U.usb_release_interface(self.h, 0)
                U.usb_close(self.h)
                self.h = None

    # --- bridge registers -------------------------------------------------
    def read(self, reg, n=1, req=0x00):
        buf = C.create_string_buffer(max(n, 1))
        r = U.check(U.usb_control_msg(self.h, 0xC0, req, 0, reg, buf, n, 500), f"read {reg:02x}")
        return buf.raw[:r]

    def rd(self, reg):
        return self.read(reg)[0]

    def write(self, reg, data, req=0x00):
        data = bytes(data)
        U.check(U.usb_control_msg(self.h, 0x40, req, 0, reg, data, len(data), 500), f"write {reg:02x}")

    def wr(self, reg, val):
        self.write(reg, [val])

    def wr_mask(self, reg, val, mask):
        self.wr(reg, (self.rd(reg) & ~mask) | (val & mask))

    # --- I2C (addr is the 7-bit address) ------------------------------------
    def _i2c_status(self):
        for _ in range(20):
            st = self.rd(0x05)
            if st == 0x00:
                return True
            if st == 0x10:
                return False          # no ACK
            time.sleep(0.002)
        return False

    def i2c_write(self, addr, data, stop=True):
        self.write(addr << 1, data, req=2 if stop else 3)
        return self._i2c_status()

    def i2c_read(self, addr, n):
        data = self.read(addr << 1, n, req=2)
        return data if self._i2c_status() else None

    # Register helpers for 8-bit-register (SCCB/OmniVision) and 16-bit-value (Micron) sensors.
    def sccb_read(self, addr, reg):
        if not self.i2c_write(addr, [reg]):
            return None
        d = self.i2c_read(addr, 1)
        return d[0] if d else None

    def sccb_write(self, addr, reg, val):
        return self.i2c_write(addr, [reg, val])

    def micron_read(self, addr, reg):
        if not self.i2c_write(addr, [reg], stop=False):
            return None
        d = self.i2c_read(addr, 2)
        return (d[0] << 8 | d[1]) if d and len(d) == 2 else None

    def micron_write(self, addr, reg, val):
        return self.i2c_write(addr, [reg, val >> 8, val & 0xFF])
