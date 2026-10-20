"""Minimal ctypes binding to libusb-win32 (libusb0.dll), including its isochronous async API.

Structures are packed to 1 byte, matching libusb-win32's headers on Windows.
"""
import ctypes as C

_lib = None    # loaded on first use, so the app can start (and explain) when the driver is missing
_PATH_MAX = 512


class EndpointDesc(C.Structure):
    _pack_ = 1
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("bEndpointAddress", C.c_uint8), ("bmAttributes", C.c_uint8),
                ("wMaxPacketSize", C.c_uint16), ("bInterval", C.c_uint8),
                ("bRefresh", C.c_uint8), ("bSynchAddress", C.c_uint8),
                ("extra", C.c_void_p), ("extralen", C.c_int)]


class InterfaceDesc(C.Structure):
    _pack_ = 1
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("bInterfaceNumber", C.c_uint8), ("bAlternateSetting", C.c_uint8),
                ("bNumEndpoints", C.c_uint8), ("bInterfaceClass", C.c_uint8),
                ("bInterfaceSubClass", C.c_uint8), ("bInterfaceProtocol", C.c_uint8),
                ("iInterface", C.c_uint8), ("endpoint", C.POINTER(EndpointDesc)),
                ("extra", C.c_void_p), ("extralen", C.c_int)]


class Interface(C.Structure):
    _pack_ = 1
    _fields_ = [("altsetting", C.POINTER(InterfaceDesc)), ("num_altsetting", C.c_int)]


class ConfigDesc(C.Structure):
    _pack_ = 1
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("wTotalLength", C.c_uint16), ("bNumInterfaces", C.c_uint8),
                ("bConfigurationValue", C.c_uint8), ("iConfiguration", C.c_uint8),
                ("bmAttributes", C.c_uint8), ("MaxPower", C.c_uint8),
                ("interface", C.POINTER(Interface)), ("extra", C.c_void_p),
                ("extralen", C.c_int)]


class DeviceDesc(C.Structure):
    _pack_ = 1
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("bcdUSB", C.c_uint16), ("bDeviceClass", C.c_uint8),
                ("bDeviceSubClass", C.c_uint8), ("bDeviceProtocol", C.c_uint8),
                ("bMaxPacketSize0", C.c_uint8), ("idVendor", C.c_uint16),
                ("idProduct", C.c_uint16), ("bcdDevice", C.c_uint16),
                ("iManufacturer", C.c_uint8), ("iProduct", C.c_uint8),
                ("iSerialNumber", C.c_uint8), ("bNumConfigurations", C.c_uint8)]


class Device(C.Structure):
    _pack_ = 1


class Bus(C.Structure):
    _pack_ = 1


Device._fields_ = [("next", C.POINTER(Device)), ("prev", C.POINTER(Device)),
                   ("filename", C.c_char * _PATH_MAX), ("bus", C.POINTER(Bus)),
                   ("descriptor", DeviceDesc), ("config", C.POINTER(ConfigDesc)),
                   ("dev", C.c_void_p), ("devnum", C.c_uint8),
                   ("num_children", C.c_uint8), ("children", C.c_void_p)]
Bus._fields_ = [("next", C.POINTER(Bus)), ("prev", C.POINTER(Bus)),
                ("dirname", C.c_char * _PATH_MAX), ("devices", C.POINTER(Device)),
                ("location", C.c_ulong), ("root_dev", C.POINTER(Device))]

_h = C.c_void_p
_PROTOS = [
    ("usb_init", None, []), ("usb_find_busses", C.c_int, []),
    ("usb_find_devices", C.c_int, []), ("usb_get_busses", C.POINTER(Bus), []),
    ("usb_open", _h, [C.POINTER(Device)]), ("usb_close", C.c_int, [_h]),
    ("usb_set_configuration", C.c_int, [_h, C.c_int]),
    ("usb_claim_interface", C.c_int, [_h, C.c_int]),
    ("usb_release_interface", C.c_int, [_h, C.c_int]),
    ("usb_set_altinterface", C.c_int, [_h, C.c_int]),
    ("usb_reset", C.c_int, [_h]),
    ("usb_clear_halt", C.c_int, [_h, C.c_uint]),
    ("usb_control_msg", C.c_int, [_h, C.c_int, C.c_int, C.c_int, C.c_int, C.c_char_p, C.c_int, C.c_int]),
    ("usb_bulk_read", C.c_int, [_h, C.c_int, C.c_char_p, C.c_int, C.c_int]),
    ("usb_get_string_simple", C.c_int, [_h, C.c_int, C.c_char_p, C.c_size_t]),
    ("usb_strerror", C.c_char_p, []),
    ("usb_isochronous_setup_async", C.c_int, [_h, C.POINTER(C.c_void_p), C.c_ubyte, C.c_int]),
    ("usb_bulk_setup_async", C.c_int, [_h, C.POINTER(C.c_void_p), C.c_ubyte]),
    ("usb_submit_async", C.c_int, [C.c_void_p, C.c_char_p, C.c_int]),
    ("usb_reap_async", C.c_int, [C.c_void_p, C.c_int]),
    ("usb_reap_async_nocancel", C.c_int, [C.c_void_p, C.c_int]),
    ("usb_cancel_async", C.c_int, [C.c_void_p]),
    ("usb_free_async", C.c_int, [C.POINTER(C.c_void_p)]),
]


def _load():
    global _lib
    if _lib is not None:
        return
    try:
        lib = C.CDLL("libusb0.dll")
    except OSError as e:
        raise OSError("libusb0.dll not found - the camera driver is not installed. Run the Microscope Camera "
                      "setup, or 'Repair camera driver' from the Start menu.") from e
    for name, res, args in _PROTOS:
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args
        globals()[name] = f
    _lib = lib


class USBError(OSError):
    pass


def check(ret, what):
    if ret < 0:
        raise USBError(f"{what} failed ({ret}): {usb_strerror().decode(errors='replace')}")
    return ret


def find(vid, pid):
    _load()
    usb_init()
    usb_find_busses()
    usb_find_devices()
    bus = usb_get_busses()
    while bus:
        dev = bus.contents.devices
        while dev:
            d = dev.contents.descriptor
            if d.idVendor == vid and d.idProduct == pid:
                return dev
            dev = dev.contents.next
        bus = bus.contents.next
    return None


def describe(dev):
    d = dev.contents.descriptor
    lines = [f"device {d.idVendor:04x}:{d.idProduct:04x} bcdUSB={d.bcdUSB:04x} bcdDevice={d.bcdDevice:04x} "
             f"class={d.bDeviceClass} configs={d.bNumConfigurations} ep0={d.bMaxPacketSize0}"]
    for ci in range(d.bNumConfigurations):
        cfg = dev.contents.config[ci]
        lines.append(f" config {cfg.bConfigurationValue}: {cfg.bNumInterfaces} interfaces, MaxPower={cfg.MaxPower * 2}mA")
        for ii in range(cfg.bNumInterfaces):
            itf = cfg.interface[ii]
            for ai in range(itf.num_altsetting):
                a = itf.altsetting[ai]
                lines.append(f"  if{a.bInterfaceNumber} alt{a.bAlternateSetting} class={a.bInterfaceClass:02x}/"
                             f"{a.bInterfaceSubClass:02x}/{a.bInterfaceProtocol:02x} eps={a.bNumEndpoints}")
                for ei in range(a.bNumEndpoints):
                    e = a.endpoint[ei]
                    kind = ["ctrl", "isoc", "bulk", "intr"][e.bmAttributes & 3]
                    mps = e.wMaxPacketSize
                    eff = (mps & 0x7FF) * (1 + ((mps >> 11) & 3))
                    lines.append(f"    ep {e.bEndpointAddress:02x} {kind} maxpkt=0x{mps:04x} (eff {eff}) interval={e.bInterval}")
    return "\n".join(lines)
