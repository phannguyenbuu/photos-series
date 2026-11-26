"""Draw the face marker: a green hand-drawn loop (about 1.2 turns, tapered ends), transparent PNG, no fill."""
import math
import sys
from PIL import Image, ImageDraw

W, H, SCALE = 400, 300, 4
GREEN = (46, 204, 64, 255)


def main(target):
    w, h = W*SCALE, H*SCALE
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cx, cy = w/2, h/2+4*SCALE
    rx, ry = w*.42, h*.40
    start, turns, steps = math.radians(200), 1.18, 900
    points = []
    for i in range(steps+1):
        t = i/steps
        angle = start+t*turns*2*math.pi
        # The second pass drifts outward and tilts slightly, like a quick pen loop.
        drift = 1+.07*t+.015*math.sin(3*angle)
        x = cx+rx*drift*math.cos(angle)+18*SCALE*t
        y = cy+ry*(1+.04*t)*math.sin(angle)-10*SCALE*t*math.cos(angle)
        width = SCALE*(2+16*math.sin(math.pi*min(1, t*1.15))**.6)  # thick enough to survive downscaling; thin at both ends
        points.append((x, y, width))
    for (x0, y0, w0), (x1, y1, w1) in zip(points, points[1:]):
        r = (w0+w1)/4
        draw.line((x0, y0, x1, y1), fill=GREEN, width=max(1, int(r*2)))
        draw.ellipse((x1-r, y1-r, x1+r, y1+r), fill=GREEN)
    image.resize((W, H), Image.Resampling.LANCZOS).save(target, optimize=True)


if __name__ == "__main__":
    main(sys.argv[1])
