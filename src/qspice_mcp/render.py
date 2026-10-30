"""Render QSPICE schematics/symbols to PNG with Pillow.

The renderer interprets the same primitives QUX draws (line, rect, ellipse,
triangle, zigzag, coil, arc, arc3p, text, pin) plus schematic wires, junctions,
net labels (ground triangles for GND), directives and comments.  An optional
coordinate grid overlay lets an LLM reason about exact canvas positions."""
from __future__ import annotations

import io
import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from . import qsch
from .model import Component, Schematic, text_string
from .qsch import Q, screen_just, xform

INK = (20, 20, 120)        # symbol ink (QSPICE default-ish dark blue)
WIRE = (0, 110, 0)
PAPER = (255, 255, 255)
TEXT = (10, 10, 10)
DIRECTIVE = (0, 0, 0)
COMMENT = (0, 110, 160)
NETLBL = (150, 0, 0)
GRIDC = (235, 235, 242)
GRIDL = (170, 170, 190)

FONT_UNITS = 125.0   # world height of a size-1 font


@lru_cache(maxsize=64)
def _font(px: int, mono=False):
    names = (["consola.ttf", "cour.ttf"] if mono else ["arial.ttf", "segoeui.ttf"]) + ["DejaVuSans.ttf"]
    for n in names:
        for d in ("C:/Windows/Fonts/", ""):
            try:
                return ImageFont.truetype(d + n, max(6, px))
            except OSError:
                continue
    return ImageFont.load_default()


def _color(tok, default):
    try:
        v = int(str(tok), 16)
    except ValueError:
        return default
    if v >= 0x1000000:
        return default
    # Windows COLORREF 0x00BBGGRR
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)


class Canvas:
    def __init__(self, bbox, width_px=1600, margin=60, max_px=2600):
        x0, y0, x1, y1 = bbox
        w = max(x1 - x0, 400); h = max(y1 - y0, 400)
        s = (width_px - 2 * margin) / w
        if h * s + 2 * margin > max_px:
            s = (max_px - 2 * margin) / h
        self.s = s
        self.x0, self.y1 = x0, y1
        self.m = margin
        W = int(w * s + 2 * margin); H = int(h * s + 2 * margin)
        self.img = Image.new("RGB", (W, H), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.lw = max(1, int(round(s * 10)))

    def P(self, p):
        return (self.m + (p[0] - self.x0) * self.s, self.m + (self.y1 - p[1]) * self.s)

    def line(self, a, b, col, w=None):
        self.d.line([self.P(a), self.P(b)], fill=col, width=w or self.lw)

    def poly(self, pts, col, fill=None, w=None):
        P = [self.P(p) for p in pts]
        self.d.polygon(P, outline=col, fill=fill, width=w or self.lw)

    def text(self, p, s, size=1.0, h=1, v=3, rot=0, col=TEXT, mono=False):
        if not s:
            return
        s = qsch.display(s)
        px = int(FONT_UNITS * size * self.s)
        if px < 5:
            return
        f = _font(px, mono)
        lines = s.split("\n")
        widths = [self.d.textlength(l, font=f) for l in lines]
        lh = px * 1.15
        tw, th = max(widths), lh * len(lines)
        tmp = Image.new("RGBA", (int(tw) + 4, int(th) + 4), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        for i, l in enumerate(lines):
            td.text((2, 2 + i * lh), l, font=f, fill=col)
        if rot % 4:
            tmp = tmp.rotate(90 * (rot % 4), expand=True)
        W, H = tmp.size
        X, Y = self.P(p)
        # anchor by justification in screen space (rot affects box dims only)
        ox = {1: 0, 2: -W, 3: -W / 2, 0: 0}[h]
        oy = {1: 0, 2: -H, 3: -H / 2, 0: -H}[v]
        self.img.paste(tmp, (int(X + ox), int(Y + oy)), tmp)


def _xf_pts(tokens, code, origin):
    return [xform(t, code, origin) for t in tokens if isinstance(t, tuple)]


def draw_symbol(cv: Canvas, sym_node, code=0, origin=(0, 0), show_pins=False, dim=False, label_pins=False):
    for c in sym_node.children:
        k = c.kind
        t = c.tokens
        pts = _xf_pts(t, code, origin)
        nums = [x for x in t[1:] if not isinstance(x, tuple) and not isinstance(x, Q)]
        col = INK
        if k == "line" and len(pts) >= 2:
            col = _color(nums[2] if len(nums) > 2 else "", INK)
            cv.line(pts[0], pts[1], col)
        elif k == "rect" and len(pts) >= 2:
            col = _color(nums[3] if len(nums) > 3 else "", INK)
            fill = _fill(nums[4] if len(nums) > 4 else "0x1000000", col)
            (ax, ay), (bx, by) = pts[0], pts[1]
            cv.poly([(ax, ay), (bx, ay), (bx, by), (ax, by)], col, fill)
        elif k == "ellipse" and len(pts) >= 2:
            col = _color(nums[3] if len(nums) > 3 else "", INK)
            fill = _fill(nums[4] if len(nums) > 4 else "0x1000000", col)
            A, B = cv.P(pts[0]), cv.P(pts[1])
            box = [min(A[0], B[0]), min(A[1], B[1]), max(A[0], B[0]), max(A[1], B[1])]
            cv.d.ellipse(box, outline=col, fill=fill, width=cv.lw)
        elif k == "triangle" and len(pts) >= 3:
            col = _color(nums[2] if len(nums) > 2 else "", INK)
            fill = _fill(nums[3] if len(nums) > 3 else "0x1000000", col)
            cv.poly(pts[:3], col, fill)
        elif k in ("zigzag", "coil") and len(pts) >= 2:
            _zig(cv, t, code, origin, k)
        elif k in ("arc", "arc3p") and len(pts) >= 3:
            _arc(cv, pts, k)
        elif k == "text":
            if len(t) < 6:
                continue
            size = float(t[2]); just = int(t[3]); flag = int(t[4])
            s = qsch.unq(t[-1]) if isinstance(t[-1], Q) else ""
            s = s.replace("<", "").replace(">", "") if "=<" in s else s
            if flag == 2:
                continue  # hidden attribute (e.g. the behavioural model name)
            hh, vv, rr = screen_just(just, code)
            colr = _color(t[5], TEXT)
            cv.text(pts[0], s, size, hh, vv, rr, colr)
        elif k == "pin":
            if show_pins:
                X, Y = cv.P(pts[0])
                r = max(2, cv.lw * 2)
                cv.d.rectangle([X - r, Y - r, X + r, Y + r], outline=(200, 0, 0), width=1)
            size = float(t[3]); just = int(t[4])
            optional = len(t) >= 2 and isinstance(t[-2], Q) and isinstance(t[-1], Q)
            if optional and not label_pins:
                continue  # pins tied to a default net are invisible until used
            name = qsch.unq(t[-2]) if optional else (qsch.unq(t[-1]) if isinstance(t[-1], Q) else "")
            if (just not in (0, 15) or label_pins) and name and size > 0:
                off = t[2] if isinstance(t[2], tuple) else (0, 0)
                lp = xform((t[1][0] + off[0], t[1][1] + off[1]), code, origin)
                hh, vv, rr = screen_just(just if just else 15, code)
                cv.text(lp, name, size * 0.8, hh, vv, rr if rr in (0, 2) else 0, (90, 90, 90))


def _fill(tok, col):
    s = str(tok)
    if s == "0x3000000":
        return col
    if s == "0x2000000":
        return col
    if s in ("0x1000000", "-1"):
        return None
    try:
        v = int(s, 16)
        if v < 0x1000000:
            return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    except ValueError:
        pass
    return None


def _zig(cv, t, code, origin, kind):
    p = [x for x in t if isinstance(x, tuple)]
    (x0, y0), (x1, y1) = p[0], p[1]
    vertical = abs(y1 - y0) >= abs(x1 - x0)
    pts = []
    if kind == "zigzag":
        n = 6
        for i in range(n * 2 + 1):
            f = i / (n * 2)
            amp = 0 if i in (0, n * 2) else (1 if i % 2 else -1)
            if vertical:
                pts.append(((x0 + x1) / 2 + amp * (x1 - x0) / 2, y0 + (y1 - y0) * f))
            else:
                pts.append((x0 + (x1 - x0) * f, (y0 + y1) / 2 + amp * (y1 - y0) / 2))
        W = [xform(q, code, origin) for q in pts]
        for a, b in zip(W, W[1:]):
            cv.line(a, b, INK)
    else:  # coil: 4 half-ellipse bumps from the centre line to one bbox edge
        n, steps = 4, 10
        if vertical:
            base, amp, a0, L = (x0 + x1) / 2, abs(x1 - x0) / 2, y0, (y1 - y0)
        else:
            base, amp, a0, L = (y0 + y1) / 2, abs(y1 - y0) / 2, x0, (x1 - x0)
        seg = L / n
        prev = None
        for i in range(n):
            for j in range(steps + 1):
                a = math.pi * j / steps
                u = a0 + seg * i + seg * (1 - math.cos(a)) / 2
                w = base + amp * math.sin(a)
                q = (w, u) if vertical else (u, w)
                Wp = xform(q, code, origin)
                if prev is not None:
                    cv.line(prev, Wp, INK)
                prev = Wp


def _arc(cv, pts, kind):
    if kind == "arc3p":
        # three points on arc: start, mid?, end — approximate by polyline through circle fit
        a, b, c = pts[0], pts[1], pts[2]
        poly = _circle3(a, b, c)
    else:
        (x0, y0), (x1, y1), s, e = pts[0], pts[1], pts[2], pts[3]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = abs(x1 - x0) / 2, abs(y1 - y0) / 2
        a0 = math.atan2(s[1] - cy, s[0] - cx); a1 = math.atan2(e[1] - cy, e[0] - cx)
        if a1 <= a0:
            a1 += 2 * math.pi
        poly = [(cx + rx * math.cos(a0 + (a1 - a0) * i / 24), cy + ry * math.sin(a0 + (a1 - a0) * i / 24)) for i in range(25)]
    for p, q in zip(poly, poly[1:]):
        cv.line(p, q, INK)


def _circle3(a, b, c):
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return [a, b, c]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux); a1 = math.atan2(by - uy, bx - ux); a2 = math.atan2(cy - uy, cx - ux)
    def norm(x):
        while x < a0: x += 2 * math.pi
        return x
    a1n, a2n = norm(a1), norm(a2)
    end = a2n if a1n <= a2n else a2n - 2 * math.pi
    return [(ux + r * math.cos(a0 + (end - a0) * i / 32), uy + r * math.sin(a0 + (end - a0) * i / 32)) for i in range(33)]


def schematic_bbox(sch: Schematic, include_text=True):
    xs, ys = [], []
    for c in sch.components():
        b = c.bbox(include_text=False)
        xs += [b[0], b[2]]; ys += [b[1], b[3]]
        if include_text:
            for tn in c.texts:
                t = tn.tokens
                p = xform(t[1], c.code, c.pos)
                try:
                    size = float(t[2]); hh, vv, rr = screen_just(int(t[3]), c.code)
                except (ValueError, IndexError):
                    xs.append(p[0]); ys.append(p[1]); continue
                s = text_string(tn)
                w = max(len(l) for l in s.split("\n")) * FONT_UNITS * size * 0.6
                h = FONT_UNITS * size * 1.2 * len(s.split("\n"))
                if rr % 2:
                    w, h = h, w
                x0 = p[0] - (0 if hh in (0, 1) else w if hh == 2 else w / 2)
                y0 = p[1] - (h if vv == 1 else 0 if vv in (0, 2) else h / 2)
                xs += [x0, x0 + w]; ys += [y0, y0 + h]
    for a, b, _ in sch.wires():
        xs += [a[0], b[0]]; ys += [a[1], b[1]]
    for p, *_ in sch.nets():
        xs.append(p[0]); ys.append(p[1])
    if include_text:
        for t in sch.texts():
            xs.append(t["pos"][0]); ys.append(t["pos"][1])
            lines = t["str"].split("\n")
            w = max(len(l) for l in lines) * FONT_UNITS * t["size"] * 0.6
            xs.append(t["pos"][0] + w); ys.append(t["pos"][1] - len(lines) * FONT_UNITS * t["size"] * 1.2)
    if not xs:
        return (-500, -500, 500, 500)
    pad = 250
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def draw_ground(cv: Canvas, p, just, col=INK):
    # direction the ground symbol points (away from wire)
    _, v, _ = (just >> 2) & 3, just & 3, 0
    h = (just >> 2) & 3
    if v == 1:     d = (0, -1)
    elif v == 2:   d = (0, 1)
    elif h == 2:   d = (-1, 0)
    elif h == 1:   d = (1, 0)
    else:          d = (0, -1)
    x, y = p
    L, W = 60, 100
    tip = (x + d[0] * (L + 80), y + d[1] * (L + 80))
    base = (x + d[0] * L, y + d[1] * L)
    nx, ny = -d[1], d[0]
    a = (base[0] + nx * W, base[1] + ny * W); b = (base[0] - nx * W, base[1] - ny * W)
    cv.line(p, base, col)
    cv.poly([a, b, tip], col, None)


def render(sch: Schematic, width=1600, grid=False, show_pins=False, highlight: list[str] | None = None,
           bbox=None) -> bytes:
    bb = bbox or schematic_bbox(sch)
    cv = Canvas(bb, width_px=width)
    if grid:
        _grid(cv, bb)
    hl = {h.lower() for h in (highlight or [])}
    for c in sch.components():
        if c.refdes.lower() in hl:
            b = c.bbox()
            A, B = cv.P((b[0] - 60, b[3] + 60)), cv.P((b[2] + 60, b[1] - 60))
            cv.d.rectangle([A, B], fill=(255, 245, 190))
        draw_symbol(cv, c.symbol.node, c.code, c.pos, show_pins=show_pins)
    for a, b, _ in sch.wires():
        cv.line(a, b, WIRE)
    for p in sch.junctions():
        X, Y = cv.P(p)
        r = max(3, cv.lw * 2.6)
        cv.d.ellipse([X - r, Y - r, X + r, Y + r], fill=WIRE)
    for p, name, just, port in sch.nets():
        if name.upper() in ("GND", "0"):
            draw_ground(cv, p, just)
        else:
            h, v, r = (just >> 2) & 3, just & 3, (just >> 5) & 3
            if port:
                X, Y = cv.P(p); rr = cv.lw * 3
                cv.d.rectangle([X - rr, Y - rr, X + rr, Y + rr], outline=NETLBL, width=1)
            # offset label slightly off the wire
            off = {1: (0, -25), 2: (0, 25), 3: (0, 0)}[v if v in (1, 2, 3) else 3]
            hoff = {1: 25, 2: -25, 3: 0}.get(h, 0)
            cv.text((p[0] + hoff, p[1] + off[1]), name, 1.0, h or 1, v or 2, r, NETLBL)
            X, Y = cv.P(p)
            cv.d.ellipse([X - 2, Y - 2, X + 2, Y + 2], fill=NETLBL)
    for t in sch.texts():
        h, v, r = (t["just"] >> 2) & 3, t["just"] & 3, (t["just"] >> 5) & 3
        cv.text(t["pos"], t["str"], t["size"], h or 1, v or 1, r,
                DIRECTIVE if t["flag"] == 0 else COMMENT, mono=(t["flag"] == 0))
    buf = io.BytesIO()
    cv.img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _grid(cv: Canvas, bb):
    x0, y0, x1, y1 = bb
    step = 100
    major = 500 if (x1 - x0) < 8000 else 1000
    gx = int(math.floor(x0 / step) * step)
    while gx <= x1:
        col = GRIDL if gx % major == 0 else GRIDC
        cv.line((gx, y0), (gx, y1), col, 1)
        if gx % major == 0:
            cv.text((gx + 10, y1 - 10), str(gx), 0.45, 1, 1, 0, (120, 120, 150))
        gx += step
    gy = int(math.floor(y0 / step) * step)
    while gy <= y1:
        col = GRIDL if gy % major == 0 else GRIDC
        cv.line((x0, gy), (x1, gy), col, 1)
        if gy % major == 0:
            cv.text((x0 + 10, gy + 10), str(gy), 0.45, 1, 2, 0, (120, 120, 150))
        gy += step


def render_symbol(sym_node, width=500) -> bytes:
    from .model import Symbol
    s = Symbol(sym_node)
    x0, y0, x1, y1 = s.bbox(include_text=True)
    cv = Canvas((x0 - 250, y0 - 250, x1 + 250, y1 + 250), width_px=width)
    _grid(cv, (x0 - 250, y0 - 250, x1 + 250, y1 + 250))
    draw_symbol(cv, sym_node, 0, (0, 0), show_pins=True, label_pins=True)
    buf = io.BytesIO()
    cv.img.save(buf, format="PNG")
    return buf.getvalue()
