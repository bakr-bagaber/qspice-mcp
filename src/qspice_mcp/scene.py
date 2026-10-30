"""Geometric scene model of a schematic.

Turns a Schematic into plain geometry in schematic units (y up, grid 100):
component bodies, keep-out grid points, pins (position, outward direction,
net), every visible text with its bounding box, wires, net labels and
junctions.  Used by the reviewer, the text map, and the editing tools so that
they all agree on where things are."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import qsch
from .connectivity import extract
from .model import Component, Schematic, text_string
from .qsch import Q, screen_just, xform

FONT = 125.0                 # world height of a size-1 font (matches render.py)
CHAR_W = 0.6 * FONT          # 75 units per character
LINE_H = 1.2 * FONT          # 150 units per line
G = 100


# advance widths of a proportional sans font (Arial metrics, em units); texts in
# QSPICE are drawn proportionally, directives in a fixed-pitch font
_EM = {" ": .278, "!": .278, '"': .355, "#": .556, "$": .556, "%": .889, "&": .667, "'": .191,
       "(": .333, ")": .333, "*": .389, "+": .584, ",": .278, "-": .333, ".": .278, "/": .278,
       ":": .278, ";": .278, "<": .584, "=": .584, ">": .584, "?": .556, "@": 1.015, "[": .278,
       "\\": .278, "]": .278, "^": .469, "_": .556, "`": .333, "{": .334, "|": .26, "}": .334,
       "~": .584, "A": .667, "B": .667, "C": .722, "D": .722, "E": .667, "F": .611, "G": .778,
       "H": .722, "I": .278, "J": .5, "K": .667, "L": .556, "M": .833, "N": .722, "O": .778,
       "P": .667, "Q": .778, "R": .722, "S": .667, "T": .611, "U": .722, "V": .667, "W": .944,
       "X": .667, "Y": .667, "Z": .611, "a": .556, "b": .556, "c": .5, "d": .556, "e": .556,
       "f": .278, "g": .556, "h": .556, "i": .222, "j": .222, "k": .5, "l": .222, "m": .833,
       "n": .556, "o": .556, "p": .556, "q": .556, "r": .333, "s": .5, "t": .278, "u": .556,
       "v": .5, "w": .722, "x": .5, "y": .5, "z": .5}
TEXT_SAFETY = 1.08            # proportional estimate + margin (font hinting, bold-ish GUI fonts)


def text_width(line: str, size: float = 1.0, mono: bool = False) -> float:
    """World width of one line of text."""
    if mono:
        return len(line) * CHAR_W * size
    em = sum(_EM.get(ch, .556 if ch.isdigit() else .62) for ch in line)
    return em * FONT * size * TEXT_SAFETY


def text_box(anchor, s: str, size: float = 1.0, h: int = 1, v: int = 3, rot: int = 0, mono: bool = False):
    """Bounding box (x0, y0, x1, y1) of a text drawn like QUX/render.py."""
    s = qsch.display(s or "")
    lines = s.split("\n") if s else [""]
    w = max(text_width(l, size, mono) for l in lines)
    hh = len(lines) * LINE_H * size
    if rot % 2:
        w, hh = hh, w
    x, y = anchor
    x0 = x - (0 if h in (0, 1) else w if h == 2 else w / 2)
    y0 = y - (hh if v == 1 else 0 if v in (0, 2) else hh / 2)
    return (x0, y0, x0 + w, y0 + hh)


@dataclass
class TextItem:
    owner: str          # refdes, "label:<name>", "directive", "comment"
    role: str           # refdes | value | attr | label | ground | directive | comment
    text: str
    box: tuple
    anchor: tuple
    node: object = None


@dataclass
class PinG:
    ref: str
    name: str
    index: int
    pos: tuple
    dir: str
    net: str | None
    optional: bool = False

    @property
    def key(self):
        return f"{self.ref}.{self.name}"


@dataclass
class PartG:
    ref: str
    comp: Component
    symbol: str
    type: str
    description: str
    code: int
    pos: tuple
    body: tuple                      # graphics bbox (no text)
    hull: list
    keepout: set
    pins: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    value: str = ""
    segs: list = field(default_factory=list)       # drawn line segments (world)
    polys: list = field(default_factory=list)      # closed outlines (world)
    ellipses: list = field(default_factory=list)   # (cx, cy, rx, ry)

    def hits_shape(self, pt, near: float = 20.0, skip_ends: set | None = None) -> bool:
        """Is the point on/inside the drawn symbol (not just its bounding box)?
        Segments ending at a point in `skip_ends` (pins the wire itself ends on)
        are ignored, so a wire drawn along its own pin lead is not flagged."""
        x, y = pt
        b = self.body
        if not (b[0] - near <= x <= b[2] + near and b[1] - near <= y <= b[3] + near):
            return False
        for (cx, cy, rx, ry) in self.ellipses:
            if rx > 0 and ry > 0 and ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 < 1.0:
                return True
        for poly in self.polys:
            if _point_in_poly(pt, poly):
                return True
        for a, c in self.segs:
            if skip_ends and ((round(a[0]), round(a[1])) in skip_ends or (round(c[0]), round(c[1])) in skip_ends):
                continue
            if _seg_dist(pt, a, c) < near:
                return True
        return False

    @property
    def rotation(self):
        r, m, e = qsch.code_parts(self.code)
        return r * 90 + (45 if e else 0)

    @property
    def mirror(self):
        return qsch.code_parts(self.code)[1]

    def full_box(self):
        xs = [self.body[0], self.body[2]] + [t.box[0] for t in self.texts] + [t.box[2] for t in self.texts]
        ys = [self.body[1], self.body[3]] + [t.box[1] for t in self.texts] + [t.box[3] for t in self.texts]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class LabelG:
    pos: tuple
    name: str
    just: int
    port: bool
    box: tuple
    node: object = None

    @property
    def is_ground(self):
        return self.name.upper() in ("GND", "0")


@dataclass
class Scene:
    sch: Schematic
    parts: list
    wires: list                       # (a, b, net_name)
    labels: list
    junctions: list
    texts: list                       # all TextItems (parts, labels, sheet texts)
    conn: dict
    bbox: tuple

    def part(self, ref: str) -> PartG | None:
        for p in self.parts:
            if p.ref.lower() == ref.lower():
                return p
        return None

    def pin(self, spec: str) -> PinG | None:
        if "." not in spec:
            return None
        ref, name = spec.split(".", 1)
        p = self.part(ref)
        if not p:
            return None
        for q in p.pins:
            if q.name.lower() == name.lower():
                return q
        if name.isdigit():
            i = int(name) - 1
            if 0 <= i < len(p.pins):
                return p.pins[i]
        return None

    def all_pins(self):
        for p in self.parts:
            yield from p.pins

    def owner_of_point(self, pt):
        """Which part's keep-out area contains the grid point (or None)."""
        for p in self.parts:
            if pt in p.keepout:
                return p
        return None


def build(sch: Schematic, with_keepout: bool = True) -> Scene:
    from .autodraw import _graphic_points, _hull, _local_pin_dir, _xf_dir, keepout_points
    conn = extract(sch)
    pin_net = {}
    for ref, lst in conn["pins"].items():
        for d in lst:
            pin_net[(ref, d["index"])] = d["net"]
    parts = []
    all_pin_pts = set()
    comps = sch.components()
    for c in comps:
        for _, w in c.pin_positions():
            all_pin_pts.add(tuple(w))
    texts = []
    from .connectivity import unique_refs
    for c, rname in unique_refs(sch):
        sym = c.symbol
        node = sym.node
        pts = [xform(q, c.code, c.pos) for q in _graphic_points(node)]
        if pts:
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            body = (min(xs), min(ys), max(xs), max(ys))
        else:
            body = (c.pos[0] - 100, c.pos[1] - 100, c.pos[0] + 100, c.pos[1] + 100)
        hull = _hull(pts) if pts else []
        ko = keepout_points(node, c.code, c.pos, all_pin_pts) if with_keepout else set()
        pg = PartG(rname, c, sym.name, sym.type, sym.description, c.code, tuple(c.pos), body, hull, ko,
                   value=c.value)
        _shapes(pg, node, c.code, c.pos)
        for pi, w in c.pin_positions():
            d = _xf_dir(_local_pin_dir(node, pi.local, None), c.code)
            pg.pins.append(PinG(rname, pi.name or str(pi.index + 1), pi.index, tuple(w), d,
                                pin_net.get((rname, pi.index)), bool(pi.default_net)))
        for k, tn in enumerate(sym.texts()):
            t = tn.tokens
            try:
                size = float(t[2]); just = int(t[3]); flag = int(t[4])
            except (ValueError, IndexError):
                continue
            if flag == 2:
                continue
            s = text_string(tn)
            if "=<" in s:
                s = s.replace("<", "").replace(">", "")
            if not s.strip():
                continue
            anchor = xform(t[1], c.code, c.pos)
            hh, vv, rr = screen_just(just, c.code)
            role = "refdes" if k == 0 else ("value" if k == 1 else "attr")
            ti = TextItem(rname, role, s, text_box(anchor, s, size, hh, vv, rr), anchor, tn)
            pg.texts.append(ti)
            texts.append(ti)
        parts.append(pg)
    labels = []
    for n in sch.root.findall("net"):
        tk = n.tokens
        p = tuple(tk[1]); just = int(tk[3]); port = int(tk[4]) if len(tk) > 4 else 0
        name = text_string(n)
        h, v, r = (just >> 2) & 3, just & 3, (just >> 5) & 3
        if name.upper() in ("GND", "0"):
            d = _ground_dir(just)
            tip = (p[0] + d[0] * 140, p[1] + d[1] * 140)
            nx, ny = -d[1], d[0]
            xs = [p[0], tip[0], p[0] + d[0] * 60 + nx * 100, p[0] + d[0] * 60 - nx * 100]
            ys = [p[1], tip[1], p[1] + d[1] * 60 + ny * 100, p[1] + d[1] * 60 - ny * 100]
            box = (min(xs), min(ys), max(xs), max(ys))
            role = "ground"
        else:
            off = {1: -25, 2: 25, 3: 0}.get(v, 25)
            hoff = {1: 25, 2: -25, 3: 0}.get(h, 0)
            box = text_box((p[0] + hoff, p[1] + off), name, 1.0, h or 1, v or 2, r)
            role = "label"
        lg = LabelG(p, name, just, bool(port), box, n)
        labels.append(lg)
        texts.append(TextItem("label:" + name, role, name, box, p, n))
    for n in sch.root.findall("text"):
        tk = n.tokens
        try:
            size = float(tk[2]); just = int(tk[3]); flag = int(tk[4])
        except (ValueError, IndexError):
            continue
        s = text_string(n)
        h, v, r = (just >> 2) & 3, just & 3, (just >> 5) & 3
        box = text_box(tuple(tk[1]), s, size, h or 1, v or 1, r, mono=(flag == 0))
        texts.append(TextItem("directive" if flag == 0 else "comment", "directive" if flag == 0 else "comment",
                              s, box, tuple(tk[1]), n))
    wires = [(tuple(a), tuple(b), n) for a, b, n in sch.wires()]
    xs, ys = [], []
    for p in parts:
        fb = p.full_box()
        xs += [fb[0], fb[2]]; ys += [fb[1], fb[3]]
    for a, b, _ in wires:
        xs += [a[0], b[0]]; ys += [a[1], b[1]]
    for t in texts:
        xs += [t.box[0], t.box[2]]; ys += [t.box[1], t.box[3]]
    bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)
    return Scene(sch, parts, wires, labels, [tuple(j) for j in sch.junctions()], texts, conn, bbox)


def _shapes(pg: PartG, node, code, pos):
    """Fill segs / closed polygons / ellipses of a part in world coordinates."""
    lines = []
    for c in node.children:
        tp = [t for t in c.tokens if isinstance(t, tuple)]
        k = c.kind
        W = [xform(q, code, pos) for q in tp]
        if k == "line" and len(W) >= 2:
            pg.segs.append((W[0], W[1]))
            lines.append((W[0], W[1]))
        elif k in ("rect", "zigzag", "coil") and len(W) >= 2:
            (x0, y0), (x1, y1) = W[0], W[1]
            poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            pg.polys.append(poly)
            pg.segs += list(zip(poly, poly[1:] + poly[:1]))
        elif k == "ellipse" and len(W) >= 2:
            (x0, y0), (x1, y1) = W[0], W[1]
            cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
            pg.ellipses.append((cx, cy, rx, ry))
        elif k == "triangle" and len(W) >= 3:
            pg.polys.append(W[:3])
            pg.segs += [(W[0], W[1]), (W[1], W[2]), (W[2], W[0])]
        elif k in ("arc", "arc3p") and len(W) >= 2:
            pg.segs += list(zip(W, W[1:]))
    # closed outlines made of separate lines (e.g. the op-amp triangle)
    rnd = lambda p: (round(p[0]), round(p[1]))
    adj = {}
    for a, b in lines:
        a, b = rnd(a), rnd(b)
        if a == b:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen = set()
    for start in list(adj):
        if start in seen or len(adj[start]) != 2:
            continue
        cyc = [start]
        prev, cur = None, start
        ok = False
        for _ in range(64):
            nxt = [n for n in adj[cur] if n != prev]
            if not nxt or len(adj[cur]) != 2:
                break
            prev, cur = cur, nxt[0]
            if cur == start:
                ok = True
                break
            cyc.append(cur)
        seen.update(cyc)
        if ok and len(cyc) >= 3:
            pg.polys.append(cyc)


def _point_in_poly(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def _seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _ground_dir(just: int):
    h, v = (just >> 2) & 3, just & 3
    if v == 1:
        return (0, -1)
    if v == 2:
        return (0, 1)
    if h == 2:
        return (-1, 0)
    if h == 1:
        return (1, 0)
    return (0, -1)


# ------------------------------------------------------------ geometry utils

def boxes_overlap(a, b, tol: float = 0.0) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > tol) and (min(a[3], b[3]) - max(a[1], b[1]) > tol)


def overlap_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def seg_hits_box(a, b, box, shrink: float = 0.0) -> bool:
    x0, y0, x1, y1 = box[0] + shrink, box[1] + shrink, box[2] - shrink, box[3] - shrink
    if x0 >= x1 or y0 >= y1:
        return False
    (ax, ay), (bx, by) = a, b
    if ay == by:
        return y0 < ay < y1 and min(ax, bx) < x1 and max(ax, bx) > x0
    if ax == bx:
        return x0 < ax < x1 and min(ay, by) < y1 and max(ay, by) > y0
    # generic: sample
    n = max(2, int(math.hypot(bx - ax, by - ay) / 25))
    for i in range(n + 1):
        x = ax + (bx - ax) * i / n; y = ay + (by - ay) * i / n
        if x0 < x < x1 and y0 < y < y1:
            return True
    return False


def min_translation(a, b, margin: float = 50.0):
    """Smallest axis move (dx, dy) for box a to clear box b (with margin)."""
    left = b[0] - a[2] - margin       # move a left (negative)
    right = b[2] - a[0] + margin      # move a right
    down = b[1] - a[3] - margin
    up = b[3] - a[1] + margin
    cands = [(left, 0), (right, 0), (0, down), (0, up)]
    dx, dy = min(cands, key=lambda c: abs(c[0]) + abs(c[1]))
    snap = lambda v: int(math.copysign(math.ceil(abs(v) / G) * G, v)) if v else 0
    return snap(dx), snap(dy)


def seg_points(a, b, step=G):
    """Grid points strictly inside an orthogonal segment."""
    (x0, y0), (x1, y1) = a, b
    out = []
    if x0 == x1:
        s = step if y1 > y0 else -step
        y = y0 + s
        while (y < y1) if s > 0 else (y > y1):
            out.append((x0, y)); y += s
    elif y0 == y1:
        s = step if x1 > x0 else -step
        x = x0 + s
        while (x < x1) if s > 0 else (x > x1):
            out.append((x, y0)); x += s
    return out


def on_segment_interior(p, a, b) -> bool:
    (x, y), (x0, y0), (x1, y1) = p, a, b
    if p == a or p == b:
        return False
    if x0 == x1 == x:
        return min(y0, y1) < y < max(y0, y1)
    if y0 == y1 == y:
        return min(x0, x1) < x < max(x0, x1)
    return False
