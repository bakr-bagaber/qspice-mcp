"""High-level canvas operations built for LLMs:

* reroute(sch)            keep every part where it is, redraw all wires cleanly
* move(sch, ref, ...)     move/rotate a part and re-route automatically
* add_and_connect(...)    place a new part next to what it connects to,
                          choose orientation/position automatically, wire it
* issues_delta(...)       which review problems an edit introduced
"""
from __future__ import annotations

import math
import re

from . import labels as textplace
from .autodraw import (AUTO_NET, IO_NET, Drawing, NetInfo, Part, _DIR_JUST, _is_vertical, _norm_net, classify,
                       keepout_points, prepare_geometry, route_drawing, set_part_texts)
from .canvas import code_from, connect, find_pin, refresh_junctions, snap, split_wires_at_tees, add_label
from .model import Schematic, Symbol
from .qsch import code_parts, xform
from .router import DIRS, G, OPP
from .scene import build, boxes_overlap, text_box


# ------------------------------------------------------------- utilities ---

def _kind_codes(sym: Symbol, pins) -> tuple[str, list]:
    req = [p for p in pins if not p.default_net]
    names = {(p.name or "").upper() for p in pins}
    t = sym.type or ""
    if not sym.name and not t:
        return "block", [0]
    if len(req) == 2 and len(pins) <= 3:
        return "2term", [0, 2, 4, 6]
    if t[:1] in ("Q", "M", "J", "Z") and len(req) <= 4:
        return ("bjt" if t[:1] == "Q" else "fet"), [0, 8]
    if {"IN+", "IN-", "OUT"} <= names:
        return "opamp", [0, 12]
    return "block", [0]


def parts_from_schematic(sch: Schematic, sc=None) -> list[Part]:
    """Wrap the components already on the sheet as autodraw Parts (fixed in
    place) with their present connectivity."""
    sc = sc or build(sch, with_keepout=False)
    nets_pins = sc.conn["nets"]
    label_names = {l.name for l in sc.labels}
    parts = []
    for pg in sc.parts:
        c = pg.comp
        sym = c.symbol
        pins = sym.pins()
        nets = []
        for q in pg.pins:
            n = q.net
            lone = n is not None and len(nets_pins.get(n, [])) <= 1 and n not in label_names and n != "0"
            if n is None or (lone and (q.optional or AUTO_NET.match(n))):
                nets.append(None)        # unconnected: leave it alone
            else:
                nets.append(n)
        kind, codes = _kind_codes(sym, pins)
        p = Part(ref=pg.ref, sym_name=sym.name, node=sym.node,
                 pin_names=[pp.name or str(pp.index + 1) for pp in pins],
                 pin_local=[pp.local for pp in pins], pin_optional=[bool(pp.default_net) for pp in pins],
                 nets=nets, value=c.value)
        p.kind = kind
        p.own_texts = True
        p.codes = [c.code]
        p.code = c.code
        p.pos = tuple(c.pos)
        p.fixed = True
        prepare_geometry(p)
        parts.append(p)
    return parts


def issues_delta(before: dict, after: dict) -> dict:
    key = lambda d: (d["code"], d["message"])
    old = {key(d) for d in before.get("issues", [])}
    new = [d for d in after.get("issues", []) if key(d) not in old and d["severity"] != "style"]
    fixed = len([1 for d in before.get("issues", []) if key(d) not in {key(x) for x in after.get("issues", [])}])
    return {"score": after.get("score"), "grade": after.get("grade"), "errors": after["counts"]["error"],
            "warnings": after["counts"]["warning"], "new_problems": new[:12], "problems_fixed": fixed}


# --------------------------------------------------------------- reroute ---

def reroute(sch: Schematic, fix_texts: bool = True, rails_as_labels: bool = True,
            parts: list | None = None, named: set | None = None) -> dict:
    """Delete all wires/labels/junctions and redraw them with the maze router,
    keeping every component exactly where it is.  `parts` (from
    parts_from_schematic) can carry a connectivity snapshot taken earlier."""
    sc = build(sch, with_keepout=False)
    if named is None:
        named = {l.name for l in sc.labels if l.name.upper() not in ("GND", "0")}
    if parts is None:
        parts = parts_from_schematic(sch, sc)
    if not parts:
        return {"note": "nothing to route"}
    spec = {"options": {"use_rail_labels": rails_as_labels}}
    nets = classify(parts, spec)
    # nets the author connected only through labels stay label-connected
    label_names = {n for n in nets if n in named or IO_NET.match(n)}
    dr = route_drawing(parts, nets, set(label_names))
    for kind in ("wire", "junction", "net"):
        for n in sch.root.findall(kind):
            sch.remove(n)
    for pt_, name, just in dr.labels:
        sch.add_net_label(pt_, name, just)
    for a, b, n in dr.wires:
        sch.add_wire(a, b, "" if n is None else ("GND" if n == "0" else n))
    refresh_junctions(sch)
    out = {"metrics": dr.metrics}
    if dr.failed:
        out["drawn_with_labels_instead"] = [f"{n}: {', '.join(k)}" for n, k in dr.failed]
    if fix_texts:
        out["texts"] = textplace.optimize(sch)
    return out


def move(sch: Schematic, ref: str, x=None, y=None, dx=None, dy=None, rotation=None, mirror=None,
         reroute_after: bool = True) -> dict:
    c = sch.component(ref)
    if c is None:
        raise KeyError(f"no component {ref}")
    # snapshot connectivity BEFORE moving: wires do not follow the part
    sc0 = build(sch, with_keepout=False)
    named = {l.name for l in sc0.labels if l.name.upper() not in ("GND", "0")}
    snapshot = parts_from_schematic(sch, sc0) if reroute_after else None
    nx, ny = c.pos
    if x is not None and y is not None:
        nx, ny = snap(x), snap(y)
    if dx is not None or dy is not None:
        nx, ny = nx + snap(dx or 0), ny + snap(dy or 0)
    code = c.code
    if rotation is not None or mirror is not None:
        rot, mir, _ = code_parts(c.code)
        code = code_from(rotation if rotation is not None else rot * 90, mir if mirror is None else bool(mirror))
    out = {"ref": c.refdes}
    # keep clear of other parts: nudge to the nearest free spot if needed
    free = nearest_free_position(sch, c.refdes, (nx, ny), code)
    if free != (nx, ny):
        out["adjusted"] = f"requested ({nx},{ny}) overlaps another part; moved to {free} instead"
        nx, ny = free
    c.pos = (nx, ny)
    c.code = code
    from .canvas import _relayout_texts
    _relayout_texts(c)
    out.update({"x": nx, "y": ny, "rotation": code_parts(code)[0] * 90, "mirror": code_parts(code)[1]})
    if reroute_after:
        for p in snapshot:
            if p.ref == c.refdes:
                p.pos, p.code, p.codes = (nx, ny), code, [code]
                p.geo = {}
                prepare_geometry(p)
        out["reroute"] = reroute(sch, parts=snapshot, named=named)
    return out


def nearest_free_position(sch: Schematic, ref: str | None, pos, code, margin: int = 60, max_r: int = 25):
    """Closest grid position to `pos` where component `ref` (or a new symbol
    node given via ref=None) does not overlap other parts' bodies or pins."""
    sc = build(sch, with_keepout=False)
    me = sc.part(ref) if ref else None
    if me is None:
        return pos
    from .scene import PartG
    node = me.comp.symbol.node
    from .autodraw import _graphic_points
    pts = [xform(q, code, (0, 0)) for q in _graphic_points(node)]
    xs = [q[0] for q in pts] or [0]; ys = [q[1] for q in pts] or [0]
    rel = (min(xs), min(ys), max(xs), max(ys))
    others = [p.body for p in sc.parts if p.ref != me.ref]
    pins_rel = [xform(pp.local, code, (0, 0)) for pp in me.comp.symbol.pins()]
    other_pins = {q.pos for p in sc.parts if p.ref != me.ref for q in p.pins}
    for r in range(0, max_r):
        ring = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if max(abs(dx), abs(dy)) == r]
        ring.sort(key=lambda d: abs(d[0]) + abs(d[1]))
        for dx, dy in ring:
            px, py = pos[0] + dx * G, pos[1] + dy * G
            box = (px + rel[0] - margin, py + rel[1] - margin, px + rel[2] + margin, py + rel[3] + margin)
            if any(boxes_overlap(box, ob) for ob in others):
                continue
            if any((round(px + a), round(py + b)) in other_pins for a, b in pins_rel):
                continue
            return (px, py)
    return pos


# ------------------------------------------------------- add_and_connect ---

def _target(sch: Schematic, sc, t):
    """Resolve a connection target -> dict(kind, pos, dir, net, name)."""
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return {"kind": "point", "pos": (snap(t[0]), snap(t[1])), "dir": None}
    s = str(t).strip()
    if s.upper() in ("GND", "0", "GROUND"):
        return {"kind": "ground"}
    if "." in s:
        q = sc.pin(s)
        if q is None:
            find_pin(sch, s)  # raises a helpful error
        return {"kind": "pin", "pos": q.pos, "dir": q.dir, "net": q.net, "name": q.key}
    # a net name: where is that net already on the sheet (labels / pins)?
    pts = [l.pos for l in sc.labels if l.name.lower() == s.lower()]
    pts += [q.pos for p in sc.parts for q in p.pins if q.net and q.net.lower() == s.lower()]
    where = (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)) if pts else None
    return {"kind": "label", "name": s, "where": where}


def add_and_connect(sch: Schematic, symbol: str, connections: dict, value: str = "", ref: str | None = None,
                    rotation: int | None = None, mirror: bool | None = None, params: dict | None = None,
                    prefer_side: str | None = None) -> dict:
    """Place `symbol` so that its pins are close to and facing what they connect to,
    then draw the wires, ground symbols and net labels."""
    from .canvas import place_component
    from .symbols import library
    from .autodraw import normalize_value
    value, vnote = normalize_value(value or "")
    vnotes = [vnote] if vnote else []
    sc = build(sch)
    e = library().resolve(symbol)
    sym = Symbol(e.node())
    pins = sym.pins()
    names = [p.name or str(p.index + 1) for p in pins]
    conns = {}
    for k, v in connections.items():
        idx = _pin_index(str(k), names)
        if idx is None:
            raise ValueError(f"{symbol} has no pin '{k}'. Pins: {names}")
        conns[idx] = _target(sch, sc, v)
    kind, codes = _kind_codes(sym, pins)
    if rotation is not None or mirror is not None:
        codes = [code_from(rotation or 0, bool(mirror))]
    # occupied grid: keep-outs, wires, pins, texts
    occ = set()
    for p in sc.parts:
        occ |= p.keepout
        for q in p.pins:
            occ.add(q.pos)
    wire_pts = set()
    for a, b, _ in sc.wires:
        for pt in _grid_line(a, b):
            wire_pts.add(pt)
    text_boxes = [t.box for t in sc.texts]
    bodies = [p.body for p in sc.parts]
    anchors = [(i, t) for i, t in conns.items() if t["kind"] in ("pin", "point")]
    if not anchors:
        # only net names: stay close to where those nets already are
        anchors = [(i, {"pos": t["where"]}) for i, t in conns.items() if t.get("where")]
    best = None
    tmp = Part(ref="__new", sym_name=sym.name, node=sym.node, pin_names=names,
               pin_local=[p.local for p in pins], pin_optional=[bool(p.default_net) for p in pins],
               nets=["x"] * len(pins), value=value)
    tmp.kind, tmp.codes = kind, codes
    prepare_geometry(tmp)
    # translation-invariant footprints per orientation
    foot = {}
    for code in codes:
        g = tmp.geo[code]
        pins_rel = [tuple(map(int, q)) for q in g["pins"]]
        ko_rel = keepout_points(tmp.node, code, (0, 0), set(pins_rel))
        foot[code] = (pins_rel, ko_rel, g)
    if anchors:
        ax = sum(t["pos"][0] for _, t in anchors) / len(anchors)
        ay = sum(t["pos"][1] for _, t in anchors) / len(anchors)
        radius = 14
    else:
        ax = (sc.bbox[0] + sc.bbox[2]) / 2 if sc.parts else 0
        ay = (sc.bbox[1] + sc.bbox[3]) / 2 if sc.parts else 0
        radius = 30
    ax, ay = snap(ax), snap(ay)
    for code in codes:
        pins_rel, ko_rel, g = foot[code]
        for dxk in range(-radius, radius + 1):
            for dyk in range(-radius, radius + 1):
                pos = (ax + dxk * G, ay + dyk * G)
                c = _score_candidate(tmp, code, pos, conns, occ, wire_pts, bodies, text_boxes, prefer_side,
                                     pins_rel, ko_rel)
                if c is not None and (best is None or c < best[0]):
                    best = (c, code, pos)
    if best is None:
        raise RuntimeError("no free spot found near the targets; move parts apart or give x/y with place_component")
    _, code, pos = best
    rot, mir, _ = code_parts(code)
    info = place_component(sch, symbol, pos[0], pos[1], ref=ref, value=value, rotation=rot * 90, mirror=mir,
                           params=params)
    newref = info["ref"]
    wired, problems = [], []
    for i, t in conns.items():
        pin_spec = f"{newref}.{names[i]}"
        try:
            if t["kind"] in ("pin", "point"):
                r = connect(sch, pin_spec, t["name"] if t["kind"] == "pin" else list(t["pos"]))
                wired.append({"pin": names[i], "to": t.get("name") or list(t["pos"]), "length": r["length"]})
            elif t["kind"] == "ground":
                add_label(sch, pin_spec, "GND", None, False, 1)
                wired.append({"pin": names[i], "to": "GND"})
            else:
                add_label(sch, pin_spec, t["name"], None, False, 1)
                wired.append({"pin": names[i], "to": f"label {t['name']}"})
                if t.get("where") is None:
                    vnotes.append(f"net '{t['name']}' did not exist on the sheet before: {pin_spec} now starts a NEW "
                                  f"net with that name. If you meant an existing node, connect to one of its pins "
                                  f"(e.g. 'C1.1') instead.")
        except Exception as ex:  # keep going, report
            problems.append(f"{pin_spec} -> {t.get('name') or t['kind']}: {ex}")
    split_wires_at_tees(sch)
    refresh_junctions(sch)
    # re-place the texts of the new part and of any part whose texts the new wires cross
    hit = _texts_hit_by_wires(sch) | {newref}
    textplace.optimize(sch, refs=sorted(hit))
    out = {"ref": newref, "x": pos[0], "y": pos[1], "rotation": rot * 90, "mirror": mir, "connected": wired}
    if problems:
        out["could_not_connect"] = problems
    notes = list(info.get("notes") or []) + vnotes
    missing = [names[i] for i in range(len(pins)) if i not in conns and not pins[i].default_net]
    if missing:
        notes.append(f"pins {missing} were not connected (give a target for every pin)")
    if notes:
        out["notes"] = notes
    return out


def _texts_hit_by_wires(sch: Schematic) -> set:
    from .scene import seg_hits_box
    sc = build(sch, with_keepout=False)
    refs = set()
    for t in sc.texts:
        if t.owner and t.role in ("refdes", "value", "attr"):
            for a, b, _ in sc.wires:
                if seg_hits_box(a, b, t.box, shrink=8):
                    refs.add(t.owner)
                    break
    return refs


def _pin_index(key: str, names: list[str]):
    from .autodraw import _match_pin
    return _match_pin(key, names)


def _grid_line(a, b):
    (x0, y0), (x1, y1) = a, b
    out = []
    if x0 == x1:
        s = G if y1 >= y0 else -G
        for y in range(y0, y1 + s, s):
            out.append((x0, y))
    elif y0 == y1:
        s = G if x1 >= x0 else -G
        for x in range(x0, x1 + s, s):
            out.append((x, y0))
    return out


def _score_candidate(p: Part, code, pos, conns, occ, wire_pts, bodies, text_boxes, prefer_side,
                     pins_rel=None, ko_rel=None):
    g = p.geo[code]
    rel = pins_rel if pins_rel is not None else [tuple(map(int, q)) for q in g["pins"]]
    pin_pts = [(pos[0] + x, pos[1] + y) for x, y in rel]
    req = [i for i in range(len(pin_pts)) if not p.pin_optional[i] or i in conns]
    for i in req:
        q = pin_pts[i]
        if q in occ or q in wire_pts:
            return None
        # leave room for the stub/wire leaving the pin
        vx, vy = DIRS[g["dirs"][i]]
        nxt = (q[0] + vx * G, q[1] + vy * G)
        if nxt in occ:
            return None
    b = g["body"]
    body = (pos[0] + b[0] - 60, pos[1] + b[1] - 60, pos[0] + b[2] + 60, pos[1] + b[3] + 60)
    for ob in bodies:
        if boxes_overlap(body, ob):
            return None
    ko = {(pos[0] + x, pos[1] + y) for x, y in ko_rel} if ko_rel is not None else \
        keepout_points(p.node, code, pos, set(pin_pts))
    if ko & occ or ko & wire_pts:
        return None
    cost = 0.0
    for tb in text_boxes:
        if boxes_overlap(body, tb):
            cost += 600
    for lb in g["labels"]:
        L = (pos[0] + lb[0], pos[1] + lb[1], pos[0] + lb[2], pos[1] + lb[3])
        for tb in text_boxes:
            if boxes_overlap(L, tb):
                cost += 300
        for ob in bodies:
            if boxes_overlap(L, ob):
                cost += 300
        for wp in wire_pts:
            if L[0] < wp[0] < L[2] and L[1] < wp[1] < L[3]:
                cost += 120
    for i, t in conns.items():
        if t["kind"] == "ground":
            q = pin_pts[i]
            vx, vy = DIRS[g["dirs"][i]]
            for k in (2, 3):
                z = (q[0] + vx * k * G, q[1] + vy * k * G)
                for zz in (z, (z[0] + vy * G, z[1] + vx * G), (z[0] - vy * G, z[1] - vx * G)):
                    if zz in occ or zz in wire_pts:
                        cost += 150
    for i, t in conns.items():
        q = pin_pts[i]
        d = g["dirs"][i]
        if t["kind"] in ("pin", "point"):
            tp = t["pos"]
            dist = abs(q[0] - tp[0]) + abs(q[1] - tp[1])
            cost += dist
            if q[0] != tp[0] and q[1] != tp[1]:
                cost += 250           # needs a bend
            vx, vy = DIRS[d]
            if vx * (tp[0] - q[0]) + vy * (tp[1] - q[1]) <= 0:
                cost += 400           # pin faces away from its target
            if t.get("dir"):
                tx, ty = DIRS[t["dir"]]
                if tx * (q[0] - tp[0]) + ty * (q[1] - tp[1]) < 0:
                    cost += 250       # we are behind the target pin (beside it is fine: a T joint)
        elif t["kind"] == "ground":
            cost += {"D": 0, "L": 600, "R": 600, "U": 900}[d]
        elif re.match(r"^(v?cc|v?dd|vp|v\+|\+|\d+v)", t["name"], re.I):
            cost += {"U": 0, "L": 100, "R": 100, "D": 200}[d]
        elif re.match(r"^(v?ee|v?ss|vn|v-|-)", t["name"], re.I):
            cost += {"D": 0, "L": 100, "R": 100, "U": 200}[d]
        elif t.get("where"):
            # an existing net elsewhere on the sheet: be near it and face toward it
            wx, wy = t["where"]
            cost += 0.7 * (abs(q[0] - wx) + abs(q[1] - wy))
            vx, vy = DIRS[d]
            if vx * (wx - q[0]) + vy * (wy - q[1]) < 0:
                cost += 200
        else:
            # a new signal net: the signal continues to the right
            cost += {"R": 0, "U": 150, "D": 150, "L": 300}[d]
    if prefer_side and conns:
        anchors = [t["pos"] for t in conns.values() if t.get("pos")]
        if anchors:
            ax = sum(a[0] for a in anchors) / len(anchors); ay = sum(a[1] for a in anchors) / len(anchors)
            cx, cy = pos
            ok = {"right": cx > ax, "left": cx < ax, "above": cy > ay, "below": cy < ay, "up": cy > ay,
                  "down": cy < ay}.get(prefer_side.lower(), True)
            if not ok:
                cost += 800
    return cost
