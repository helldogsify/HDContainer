# -*- coding: utf-8 -*-
"""Icons of the tray menu tool tiles (1.17.1): photo camera, video camera on a handle, record
dot, devices. One line style, drawn with Pillow on a 20-unit grid at 8x and scaled down, so
they stay sharp at any display scale. The video camera follows the user's sketch (2026-11-25).
photo(root, name, px, color) -> cached ImageTk.PhotoImage."""

from PIL import Image, ImageDraw, ImageTk

SS = 8                       # supersampling
STROKE = 1.45                # line width in grid units


def _line(d, u, w, pts):
    d.line([(x * u, y * u) for x, y in pts], fill=255, width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):                      # round caps
        r = w / 2
        d.ellipse((x * u - r, y * u - r, x * u + r, y * u + r), fill=255)


def _rrect(d, u, w, box, r, fill=None):
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x0 * u, y0 * u, x1 * u, y1 * u), radius=r * u, outline=255, width=w, fill=fill)


def _dot(d, u, x, y, r, outline_w=None):
    box = ((x - r) * u, (y - r) * u, (x + r) * u, (y + r) * u)
    if outline_w:
        d.ellipse(box, outline=255, width=outline_w)
    else:
        d.ellipse(box, fill=255)


def _shot(d, u, w):
    _rrect(d, u, w, (2, 6, 18, 16.5), 2.2)
    _line(d, u, w, [(7, 6), (8.2, 3.8), (11.8, 3.8), (13, 6)])
    _dot(d, u, 10, 11.2, 3.1, w)


def _video(d, u, w):
    _rrect(d, u, w, (1.3, 3.7, 12.7, 12.5), 1.8)
    _line(d, u, w, [(12.7, 8.1), (18.7, 3.5), (18.7, 12.7), (12.7, 8.1)])
    _dot(d, u, 3.9, 6.7, 1.05)
    _line(d, u, w, [(7.0, 12.5), (7.0, 16.5)])


def _rec(d, u, w):
    _dot(d, u, 10, 10, 7.6, w)
    _dot(d, u, 10, 10, 4.4)


def _devices(d, u, w):
    _rrect(d, u, w, (5.5, 3, 18.5, 12.5), 1.2)
    _line(d, u, w, [(12, 12.5), (12, 15.5)])
    _line(d, u, w, [(9.5, 15.8), (14.5, 15.8)])
    d.rounded_rectangle((0.6 * u, 7.2 * u, 8.4 * u, 18.6 * u), radius=1.6 * u, fill=0)   # phone in front
    _rrect(d, u, w, (1.5, 8, 7.5, 17.8), 1.2)
    _line(d, u, w, [(4, 15.6), (5, 15.6)])


DRAW = {"shot": _shot, "video": _video, "rec": _rec, "devices": _devices}
_CACHE = {}


def image(name, px, color):
    """RGBA Pillow image of an icon, px square, color '#rrggbb'."""
    n = px * SS
    mask = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(mask)
    u = n / 20.0
    DRAW[name](d, u, max(1, round(STROKE * u)))
    mask = mask.resize((px, px), Image.LANCZOS)
    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    out = Image.new("RGBA", (px, px), rgb + (0,))
    out.putalpha(mask)
    return out


def photo(root, name, px, color):
    key = (name, px, color)
    p = _CACHE.get(key)
    if p is None:
        p = _CACHE[key] = ImageTk.PhotoImage(image(name, px, color), master=root)
    return p
