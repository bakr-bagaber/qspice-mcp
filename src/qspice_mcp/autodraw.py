"""LLM-oriented automatic schematic drawing for QSPICE.

Input is a *circuit spec* (components with pin->net connections, directives,
optional hints).  Output is a neat, readable .qsch that follows the drafting
conventions in Mike Engelhardt's "Empirically Found Drafting Guidelines"
(shipped with QSPICE):

  * signal flows left -> right, current flows top -> bottom
  * ground symbols (triangles) instead of return wires; supply rails as net
    labels instead of long wires
  * reference designator above the value, both to the right of vertical parts;
    above/below for horizontal parts
  * compact drafting on the 100-unit grid, T-junctions with dots, no 4-way
    junctions, no wire overlaps, minimal bends and crossings

Pipeline: normalize spec -> classify nets (ground / rails / signal) ->
initial layered placement -> simulated-annealing refinement (cost encodes the
conventions) -> multi-start + maze routing -> fallback labels for unroutable
nets -> text placement -> emit .qsch -> validate connectivity with QUX.exe.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import qsch
from .connectivity import GROUND_NAMES
from .model import Schematic, Symbol, make_text_node, q, set_text_string, text_string
from .qsch import Node, Q, code_parts, inv_xform, screen_to_local_just, xform
from .router import DIRS, G, OPP, Router, Terminal
from .symbols import library, make_block_symbol

CHAR_W = 75.0     # approx. width of one size-1 character in schematic units
LINE_H = 150.0

POS_RAIL = re.compile(r"^(v?cc|v?dd|vp|vpos|v\+|\+v|\+\d+v?|vbat|vsupply|vs|vin_?dc|vbus|v\d+(v\d*)?|\d+v\d*)$", re.I)
NEG_RAIL = re.compile(r"^(v?ee|v?ss|vn|vneg|v-|-v|-\d+v?)$", re.I)
AUTO_NET = re.compile(r"^(n|net|node|nn|x)?_?\d+$", re.I)
IO_NET = re.compile(r"^(v?in|v?out|input|output|vi|vo|in[pn+-]?|out[pn+-]?|in_?\d+|out_?\d+|sig\w*|fb|ref|vref|clk|en|sw|lx)$", re.I)
TIME_VARYING = re.compile(r"\b(sin|sine|pulse|pwl|exp|sffm|am|fm|ac|wavefile|noise|tri|square)\b", re.I)


# ===================================================================== spec ==

@dataclass
class Part:
    ref: str
    sym_name: str
    node: Node                    # symbol node (copied)
    pin_names: list
    pin_local: list
    pin_optional: list
    nets: list                    # per pin index: net name or None
    value: str = ""
    params: dict = field(default_factory=dict)
    extra_texts: list = field(default_factory=list)
    codes: list = field(default_factory=lambda: [0])
    code: int = 0
    pos: tuple = (0, 0)
    fixed: bool = False
    own_texts: bool = False       # keep the symbol's designed text layout
    kind: str = "2term"
    geo: dict = field(default_factory=dict)

    @property
    def npins(self):
        return len(self.pin_names)


def _type_letter(sym_type: str) -> str:
    if not sym_type:
        return "X"
    return sym_type[0]


def _norm_net(n) -> str | None:
    if n is None:
        return None
    s = str(n).strip()
    if s == "":
        return None
    return "0" if s.lower() in GROUND_NAMES else s


def build_parts(spec: dict) -> tuple[list[Part], list[str]]:
    lib = library()
    parts: list[Part] = []
    notes: list[str] = []
    seen = set()
    for c in spec.get("components", []):
        ref = str(c.get("ref") or c.get("name") or "").strip()
        symname = c.get("symbol") or c.get("type") or (ref[:1] if ref else "")
        if c.get("_symbol_node") is not None:
            node = c["_symbol_node"].copy()
            sym = Symbol(node)
        elif c.get("subckt_pins") or (str(symname).upper() == "X" and c.get("subckt")):
            pins = c.get("subckt_pins") or list((c.get("pins") or {}).keys())
            node = make_block_symbol(c.get("subckt") or c.get("value") or "SUB", pins,
                                     library=c.get("library"), left=c.get("left"), right=c.get("right"),
                                     top=c.get("top"), bottom=c.get("bottom"))
            sym = Symbol(node)
        else:
            try:
                e = lib.resolve(str(symname))
            except KeyError:
                if str(symname).upper().startswith("X") and (c.get("nodes") or c.get("pins")):
                    pins = [f"P{i+1}" for i in range(len(c.get("nodes") or []))] or list(c["pins"].keys())
                    node = make_block_symbol(c.get("value") or "SUB", pins)
                    sym = Symbol(node)
                    e = None
                else:
                    raise
            if e is not None:
                node = e.node()
                sym = Symbol(node)
        pins = sym.pins()
        names = [p.name or str(p.index + 1) for p in pins]
        nets: list = [None] * len(pins)
        if "nodes" in c and c["nodes"] is not None:
            nodes = list(c["nodes"])
            req = [i for i, p in enumerate(pins) if not p.default_net]
            # nodes map to required pins first (in order), then optional pins
            order = req + [i for i in range(len(pins)) if i not in req] if len(nodes) <= len(req) else list(range(len(pins)))
            for k, n in enumerate(nodes):
                if k < len(order):
                    nets[order[k]] = _norm_net(n)
                else:
                    notes.append(f"{ref}: extra node '{n}' ignored (symbol {sym.name} has {len(pins)} pins)")
        if "pins" in c and c["pins"]:
            for pk, n in c["pins"].items():
                idx = _match_pin(str(pk), names)
                if idx is None:
                    raise ValueError(f"{ref}: symbol {sym.name} has no pin '{pk}'. Pins: {names}")
                nets[idx] = _norm_net(n)
        missing = [names[i] for i, p in enumerate(pins) if nets[i] is None and not p.default_net]
        if missing:
            raise ValueError(f"{ref}: pins {missing} of {sym.name} are not connected (pins: {names})")
        # reference designator must start with the device letter QSPICE expects
        letter = _type_letter(sym.type)
        if not ref:
            ref = f"{letter}{len(parts)+1}"
        if letter and not ref.upper().startswith(letter.upper()):
            # QUX prefixes the device letter itself: U1 -> Ã´U1 in the netlist
            notes.append(f"{ref} is netlisted as {letter}\u00b4{ref} (QSPICE device letter '{letter}' for "
                         f"{sym.description or sym.name}); use that name in I()/P() expressions")
        if ref.lower() in seen:
            raise ValueError(f"duplicate reference designator {ref}")
        seen.add(ref.lower())
        p = Part(ref=ref, sym_name=sym.name, node=node, pin_names=names,
                 pin_local=[pp.local for pp in pins], pin_optional=[bool(pp.default_net) for pp in pins],
                 nets=nets, value=str(c.get("value", "") or ""), params=dict(c.get("params") or {}),
                 extra_texts=list(c.get("attributes") or []))
        t = sym.type
        required = [i for i in range(len(pins)) if not p.pin_optional[i]]
        if len(required) == 2 and len(pins) <= 3:
            p.kind = "2term"; p.codes = [0, 2, 4, 6]
        elif t[:1] in ("Q", "M", "J", "Z") and len(required) <= 4:
            p.kind = "fet" if t[:1] in "MJZ" else "bjt"; p.codes = [0, 8]
        elif {"IN+", "IN-", "OUT"} <= {n.upper() for n in names}:
            p.kind = "opamp"; p.codes = [0, 12]; p.own_texts = True
        else:
            p.kind = "block"; p.codes = [0]; p.own_texts = len(sym.texts()) > 2 or sym.type == "X"
        if "rotation" in c or "mirror" in c:
            rot = int(c.get("rotation", 0)) // 90 % 4
            p.codes = [qsch.make_code(rot, bool(c.get("mirror")))]
        if "position" in c and c["position"]:
            x, y = c["position"]
            p.pos = (int(round(x / G)) * G, int(round(y / G)) * G)
            p.fixed = bool(c.get("fixed", True))
        p.code = p.codes[0]
        parts.append(p)
    return parts, notes


def _match_pin(key: str, names: list[str]) -> int | None:
    for i, n in enumerate(names):
        if n == key:
            return i
    for i, n in enumerate(names):
        if n.lower() == key.lower():
            return i
    if key.isdigit() and 1 <= int(key) <= len(names):
        return int(key) - 1
    aliases = {"anode": "a", "cathode": "k", "collector": "c", "base": "b", "emitter": "e",
               "drain": "d", "gate": "g", "source": "s", "plus": "+", "minus": "-", "p": "+", "n": "-",
               "pos": "+", "neg": "-", "inp": "in+", "inn": "in-", "in_p": "in+", "in_n": "in-",
               "non_inverting": "in+", "inverting": "in-", "output": "out", "v+": "vdd", "v-": "vss",
               "vcc": "vdd", "vee": "vss"}
    k = aliases.get(key.lower())
    if k:
        for i, n in enumerate(names):
            if n.lower() == k:
                return i
    return None


# ================================================================= geometry ==

def _hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _dist_to_hull(p, hull):
    """Signed-ish distance: <=0 inside, >0 outside (distance to boundary)."""
    if len(hull) < 3:
        if not hull:
            return 1e9
        if len(hull) == 1:
            return math.dist(p, hull[0])
        return _seg_dist(p, hull[0], hull[1])
    inside = True
    n = len(hull)
    for i in range(n):
        a, b = hull[i], hull[(i + 1) % n]
        if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) < 0:
            inside = False
            break
    d = min(_seg_dist(p, hull[i], hull[(i + 1) % n]) for i in range(n))
    return -d if inside else d


def _seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _graphic_points(node: Node, skip_pin_stubs: bool = False):
    pts = []
    pinpts = {tuple(c.tokens[1]) for c in node.children if c.kind == "pin"} if skip_pin_stubs else set()
    for c in node.children:
        if c.kind in ("line", "rect", "ellipse", "triangle", "zigzag", "coil", "arc", "arc3p"):
            tp = [t for t in c.tokens if isinstance(t, tuple)]
            if c.kind == "line" and pinpts and len(tp) >= 2 and (tp[0] in pinpts or tp[1] in pinpts):
                # a lead going to a pin: keep only its inner end
                inner = tp[1] if tp[0] in pinpts else tp[0]
                pts.append(inner)
                continue
            if c.kind in ("rect", "ellipse", "zigzag", "coil") and len(tp) >= 2:
                (x0, y0), (x1, y1) = tp[0], tp[1]
                pts += [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
            else:
                pts += tp
    return pts


def _segments(node: Node):
    """Drawn primitives of a symbol as line segments (local coordinates)."""
    segs = []
    for c in node.children:
        tp = [t for t in c.tokens if isinstance(t, tuple)]
        k = c.kind
        if k == "line" and len(tp) >= 2:
            segs.append((tp[0], tp[1]))
        elif k in ("rect", "ellipse", "zigzag", "coil") and len(tp) >= 2:
            (x0, y0), (x1, y1) = tp[0], tp[1]
            if k == "zigzag" or k == "coil":
                # the drawn wiggle spans the long axis; keep its centre line + box
                pass
            segs += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
        elif k == "triangle" and len(tp) >= 3:
            segs += [(tp[0], tp[1]), (tp[1], tp[2]), (tp[2], tp[0])]
        elif k in ("arc", "arc3p") and len(tp) >= 2:
            segs += list(zip(tp, tp[1:]))
    return segs


def keepout_points(node: Node, code: int, origin, pin_points: set, dist=25.0) -> set:
    """Grid points a wire must not use: inside the body hull or within `dist`
    of any drawn primitive (pin leads included); pins themselves are exempt."""
    core = _graphic_points(node, skip_pin_stubs=True) or _graphic_points(node)
    hull = _hull([xform(q_, code, origin) for q_ in core])
    segs = [(xform(a, code, origin), xform(b, code, origin)) for a, b in _segments(node)]
    pts = [p for s in segs for p in s] + list(hull)
    if not pts:
        return set()
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    out = set()
    for gx in range(int(min(xs) // G * G) - G, int(max(xs)) + 2 * G, G):
        for gy in range(int(min(ys) // G * G) - G, int(max(ys)) + 2 * G, G):
            q_ = (gx, gy)
            if q_ in pin_points:
                continue
            if len(hull) >= 3 and _dist_to_hull(q_, hull) < 0:
                out.add(q_)
                continue
            for a, b in segs:
                if _seg_dist(q_, a, b) < dist:
                    out.add(q_)
                    break
    return out


def _local_pin_dir(node: Node, pin, allpins):
    # 1) stub line ending at the pin
    for c in node.children:
        if c.kind == "line":
            tp = [t for t in c.tokens if isinstance(t, tuple)]
            if len(tp) >= 2:
                for a, b in ((tp[0], tp[1]), (tp[1], tp[0])):
                    if a == tuple(pin) and a != b:
                        dx, dy = a[0] - b[0], a[1] - b[1]
                        if abs(dx) >= abs(dy):
                            return "R" if dx > 0 else "L"
                        return "U" if dy > 0 else "D"
    pts = _graphic_points(node)
    if pts:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        x, y = pin
        if x <= x0: return "L"
        if x >= x1: return "R"
        if y >= y1: return "U"
        if y <= y0: return "D"
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    else:
        cx, cy = 0, 0
        x, y = pin
    dx, dy = x - cx, y - cy
    if abs(dx) >= abs(dy):
        return "R" if dx > 0 else "L"
    return "U" if dy > 0 else "D"


def _xf_dir(d, code):
    v = DIRS[d]
    w = xform(v, code)
    w = (round(w[0]), round(w[1]))
    for k, vv in DIRS.items():
        if vv == w:
            return k
    return d


def _text_extent(s: str, size=1.0):
    lines = s.split("\n") or [""]
    return max(len(l) for l in lines) * CHAR_W * size, len(lines) * LINE_H * size


def prepare_geometry(p: Part):
    local_dirs = [_local_pin_dir(p.node, pl, p.pin_local) for pl in p.pin_local]
    gp = _graphic_points(p.node) + [tuple(pl) for pl in p.pin_local]
    core = _graphic_points(p.node, skip_pin_stubs=True) or gp
    labels = [p.ref, p.value] + [f"{k}={v}" for k, v in p.params.items()] + p.extra_texts
    labels = [s for s in labels if s]
    tw = max([_text_extent(s)[0] for s in labels] or [100])
    th = LINE_H * max(2, len(labels))
    for code in p.codes:
        pins = [tuple(map(int, map(round, xform(pl, code)))) for pl in p.pin_local]
        dirs = [_xf_dir(d, code) for d in local_dirs]
        hull_all = _hull([xform(q_, code) for q_ in gp])
        hull = _hull([xform(q_, code) for q_ in core])
        xs = [h[0] for h in hull_all] or [0]; ys = [h[1] for h in hull_all] or [0]
        body = (min(xs), min(ys), max(xs), max(ys))
        # label boxes (screen, relative to pos)
        lab = []
        if p.own_texts:
            for tn in Symbol(p.node).texts():
                t = tn.tokens
                if int(t[4]) == 2 or len(t) < 6:
                    continue
                s = text_string(tn).replace("<", "").replace(">", "")
                w, h = _text_extent(s, float(t[2]))
                tp = xform(t[1], code)
                hh, vv, _ = qsch.screen_just(int(t[3]), code)
                x0 = tp[0] - (0 if hh == 1 else w if hh == 2 else w / 2)
                y0 = tp[1] - (h if vv == 1 else 0 if vv == 2 else h / 2)
                lab.append((x0, y0, x0 + w, y0 + h))
        else:
            vertical = _is_vertical(p, dirs)
            if p.kind == "2term" and not vertical:
                cx = (body[0] + body[2]) / 2
                lab.append((cx - tw / 2, body[3], cx + tw / 2, body[3] + LINE_H))
                lab.append((cx - tw / 2, body[1] - LINE_H * (len(labels) - 1), cx + tw / 2, body[1]))
            else:
                side = _label_side(p, dirs)
                cy = (body[1] + body[3]) / 2
                if side == "R":
                    lab.append((body[2] + 20, cy - th / 2, body[2] + 20 + tw, cy + th / 2))
                else:
                    lab.append((body[0] - 20 - tw, cy - th / 2, body[0] - 20, cy + th / 2))
        p.geo[code] = {"pins": pins, "dirs": dirs, "hull": hull, "body": body, "labels": lab}


def _is_vertical(p: Part, dirs):
    req = [i for i in range(p.npins) if not p.pin_optional[i]]
    ds = {dirs[i] for i in req}
    return ds <= {"U", "D"}


def _label_side(p: Part, dirs):
    """Put transistor labels away from the control pin (base/gate)."""
    if p.kind in ("bjt", "fet"):
        for i, n in enumerate(p.pin_names):
            if n.upper() in ("B", "G"):
                return "L" if dirs[i] == "R" else "R"
    return "R"


# ============================================================== classification

@dataclass
class NetInfo:
    name: str
    pins: list = field(default_factory=list)   # (part_idx, pin_idx)
    kind: str = "signal"                        # ground | pos | neg | signal
    rank: float = 0.0
    labeled: bool = False


def classify(parts: list[Part], spec: dict) -> dict[str, NetInfo]:
    nets: dict[str, NetInfo] = {}
    for i, p in enumerate(parts):
        for j, n in enumerate(p.nets):
            if n is None:
                continue
            nets.setdefault(n, NetInfo(n)).pins.append((i, j))
    rails = {k: v for k, v in (spec.get("rails") or {}).items()}
    for k in list(rails):
        rails[_norm_net(k)] = rails.pop(k)
    use_rail_labels = (spec.get("options") or {}).get("use_rail_labels", True)
    for n, ni in nets.items():
        if n == "0":
            ni.kind = "ground"
        elif n in rails:
            ni.kind = "pos" if str(rails[n]).startswith("+") or str(rails[n]).lower().startswith("pos") else "neg"
        elif use_rail_labels and POS_RAIL.match(n):
            ni.kind = "pos"
        elif use_rail_labels and NEG_RAIL.match(n):
            ni.kind = "neg"
    # DC sources between a net and ground with several loads -> rail
    if use_rail_labels:
        for i, p in enumerate(parts):
            if Symbol(p.node).type in ("V",) and p.kind == "2term" and not TIME_VARYING.search(p.value or ""):
                a, b = p.nets[0], p.nets[1]
                if b == "0" and a and a != "0" and nets[a].kind == "signal" and len(nets[a].pins) >= 3:
                    nets[a].kind = "neg" if _is_negative(p.value) else "pos"
                elif a == "0" and b and b != "0" and nets[b].kind == "signal" and len(nets[b].pins) >= 3:
                    nets[b].kind = "pos" if _is_negative(p.value) else "neg"
    # rank signal nets by BFS from inputs
    inputs = [_norm_net(x) for x in (spec.get("inputs") or [])]
    if not inputs:
        for i, p in enumerate(parts):
            st = Symbol(p.node).type
            if st in ("V", "I") and TIME_VARYING.search(p.value or ""):
                inputs += [n for n in p.nets if n and n != "0" and nets[n].kind == "signal"]
        inputs += [n for n in nets if re.match(r"^(v?in|input|vi|in\d*|sig|src)$", n, re.I) and nets[n].kind == "signal"]
    outputs = [_norm_net(x) for x in (spec.get("outputs") or [])] or \
              [n for n in nets if re.match(r"^(v?out|output|vo|out\d*)$", n, re.I)]
    sig = {n for n, ni in nets.items() if ni.kind == "signal"}
    if not inputs and sig:
        # pick a signal net far from outputs: the one attached to the first source-like part
        inputs = [sorted(sig, key=lambda n: (-len(nets[n].pins), n))[0]] if not outputs else []
        if not inputs:
            inputs = [_farthest(nets, parts, outputs, sig)]
    rank = {n: 0.0 for n in inputs if n in sig}
    frontier = list(rank)
    while frontier:
        nxt = []
        for n in frontier:
            for (i, j) in nets[n].pins:
                for m in parts[i].nets:
                    if m in sig and m not in rank:
                        # series elements advance one rank; multi-pin devices too
                        rank[m] = rank[n] + 1
                        nxt.append(m)
        frontier = nxt
    maxr = max(rank.values() or [0])
    for n in sig:
        nets[n].rank = rank.get(n, maxr + 1)
    for o in outputs:
        if o in nets and nets[o].kind == "signal":
            nets[o].rank = max(nets[o].rank, maxr + 0.5)
    return nets


def _is_negative(v: str) -> bool:
    t = (v or "").strip().split()
    return bool(t) and t[0].startswith("-") or (len(t) > 1 and t[0].upper() == "DC" and t[1].startswith("-"))


def _farthest(nets, parts, outputs, sig):
    # BFS from outputs; return farthest signal net
    dist = {o: 0 for o in outputs if o in sig}
    fr = list(dist)
    while fr:
        nx = []
        for n in fr:
            for (i, j) in nets[n].pins:
                for m in parts[i].nets:
                    if m in sig and m not in dist:
                        dist[m] = dist[n] + 1; nx.append(m)
        fr = nx
    return max(dist, key=dist.get) if dist else sorted(sig)[0]


# ================================================================ placement ==

class Placer:
    def __init__(self, parts: list[Part], nets: dict[str, NetInfo], rng: random.Random, style="normal"):
        self.parts, self.nets, self.rng = parts, nets, rng
        self.spacing = {"compact": 1.0, "normal": 1.25, "spacious": 1.6}.get(style, 1.25)
        self.part_nets = [[n for n in p.nets if n] for p in parts]
        self._memo: dict = {}
        self.sig_nets = [n for n, ni in nets.items() if ni.kind == "signal" and len(ni.pins) >= 2]
        self.supply_parts = {i for i, p in enumerate(parts)
                             if self._is_source(p) and all((not n) or nets[n].kind != "signal" for n in p.nets)}
        self.input_parts = {i for i, p in enumerate(parts)
                            if self._is_source(p) and i not in self.supply_parts}
        # keep-free zones for ground symbols / rail labels hanging off pins
        for p in parts:
            for code in p.codes:
                g = p.geo[code]
                zones = []
                for j, n in enumerate(p.nets):
                    if not n or (nets[n].kind == "signal" and len(nets[n].pins) > 1):
                        continue
                    x, y = g["pins"][j]
                    d = g["dirs"][j]
                    name = "GND" if n == "0" else n
                    L = 260 if n == "0" else max(200, len(name) * CHAR_W + 60)
                    if d == "D":
                        zones.append((x - 130, y - 330, x + 130, y - 40))
                    elif d == "U":
                        zones.append((x - L / 2, y + 40, x + L / 2, y + 120 + LINE_H))
                    elif d == "L":
                        zones.append((x - 150 - L, y - 130, x - 40, y + 130))
                    else:
                        zones.append((x + 40, y - 130, x + 150 + L, y + 130))
                g["stubs"] = zones

    # ---------------------------------------------------- initial layout --
    def initial(self):
        parts, nets = self.parts, self.nets
        col: dict[int, float] = {}
        for i, p in enumerate(parts):
            ranks = [nets[n].rank for n in p.nets if n and nets[n].kind == "signal"]
            col[i] = (sum(ranks) / len(ranks)) if ranks else -1.5
            if p.kind == "2term":
                sig = [n for n in p.nets if n and nets[n].kind == "signal"]
                if len(sig) == 1:
                    col[i] = nets[sig[0]].rank
        # orientation
        for i, p in enumerate(parts):
            if p.fixed:
                continue
            p.code = self._best_code_initial(p)
        # place by columns
        cols: dict[int, list[int]] = {}
        for i, c in col.items():
            cols.setdefault(int(round(c * 2)), []).append(i)
        xstep = int(500 * self.spacing / G) * G
        for k in list(cols):
            cols[k] = [i for i in cols[k] if i not in self.supply_parts]
            if not cols[k]:
                del cols[k]
        # supply sources in a row, left of everything, near the top
        minc = min(cols) if cols else 0
        for n_, i in enumerate(sorted(self.supply_parts)):
            if not parts[i].fixed:
                parts[i].pos = ((minc - 2 - n_) * xstep, 600)
        for k in sorted(cols):
            members = cols[k]
            # shunts (to ground/rails) go below, series parts on the signal row
            members.sort(key=lambda i: (self._is_shunt(i), i))
            y = 0
            for i in members:
                p = parts[i]
                if p.fixed:
                    continue
                b = p.geo[p.code]["body"]
                h = b[3] - b[1]
                p.pos = (k * xstep, int(round((y - b[3]) / G)) * G)
                y -= int((h + 400 * self.spacing) / G) * G

    def _is_shunt(self, i):
        p = self.parts[i]
        return any(n and self.nets[n].kind != "signal" for n in p.nets)

    def _best_code_initial(self, p: Part):
        best, bc = None, 1e18
        for code in p.codes:
            c = self._orient_cost(p, code)
            if c < bc:
                best, bc = code, c
        return best

    def _is_source(self, p: Part):
        return p.kind == "2term" and Symbol(p.node).type in ("V", "I", "B") and \
            any(n and self.nets[n].kind != "signal" for n in p.nets)

    def _orient_cost(self, p: Part, code):
        g = p.geo[code]
        cost = 0.0
        src = self._is_source(p)
        if src:
            # sources stand upright, '+' on top (also for negative supplies)
            plus = 0
            for j, nm in enumerate(p.pin_names):
                if nm == "+":
                    plus = j
            d = g["dirs"][plus]
            cost += {"U": 0, "D": 6, "L": 12, "R": 12}[d]
        for j, n in enumerate(p.nets):
            if not n:
                continue
            k = self.nets[n].kind
            d = g["dirs"][j]
            if k == "ground":
                cost += {"D": 0, "L": 4, "R": 4, "U": 10}[d]
            elif src:
                continue
            elif k == "neg":
                cost += {"D": 0, "L": 3, "R": 3, "U": 8}[d]
            elif k == "pos":
                cost += {"U": 0, "L": 3, "R": 3, "D": 8}[d]
        # flow: lower rank pins should face left
        sig = [(self.nets[n].rank, j) for j, n in enumerate(p.nets) if n and self.nets[n].kind == "signal"]
        if len(sig) >= 2:
            sig.sort()
            lo, hi = sig[0][1], sig[-1][1]
            if sig[0][0] < sig[-1][0]:
                if g["pins"][lo][0] > g["pins"][hi][0]:
                    cost += 4
                if p.kind == "2term":
                    if g["dirs"][lo] != "L":
                        cost += 3.0
        elif p.kind == "2term" and len(sig) == 0:
            pass
        return cost

    # ------------------------------------------------------------ cost --
    def pin_abs(self, i):
        p = self.parts[i]
        key = ("pins", i, p.pos, p.code)
        r = self._memo.get(key)
        if r is None:
            g = p.geo[p.code]
            r = [(p.pos[0] + x, p.pos[1] + y) for x, y in g["pins"]]
            self._memo[key] = r
        return r

    def rect_abs(self, i, which="body", margin=0):
        p = self.parts[i]
        key = (which, margin, i, p.pos, p.code)
        r = self._memo.get(key)
        if r is not None:
            return r
        g = p.geo[p.code]
        if which == "body":
            b = g["body"]
            r = [(p.pos[0] + b[0] - margin, p.pos[1] + b[1] - margin, p.pos[0] + b[2] + margin, p.pos[1] + b[3] + margin)]
        else:
            rects = g["labels"] + g.get("stubs", [])
            r = [(p.pos[0] + q_[0], p.pos[1] + q_[1], p.pos[0] + q_[2], p.pos[1] + q_[3]) for q_ in rects]
        if len(self._memo) > 200000:
            self._memo.clear()
        self._memo[key] = r
        return r

    def net_box(self, n):
        pts = [self.pin_abs(i)[j] for (i, j) in self.nets[n].pins]
        xs = [q_[0] for q_ in pts]; ys = [q_[1] for q_ in pts]
        return (min(xs), min(ys), max(xs), max(ys)), len(pts)

    def block_cost(self, n, only: set | None = None):
        """Penalty for foreign part bodies lying inside a signal net's span."""
        ni = self.nets[n]
        if ni.kind != "signal" or len(ni.pins) < 2:
            return 0.0
        (x0, y0, x1, y1), npts = self.net_box(n)
        own = {i for i, _ in ni.pins}
        c = 0.0
        straight = (x0 == x1 or y0 == y1)
        rng_ = only if only is not None else range(len(self.parts))
        for k in rng_:
            if k in own:
                continue
            b = self.rect_abs(k, "body", 30)[0]
            if b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0 or (straight and _seg_hits(b, x0, y0, x1, y1)):
                c += 60 if straight or npts == 2 else 25
            if straight:
                # a straight wire would run through this part's text
                for lr in self.rect_abs(k, "labels"):
                    if _seg_hits(lr, x0, y0, x1, y1):
                        c += 35
        return c

    def part_cost(self, i):
        p = self.parts[i]
        c = self._orient_cost(p, p.code) * 40
        # stubs for ground/rails need free room: handled in overlap via pin zones
        return c

    def pair_cost(self, i, j):
        A = self.rect_abs(i, "body", 90)[0]
        B = self.rect_abs(j, "body", 90)[0]
        c = 0.0
        ov = _ov(A, B)
        if ov > 0:
            c += 400 + ov / 2000
        for la in self.rect_abs(i, "labels"):
            if _ov(la, B) > 0:
                c += 180
            for lb in self.rect_abs(j, "labels"):
                if _ov(la, lb) > 0:
                    c += 160
        for lb in self.rect_abs(j, "labels"):
            if _ov(lb, A) > 0:
                c += 180
        # pins of one part inside the other's body / on foreign pins
        pa, pb = self.pin_abs(i), self.pin_abs(j)
        Bi = self.rect_abs(j, "body", 40)[0]
        Ai = self.rect_abs(i, "body", 40)[0]
        for k, q in enumerate(pa):
            if self.parts[i].nets[k] is None:
                continue
            if Bi[0] < q[0] < Bi[2] and Bi[1] < q[1] < Bi[3]:
                c += 300
        for k, q in enumerate(pb):
            if self.parts[j].nets[k] is None:
                continue
            if Ai[0] < q[0] < Ai[2] and Ai[1] < q[1] < Ai[3]:
                c += 300
        for k, q in enumerate(pa):
            for m, r in enumerate(pb):
                if q == r and self.parts[i].nets[k] != self.parts[j].nets[m]:
                    c += 1000
                # stub zones of ground/rail pins colliding with other pins
        return c

    def net_cost(self, n):
        ni = self.nets[n]
        if ni.kind != "signal":
            # rails / ground are drawn with labels: only orientation matters
            return 0.0
        pts, dirs = [], []
        for (i, j) in ni.pins:
            pts.append(self.pin_abs(i)[j])
            p = self.parts[i]
            dirs.append(p.geo[p.code]["dirs"][j])
        if len(pts) < 2:
            return 0.0
        xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
        c = (max(xs) - min(xs) + max(ys) - min(ys)) / G
        if len(pts) == 2:
            (ax, ay), (bx, by) = pts
            da, db = dirs
            if ax != bx and ay != by:
                c += 3.0
            # facing: each pin should point toward the other
            c += _facing_pen(pts[0], da, pts[1]) + _facing_pen(pts[1], db, pts[0])
        else:
            for k, q in enumerate(pts):
                aligned = any((q[0] == r[0] or q[1] == r[1]) for m, r in enumerate(pts) if m != k)
                if not aligned:
                    c += 1.5
                cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                c += 0.5 * _facing_pen(q, dirs[k], (cx, cy))
        return c * 10

    def total(self):
        n = len(self.parts)
        c = sum(self.part_cost(i) for i in range(n))
        for i in range(n):
            for j in range(i + 1, n):
                c += self.pair_cost(i, j)
        c += sum(self.net_cost(k) for k in self.nets)
        c += sum(self.block_cost(k) for k in self.sig_nets)
        c += self.flow_cost() + self.area_cost()
        return c

    def flow_cost(self):
        # nets of increasing rank should progress left -> right
        c = 0.0
        # supply-only sources form a tidy row left of the signal circuitry
        sup = self.supply_parts
        if sup:
            sig_x = [self.parts[i].pos[0] + self.parts[i].geo[self.parts[i].code]["body"][0]
                     for i in range(len(self.parts)) if i not in sup]
            minx = min(sig_x) if sig_x else 0
            ys = [self.parts[i].pos[1] for i in sup]
            ref_y = max(ys)
            for i in sup:
                p = self.parts[i]
                right = p.pos[0] + p.geo[p.code]["body"][2]
                if right > minx - 300:
                    c += 30 + (right - minx + 300) / G * 4
                c += abs(p.pos[1] - ref_y) / G * 3
        # input (signal) sources sit at the left edge of the signal circuitry
        if self.input_parts:
            others = [self.parts[i].pos[0] for i in range(len(self.parts))
                      if i not in self.input_parts and i not in sup]
            if others:
                mo = min(others)
                for i in self.input_parts:
                    x = self.parts[i].pos[0]
                    if x > mo:
                        c += 20 + (x - mo) / G * 6
        for i, p in enumerate(self.parts):
            ranks = [(self.nets[n].rank, j) for j, n in enumerate(p.nets) if n and self.nets[n].kind == "signal"]
            if len(ranks) < 2:
                continue
            ranks.sort()
            if ranks[0][0] == ranks[-1][0]:
                continue
            pa = self.pin_abs(i)
            lo, hi = pa[ranks[0][1]], pa[ranks[-1][1]]
            if lo[0] > hi[0]:
                c += 25 + (lo[0] - hi[0]) / G * 5
        return c

    def area_cost(self):
        xs, ys = [], []
        for i in range(len(self.parts)):
            r = self.rect_abs(i, "body")[0]
            xs += [r[0], r[2]]; ys += [r[1], r[3]]
        if not xs:
            return 0
        w = (max(xs) - min(xs)) / G; h = (max(ys) - min(ys)) / G
        # prefer landscape, compact
        return 2.0 * (w + h) + 0.04 * w * h + max(0, h - w) * 1.0

    # ---------------------------------------------------------- anneal --
    def local(self, moved: set):
        c = 0.0
        n = len(self.parts)
        for i in moved:
            c += self.part_cost(i)
            for j in range(n):
                if j in moved and j < i:
                    continue
                if j != i:
                    c += self.pair_cost(i, j) if i < j or j not in moved else 0
        nets = set()
        for i in moved:
            nets.update(self.part_nets[i])
        c += sum(self.net_cost(k) for k in nets)
        # obstruction: nets of moved parts vs everyone, other nets vs moved parts
        for k in self.sig_nets:
            if k in nets:
                c += self.block_cost(k)
            else:
                c += self.block_cost(k, only=moved)
        return c

    def anneal(self, iters: int, T0: float = 60.0, T1: float = 0.3):
        parts, rng = self.parts, self.rng
        movable = [i for i, p in enumerate(parts) if not p.fixed]
        if not movable:
            return
        cur_glob = self.flow_cost() + self.area_cost()
        best_state = [(p.pos, p.code) for p in parts]
        best = self.total()
        cur = best
        for it in range(iters):
            T = T0 * (T1 / T0) ** (it / max(1, iters - 1))
            r = rng.random()
            i = rng.choice(movable)
            p = parts[i]
            moved = {i}
            old = [(i, p.pos, p.code)]
            if r < 0.45:
                span = max(1, int(4 * (T / T0) ** 0.5 + 1))
                dx = rng.randint(-span, span) * G
                dy = rng.randint(-span, span) * G
                if dx == 0 and dy == 0:
                    dx = G
                newstate = [(i, (p.pos[0] + dx, p.pos[1] + dy), p.code)]
            elif r < 0.62 and len(p.codes) > 1:
                newstate = [(i, p.pos, rng.choice([c for c in p.codes if c != p.code]))]
            elif r < 0.75 and len(movable) > 1:
                j = rng.choice([m for m in movable if m != i])
                q = parts[j]
                moved.add(j)
                old.append((j, q.pos, q.code))
                newstate = [(i, q.pos, p.code), (j, p.pos, q.code)]
            else:
                # alignment move: snap a pin onto the row/column of a connected pin
                cand = []
                for k, n in enumerate(p.nets):
                    if n and self.nets[n].kind == "signal":
                        for (a, b) in self.nets[n].pins:
                            if a != i:
                                cand.append((k, a, b))
                if not cand:
                    continue
                k, a, b = rng.choice(cand)
                mine = self.pin_abs(i)[k]
                other = self.pin_abs(a)[b]
                if rng.random() < 0.5:
                    newstate = [(i, (p.pos[0], p.pos[1] + other[1] - mine[1]), p.code)]
                else:
                    newstate = [(i, (p.pos[0] + other[0] - mine[0], p.pos[1]), p.code)]
            before = self.local(moved) + cur_glob
            for (k, pos, code) in newstate:
                parts[k].pos, parts[k].code = pos, code
            g2 = self.flow_cost() + self.area_cost()
            after = self.local(moved) + g2
            d = after - before
            if d <= 0 or rng.random() < math.exp(-d / max(T, 1e-9)):
                cur += d
                cur_glob = g2
                if cur < best - 1e-6:
                    best = cur
                    best_state = [(pp.pos, pp.code) for pp in parts]
            else:
                for (k, pos, code) in old:
                    parts[k].pos, parts[k].code = pos, code
        for pp, (pos, code) in zip(parts, best_state):
            pp.pos, pp.code = pos, code


def _seg_hits(b, x0, y0, x1, y1):
    if x0 == x1:
        return b[0] < x0 < b[2] and b[1] < y1 and b[3] > y0
    if y0 == y1:
        return b[1] < y0 < b[3] and b[0] < x1 and b[2] > x0
    return False


def _ov(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def _facing_pen(p, d, target):
    dx, dy = target[0] - p[0], target[1] - p[1]
    vx, vy = DIRS[d]
    dot = vx * dx + vy * dy
    if dx == 0 and dy == 0:
        return 0
    if dot > 0:
        return 0.0
    return 2.5 if dot == 0 else 5.0


# ================================================================== drawing ==

@dataclass
class Drawing:
    parts: list
    nets: dict
    wires: list = field(default_factory=list)       # (a, b, net)
    junctions: list = field(default_factory=list)
    labels: list = field(default_factory=list)      # (pt, name, just)
    failed: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    occupied: dict = field(default_factory=dict)


_DIR_JUST = {"U": 14, "D": 13, "L": 11, "R": 7}


def route_drawing(parts: list[Part], nets: dict[str, NetInfo], label_names: set[str], style="normal") -> Drawing:
    dr = Drawing(parts, nets)
    pin_pts = {}
    pin_net = {}
    for i, p in enumerate(parts):
        g = p.geo[p.code]
        for j, (x, y) in enumerate(g["pins"]):
            P = (p.pos[0] + x, p.pos[1] + y)
            pin_pts[(i, j)] = P
            if p.nets[j] is not None:
                pin_net[P] = p.nets[j]
            else:
                pin_net.setdefault(P, f"__nc_{p.ref}_{j}")
    xs = [q[0] for q in pin_pts.values()]; ys = [q[1] for q in pin_pts.values()]
    for i in range(len(parts)):
        p = parts[i]; b = p.geo[p.code]["body"]
        xs += [p.pos[0] + b[0], p.pos[0] + b[2]]; ys += [p.pos[1] + b[1], p.pos[1] + b[3]]
    bounds = (int(min(xs) // G * G) - 1500, int(min(ys) // G * G) - 1500, int(max(xs) // G * G) + 1500, int(max(ys) // G * G) + 1500)
    blocked = set()
    allpin = set(pin_net)
    for i, p in enumerate(parts):
        blocked |= keepout_points(p.node, p.code, p.pos, allpin)
    soft = {}
    for i, p in enumerate(parts):
        for (x0, y0, x1, y1) in [(p.pos[0] + r[0], p.pos[1] + r[1], p.pos[0] + r[2], p.pos[1] + r[3]) for r in p.geo[p.code]["labels"]]:
            for gx in range(int(x0 // G * G), int(x1) + G, G):
                for gy in range(int(y0 // G * G), int(y1) + G, G):
                    if x0 - 20 <= gx <= x1 + 20 and y0 - 20 <= gy <= y1 + 20:
                        soft[(gx, gy)] = soft.get((gx, gy), 0) + 3
    R = Router(bounds, blocked, pin_net, soft)
    # ---- stubs for ground / rails / single-pin labelled nets
    stub_list = []
    for (i, j), P in pin_pts.items():
        n = parts[i].nets[j]
        if n is None:
            continue
        ni = nets[n]
        if ni.kind in ("ground", "pos", "neg") or (len(ni.pins) == 1):
            stub_list.append((i, j, P, n))
    for (i, j, P, n) in stub_list:
        d = parts[i].geo[parts[i].code]["dirs"][j]
        placed = False
        order = [d] + [x for x in ("D", "L", "R", "U") if x != d]
        if nets[n].kind == "pos":
            order = [d, "U", "L", "R", "D"]
        for dd in order:
            if dd == OPP[d]:
                continue
            for L in (1, 2, 3):
                pts = [(P[0] + DIRS[dd][0] * G * k, P[1] + DIRS[dd][1] * G * k) for k in range(1, L + 1)]
                zone = _label_zone(pts[-1], dd, n if nets[n].kind != "ground" else "GND")
                if all(_free(R, q) for q in pts) and all(_free(R, q, soft_ok=True) for q in zone):
                    path = [P] + pts
                    key = f"__stub_{n}_{i}_{j}"
                    R.pins[pts[-1]] = key
                    R.owner[P] = key if False else R.owner.get(P, n)
                    for q_ in pts:
                        R.owner[q_] = key
                    for a, b in zip(path, path[1:]):
                        R.edges[frozenset((a, b))] = key
                    for q_ in zone:
                        R.blocked.add(q_)
                    dr.wires.append((P, pts[-1], n))
                    dr.labels.append((pts[-1], "GND" if n == "0" else n, _DIR_JUST[dd]))
                    placed = True
                    break
            if placed:
                break
        if not placed:
            # label directly on the pin
            dr.labels.append((P, "GND" if n == "0" else n, _DIR_JUST[d]))
    # ---- route signal nets: shortest first
    sig = [n for n, ni in nets.items() if ni.kind == "signal" and len(ni.pins) >= 2]
    def span(n):
        pts = [pin_pts[k] for k in nets[n].pins]
        return (len(pts), max(q[0] for q in pts) - min(q[0] for q in pts) + max(q[1] for q in pts) - min(q[1] for q in pts))
    sig.sort(key=span)
    for n in sig:
        terms = []
        for (i, j) in nets[n].pins:
            terms.append(Terminal(f"{parts[i].ref}.{parts[i].pin_names[j]}", pin_pts[(i, j)], parts[i].geo[parts[i].code]["dirs"][j]))
        r = R.route_net(n, terms)
        if r.failed:
            dr.failed.append((n, list(r.failed)))
    # ---- collect segments
    all_pins = set(pin_pts.values())
    total_len = bends = 0
    failed_nets = {n for n, _ in dr.failed}
    for n in sig:
        if n in failed_nets:
            continue
        segs, juncs = R.segments(n, all_pins)
        for a, b in segs:
            dr.wires.append((a, b, n))
            total_len += abs(a[0] - b[0]) + abs(a[1] - b[1])
        dr.junctions += juncs
        rr = R.routes.get(n)
        if rr:
            adj = {}
            for e in rr.edges:
                a, b = tuple(e)
                adj.setdefault(a, []).append(b); adj.setdefault(b, []).append(a)
            for pnt, nb in adj.items():
                if len(nb) == 2 and not (nb[0][0] == pnt[0] == nb[1][0] or nb[0][1] == pnt[1] == nb[1][1]):
                    bends += 1
    # ---- unroutable nets -> a named label on every pin of the net
    for n, keys in dr.failed:
        rr = R.routes.get(n)
        if rr:  # release the partial route so labels/stubs can use the space
            for e in rr.edges:
                R.edges.pop(e, None)
            for p_ in rr.points:
                if R.owner.get(p_) == n and p_ not in all_pins:
                    R.owner.pop(p_, None)
                if R.through.get(p_, (None,))[0] == n:
                    R.through.pop(p_, None)
            rr.edges.clear()
        for (i, j) in nets[n].pins:
            P = pin_pts[(i, j)]
            d = parts[i].geo[parts[i].code]["dirs"][j]
            q_ = (P[0] + DIRS[d][0] * G, P[1] + DIRS[d][1] * G)
            if _free(R, q_):
                dr.wires.append((P, q_, n))
                dr.labels.append((q_, n, _DIR_JUST[d]))
                R.owner[q_] = n
            else:
                dr.labels.append((P, n, _DIR_JUST[d]))
        label_names.discard(n)
    # ---- name labels on routed nets
    for n in label_names:
        if n not in nets or nets[n].kind != "signal" or len(nets[n].pins) < 2:
            continue
        rr = R.routes.get(n)
        if not rr or not rr.points:
            continue
        pt = _pick_label_point(R, rr, n, all_pins, dr)
        if pt:
            dr.labels.append(pt)
    dr.metrics = {"wire_length": total_len, "bends": bends, "crossings": len(R.cross),
                  "failed_connections": sum(len(k) for _, k in dr.failed), "junctions": len(dr.junctions)}
    dr.occupied = {"owner": R.owner, "through": R.through, "blocked": R.blocked}
    dr.router = R
    return dr


def _free(R: Router, q, soft_ok=False):
    if not R.inb(q) or q in R.blocked:
        return False
    if q in R.pins or q in R.owner or q in R.through:
        return False
    return True


def _label_zone(p, d, name):
    """grid points covered by a ground symbol / rail label text beyond p."""
    zone = []
    if name == "GND":
        q1 = (p[0] + DIRS[d][0] * G, p[1] + DIRS[d][1] * G)
        zone.append(q1)
        nx, ny = -DIRS[d][1], DIRS[d][0]
        zone.append((q1[0] + nx * G, q1[1] + ny * G)); zone.append((q1[0] - nx * G, q1[1] - ny * G))
    else:
        w = max(1, int(len(name) * CHAR_W / G + 0.5))
        if d in ("L", "R"):
            for k in range(1, w + 1):
                zone.append((p[0] + DIRS[d][0] * G * k, p[1]))
        else:
            q1 = (p[0], p[1] + DIRS[d][1] * G)
            zone.append(q1)
            half = max(0, int((len(name) * CHAR_W / 2) / G + 0.5))
            for k in range(1, half + 1):
                zone.append((q1[0] - k * G, q1[1])); zone.append((q1[0] + k * G, q1[1]))
    return zone


def _pick_label_point(R: Router, rr, n, all_pins, dr):
    adj = {}
    for e in rr.edges:
        a, b = tuple(e)
        adj.setdefault(a, []).append(b); adj.setdefault(b, []).append(a)
    w = len(n) * CHAR_W + 30
    hgt = LINE_H
    taken = [(lp, ln) for lp, ln, _ in dr.labels]
    soft = R.soft

    def box_conflicts(x0, y0, x1, y1):
        c = 0
        for gx in range(int(x0 // G * G), int(x1) + G, G):
            for gy in range(int(y0 // G * G), int(y1) + G, G):
                if not (x0 - 1 <= gx <= x1 + 1 and y0 - 1 <= gy <= y1 + 1):
                    continue
                q_ = (gx, gy)
                if q_ in R.owner or q_ in R.through or q_ in R.pins:
                    c += 3
                if q_ in R.blocked:
                    c += 2
                c += soft.get(q_, 0)
        for lp, _ in taken:
            if x0 - 150 <= lp[0] <= x1 + 150 and y0 - 150 <= lp[1] <= y1 + 150:
                c += 4
        return c

    best, bs = None, 1e9
    for p, nb in adj.items():
        if p in all_pins or p in R.cross:
            continue
        horiz = all(q[1] == p[1] for q in nb)
        vert = all(q[0] == p[0] for q in nb)
        near_pin = any(abs(p[0] - a[0]) + abs(p[1] - a[1]) <= G for a in all_pins)
        cands = []
        if horiz or len(nb) == 1:
            cands.append((14, (p[0] - w / 2, p[1] + 25, p[0] + w / 2, p[1] + 25 + hgt)))
        if vert or len(nb) == 1:
            cands.append((7, (p[0] + 30, p[1] - hgt / 2, p[0] + 30 + w, p[1] + hgt / 2)))
            cands.append((11, (p[0] - 30 - w, p[1] - hgt / 2, p[0] - 30, p[1] + hgt / 2)))
        if not cands:  # corner: try above/right
            cands.append((14, (p[0] - w / 2, p[1] + 25, p[0] + w / 2, p[1] + 25 + hgt)))
            cands.append((7, (p[0] + 30, p[1] - hgt / 2, p[0] + 30 + w, p[1] + hgt / 2)))
        for just, box in cands:
            s = box_conflicts(*box) * 10 + (3 if near_pin else 0) + (0 if len(nb) == 2 else 2) + (0 if just == 14 else 1)
            if s < bs:
                best, bs = (p, n, just), s
    return best


# ============================================================ text layout ===

def set_part_texts(p: Part, dr: Drawing | None = None):
    """Write refdes/value/params into the symbol texts and position them by
    drafting convention (only for parts that don't keep their own layout)."""
    sym = Symbol(p.node)
    texts = sym.texts()
    if not texts:
        p.node.children.append(make_text_node((100, 150), p.ref))
        texts = sym.texts()
    set_text_string(texts[0], p.ref)
    if p.own_texts:
        # programmable attributes: KEY=<val>
        if p.value and len(texts) > 1 and int(texts[1].tokens[4]) != 2 and "=" not in text_string(texts[1]):
            set_text_string(texts[1], p.value)
        elif p.value:
            # value is a model/subckt name for blocks: put into the 2nd visible text
            vis = [t for t in texts[1:] if int(t.tokens[4]) != 1]
            if vis and "=" not in text_string(vis[0]):
                set_text_string(vis[0], p.value)
            elif p.value.strip():
                for kv in re.findall(r"(\S+?)=(\S+)", p.value):
                    p.params.setdefault(kv[0], kv[1])
        for k, v in p.params.items():
            done = False
            for t in texts[1:]:
                s = text_string(t)
                if s.lower().startswith(k.lower() + "="):
                    set_text_string(t, f"{k}=<{v}>" if "=<" in s else f"{k}={v}")
                    done = True
                    break
            if not done:
                last = texts[-1].tokens[1]
                p.node.children.insert(_last_text_index(p.node) + 1,
                                       make_text_node((last[0], last[1] - 80), f"{k}={v}", size=0.5))
        return
    vals = [p.value] if p.value else []
    vals += [f"{k}={v}" for k, v in p.params.items()] + list(p.extra_texts)
    # make sure there are enough text nodes
    while len(sym.texts()) < 1 + max(1, len(vals)):
        p.node.children.insert(_last_text_index(p.node) + 1, make_text_node((100, -150), ""))
    texts = sym.texts()
    if vals:
        for t, s in zip(texts[1:], vals):
            set_text_string(t, s)
    elif len(texts) > 1:
        set_text_string(texts[1], "")
    g = p.geo[p.code]
    b = g["body"]
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    lines = [p.ref] + vals
    vertical = _is_vertical(p, g["dirs"])
    placements = []  # (screen_pos, h, v)
    if p.kind == "2term" and not vertical:
        placements.append(((cx, b[3] + 10), 3, 2))           # refdes above
        y = b[1] - 10
        for _ in vals:
            placements.append(((cx, y), 3, 1)); y -= LINE_H    # values below
    else:
        side = _label_side(p, g["dirs"])
        if dr is not None:
            side = _choose_side(p, dr, side, lines)
        x = b[2] + 20 if side == "R" else b[0] - 20
        hj = 1 if side == "R" else 2
        n = len(lines)
        top = cy + (n - 1) * LINE_H / 2 + (50 if n == 2 else 0)
        if n == 2:
            ys = [cy + 150, cy - 150] if p.kind == "2term" else [cy + 160, cy - 90]
        else:
            ys = [top - k * LINE_H for k in range(n)]
        for k in range(n):
            placements.append(((x, ys[k]), hj, 3))
    for t, (sp, h, v) in zip(texts, placements):
        local = inv_xform(sp, p.code)
        just = screen_to_local_just(h, v, p.code)
        tok = t.tokens
        s = text_string(t)
        rest = t.header.split(") ", 1)[1].split(" ", 2)  # size just rest...
        size = rest[0]
        tail = rest[2] if len(rest) > 2 else '0 0x1000000 -1 -1 ""'
        t.header = f"text ({int(round(local[0]))},{int(round(local[1]))}) {size} {just} {tail}"
        set_text_string(t, s)


def _last_text_index(node: Node):
    idx = 0
    for k, c in enumerate(node.children):
        if c.kind == "text":
            idx = k
    return idx


def _choose_side(p: Part, dr: Drawing, default, lines):
    g = p.geo[p.code]
    b = g["body"]
    w = max(_text_extent(s)[0] for s in lines)
    h = LINE_H * len(lines)
    cy = p.pos[1] + (b[1] + b[3]) / 2
    R = getattr(dr, "router", None)
    def conflicts(side):
        if side == "R":
            x0 = p.pos[0] + b[2] + 20; x1 = x0 + w
        else:
            x1 = p.pos[0] + b[0] - 20; x0 = x1 - w
        y0, y1 = cy - h / 2, cy + h / 2
        c = 0
        if R:
            for gx in range(int(x0 // G * G), int(x1) + G, G):
                for gy in range(int(y0 // G * G), int(y1) + G, G):
                    if x0 <= gx <= x1 and y0 <= gy <= y1:
                        if (gx, gy) in R.owner or (gx, gy) in R.through or (gx, gy) in R.pins:
                            c += 2
                        if (gx, gy) in R.blocked:
                            c += 1
        return c
    other = "L" if default == "R" else "R"
    ca, cb = conflicts(default), conflicts(other)
    # the conventional side wins unless it is clearly worse
    return default if ca <= cb + 3 else other


# ================================================================== emit ====

def emit(dr: Drawing, directives: list[str], title: str | None = None, comments: list | None = None) -> Schematic:
    sch = Schematic()
    for p in dr.parts:
        set_part_texts(p, dr)
        sch.add_component(p.node, p.pos, p.code)
    named = {}
    for pt_, name, just in dr.labels:
        sch.add_net_label(pt_, name, just)
    for p_ in sorted(set(dr.junctions)):
        sch.add_junction(p_)
    for a, b, n in dr.wires:
        sch.add_wire(a, b, "" if n is None else ("GND" if n == "0" else n))
    from .render import schematic_bbox
    bb = schematic_bbox(sch, include_text=True)
    if directives:
        sch.add_text((bb[0] + 250, bb[1] + 100), "\n".join(directives), directive=True, size=1, just=5)
    if title:
        sch.add_text((bb[0] + 250, bb[3] + 50), title, directive=False, size=1.0, just=6)
    for c in comments or []:
        sch.add_text(tuple(c.get("position", (bb[2], bb[3]))), c["text"], directive=False, size=c.get("size", 1), just=7)
    return sch


def _directive_nets(directives: list[str]) -> set[str]:
    names = set()
    for d in directives:
        for m in re.finditer(r"\b[VI]\w*\(\s*([^()]+?)\s*\)", d, re.I):
            for a in m.group(1).split(","):
                names.add(a.strip())
    return names


def _score(dr: Drawing) -> float:
    m = dr.metrics
    return m["wire_length"] / G + 4 * m["bends"] + 10 * m["crossings"] + 150 * m["failed_connections"]


def draw(spec: dict, out_path: str | Path | None = None, validate: bool = True) -> dict:
    """Main entry: spec -> .qsch (+ validation report)."""
    opts = spec.get("options") or {}
    effort = int(opts.get("effort", 2))
    style = opts.get("style", "normal")
    seed = int(opts.get("seed", 1))
    parts, notes = build_parts(spec)
    if not parts:
        raise ValueError("spec has no components")
    for p in parts:
        prepare_geometry(p)
    nets = classify(parts, spec)
    directives = [d for d in (spec.get("directives") or []) if str(d).strip()]
    policy = spec.get("label_nets", "auto")
    referenced = {x.lower() for x in _directive_nets(directives)}
    label_names = set()
    if isinstance(policy, list):
        label_names = {_norm_net(n) for n in policy}
        policy = "auto"
    for n, ni in nets.items():
        if ni.kind != "signal":
            continue
        if policy == "all" and not AUTO_NET.match(n):
            label_names.add(n)
        elif policy != "none" and (n.lower() in referenced or IO_NET.match(n)):
            label_names.add(n)
    n = len(parts)
    iters = int(min(60000, (900 + 450 * n) * effort))
    starts = max(1, min(6, effort * 2))
    if n > 25:
        starts = max(1, starts - 2)      # big sheets: fewer, longer anneals
    elif n > 14:
        starts = max(1, starts - 1)
    if "starts" in opts:
        starts = max(1, int(opts["starts"]))
    best = None
    tried = []
    for s in range(starts):
        rng = random.Random(seed * 7919 + s)
        for p in parts:
            if not p.fixed:
                p.code = p.codes[0]
        pl = Placer(parts, nets, rng, style)
        pl.initial()
        if s > 0:
            # diversify: jitter initial positions
            for p in parts:
                if not p.fixed:
                    p.pos = (p.pos[0] + rng.randint(-3, 3) * G, p.pos[1] + rng.randint(-3, 3) * G)
                    p.code = rng.choice(p.codes) if rng.random() < .3 else p.code
        temps = [10.0, 60.0, 25.0, 60.0, 15.0, 40.0]
        pl.anneal(iters, T0=temps[s % len(temps)])
        state = [(p.pos, p.code) for p in parts]
        dr = route_drawing(parts, nets, set(label_names), style)
        sc = _score(dr) + pl.total() / 60.0
        tried.append(round(sc, 1))
        if best is None or sc < best[0]:
            best = (sc, state)
    for p, (pos, code) in zip(parts, best[1]):
        p.pos, p.code = pos, code
    dr = route_drawing(parts, nets, set(label_names), style)
    # fresh symbol copies before writing texts (route_drawing doesn't touch nodes)
    sch = emit(dr, directives, spec.get("title"), spec.get("comments"))
    out = Path(out_path) if out_path else None
    result = {"notes": notes, "metrics": dr.metrics, "attempt_scores": tried,
              "nets": {k: {"kind": v.kind, "pins": [f"{parts[i].ref}.{parts[i].pin_names[j]}" for i, j in v.pins]} for k, v in nets.items()},
              "refdes": [p.ref for p in parts]}
    if dr.failed:
        result["label_fallbacks"] = [f"{n}: {', '.join(k)}" for n, k in dr.failed]
    if out:
        sch.save(out)
        result["path"] = str(out)
        if validate:
            result["validation"] = validate_drawing(out, parts)
    result["_schematic"] = sch
    return result


def validate_drawing(path, parts: list[Part]) -> dict:
    from . import runner
    from .connectivity import compare_connectivity
    try:
        cir = runner.netlist(path)
    except Exception as e:
        return {"ok": False, "problems": [f"netlisting failed: {e}"]}
    txt = runner.read_text(cir)
    pin_counts = {p.ref: p.npins for p in parts}
    from .connectivity import parse_netlist_connections
    actual = parse_netlist_connections(txt, pin_counts)
    # DLL blocks (Ø) are netlisted with ports grouped by direction, not in pin
    # order -> check those geometrically, translating names through other pins
    from .connectivity import extract
    from .model import Schematic as _S
    geo = extract(_S.load(path))
    geo_to_qux = {}
    dll = {p.ref for p in parts if Symbol(p.node).type.startswith("\u00d8")}
    for p in parts:
        if p.ref in dll or p.ref not in actual:
            continue
        for d in geo["pins"].get(p.ref, []):
            j = d["index"]
            if j < len(actual[p.ref]):
                geo_to_qux.setdefault(d["net"], actual[p.ref][j])
    intended, act2 = {}, {}
    for p in parts:
        idx = [j for j in range(p.npins) if p.nets[j] is not None]
        intended[p.ref] = [p.nets[j] for j in idx]
        if p.ref in dll:
            gp = {d["index"]: d["net"] for d in geo["pins"].get(p.ref, [])}
            act2[p.ref] = [geo_to_qux.get(gp.get(j), "g:" + str(gp.get(j))).lower() for j in idx]
        elif p.ref in actual:
            act2[p.ref] = [actual[p.ref][j] if j < len(actual[p.ref]) else "?" for j in idx]
    res = compare_connectivity(intended, act2)
    res["netlist"] = txt
    return res
