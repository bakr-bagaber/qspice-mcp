"""Incremental, LLM-friendly editing operations on a QSPICE schematic canvas."""
from __future__ import annotations

import re
from pathlib import Path

from . import qsch
from .autodraw import Part, _dist_to_hull, _DIR_JUST, build_parts, prepare_geometry, set_part_texts
from .connectivity import _on_seg, extract
from .model import Component, Schematic, text_string
from .qsch import code_parts, make_code, xform
from .router import DIRS, G, OPP, Router, Terminal

ROT_DEG = {0: 0, 90: 1, 180: 2, 270: 3}


def code_from(rotation: int = 0, mirror: bool = False) -> int:
    r = int(rotation) % 360
    if r not in ROT_DEG:
        raise ValueError("rotation must be 0, 90, 180 or 270 (degrees counter-clockwise)")
    return make_code(ROT_DEG[r], mirror)


def snap(v) -> int:
    return int(round(float(v) / G)) * G


# ------------------------------------------------------------ describe ----

def describe(sch: Schematic, include_symbol_geometry=False) -> dict:
    conn = extract(sch)
    comps = []
    from .connectivity import unique_refs
    for c, un in unique_refs(sch):
        rot, mir, e45 = code_parts(c.code)
        pins = []
        netmap = {(d["pin"], d["index"]): d["net"] for d in conn["pins"].get(un, [])}
        sym = c.symbol
        for p, w in c.pin_positions():
            item = {"pin": p.name or str(p.index + 1), "order": p.index + 1, "x": w[0], "y": w[1],
                    "net": netmap.get((p.name or str(p.index + 1), p.index))}
            if p.default_net:
                item["optional"] = True
            pins.append(item)
        b = c.bbox()
        d = {"ref": un, "symbol": sym.name, "type": sym.type, "description": sym.description,
             "value": c.value, "attributes": c.attributes[1:] if len(c.attributes) > 1 else [],
             "x": c.pos[0], "y": c.pos[1], "rotation": rot * 90 + (45 if e45 else 0), "mirror": mir,
             "bbox": [int(v) for v in b], "pins": pins}
        comps.append(d)
    wires = [{"from": list(a), "to": list(b)} for a, b, _ in sch.wires()]
    labels = [{"x": p[0], "y": p[1], "name": n, "port": bool(port)} for p, n, j, port in sch.nets()]
    texts = sch.texts()
    from .render import schematic_bbox
    bb = schematic_bbox(sch)
    return {"components": comps, "wires": wires, "net_labels": labels, "junctions": [list(j) for j in sch.junctions()],
            "directives": [qsch.display(t["str"]) for t in texts if t["flag"] == 0],
            "comments": [qsch.display(t["str"]) for t in texts if t["flag"] != 0],
            "nets": conn["nets"], "unconnected_pins": conn["unconnected_pins"],
            "dangling_wire_ends": conn["dangling_wire_ends"],
            "extent": {"xmin": int(bb[0]), "ymin": int(bb[1]), "xmax": int(bb[2]), "ymax": int(bb[3])},
            "coordinate_system": "y increases upward; grid = 100 units; pins and wires must sit on multiples of 100"}


# ------------------------------------------------------------- placing ----

def place_component(sch: Schematic, symbol: str, x, y, ref: str | None = None, value: str = "",
                    rotation: int = 0, mirror: bool = False, params: dict | None = None,
                    attributes: list | None = None) -> dict:
    code = code_from(rotation, mirror)
    spec = {"components": [{"ref": ref or "", "symbol": symbol, "value": value, "params": params or {},
                            "attributes": attributes or [], "_allow_unconnected": True}]}
    # build a Part without requiring connections
    from .symbols import library
    from .model import Symbol
    e = library().resolve(symbol)
    node = e.node()
    sym = Symbol(node)
    pins = sym.pins()
    letter = sym.type[:1] if sym.type else "X"
    existing = {c.refdes.lower() for c in sch.components()}
    if not ref:
        k = 1
        while f"{letter}{k}".lower() in existing:
            k += 1
        ref = f"{letter}{k}"
    notes = []
    if letter and not ref.upper().startswith(letter.upper()):
        notes.append(f"{ref}: fine as is (QSPICE internally prefixes its device letter; node voltages are unaffected)")
    if ref.lower() in existing:
        raise ValueError(f"reference designator {ref} already exists")
    p = Part(ref=ref, sym_name=sym.name, node=node, pin_names=[pp.name or str(pp.index + 1) for pp in pins],
             pin_local=[pp.local for pp in pins], pin_optional=[bool(pp.default_net) for pp in pins],
             nets=["x"] * len(pins), value=value or "", params=dict(params or {}), extra_texts=list(attributes or []))
    req = [i for i in range(len(pins)) if not p.pin_optional[i]]
    if not sym.name and not sym.type:      # hierarchical block (child schematic)
        p.kind = "block"; p.own_texts = True
    elif len(req) == 2 and len(pins) <= 3:
        p.kind = "2term"
    elif sym.type[:1] in ("Q", "M", "J", "Z"):
        p.kind = "bjt" if sym.type[:1] == "Q" else "fet"
    elif {"IN+", "IN-", "OUT"} <= {n.upper() for n in p.pin_names}:
        p.kind = "opamp"; p.own_texts = True
    else:
        p.kind = "block"; p.own_texts = len(sym.texts()) > 2 or sym.type in ("X", "")
    p.codes = [code]; p.code = code
    p.pos = (snap(x), snap(y))
    prepare_geometry(p)
    set_part_texts(p, None)
    c = sch.add_component(p.node, p.pos, code)
    return {"ref": ref, "notes": notes, "pins": [{"pin": pi.name or str(pi.index + 1), "x": w[0], "y": w[1]} for pi, w in c.pin_positions()]}


def find_pin(sch: Schematic, spec: str):
    """'R1.2' / 'U1.IN-' / 'Q1.B' -> (component, PinInfo, (x,y))"""
    if "." not in spec:
        raise ValueError(f"pin reference must look like REF.PIN (e.g. R1.1, Q1.B), got '{spec}'")
    ref, pin = spec.split(".", 1)
    c = sch.component(ref)
    if c is None:
        raise KeyError(f"no component {ref}")
    for p, w in c.pin_positions():
        nm = p.name or str(p.index + 1)
        if nm == pin or nm.lower() == pin.lower() or (pin.isdigit() and int(pin) == p.index + 1 and not any(
                (q.name or "").lower() == pin.lower() for q, _ in c.pin_positions())):
            return c, p, w
    raise KeyError(f"{ref} has no pin '{pin}'. Pins: {[p.name or str(p.index+1) for p, _ in c.pin_positions()]}")


def _pin_dir(c: Component, p) -> str:
    from .autodraw import _local_pin_dir, _xf_dir
    d = _local_pin_dir(c.symbol.node, p.local, None)
    return _xf_dir(d, c.code)


def resolve_point(sch: Schematic, target):
    """target: 'REF.PIN' or [x, y]."""
    if isinstance(target, str):
        c, p, w = find_pin(sch, target)
        return w, _pin_dir(c, p)
    x, y = target
    return (snap(x), snap(y)), None


# ------------------------------------------------------------- wiring -----

def add_polyline(sch: Schematic, points: list, net: str = "") -> int:
    pts = [(snap(x), snap(y)) for x, y in points]
    n = 0
    for a, b in zip(pts, pts[1:]):
        if a != b:
            sch.add_wire(a, b, net)
            n += 1
    split_wires_at_tees(sch)
    refresh_junctions(sch)
    return n


def split_wires_at_tees(sch: Schematic):
    """Split wires where another wire's endpoint or a pin lands on their
    interior, so every connection is endpoint based (keeps QUX happy)."""
    changed = True
    while changed:
        changed = False
        wires = sch.root.findall("wire")
        ends = set()
        for w in wires:
            t = w.tokens
            ends.add(tuple(t[1])); ends.add(tuple(t[2]))
        for c in sch.components():
            for _, pnt in c.pin_positions():
                ends.add(tuple(pnt))
        for p, *_ in sch.nets():
            ends.add(tuple(p))
        for w in wires:
            t = w.tokens
            a, b = tuple(t[1]), tuple(t[2])
            net = qsch.unq(t[3]) if len(t) > 3 else ""
            for e in ends:
                if e != a and e != b and _on_seg(e, a, b):
                    sch.remove(w)
                    sch.add_wire(a, e, net)
                    sch.add_wire(e, b, net)
                    changed = True
                    break
            if changed:
                break


def refresh_junctions(sch: Schematic):
    for j in sch.root.findall("junction"):
        sch.remove(j)
    deg = {}
    for a, b, _ in sch.wires():
        for e in (tuple(a), tuple(b)):
            deg[e] = deg.get(e, 0) + 1
    pins = set()
    for c in sch.components():
        for _, w in c.pin_positions():
            pins.add(tuple(w))
    for p, d in deg.items():
        if d + (1 if p in pins else 0) >= 3:
            sch.add_junction(p)


def _router_for(sch: Schematic, allow_nets: set):
    conn = extract(sch)
    # net name for each wire point
    pin_net = {}
    from .connectivity import unique_refs
    for c, un in unique_refs(sch):
        netmap = {(d["pin"], d["index"]): d["net"] for d in conn["pins"].get(un, [])}
        for p, w in c.pin_positions():
            pin_net[tuple(w)] = netmap.get((p.name or str(p.index + 1), p.index), f"__{c.refdes}{p.index}")
    from .render import schematic_bbox
    bb = schematic_bbox(sch)
    bounds = (int(bb[0] // G * G) - 2000, int(bb[1] // G * G) - 2000, int(bb[2] // G * G) + 2000, int(bb[3] // G * G) + 2000)
    blocked = set()
    from .autodraw import keepout_points
    allpins = set(pin_net)
    for c in sch.components():
        blocked |= keepout_points(c.symbol.node, c.code, c.pos, allpins)
    MERGE = "__route__"
    pins = {p: (MERGE if n in allow_nets else n) for p, n in pin_net.items()}
    R = Router(bounds, blocked, pins)
    for c in sch.components():
        pp = c.pin_positions()
        if len([1 for p, _ in pp if not p.default_net]) == 2 and len(pp) <= 3:
            R.easy.update(tuple(w) for p, w in pp if not p.default_net)
    # existing wires: occupy their grid points/edges
    wire_net = {}
    for a, b, _ in sch.wires():
        a, b = tuple(a), tuple(b)
        # find net of this wire via any pin/label on it: use extract's nets by pin location
        net = None
        for p, n in pin_net.items():
            if _on_seg(p, a, b):
                net = n
                break
        wire_net[(a, b)] = net
    # propagate nets along connected wires
    changed = True
    while changed:
        changed = False
        for (a, b), n in list(wire_net.items()):
            if n is None:
                continue
            for (c_, d_), m in list(wire_net.items()):
                if m is None and (_on_seg(c_, a, b) or _on_seg(d_, a, b) or _on_seg(a, c_, d_) or _on_seg(b, c_, d_)):
                    wire_net[(c_, d_)] = n
                    changed = True
    net_points: dict = {}
    for p, n in pin_net.items():
        net_points.setdefault(n, set()).add(p)
    for k, ((a, b), n) in enumerate(wire_net.items()):
        n = n or f"__w{k}"
        key = MERGE if n in allow_nets else n
        pts = _grid_points(a, b)
        net_points.setdefault(n, set()).update(pts)
        for i, pnt in enumerate(pts):
            if i in (0, len(pts) - 1):
                R.owner[pnt] = key
            else:
                ax = "H" if a[1] == b[1] else "V"
                if pnt not in R.owner:
                    R.through[pnt] = (key, ax)
        for p1, p2 in zip(pts, pts[1:]):
            R.edges[frozenset((p1, p2))] = key
    for p, name, *_ in sch.nets():
        R.owner.setdefault(tuple(p), "__lbl_" + name)
    R.net_points = net_points
    return R, MERGE, pin_net


def _grid_points(a, b):
    (x0, y0), (x1, y1) = a, b
    if x0 == x1:
        s = G if y1 > y0 else -G
        return [(x0, y) for y in range(y0, y1 + s, s)] if y0 != y1 else [a]
    if y0 == y1:
        s = G if x1 > x0 else -G
        return [(x, y0) for x in range(x0, x1 + s, s)]
    return [a, b]


def connect(sch: Schematic, frm, to) -> dict:
    """Auto-route an orthogonal wire between two pins / points, avoiding parts
    and never touching other nets."""
    pa, da = resolve_point(sch, frm)
    pb, db = resolve_point(sch, to)
    frm = frm if isinstance(frm, str) else tuple(frm)       # [x, y] lists -> hashable
    to = to if isinstance(to, str) else tuple(to)
    conn = extract(sch)
    net_of = {}
    for tgt in (frm, to):
        if isinstance(tgt, str):
            ref, pin = tgt.split(".", 1)
            from .connectivity import uname
            for d in conn["pins"].get(uname(sch, ref), []):
                if d["pin"].lower() == pin.lower() or str(d["index"] + 1) == pin:
                    net_of[tgt] = d["net"]
    nets = set(net_of.values())
    na, nb = net_of.get(frm), net_of.get(to)
    if na is not None and na == nb and len(conn["nets"].get(na, [])) > 1:
        return {"points": [], "segments": 0, "length": 0,
                "note": f"{frm} and {to} are already connected (net {na}); nothing drawn"}
    R, MERGE, pin_net = _router_for(sch, nets)
    for p in (pa, pb):
        if p not in R.pins:
            if p in R.blocked:
                raise ValueError(f"point {p} is inside a component body")
            R.owner[p] = MERGE
    targets = {pb: ("pin", db)}
    # join the destination net anywhere along its existing wiring (a clean T),
    # never retracing it; the source net's own wiring is not a target
    if nb is not None:
        src_pts = R.net_points.get(na, set()) if na is not None else set()
        for q in R.net_points.get(nb, set()):
            if q != pb and q not in src_pts and q not in R.cross and q not in R.pins:
                targets[q] = ("tree", None)
    path, cost = R._search(MERGE, Terminal("from", pa, da), targets, max_expand=200000)
    if path is None:
        raise RuntimeError("no clean route found (blocked). Try moving parts apart, or use add_net_label on both ends.")
    # compress path to corner points
    corners = [path[0]]
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1], path[i], path[i + 1]
        if not ((a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1])):
            corners.append(b)
    corners.append(path[-1])
    n = add_polyline(sch, corners)
    return {"points": [list(p) for p in corners], "segments": n, "length": sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(corners, corners[1:]))}


def _free_stub_dirs(sch: Schematic, p, stub: int) -> list[str]:
    """Directions in which a `stub`-long wire from p runs over nothing (no wire,
    no part, no pin, no label)."""
    from .scene import build
    sc = build(sch)
    busy = set()
    for q in sc.parts:
        busy |= q.keepout
        busy |= {pp.pos for pp in q.pins}
    for a, b, _ in sch.wires():
        busy |= set(_grid_points(tuple(a), tuple(b)))
    busy |= {tuple(l.pos) for l in sc.labels}
    out = []
    for k, v in DIRS.items():
        pts = [(p[0] + v[0] * G * i, p[1] + v[1] * G * i) for i in range(1, max(1, stub) + 1)]
        if not any(q in busy for q in pts):
            out.append(k)
    return out


def _add_ground_smart(sch: Schematic, p, d, stub: int) -> dict:
    """Ground symbol that always points DOWN: straight down from a downward pin,
    otherwise an L-shaped stub (out of the pin, then down)."""
    free = _free_stub_dirs(sch, p, 1)
    sc_busy = None

    def ok(pts):
        nonlocal sc_busy
        if sc_busy is None:
            from .scene import build
            sc = build(sch)
            sc_busy = set()
            for q in sc.parts:
                sc_busy |= q.keepout
                sc_busy |= {pp.pos for pp in q.pins}
            for a, b, _ in sch.wires():
                sc_busy |= set(_grid_points(tuple(a), tuple(b)))
            sc_busy |= {tuple(l.pos) for l in sc.labels}
        return not any(q in sc_busy for q in pts)

    def finish(pts):
        for a, b in zip(pts, pts[1:]):
            sch.add_wire(a, b, "GND")
        sch.add_net_label(pts[-1], "GND", _DIR_JUST["D"], port=False)
        split_wires_at_tees(sch)
        refresh_junctions(sch)
        return {"label": "GND", "x": pts[-1][0], "y": pts[-1][1], "direction": "D", "path": [list(q) for q in pts]}

    if not stub:
        return finish([p])
    down1 = (p[0], p[1] - G)
    if d in (None, "D") and "D" in free:
        return finish([p, down1])
    for h in ([d] if d in ("L", "R") else []) + ["R", "L"]:
        out = (p[0] + DIRS[h][0] * G, p[1])
        dn = (out[0], out[1] - G)
        if ok([out, dn]):
            return finish([p, out, dn])
    if d == "U":
        for h in ("R", "L"):
            up = (p[0], p[1] + G)
            out = (up[0] + DIRS[h][0] * G, up[1])
            dn = (out[0], p[1])
            if ok([up, out, dn]):
                return finish([p, up, out, dn])
    if "D" in free:
        return finish([p, down1])
    return finish([p])


def add_label(sch: Schematic, at, name: str, direction: str | None = None, port: bool = False,
              stub: int = 0) -> dict:
    p, d = resolve_point(sch, at)
    is_gnd = name.upper() in ("GND", "0")
    # already on that net? (same-named label on the pin's net)
    conn = extract(sch)
    if isinstance(at, str) and not is_gnd:
        ref, pin = at.split(".", 1)
        c = sch.component(ref)
        from .connectivity import uname
        for dd in conn["pins"].get(uname(sch, ref), []):
            if (dd["pin"].lower() == pin.lower() or str(dd["index"] + 1) == pin) and \
                    str(dd["net"]).lower() == name.lower():
                return {"label": name, "note": f"{at} is already on net '{name}'; nothing added"}
    explicit = direction is not None
    direction = (direction or d or "U").upper()[:1]
    if direction not in DIRS:
        direction = "U"
    if is_gnd and not explicit:
        return _add_ground_smart(sch, p, d, stub)
    if stub and not explicit:
        free = _free_stub_dirs(sch, p, stub)
        pref = [x for x in ([direction] if direction else []) + ([d] if d else []) + ["U", "R", "L", "D"]
                if x in free]
        pref = list(dict.fromkeys(pref))
        if pref:
            # cheapest free direction: label text over a part body / other label is bad,
            # over a refdes/value text is tolerable (those texts get moved afterwards)
            costs = [(_label_text_hits(sch, p, x, stub, name), i, x) for i, x in enumerate(pref)]
            direction = min(costs)[2]
        else:
            stub = 0          # nowhere to go: put the label right on the pin
    pt = p
    if stub:
        q = (p[0] + DIRS[direction][0] * G * stub, p[1] + DIRS[direction][1] * G * stub)
        sch.add_wire(p, q, name if not is_gnd else "GND")
        pt = q
    sch.add_net_label(pt, name, _DIR_JUST[direction], port=port)
    split_wires_at_tees(sch)
    refresh_junctions(sch)
    out = {"label": name, "x": pt[0], "y": pt[1], "direction": direction}
    moved = _retidy_texts_near(sch, p, pt, name, direction)
    if moved:
        out["texts_moved"] = moved
    return out


def _label_box(pt, name: str, direction: str):
    from .scene import text_box
    just = _DIR_JUST[direction]
    h, v, r = (just >> 2) & 3, just & 3, (just >> 5) & 3
    off = {1: -25, 2: 25, 3: 0}.get(v, 25)
    hoff = {1: 25, 2: -25, 3: 0}.get(h, 0)
    return text_box((pt[0] + hoff, pt[1] + off), name, 1.0, h or 1, v or 2, r)


def _label_text_hits(sch: Schematic, p, direction: str, stub: int, name: str) -> int:
    """Cost of a label stub in `direction`: 0 = clear; +1 per part text (refdes/value,
    movable) it covers; +10 per part body, wire, other label or note it covers."""
    from .scene import boxes_overlap, build, seg_hits_box
    q = (p[0] + DIRS[direction][0] * G * stub, p[1] + DIRS[direction][1] * G * stub)
    lb = _label_box(q, name, direction)
    sc = build(sch, with_keepout=False)
    cost = 0
    for t in sc.texts:
        if t.role not in ("refdes", "value", "attr", "label", "directive", "comment"):
            continue
        if seg_hits_box(p, q, t.box, shrink=8) or boxes_overlap(lb, t.box):
            cost += 1 if t.role in ("refdes", "value", "attr") else 10
    shr = (lb[0] + 10, lb[1] + 10, lb[2] - 10, lb[3] - 10)
    for part in sc.parts:
        b = part.body
        if boxes_overlap(shr, (b[0] + 8, b[1] + 8, b[2] - 8, b[3] - 8)):
            cost += 10
    for a, b, _ in sc.wires:
        if seg_hits_box(a, b, lb, shrink=12):
            cost += 10
    return cost


def _retidy_texts_near(sch: Schematic, p, pt, name: str, direction: str) -> list[str]:
    """Move refdes/value texts of parts that the new label stub or label text now
    covers (the same text optimiser auto_draw uses).  Returns the parts moved."""
    from .scene import boxes_overlap, build, seg_hits_box
    from . import labels as _labels
    lb = _label_box(pt, name, direction)
    hit = set()
    for t in build(sch, with_keepout=False).texts:
        if t.owner and t.role in ("refdes", "value", "attr"):
            if (p != pt and seg_hits_box(p, pt, t.box, shrink=8)) or boxes_overlap(lb, t.box):
                hit.add(t.owner)
    if not hit:
        return []
    try:
        _labels.optimize(sch, refs=sorted(hit))
    except Exception:
        return []
    return sorted(hit)


def delete(sch: Schematic, refs=(), wires_touching=(), labels_at=(), remove_dangling=True) -> dict:
    removed = {"components": [], "wires": 0, "labels": 0}
    pinpts = []
    for r in refs:
        c = sch.component(r)
        if c is None:
            continue
        pinpts += [tuple(w) for _, w in c.pin_positions()]
        sch.remove(c.node)
        removed["components"].append(c.refdes)
    removed["removed_wires"] = []
    for x, y in wires_touching:
        q = (snap(x), snap(y))
        ws = [(w, tuple(w.tokens[1]), tuple(w.tokens[2])) for w in sch.root.findall("wire")]
        # a point INSIDE a segment selects just that segment; a corner/junction point
        # (segment end) selects the segments ending there
        inner = [w for w, a, b in ws if _on_seg(q, a, b) and q not in (a, b)]
        hit = inner or [w for w, a, b in ws if q in (a, b)]
        for w in hit:
            removed["removed_wires"].append([list(w.tokens[1]), list(w.tokens[2])])
            sch.remove(w); removed["wires"] += 1
    label_pts = []
    for n in sch.root.findall("net"):
        if any(tuple(n.tokens[1]) == (snap(x), snap(y)) for x, y in labels_at):
            label_pts.append(tuple(n.tokens[1]))
            sch.remove(n); removed["labels"] += 1
    # a label's stub wire would dangle without its label: remove it too
    if label_pts:
        pin_pts = {tuple(w) for c in sch.components() for _, w in c.pin_positions()}
        for lp in label_pts:
            ws = [(w, tuple(w.tokens[1]), tuple(w.tokens[2])) for w in sch.root.findall("wire")]
            ends = [w for w, a, b in ws if lp in (a, b)]
            others = [w for w, a, b in ws if _on_seg(lp, a, b) and lp not in (a, b)]
            if len(ends) == 1 and not others and lp not in pin_pts and \
                    not any(tuple(p) == lp for p, *_ in sch.nets()):
                w = ends[0]
                removed["removed_wires"].append([list(w.tokens[1]), list(w.tokens[2])])
                sch.remove(w); removed["wires"] += 1
    if remove_dangling and pinpts:
        # drop wire stubs that now dangle from the removed pins
        changed = True
        while changed:
            changed = False
            for w in sch.root.findall("wire"):
                t = w.tokens
                a, b = tuple(t[1]), tuple(t[2])
                for e in (a, b):
                    if e in pinpts:
                        others = [x for x in sch.root.findall("wire") if x is not w and
                                  _on_seg(e, tuple(x.tokens[1]), tuple(x.tokens[2]))]
                        on_lbl = any(tuple(p) == e for p, *_ in sch.nets())
                        if not others and not on_lbl:
                            removed["removed_wires"].append([list(a), list(b)])
                            sch.remove(w); removed["wires"] += 1
                            other = b if e == a else a
                            pinpts.append(other)
                            changed = True
                            break
                if changed:
                    break
            if changed:
                # don't remove wires that still end on live pins
                live = set()
                for c in sch.components():
                    live.update(tuple(w_) for _, w_ in c.pin_positions())
                pinpts = [p for p in pinpts if p not in live]
    refresh_junctions(sch)
    return {"removed_components": removed["components"], "removed_wire_segments": removed["removed_wires"],
            "removed_labels": removed["labels"]}


def update_component(sch: Schematic, ref: str, value=None, params: dict | None = None, new_ref=None,
                     x=None, y=None, rotation=None, mirror=None, relayout_text=True) -> dict:
    c = sch.component(ref)
    if c is None:
        raise KeyError(f"no component {ref}")
    if value is not None:
        c.value = str(value)
    if params:
        texts = c.texts
        for k, v in params.items():
            done = False
            for t in texts[1:]:
                s = text_string(t)
                if s.lower().startswith(k.lower() + "="):
                    from .model import set_text_string
                    set_text_string(t, f"{k}=<{v}>" if "=<" in s else f"{k}={v}")
                    done = True
            if not done:
                from .model import make_text_node
                last = texts[-1].tokens[1]
                sym = c.symbol.node
                idx = max(i for i, ch in enumerate(sym.children) if ch.kind == "text")
                sym.children.insert(idx + 1, make_text_node((last[0], last[1] - 150), f"{k}={v}"))
    if new_ref:
        c.refdes = new_ref
    moved = False
    if x is not None and y is not None:
        c.pos = (snap(x), snap(y)); moved = True
    if rotation is not None or mirror is not None:
        rot, mir, _ = code_parts(c.code)
        c.code = code_from(rotation if rotation is not None else rot * 90, mir if mirror is None else mirror)
        moved = True
    if moved and relayout_text:
        _relayout_texts(c)
    refresh_junctions(sch)
    return {"ref": c.refdes, "x": c.pos[0], "y": c.pos[1], "value": c.value,
            "pins": [{"pin": p.name or str(p.index + 1), "x": w[0], "y": w[1]} for p, w in c.pin_positions()],
            "note": "wires are not moved automatically; re-connect pins whose positions changed" if moved else ""}


def _relayout_texts(c: Component):
    """Re-apply drafting conventions for refdes/value after a rotation."""
    from .model import Symbol
    sym = c.symbol
    node = sym.node
    pins = sym.pins()
    p = Part(ref=c.refdes, sym_name=sym.name, node=node, pin_names=[pp.name or str(pp.index + 1) for pp in pins],
             pin_local=[pp.local for pp in pins], pin_optional=[bool(pp.default_net) for pp in pins],
             nets=["x"] * len(pins), value=c.value)
    req = [i for i in range(len(pins)) if not p.pin_optional[i]]
    if not sym.name and not sym.type:
        return  # hierarchical block keeps its layout
    if len(req) == 2 and len(pins) <= 3:
        p.kind = "2term"
    elif sym.type[:1] in ("Q", "M", "J", "Z"):
        p.kind = "bjt" if sym.type[:1] == "Q" else "fet"
    else:
        return  # blocks / op-amps keep their designed text layout
    texts = sym.texts()
    p.extra_texts = [text_string(t) for t in texts[2:]]
    p.codes = [c.code]; p.code = c.code
    prepare_geometry(p)
    set_part_texts(p, None)
