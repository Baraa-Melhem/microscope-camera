"""Read-only I2C bus scan plus Micron / OmniVision ID probes."""
from em28xx import EM28xx

cam = EM28xx()
try:
    found = [a for a in range(0x08, 0x78) if cam.i2c_read(a, 1) is not None]
    print("I2C devices answering:", " ".join(f"0x{a:02x}" for a in found) or "none")
    for a in found:
        mic = cam.micron_read(a, 0x00)
        ov = [cam.sccb_read(a, r) for r in (0x0A, 0x0B, 0x1C, 0x1D)]
        print(f" 0x{a:02x}: micron reg00=0x{mic:04x}" if mic is not None else f" 0x{a:02x}: micron read failed",
              " | sccb 0a,0b,1c,1d =", ["--" if v is None else f"{v:02x}" for v in ov])
        if a == 0x50:
            ee = b"".join(cam.i2c_read(a, 16) or b"" for _ in range(1))
            cam.i2c_write(a, [0x00], stop=False)
            ee = cam.i2c_read(a, 64)
            print("  eeprom[0:64]:", ee.hex(" ") if ee else None)
finally:
    cam.close()
