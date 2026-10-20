# HDCE-10A camera protocol notes

Notes from bringing the camera up on 64-bit Windows. Register names follow the Linux `em28xx`
driver (`em28xx-reg.h`). Everything below was verified on real hardware.

## Hardware

- USB 2.0 high-speed, VID `1578`, PID `800C`, one configuration, interface 0 with 8 alt settings.
  - EP `0x81`: interrupt (unused).
  - EP `0x82`: isochronous video. Alt 7 = 3 × 1024 bytes per microframe.
- Bridge: eMPIA **EM2750** (reg `0x0A` = `0x21`).
- I2C devices: EEPROM at `0x50` (starts with the eMPIA magic `1A EB 67 95`, then VID/PID) and
  **MT9M001** colour sensor at `0x5D` (reg `0x00` = `0x8431`).
- The vendor driver (`etDevice.sys` 2.524, 2005, "emVideo" INF) is **i386 only**. The Vista
  installer contains the same binaries.

## Bridge access

| Operation | bmRequestType | bRequest | wIndex |
|---|---|---|---|
| Read register | `0xC0` | `0x00` | register |
| Write register | `0x40` | `0x00` | register |
| I2C write (with stop) | `0x40` | `0x02` | 8-bit address (`addr << 1`) |
| I2C write (no stop) | `0x40` | `0x03` | 8-bit address |
| I2C read | `0xC0` | `0x02` | 8-bit address |

After each I2C transfer, read reg `0x05`: `0x00` = done, `0x10` = no ACK.
MT9M001 registers are 8-bit addresses with 16-bit big-endian values.

## Bring-up sequence

1. **Sensor**: reset (`0x0D` = 1, then 0), row/column start `0x0C`/`0x14`, and **row/column size
   1024/1280 exactly**. The datasheet's size−1 values make the bridge deliver short frames.
   Blanking `0x05` = 9, `0x06` = 25. Colour gains: `0x2B`/`0x2E` green, `0x2C` blue, `0x2D` red
   (bits 0–5 = gain × 8, bit 6 doubles it). Shutter width in rows at `0x09`.
2. **Bridge colour levels**: these power up as junk. **Red gain (`0x15`) starts at 0**, which pins
   the red channel to its offset, and they apply *even to raw Bayer output*. Set gamma `0x14` =
   `0x20`, RGB gains `0x15`–`0x17` = `0x20`, RGB offsets `0x18`–`0x1A` = 0. Contrast/brightness/
   saturation (`0x20`–`0x25`) don't affect raw output, but set them to defaults anyway.
3. **Format**: OUTFMT `0x27` = `0x28` (raw 8-bit Bayer, bit 5 set as the Windows driver does),
   VINMODE `0x10` = `0x0C`. The result is **RGGB** (row 0: R G, row 1: G B), 1280 × 1025
   (one extra line; use the first 1024). VINMODE's low two bits only shift the Bayer phase.
   OUTFMT `0x20`–`0x23` are *processed* Bayer, and `0x34` is YUYV (16 bpp).
4. **Capture window**: `0x1C`–`0x1F` = 0, 0, width/4, height/4; `0x1B` = overflow bits (`0x03`).
   No scaling (`0x30`–`0x33` = 0, `0x26` = 0).
5. **Start**: `0x13` = `0x0C`, `0x48` = 0, VINENABLE `0x12` = `0x67` (stop: `0x27`). Then select
   alt 7.
6. **Sensor clock**: XCLK reg `0x0F`. `0x0B` (24 MHz) gives 15 fps at 19.7 MB/s, `0x07` (12 MHz)
   gives 8 fps. 30 and 48 MHz exceed what USB 2.0 isochronous can carry.

## Stream format

Every isochronous packet starts with a 4-byte header:

- `22 5A xx xx`: start of a frame
- `88 88 88 88`: continuation

Pixel data follows the header. Packet lengths are always multiples of 4.

## libusb-win32 isochronous quirk

`usb_reap_async()` returns only the *total* byte count. Each packet stays at a fixed
`packet_size` slot in the buffer, and per-packet lengths aren't reported. Workaround: fill the
buffer with a marker byte (`0xEE`) before each submit. A packet's length is then the position after
its last non-marker byte, rounded up to 4, and the sum of all packets must equal the reported total
(it always has). Transfers larger than about 32 × 3072 bytes are rejected with `EINVAL`, so keep
16 smaller transfers in flight.
