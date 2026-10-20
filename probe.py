"""Read-only probe: USB descriptors, eMPIA chip ID and a register dump."""
import ctypes as C
import sys
import lusb0 as U

VID, PID = 0x1578, 0x800C

dev = U.find(VID, PID)
if not dev:
    sys.exit("camera not found on libusb0")
print(U.describe(dev))
h = U.usb_open(dev)
if not h:
    sys.exit("usb_open failed")
s = C.create_string_buffer(256)
for idx, label in [(dev.contents.descriptor.iManufacturer, "manufacturer"), (dev.contents.descriptor.iProduct, "product")]:
    if idx and U.usb_get_string_simple(h, idx, s, 256) > 0:
        print(label, s.value)

U.check(U.usb_set_configuration(h, dev.contents.config[0].bConfigurationValue), "set_configuration")
U.check(U.usb_claim_interface(h, 0), "claim_interface")


def rd(reg, n=1):
    buf = C.create_string_buffer(n)
    r = U.check(U.usb_control_msg(h, 0xC0, 0x00, 0, reg, buf, n, 500), f"read reg {reg:02x}")
    return buf.raw[:r]


print("chip id (reg 0x0a):", rd(0x0A).hex())
dump = b"".join(rd(r) for r in range(0x00, 0x60))
for row in range(0, len(dump), 16):
    print(f"{row:02x}: " + " ".join(f"{b:02x}" for b in dump[row:row + 16]))
U.usb_release_interface(h, 0)
U.usb_close(h)
