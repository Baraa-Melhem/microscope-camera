"""Draw the app icon (a microscope objective's view: lens with reticle) as a multi-size .ico."""
import sys
from PIL import Image, ImageDraw

out = sys.argv[1]
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=(22, 52, 92, 255))
c, r = S // 2, 330
d.ellipse([c - r - 40, c - r - 40, c + r + 40, c + r + 40], fill=(200, 212, 225, 255))      # lens barrel
d.ellipse([c - r, c - r, c + r, c + r], fill=(38, 120, 160, 255))                            # field of view
for k, col in ((250, (60, 170, 120, 255)), (140, (230, 120, 160, 255))):                     # stained cells
    d.ellipse([c - k + 60, c - k // 2 - 40, c + 60, c + k // 2 - 40], fill=col)
w = 22
d.rectangle([c - w // 2, c - r, c + w // 2, c + r], fill=(255, 255, 255, 230))               # reticle
d.rectangle([c - r, c - w // 2, c + r, c + w // 2], fill=(255, 255, 255, 230))
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon written", out)
