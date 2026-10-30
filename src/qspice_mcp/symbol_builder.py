"""Create QSPICE symbols (.qsym) the right way.

Conventions enforced (from QSPICE's own symbols):
  * pins sit on the 100-unit grid, at the OUTER end of a lead line, so wires
    attach outside the body; pin order in the file = SPICE node order
  * «type: X» (subcircuit), or a primitive device letter (R, C, D, Q...); blank
    type = hierarchical block (a child schematic with the same name)
  * first text = reference designator (e.g. "X1"), second = value/model name;
    further texts = extra attributes passed on the netlist line (e.g. "Rload=1K")
  * «library file: <path>» tells QUX which model file to .lib in
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import qsch
from .model import Symbol, q
from .qsch import Node

G = 100
CHAR_W = 75


# ------------------------------------------------------------------ helpers

def _pt(x, y):
    return f"({int(round(x))},{int(round(y))})"


def _line(a, b):
    return Node(f"line {_pt(*a)} {_pt(*b)} 0 0 0x1000000 -1 -1")


def _rect(a, b, fill=False):
    return Node(f"rect {_pt(*a)} {_pt(*b)} 0 0 0 0x1000000 {'0x3000000' if fill else '0x1000000'} -1 0 -1")


def _ellipse(a, b, fill=False):
    return Node(f"ellipse {_pt(*a)} {_pt(*b)} 0 0 0 0x1000000 {'0x3000000' if fill else '0x1000000'} -1 -1")


def _triangle(a, b, c, fill=True):
    return Node(f"triangle {_pt(*a)} {_pt(*b)} {_pt(*c)} 0 0 0x1000000 {'0x3000000' if fill else '0x1000000'} -1 -1")


def _text(at, s, size=1.0, just=7, flag=0):
    sz = int(size) if float(size).is_integer() else size
    return Node(f"text {_pt(*at)} {sz} {just} {flag} 0x1000000 -1 -1 {q(s)}")


def _pin(at, name, label_off=(0, 0), just=0, size=1.0, default_net=None):
    sz = int(size) if float(size).is_integer() else size
    tail = f" {q(default_net)}" if default_net else ""
    return Node(f"pin {_pt(*at)} {_pt(*label_off)} {sz} {just} 0 0x1000000 -1 {q(name)}{tail}")


def _snap(v):
    return int(round(v / G)) * G


DIR_VEC = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1),
           "L": (-1, 0), "R": (1, 0), "U": (0, 1), "D": (0, -1), "up": (0, 1), "down": (0, -1)}

_IN = re.compile(r"^(in|inp|inn|in\+|in-|vin|en|enable|clk|d|set|reset|rst|ref|fb|sense|ctrl|ss|rt|ct|iset|ilim|uvlo|sync|mode|a\d*|b\d*|x\d*|s\d*)$", re.I)
_OUT = re.compile(r"^(out|vout|q|qb|q_|sw|lx|drv|gate|hg|lg|hsg|lsg|ho|lo|o\d*|y\d*|pgood|pg|flt|fault)$", re.I)
_PWR = re.compile(r"^(vcc|vdd|v\+|vp|vs\+|vbat|boot|bst|vpp|avdd|dvdd|pvin|v12|v5|v3v3|vin_?pwr)$", re.I)
_GND = re.compile(r"^(gnd|vss|v-|vee|agnd|pgnd|dgnd|vs-|vn|com|0|ep|pad)$", re.I)


def _name_side(name: str) -> str | None:
    """Side implied by a conventional pin name, or None if the name says nothing."""
    if _GND.match(name):
        return "bottom"
    if _PWR.match(name):
        return "top"
    if _OUT.match(name) or re.match(r"^(v?out\w*|\w*_?out\d*)$", name, re.I):
        return "right"
    if _IN.match(name) or re.match(r"^(v?in\w*|\w*_?in\d*)$", name, re.I):
        return "left"
    return None


def auto_side(name: str) -> str:
    """Drafting convention: inputs left, outputs right, supplies top, ground bottom."""
    return _name_side(name) or "left"


# ---------------------------------------------------------------- templates

def build(spec: dict) -> tuple[Node, list[str]]:
    kind = (spec.get("kind") or spec.get("template") or "block").lower()
    if kind in ("block", "ic", "box", "subckt", "subcircuit"):
        node, warn = _block(spec)
    elif kind in ("two_terminal", "2terminal", "two-terminal", "2pin"):
        node, warn = _two_terminal(spec)
    elif kind in ("amplifier", "amp", "opamp", "comparator", "triangle"):
        node, warn = _amplifier(spec)
    elif kind in ("custom", "free", "graphics"):
        node, warn = _custom(spec)
    else:
        raise ValueError("kind must be one of: block, two_terminal, amplifier, custom")
    warn += [f"{i['severity']}: {i['message']}" for i in validate(node) if i["severity"] != "ok"]
    return node, warn


def _header(spec, default_type="X"):
    name = spec.get("name") or "SYMBOL"
    if not re.match(r"^[A-Za-z0-9_\-+.]+$", name):
        raise ValueError(f"symbol name '{name}' must use letters, digits, _ - + . only")
    typ = spec.get("spice_prefix", spec.get("type", default_type))
    ch = [Node(f"type: {typ}")] if typ is not None else []
    ch.append(Node(f"description: {spec.get('description') or ('Subcircuit ' + name)}"))
    if spec.get("library"):
        ch.append(Node(f"library file: {spec['library']}"))
    ch.append(Node("shorted pins: false"))
    return name, typ, ch


def _refdes(spec, typ):
    r = spec.get("refdes")
    if r:
        return r
    letter = (typ or "X")[:1] if typ else "X"
    return f"{letter}1"


def _block(spec):
    warn = []
    name, typ, ch = _header(spec, "X")
    pins = _norm_pins(spec.get("pins"), warn)
    sides = {"left": [], "right": [], "top": [], "bottom": []}
    for p in pins:
        s = (p.get("side") or auto_side(p["name"])).lower()
        s = {"l": "left", "r": "right", "t": "top", "u": "top", "up": "top", "b": "bottom", "d": "bottom",
             "down": "bottom"}.get(s, s)
        if s not in sides:
            raise ValueError(f"pin {p['name']}: side must be left/right/top/bottom")
        sides[s].append(p["name"])
    pitch = _snap(spec.get("pin_pitch", 200)) or 200
    lead = _snap(spec.get("lead", 200)) or 200
    nlr = max(len(sides["left"]), len(sides["right"]), 1)
    ntb = max(len(sides["top"]), len(sides["bottom"]), 0)
    longest_lr = max([len(n) for n in sides["left"]] + [0]) + max([len(n) for n in sides["right"]] + [0])
    body = spec.get("body") or {}
    hw = max(300, _ceil_g((longest_lr * CHAR_W + 200) / 2), _ceil_g(((ntb + 1) * pitch) / 2))
    hh = max(300, _ceil_g(((nlr + 1) * pitch) / 2))
    if body.get("width"):
        hw = max(hw, _ceil_g(body["width"] / 2))
    if body.get("height"):
        hh = max(hh, _ceil_g(body["height"] / 2))
    if ntb and longest_lr * CHAR_W > 2 * hw - 200:
        warn.append("pin names are long for the body width; consider shorter names or body.width")
    pos = {}

    def spread(n, half):
        if n == 0:
            return []
        start = ((n - 1) * pitch) / 2
        vals = [start - i * pitch for i in range(n)]
        return [_snap(v) if abs(v - _snap(v)) < 1 else int(v) for v in vals]

    for p, y in zip(sides["left"], spread(len(sides["left"]), hh)):
        pos[p] = (-hw - lead, y, "left")
    for p, y in zip(sides["right"], spread(len(sides["right"]), hh)):
        pos[p] = (hw + lead, y, "right")
    for p, x in zip(sides["top"], spread(len(sides["top"]), hw)):
        pos[p] = (-x, hh + lead, "top")
    for p, x in zip(sides["bottom"], spread(len(sides["bottom"]), hw)):
        pos[p] = (-x, -hh - lead, "bottom")
    for pn, (x, y, s) in pos.items():
        if x % G or y % G:
            warn.append(f"pin {pn} landed off-grid at ({x},{y}); use an even pin count per side or pin_pitch 200")
    ch.append(_rect((-hw, hh), (hw, -hh)))
    for p in pins:
        x, y, s = pos[p["name"]]
        if s == "left":
            ch.append(_line((x, y), (-hw, y)))
        elif s == "right":
            ch.append(_line((hw, y), (x, y)))
        elif s == "top":
            ch.append(_line((x, y), (x, hh)))
        else:
            ch.append(_line((x, -hh), (x, y)))
    model = spec.get("model") or spec.get("value") or name
    refdes = _refdes(spec, typ)
    attrs = list(spec.get("attributes") or [])
    top_xs = [pos[p][0] for p in sides["top"]]
    bot_xs = [pos[p][0] for p in sides["bottom"]]

    def clear(xs, x0, x1):
        return all(not (x0 - 60 <= x <= x1 + 60) for x in xs)

    rw = len(refdes) * CHAR_W
    vw = max([len(model) * CHAR_W] + [len(a) * CHAR_W * 0.8 for a in attrs])
    # reference designator: above the body (left, else right, else above the top leads)
    if clear(top_xs, -hw, -hw + rw):
        ref_at, ref_j, ref_side = (-hw, hh + 40), 6, "L"
    elif clear(top_xs, hw - rw, hw):
        ref_at, ref_j, ref_side = (hw, hh + 40), 10, "R"
    else:
        ref_at, ref_j, ref_side = (-hw, hh + lead + 60), 6, "L"
    ch.append(_text(ref_at, refdes, 1, ref_j))
    # value / model (+ attributes): below (left, else right), else above-right, else inside
    if clear(bot_xs, -hw, -hw + vw):
        v_at, v_j, step = (-hw, -hh - 40), 5, -150
    elif clear(bot_xs, hw - vw, hw):
        v_at, v_j, step = (hw, -hh - 40), 9, -150
    elif ref_side == "L" and clear(top_xs, hw - vw, hw) and -hw + rw + 100 < hw - vw:
        v_at, v_j, step = (hw, hh + 40 + 150 * len(attrs)), 10, -150
    else:
        v_at, v_j, step = (0, 0), 15, -130
    ch.append(_text(v_at, model, 1, v_j))
    for k, attr in enumerate(attrs):
        ch.append(_text((v_at[0], v_at[1] + (k + 1) * step), attr, 0.8, v_j))
    show = spec.get("show_pin_names", True)
    for p in pins:
        x, y, s = pos[p["name"]]
        size = 0.8 if show else 0
        if s == "left":
            ch.append(_pin((x, y), p["name"], (lead + 40, 0), 7 if show else 0, size, p.get("default_net")))
        elif s == "right":
            ch.append(_pin((x, y), p["name"], (-lead - 40, 0), 11 if show else 0, size, p.get("default_net")))
        elif s == "top":
            ch.append(_pin((x, y), p["name"], (0, -lead - 40), 13 if show else 0, size, p.get("default_net")))
        else:
            ch.append(_pin((x, y), p["name"], (0, lead + 40), 14 if show else 0, size, p.get("default_net")))
    return Node(f"symbol {name}", ch, leaf=False), warn


def _ceil_g(v):
    return int(math.ceil(v / G)) * G


def _norm_pins(pins, warn):
    if not pins:
        raise ValueError("a symbol needs at least one pin (pins: [{'name': 'IN', 'side': 'left'}, ...])")
    out = []
    seen = set()
    for i, p in enumerate(pins):
        if isinstance(p, str):
            p = {"name": p}
        p = dict(p)
        nm = str(p.get("name") or "").strip()
        if not nm:
            raise ValueError(f"pin #{i + 1} has no name")
        if nm.lower() in seen:
            raise ValueError(f"duplicate pin name '{nm}'")
        seen.add(nm.lower())
        p["name"] = nm
        out.append(p)
    return out


def _two_terminal(spec):
    warn = []
    name, typ, ch = _header(spec, spec.get("spice_prefix", "X"))
    pins = _norm_pins(spec.get("pins") or [{"name": "1"}, {"name": "2"}], warn)
    if len(pins) != 2:
        raise ValueError("two_terminal symbols have exactly 2 pins (top pin first in SPICE order)")
    style = (spec.get("body_style") or spec.get("style") or "box").lower()
    top, bot = (0, 200), (0, -200)
    if style == "box":
        ch.append(_rect((-60, 130), (60, -130)))
        ch += [_line(top, (0, 130)), _line((0, -130), bot)]
    elif style in ("circle", "source"):
        ch.append(_ellipse((-130, 130), (130, -130)))
        ch += [_line(top, (0, 130)), _line((0, -130), bot)]
    elif style in ("resistor", "zigzag"):
        ch.append(Node("zigzag (-80,180) (80,-180) 0 0 0 0x1000000 -1 -1"))
        ch += [_line(top, (0, 180)), _line((0, -180), bot)]
    elif style in ("inductor", "coil"):
        ch.append(Node("coil (-80,180) (80,-180) 0 0 0 0x1000000 -1 -1"))
        ch += [_line(top, (0, 180)), _line((0, -180), bot)]
    elif style in ("capacitor", "cap"):
        ch += [_rect((-130, 30), (130, 15), True), _rect((-130, -15), (130, -30), True),
               _line(top, (0, 30)), _line((0, -30), bot)]
    elif style == "diode":
        ch += [_triangle((-100, 70), (100, 70), (0, -80), True), _line((-100, -80), (100, -80)),
               _line(top, (0, 70)), _line((0, -80), bot)]
    else:
        raise ValueError("body_style: box | circle | resistor | inductor | capacitor | diode")
    if spec.get("label"):
        ch.append(_text((0, 0), spec["label"], 0.6, 15))
    ch.append(_text((100, 150), _refdes(spec, typ), 1, 7))
    ch.append(_text((100, -150), spec.get("model") or spec.get("value") or name, 1, 7))
    for k, attr in enumerate(spec.get("attributes") or []):
        ch.append(_text((100, -300 - k * 150), attr, 0.8, 7))
    ch.append(_pin(top, pins[0]["name"]))
    ch.append(_pin(bot, pins[1]["name"]))
    return Node(f"symbol {name}", ch, leaf=False), warn


def _amplifier(spec):
    """Triangle pointing right. pins: give names with side left (inputs), right
    (output), top/bottom (supplies)."""
    warn = []
    name, typ, ch = _header(spec, "X")
    pins = _norm_pins(spec.get("pins"), warn)
    ins = [p for p in pins if (p.get("side") or auto_side(p["name"])) == "left"]
    outs = [p for p in pins if (p.get("side") or auto_side(p["name"])) == "right"]
    tops = [p for p in pins if (p.get("side") or auto_side(p["name"])) == "top"]
    bots = [p for p in pins if (p.get("side") or auto_side(p["name"])) == "bottom"]
    if len(ins) > 3 or len(outs) > 1 or len(tops) > 1 or len(bots) > 1:
        raise ValueError("amplifier template: up to 3 left inputs, 1 output (right), 1 top and 1 bottom pin")
    ch += [_line((-200, 400), (-200, -400)), _line((-200, 400), (400, 0)), _line((-200, -400), (400, 0))]
    pos = {}
    in_y = {1: [0], 2: [200, -200], 3: [200, 0, -200]}[len(ins)] if ins else []
    for p, y in zip(ins, in_y):
        pos[p["name"]] = (-400, y)
        ch.append(_line((-400, y), (-200, y)))
        nm = p["name"].upper()
        if nm in ("IN+", "+", "INP", "NONINV"):
            ch += [_line((-170, y), (-90, y)), _line((-130, y + 40), (-130, y - 40))]
        elif nm in ("IN-", "-", "INN", "INV"):
            ch.append(_line((-170, y), (-90, y)))
    for p in outs:
        pos[p["name"]] = (600, 0)
        ch.append(_line((400, 0), (600, 0)))
    for p in tops:
        pos[p["name"]] = (0, 500)
        ch.append(_line((0, 500), (0, 267)))
    for p in bots:
        pos[p["name"]] = (0, -500)
        ch.append(_line((0, -500), (0, -267)))
    ch.append(_text((150, 300), _refdes(spec, typ), 1, 7))
    ch.append(_text((150, -300), spec.get("model") or spec.get("value") or name, 0.8, 7))
    for k, attr in enumerate(spec.get("attributes") or []):
        ch.append(_text((150, -450 - k * 130), attr, 0.7, 7))
    for p in pins:
        x, y = pos[p["name"]]
        ch.append(_pin((x, y), p["name"], (0, 0), 0, 1, p.get("default_net")))
    return Node(f"symbol {name}", ch, leaf=False), warn


def _custom(spec):
    """graphics: list of {"line":[[x,y],[x,y]]} | {"rect":[[x,y],[x,y]],"fill":bool} |
    {"circle":[cx,cy,r]} | {"ellipse":[[x,y],[x,y]]} | {"polygon"/"triangle":[[x,y],[x,y],[x,y]]} |
    {"arc":[[x,y],[x,y],[x,y]]} (3 points on the arc) | {"text":"...","at":[x,y],"size":0.8,"align":"center"}.
    pins: [{"name","x","y","dir":"left|right|up|down","lead":200}] - the pin is the
    OUTER end of the lead; a lead line of length `lead` is drawn inward."""
    warn = []
    name, typ, ch = _header(spec, "X")
    for g in spec.get("graphics") or []:
        if "line" in g:
            a, b = g["line"]
            ch.append(_line(a, b))
        elif "rect" in g:
            a, b = g["rect"]
            ch.append(_rect(a, b, g.get("fill", False)))
        elif "circle" in g:
            cx, cy, r = g["circle"]
            ch.append(_ellipse((cx - r, cy + r), (cx + r, cy - r), g.get("fill", False)))
        elif "ellipse" in g:
            a, b = g["ellipse"]
            ch.append(_ellipse(a, b, g.get("fill", False)))
        elif "triangle" in g or "polygon" in g:
            pts = g.get("triangle") or g.get("polygon")
            if len(pts) == 3:
                ch.append(_triangle(*pts, fill=g.get("fill", True)))
            else:
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    ch.append(_line(a, b))
        elif "arc" in g:
            a, b, c = g["arc"]
            ch.append(Node(f"arc3p {_pt(*a)} {_pt(*b)} {_pt(*c)} 0 0 0x1000000 -1 -1"))
        elif "text" in g:
            align = {"left": 7, "right": 11, "center": 15}.get(g.get("align", "center"), 15)
            ch.append(_text(g.get("at", (0, 0)), g["text"], g.get("size", 0.8), align, 1))
        else:
            warn.append(f"unknown graphics item {list(g)[:1]}")
    pins = _norm_pins(spec.get("pins"), warn)
    refpos = spec.get("refdes_at") or (200, 200)
    valpos = spec.get("value_at") or (200, -200)
    ch.append(_text(refpos, _refdes(spec, typ), 1, 7))
    ch.append(_text(valpos, spec.get("model") or spec.get("value") or name, 1, 7))
    for k, attr in enumerate(spec.get("attributes") or []):
        ch.append(_text((valpos[0], valpos[1] - (k + 1) * 150), attr, 0.8, 7))
    for p in pins:
        if "x" not in p or "y" not in p:
            raise ValueError(f"custom pin {p['name']} needs x and y")
        x, y = _snap(p["x"]), _snap(p["y"])
        if (x, y) != (p["x"], p["y"]):
            warn.append(f"pin {p['name']} snapped to the 100 grid: ({x},{y})")
        d = p.get("dir")
        lead = p.get("lead", 200)
        if d:
            vx, vy = DIR_VEC[d]
            inner = (x - vx * lead, y - vy * lead)
            if lead:
                ch.append(_line((x, y), inner))
        show = p.get("show_name", False)
        if show and d:
            vx, vy = DIR_VEC[d]
            off = (-vx * (lead + 40), -vy * (lead + 40))
            just = {(-1, 0): 7, (1, 0): 11, (0, 1): 13, (0, -1): 14}[(vx, vy)]
            ch.append(_pin((x, y), p["name"], off, just, 0.8, p.get("default_net")))
        else:
            ch.append(_pin((x, y), p["name"], (0, 0), 0, 1, p.get("default_net")))
    return Node(f"symbol {name}", ch, leaf=False), warn


# ------------------------------------------------------------- validation

def validate(node: Node) -> list[dict]:
    """Check a symbol against QSPICE conventions."""
    out = []
    s = Symbol(node)
    pins = s.pins()
    if not pins:
        out.append({"severity": "error", "message": "symbol has no pins"})
    names = [p.name for p in pins]
    if len(set(n.lower() for n in names)) != len(names):
        out.append({"severity": "error", "message": f"duplicate pin names: {names}"})
    lines = []
    for c in node.children:
        if c.kind == "line":
            tp = [t for t in c.tokens if isinstance(t, tuple)]
            if len(tp) >= 2:
                lines.append((tuple(tp[0]), tuple(tp[1])))
    from .autodraw import _graphic_points, _hull, _dist_to_hull
    core = _graphic_points(node, skip_pin_stubs=True)
    hull = _hull(core) if core else []
    for p in pins:
        x, y = p.local
        if x % G or y % G:
            out.append({"severity": "error", "message": f"pin {p.name} at {p.local} is off the 100-unit grid; wires cannot attach cleanly"})
        if not any(tuple(p.local) in (a, b) for a, b in lines):
            out.append({"severity": "warning", "message": f"pin {p.name} has no lead line ending at it"})
        if len(hull) >= 3 and _dist_to_hull(tuple(p.local), hull) < -10:
            out.append({"severity": "warning", "message": f"pin {p.name} is inside the body; place it at the end of a lead outside the outline"})
    texts = s.texts()
    if len(texts) < 2:
        out.append({"severity": "warning", "message": "symbol should have 2 texts: reference designator, then value/model"})
    if s.type == "":
        out.append({"severity": "info", "message": "blank type: this is a hierarchical block (needs a schematic with the same name)"})
    if not out:
        out.append({"severity": "ok", "message": "symbol follows QSPICE conventions"})
    return out


def describe(node: Node) -> dict:
    """Machine-readable listing of a symbol: pins (order, position, outward
    direction), texts, graphics."""
    from .autodraw import _local_pin_dir
    s = Symbol(node)
    pins = []
    for p in s.pins():
        pins.append({"order": p.index + 1, "name": p.name, "x": p.local[0], "y": p.local[1],
                     "outward": {"L": "left", "R": "right", "U": "up", "D": "down"}[_local_pin_dir(node, p.local, None)],
                     **({"optional_default_net": p.default_net} if p.default_net else {})})
    from .model import text_string
    texts = []
    for k, t in enumerate(s.texts()):
        tk = t.tokens
        texts.append({"index": k, "role": "refdes" if k == 0 else "value" if k == 1 else "attribute",
                      "text": qsch.display(text_string(t)), "at": list(tk[1]), "size": tk[2]})
    graphics = [c.header for c in node.children if c.kind in ("line", "rect", "ellipse", "triangle", "zigzag", "coil", "arc", "arc3p")]
    return {"name": s.name, "type": s.type, "description": s.description, "library": s.library,
            "pins_in_spice_order": pins, "texts": texts, "graphics": graphics, "bbox": list(s.bbox())}


# ---------------------------------------------------------- from subckt ---

def parse_subckt_header(text: str):
    """-> (name, pins, params) from the first .subckt line of `text`."""
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        if raw.lstrip().startswith("+") and lines:
            lines[-1] += " " + raw.lstrip()[1:]
        else:
            lines.append(raw.strip())
    for l in lines:
        if l.lower().startswith(".subckt"):
            toks = l.split()
            name = toks[1]
            pins, params = [], []
            in_params = False
            for t in toks[2:]:
                if t.lower() in ("params:", "param:"):
                    in_params = True
                    continue
                if "=" in t or in_params:
                    params.append(t)
                else:
                    pins.append(t)
            return name, pins, params
    raise ValueError("no .subckt line found")


def from_subckt(text: str, library: str | None, sides: dict | None = None, description: str | None = None,
                name: str | None = None) -> tuple[Node, list[str]]:
    sname, pins, params = parse_subckt_header(text)
    sides = sides or {}
    spec = {"name": name or sname, "kind": "block", "spice_prefix": "X", "model": sname, "library": library,
            "description": description or f"Subcircuit {sname}",
            "pins": [{"name": p, "side": sides.get(p) or auto_side(p)} for p in pins],
            "attributes": [p for p in params]}
    return build(spec)


# ---------------------------------------------------------- hierarchy -----

def hierarchical_block_node(name: str, ports: list, pitch: int = 200) -> Node:
    """Block symbol for a child schematic NAME.qsch (blank type).  ports:
    [{"name":"IN","side":"left"}...] in the order you want them netlisted."""
    ps = [p if isinstance(p, dict) else {"name": p} for p in ports]
    left = [p["name"] for p in ps if (p.get("side") or auto_side(p["name"])) == "left"]
    right = [p["name"] for p in ps if (p.get("side") or auto_side(p["name"])) == "right"]
    top = [p["name"] for p in ps if (p.get("side") or auto_side(p["name"])) == "top"]
    bot = [p["name"] for p in ps if (p.get("side") or auto_side(p["name"])) == "bottom"]
    n = max(len(left), len(right), 1)
    longest = max([len(x) for x in left] + [0]) + max([len(x) for x in right] + [0])
    hw = max(600, _ceil_g((longest * CHAR_W + 300) / 2), _ceil_g((max(len(top), len(bot)) + 1) * pitch / 2))
    hh = max(400, _ceil_g((n + 1) * pitch / 2))
    ch = [Node("shorted pins: false"),
          Node(f"rect ({-hw},{hh}) ({hw},{-hh}) 0 0 0 0x1000000 0x4000000 -1 1 -1"),
          _text((0, 100), "X1", 1, 14), _text((0, -50), name, 0.8, 13)]
    def ys(k):
        start = (k - 1) * pitch / 2
        return [int(start - i * pitch) for i in range(k)]
    pos = {}
    for p, y in zip(left, ys(len(left))):
        pos[p] = ((-hw, y), (50, 0), 7)
    for p, y in zip(right, ys(len(right))):
        pos[p] = ((hw, y), (-50, 0), 11)
    for p, x in zip(top, ys(len(top))):
        pos[p] = ((x, hh), (0, -50), 13)
    for p, x in zip(bot, ys(len(bot))):
        pos[p] = ((x, -hh), (0, 50), 14)
    for p in ps:
        at, off, just = pos[p["name"]]
        ch.append(_pin(at, p["name"], off, just, 1))
    return Node("symbol", ch, leaf=False)


# ------------------------------------------------------------ persistence --

def user_symbol_dir() -> Path:
    from .config import work_dir
    d = work_dir() / "symbols"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(node: Node, folder: str | Path | None = None, overwrite: bool = True) -> Path:
    """Write NAME.qsym and register it in the symbol library (usable by name
    in place_component / draw_schematic immediately and after restarts)."""
    s = Symbol(node)
    d = Path(folder) if folder else user_symbol_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{s.name}.qsym"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists (pass overwrite=true)")
    qsch.write_file(node, path)
    from .symbols import library
    library().add_qsym(path)
    return path


def subckt_text_from_source(source: str) -> tuple[str, str | None]:
    """source = .subckt text | path of a model file | name of a subckt in the
    model index.  -> (subckt text, library file path or None)."""
    s = source.strip()
    if ".subckt" in s.lower() and "\n" in s or s.lower().startswith(".subckt"):
        return s, None
    p = Path(s)
    if p.exists() and p.is_file():
        return p.read_text(encoding="latin-1", errors="replace"), str(p)
    from . import models
    hits = [h for h in models.search(s, kind="subckt", limit=200) if h["name"].lower() == s.lower()]
    if not hits:
        raise KeyError(f"'{source}' is not .subckt text, an existing file, or a known subcircuit name (try search_models)")
    return models.get_text(hits[0]["name"], hits[0]["file"]), hits[0]["file"]


def make_from_source(source: str, name: str | None = None, sides: dict | None = None,
                     library: str | None = None, description: str | None = None,
                     subckt_name: str | None = None) -> tuple[Node, list[str], str | None]:
    text, lib = subckt_text_from_source(source)
    warn = []
    if subckt_name:
        # pick that subckt inside a multi-model file
        m = re.search(r"^\s*\.subckt\s+" + re.escape(subckt_name) + r"\b.*$", text, re.I | re.M)
        if not m:
            raise KeyError(f"no .subckt {subckt_name} in source")
        text = text[m.start():]
    sname, pins, params = parse_subckt_header(text)
    if lib is None and library is None:
        # inline text: store it next to the symbol so QUX can .lib it
        d = user_symbol_dir()
        libp = d / f"{sname}.lib"
        body = text
        if ".ends" not in body.lower():
            body += "\n.ends " + sname
        libp.write_text(body.strip() + "\n", encoding="latin-1", errors="replace")
        lib = str(libp)
        warn.append(f"subcircuit text saved to {libp}")
    node, w = from_subckt(text, library or lib, sides, description, name)
    return node, warn + w, library or lib


# ---------------------------------------------------- hierarchical blocks --

_JUST_SIDE = {11: "left", 7: "right", 13: "bottom", 14: "top"}


def child_ports(child_path: str | Path) -> list[dict]:
    """Port labels («net ... port=1») of a child schematic, as block pins."""
    from .model import Schematic as _S
    sch = _S.load(child_path)
    out, seen = [], set()
    for (x, y), name, just, port in sch.nets():
        if not port or name.lower() in seen:
            continue
        seen.add(name.lower())
        side = _name_side(name) or _JUST_SIDE.get(just & 15) or "left"
        out.append({"name": name, "side": side, "at": [x, y]})
    # order: left top->bottom, then top, right, bottom (stable, readable)
    rank = {"left": 0, "top": 1, "right": 2, "bottom": 3}
    out.sort(key=lambda p: (rank[p["side"]], -p["at"][1] if p["side"] in ("left", "right") else p["at"][0]))
    return out


def block_for_child(child_path: str | Path, ports: list | None = None) -> Node:
    name = Path(child_path).stem
    ps = ports or child_ports(child_path)
    if not ps:
        raise ValueError(f"{child_path} has no port labels. Hierarchical sheets need port labels "
                         f"(net labels with port=true) for every signal that connects to the parent.")
    return hierarchical_block_node(name, ps)
