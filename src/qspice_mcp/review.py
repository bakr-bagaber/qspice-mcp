"""Schematic reviewer: objective drafting-quality checks with a score and
concrete, tool-level fix suggestions.  This is the "eye" that lets any LLM run
an iterative render -> review -> fix loop until the sheet is clean."""
from __future__ import annotations

import math
import re
from collections import defaultdict

from .autodraw import _dist_to_hull
from .model import Schematic
from .scene import (G, Scene, boxes_overlap, build, min_translation, on_segment_interior, overlap_area,
                    seg_hits_box, seg_points)

PENALTY = {"error": 12.0, "warning": 4.0, "style": 1.0}
CAP = {"error": 72.0, "warning": 40.0, "style": 15.0}
ANALYSES = re.compile(r"^\s*\.(tran|ac|dc|op|noise|tf|sens|pz|four)\b", re.I | re.M)
TIME_VARYING = re.compile(r"\b(sin|sine|pulse|pwl|exp|sffm|ac|wavefile|tri|square)\b", re.I)


class Issues:
    def __init__(self):
        self.items: list[dict] = []
        self._seen = set()

    def add(self, severity, code, msg, at=None, refs=None, fix=None, key=None):
        k = key or (code, msg)
        if k in self._seen:
            return
        self._seen.add(k)
        d = {"severity": severity, "code": code, "message": msg}
        if at is not None:
            d["at"] = [int(round(v)) for v in at]
        if refs:
            d["refs"] = refs
        if fix:
            d["fix"] = fix
        self.items.append(d)


def _kind_of(p):
    from .labels import _kind
    return _kind(p)


def _fmt_box(b):
    return [int(round(v)) for v in b]


def review(sch: Schematic, scene: Scene | None = None, intended: dict | None = None) -> dict:
    sc = scene or build(sch)
    I = Issues()
    parts = sc.parts
    wires = sc.wires
    # ------------------------------------------------------------ basics
    refs = defaultdict(int)
    for p in parts:
        refs[p.comp.refdes.lower()] += 1
    for r, n in refs.items():
        if n > 1:
            same = [p for p in parts if p.comp.refdes.lower() == r]
            desc = "; ".join(f"{same[0].comp.refdes}#{k + 1} = value '{(p.comp.value or '').strip()}' at "
                             f"({p.comp.pos[0]},{p.comp.pos[1]})" for k, p in enumerate(same))
            I.add("error", "DUPLICATE_REFDES", f"reference designator {same[0].comp.refdes} is used {n} times: {desc}. "
                  f"Address each one as {same[0].comp.refdes}#1, {same[0].comp.refdes}#2, ... until they are renamed.",
                  at=same[-1].comp.pos, refs=[same[0].comp.refdes],
                  fix=f"update_component(ref='{same[0].comp.refdes}#{n}', new_ref='<unused name>') renames the "
                      f"#{n} one (pick the one whose value/position is wrong)")
    subsheet = any(l.port for l in sc.labels)   # hierarchical block contents
    if parts and not subsheet and not any(l.is_ground for l in sc.labels) and "0" not in sc.conn["nets"]:
        I.add("error", "NO_GROUND", "the circuit has no ground (net 0). SPICE needs a ground reference.",
              fix="add_ground(at='<REF.PIN>') on the circuit's common/return node")
    from .qsch import display
    directives = [display(t.text) for t in sc.texts if t.role == "directive"]
    alltext = "\n".join(directives)
    if parts and not subsheet and not ANALYSES.search(alltext):
        I.add("warning", "NO_ANALYSIS", "no analysis directive (.tran/.ac/.dc/.op/.noise) on the sheet",
              fix="set_directives(directives=['.tran 1m']) (choose the analysis you need)")
    netnames = {n.lower() for n in sc.conn["nets"]} | {l.name.lower() for l in sc.labels}
    for d in directives:
        for m in re.finditer(r"\bV\(\s*([^(),]+?)\s*(?:,\s*([^(),]+?)\s*)?\)", d, re.I):
            for nm in (m.group(1), m.group(2)):
                if nm and nm.strip().lower() not in netnames and nm.strip().lower() not in ("0", "gnd"):
                    I.add("warning", "DIRECTIVE_UNKNOWN_NET",
                          f"directive refers to V({nm.strip()}) but no net is named '{nm.strip()}'",
                          fix=f"add_net_label(at='<REF.PIN on that node>', name='{nm.strip()}')")
    # ------------------------------------------------------- connectivity
    for k in sc.conn["unconnected_pins"]:
        pin = sc.pin(k)
        I.add("error", "UNCONNECTED_PIN", f"pin {k} is not connected to anything",
              at=pin.pos if pin else None, refs=[k.split(".")[0]],
              fix=f"connect_pins(from_pin='{k}', to='<REF.PIN | GND | net name>')")
    for e in sc.conn["dangling_wire_ends"]:
        I.add("error", "DANGLING_WIRE", f"wire end at ({e[0]},{e[1]}) touches nothing",
              at=e, fix=f"delete_items(wires_at=[[{e[0]},{e[1]}]]) or extend it to a pin")
    pin_pts = {}
    for q in sc.all_pins():
        pin_pts.setdefault(q.pos, []).append(q)
    wire_pts = set()
    for a, b, _ in wires:
        wire_pts.add(a); wire_pts.add(b)
    for lb in sc.labels:
        on = lb.pos in pin_pts or any(lb.pos == a or lb.pos == b or on_segment_interior(lb.pos, a, b) for a, b, _ in wires)
        if not on:
            I.add("error", "FLOATING_LABEL", f"net label '{lb.name}' at {lb.pos} is not on a wire or pin, so it connects nothing",
                  at=lb.pos, fix=f"delete_items(labels_at=[[{lb.pos[0]},{lb.pos[1]}]]) then add_net_label(at='<REF.PIN>', name='{lb.name}')")
    # wires running over pins (silent connection)
    for q in sc.all_pins():
        for a, b, _ in wires:
            if on_segment_interior(q.pos, a, b):
                I.add("warning", "WIRE_OVER_PIN",
                      f"a wire passes straight over pin {q.key} at {q.pos}: it IS connected, but it looks like a crossing",
                      at=q.pos, refs=[q.ref], fix="reroute_schematic()")
                break
    # pin-to-pin direct contact between different parts
    for pt, lst in pin_pts.items():
        owners = {q.ref for q in lst}
        if len(owners) > 1 and not any(pt == a or pt == b or on_segment_interior(pt, a, b) for a, b, _ in wires):
            ks = ", ".join(q.key for q in lst)
            I.add("style", "PIN_TO_PIN", f"pins {ks} touch directly at {pt}; a short wire between parts reads better",
                  at=pt, refs=sorted(owners), fix=f"move_component(ref='{sorted(owners)[-1]}', dx=200) (wires follow)")
    # shorted two-terminal parts
    for p in parts:
        req = [q for q in p.pins if not q.optional]
        if len(req) == 2 and req[0].net and req[0].net == req[1].net:
            I.add("warning", "SHORTED_PART", f"both pins of {p.ref} are on the same net '{req[0].net}' (part is shorted)",
                  refs=[p.ref], fix=f"read_schematic() and check the nets of {p.ref}; delete_items(refs=['{p.ref}']) if it should not be there")
    # ------------------------------------------------------------ geometry
    for p in parts:
        if p.pos[0] % 50 or p.pos[1] % 50:
            I.add("warning", "OFF_GRID_PART", f"{p.ref} sits off the grid at {p.pos}", at=p.pos, refs=[p.ref],
                  fix=f"move_component(ref='{p.ref}', x={round(p.pos[0] / G) * G}, y={round(p.pos[1] / G) * G})")
    for a, b, _ in wires:
        if a[0] != b[0] and a[1] != b[1]:
            I.add("warning", "DIAGONAL_WIRE", f"diagonal wire {a}->{b}", at=a,
                  fix=f"delete_items(wires_at=[[{a[0]},{a[1]}]]) then connect_pins(...) (orthogonal routing), or reroute_schematic()")
        for pnt in (a, b):
            if pnt[0] % 50 or pnt[1] % 50:
                I.add("warning", "OFF_GRID_WIRE", f"wire end {pnt} is off the 100-unit grid", at=pnt,
                      key=("OFF_GRID_WIRE", pnt))
        if a == b:
            I.add("style", "ZERO_WIRE", f"zero-length wire at {a}", at=a, fix=f"delete_items(wires_at=[[{a[0]},{a[1]}]])")
    # overlapping wires (collinear)
    for i in range(len(wires)):
        a, b, _ = wires[i]
        for j in range(i + 1, len(wires)):
            c, d, _ = wires[j]
            ov = _collinear_overlap(a, b, c, d)
            if ov:
                I.add("warning", "WIRES_OVERLAP", f"two wires lie on top of each other around {ov} (they are merged electrically)",
                      at=ov, fix="reroute_schematic() to redraw clean wires")
    # wires through component bodies
    for a, b, n in wires:
        if a[0] != b[0] and a[1] != b[1]:
            continue
        hit = set()
        ends = {e for e in (a, b) if e in pin_pts}
        for pt in [a, b] + seg_points(a, b, 50):
            owner = None
            if pt in pin_pts:
                continue
            for p in parts:
                if p.hits_shape(pt, near=20, skip_ends=ends):
                    owner = p
                    break
            if owner is not None and owner.ref not in hit:
                hit.add(owner.ref)
                I.add("error", "WIRE_THROUGH_PART", f"wire {a}->{b} runs through the body of {owner.ref}",
                      at=pt, refs=[owner.ref], fix=f"reroute_schematic(); if it persists move_component(ref='{owner.ref}', dx=200)",
                      key=("WTP", a, b, owner.ref))
    # junction bookkeeping
    deg = defaultdict(int)
    for a, b, _ in wires:
        deg[a] += 1; deg[b] += 1
    for a, b, _ in wires:
        for pt in list(deg.keys()):
            if on_segment_interior(pt, a, b):
                deg[pt] += 2
    for q in sc.all_pins():
        if q.pos in deg:
            deg[q.pos] += 1
    juncs = set(sc.junctions)
    for pt, d in deg.items():
        # QSPICE's own drafting guideline accepts 4-way joins *with* a dot
        # (staggering tees is discouraged), so only an undotted one is a problem.
        if d >= 4 and pt not in pin_pts and pt not in juncs:
            I.add("warning", "FOUR_WAY_NO_DOT", f"four wires meet at {pt} without a junction dot - it reads as a crossing",
                  at=pt, fix="reroute_schematic()")
        elif d >= 3 and pt not in juncs:
            I.add("style", "MISSING_JUNCTION_DOT", f"T connection at {pt} has no junction dot", at=pt,
                  fix="reroute_schematic()")
    for j in juncs:
        if deg.get(j, 0) < 3:
            I.add("style", "EXTRA_JUNCTION_DOT", f"junction dot at {j} where fewer than 3 wires meet", at=j)
    crossings = _crossings(wires)
    for pt in crossings[:30]:
        I.add("style", "WIRE_CROSSING", f"wires cross at {pt} (not connected)", at=pt, key=("X", pt))
    # parts overlapping
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            A, B = parts[i], parts[j]
            if boxes_overlap(_shrink(A.body, 15), _shrink(B.body, 15)):
                dx, dy = min_translation(B.body, A.body)
                I.add("error", "PARTS_OVERLAP", f"{A.ref} and {B.ref} overlap",
                      at=((A.body[0] + A.body[2]) / 2, (A.body[1] + A.body[3]) / 2), refs=[A.ref, B.ref],
                      fix=f"move_component(ref='{B.ref}', dx={dx}, dy={dy}) (wires follow)")
            elif _gap(A.body, B.body) < 60 and not _share_pin(A, B):
                I.add("style", "PARTS_CROWDED", f"{A.ref} and {B.ref} are very close (<60 units)", refs=[A.ref, B.ref])
    # ---------------------------------------------------------------- text
    texts = sc.texts
    for i in range(len(texts)):
        ti = texts[i]
        if ti.role in ("ground",):
            continue
        for j in range(i + 1, len(texts)):
            tj = texts[j]
            if tj.role in ("ground",):
                continue
            if boxes_overlap(_shrink(ti.box, 12), _shrink(tj.box, 12)):
                who = _who(ti), _who(tj)
                I.add("warning", "TEXT_OVERLAP", f"text '{_short(ti.text)}' ({who[0]}) overlaps '{_short(tj.text)}' ({who[1]})",
                      at=_center(ti.box), refs=sorted({x for x in (ti.owner, tj.owner) if not x.startswith(('label', 'dir', 'com'))}),
                      fix=_text_fix(ti, tj))
    for t in texts:
        if t.role in ("ground",):
            continue
        for a, b, n in wires:
            if t.role == "label" and (t.anchor == a or t.anchor == b or on_segment_interior(t.anchor, a, b)):
                continue
            if seg_hits_box(a, b, t.box, shrink=15):
                I.add("warning", "TEXT_ON_WIRE", f"wire {a}->{b} runs through text '{_short(t.text)}' ({_who(t)})",
                      at=_center(t.box), refs=[t.owner] if not t.owner.startswith(("label", "dir", "com")) else None,
                      fix=(f"fix_text_placement(refs=['{t.owner}'])" if not t.owner.startswith(("label", "dir", "com")) else "reroute_schematic()"), key=("TOW", t.owner, t.role, a, b))
                break
        for p in parts:
            own = (p.ref == t.owner)
            if own and (_inside(t.anchor, p.body, 0) or _kind_of(p) == "block"):
                continue  # text designed inside / on its own symbol (op-amp, switches, blocks)
            if boxes_overlap(_shrink(t.box, 15), _shrink(p.body, 10)) and _box_hits_shape(_shrink(t.box, 15), p):
                I.add("warning", "TEXT_ON_PART", f"text '{_short(t.text)}' ({_who(t)}) is drawn over {p.ref}",
                      at=_center(t.box), refs=[p.ref], fix=(f"fix_text_placement(refs=['{t.owner}'])" if not t.owner.startswith(("label", "dir", "com")) else f"move_component(ref='{p.ref}', dx=200)"),
                      key=("TOP", t.owner, t.role, p.ref))
    from .labels import _kind
    for p in parts:
        if _kind(p) == "block":
            continue  # designed symbol texts (op-amps, ICs) keep their own layout
        for t in p.texts:
            d = _gap(t.box, p.body)
            if d > 350:
                I.add("warning", "TEXT_FAR_FROM_PART", f"{p.ref}'s {t.role} text is {int(d)} units away from the symbol",
                      at=_center(t.box), refs=[p.ref], fix=f"fix_text_placement(refs=['{p.ref}'])")
    # ---------------------------------------------------------- conventions
    for lb in sc.labels:
        if lb.is_ground and (lb.just & 3) != 1:
            I.add("style", "GROUND_NOT_DOWN", f"ground symbol at {lb.pos} does not point down", at=lb.pos,
                  fix=f"delete_items(labels_at=[[{lb.pos[0]},{lb.pos[1]}]]) then add_ground(at='<REF.PIN>')")
    for p in parts:
        req = [q for q in p.pins if not q.optional]
        if p.type in ("V", "I", "B") and len(req) == 2:
            nets = [q.net for q in req]
            if "0" in nets:
                g = req[nets.index("0")]
                o = req[1 - nets.index("0")]
                if g.pos[1] > o.pos[1]:
                    I.add("warning", "SOURCE_UPSIDE_DOWN", f"{p.ref} has its ground pin on top", refs=[p.ref],
                          fix=f"move_component(ref='{p.ref}', rotation=0, mirror=False) (wires follow)")
                elif g.pos[1] == o.pos[1]:
                    I.add("style", "SOURCE_SIDEWAYS", f"{p.ref} lies sideways; sources read best upright with ground below",
                          refs=[p.ref])
    _flow_checks(sc, I)
    # long wires
    W = max(1.0, sc.bbox[2] - sc.bbox[0])
    for a, b, n in wires:
        L = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if L > max(2500, 0.6 * W):
            I.add("style", "LONG_WIRE", f"wire {a}->{b} is {L} units long; consider net labels instead", at=a,
                  key=("LW", a, b))
    # ---------------------------------------------------------------- score
    if intended:
        _check_intended(sc, intended, I)
    score = 100.0
    by = defaultdict(float)
    for it in I.items:
        by[it["severity"]] += PENALTY[it["severity"]]
    for sev, v in by.items():
        score -= min(v, CAP[sev])
    score = max(0, round(score, 1))
    grade = "A" if score >= 90 else "B" if score >= 78 else "C" if score >= 62 else "D" if score >= 45 else "F"
    order = {"error": 0, "warning": 1, "style": 2}
    I.items.sort(key=lambda d: order[d["severity"]])
    counts = {s: sum(1 for d in I.items if d["severity"] == s) for s in ("error", "warning", "style")}
    label_names = {l.name.lower() for l in sc.labels}
    real_nets = [n for n, pins in sc.conn["nets"].items() if len(pins) >= 2 or n.lower() in label_names]
    stats = {"parts": len(parts), "nets": len(real_nets), "wires": len(wires),
             "wire_length": sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b, _ in wires),
             "crossings": len(crossings), "labels": len(sc.labels),
             "extent": _fmt_box(sc.bbox)}
    return {"score": score, "grade": grade, "counts": counts, "issues": I.items, "stats": stats,
            "next_steps": _next_steps(I.items, score)}


# ------------------------------------------------------------------ helpers

def _box_hits_shape(box, p, step: float = 25.0) -> bool:
    """Does a text box touch the symbol's drawing itself (not just the empty corners
    of its bounding box, e.g. beside an op-amp triangle)?"""
    x0, y0 = max(box[0], p.body[0]), max(box[1], p.body[1])
    x1, y1 = min(box[2], p.body[2]), min(box[3], p.body[3])
    if x0 >= x1 or y0 >= y1:
        return False
    if not (p.polys or p.segs or p.ellipses):
        return True
    nx = max(2, int((x1 - x0) / step) + 1)
    ny = max(2, int((y1 - y0) / step) + 1)
    for i in range(nx):
        for j in range(ny):
            if p.hits_shape((x0 + (x1 - x0) * i / (nx - 1), y0 + (y1 - y0) * j / (ny - 1)), near=12.0):
                return True
    return False


def _next_steps(items, score):
    codes = {d["code"] for d in items}
    steps = []
    if {"UNCONNECTED_PIN", "DANGLING_WIRE", "FLOATING_LABEL", "NO_GROUND"} & codes:
        steps.append("Fix connectivity first (errors marked UNCONNECTED_PIN / DANGLING_WIRE / FLOATING_LABEL / NO_GROUND).")
    if "PARTS_OVERLAP" in codes:
        steps.append("Move overlapping parts apart with move_component (see each fix; wires follow).")
    if {"WIRE_THROUGH_PART", "WIRES_OVERLAP", "WIRE_OVER_PIN", "FOUR_WAY_JUNCTION"} & codes:
        steps.append("Call reroute_schematic() to redraw all wires cleanly around the parts.")
    if {"TEXT_OVERLAP", "TEXT_ON_WIRE", "TEXT_ON_PART", "TEXT_FAR_FROM_PART"} & codes:
        steps.append("Call fix_text_placement() to re-place reference/value texts; if overlaps remain, move_component the crowded parts apart.")
    if score >= 90 and not steps:
        steps.append("Looks good. Render once more to confirm visually, then simulate.")
    elif not steps:
        steps.append("Address the remaining warnings, then render_schematic() and review_schematic() again.")
    steps.append("Repeat render -> review -> fix until score >= 90 with 0 errors.")
    return steps


def _check_intended(sc: Scene, intended: dict, I: Issues):
    """intended: {ref: {pin: net}} — compare actual partition with the intended one."""
    fwd, back = {}, {}
    for ref, pins in intended.items():
        p = sc.part(ref)
        if not p:
            I.add("error", "MISSING_PART", f"{ref} is not on the sheet", refs=[ref])
            continue
        for pin, net in pins.items():
            q = sc.pin(f"{ref}.{pin}")
            if not q:
                I.add("error", "UNKNOWN_PIN", f"{ref} has no pin {pin}", refs=[ref])
                continue
            a = q.net or f"?{q.key}"
            n = str(net).lower()
            n = "0" if n in ("gnd", "ground") else n
            if n in fwd and fwd[n] != a:
                I.add("error", "NET_SPLIT", f"{ref}.{pin} should be on net '{net}' with the other pins of that net, but is not connected to them",
                      at=q.pos, refs=[ref])
            elif a in back and back[a] != n:
                I.add("error", "NET_SHORT", f"{ref}.{pin} (net '{net}') is wrongly connected to net '{back[a]}'",
                      at=q.pos, refs=[ref])
            elif q.net and not re.fullmatch(r"n\d+", q.net.lower()) and q.net.lower() != n and \
                    not re.fullmatch(r"n\d+", n):
                # the pin sits on a NAMED net (label) whose name differs from the intended one
                I.add("error", "WRONG_NET", f"{ref}.{pin} is on net '{q.net}', but should be on '{net}'",
                      at=q.pos, refs=[ref])
            fwd.setdefault(n, a); back.setdefault(a, n)


def _flow_checks(sc: Scene, I: Issues):
    ins, outs = [], []
    for lb in sc.labels:
        nm = lb.name.lower()
        if re.match(r"^(v?in|input|vi)\w*$", nm):
            ins.append(lb.pos[0])
        elif re.match(r"^(v?out|output|vo)\w*$", nm):
            outs.append(lb.pos[0])
    if ins and outs and min(ins) > max(outs):
        I.add("style", "FLOW_RIGHT_TO_LEFT", "input labels are to the right of output labels; signal should flow left -> right")
    xs = [p.pos[0] for p in sc.parts]
    if len(xs) >= 4:
        xs_sorted = sorted(xs)
        median = xs_sorted[len(xs) // 2]
        for p in sc.parts:
            if p.type == "V" and TIME_VARYING.search(p.value or "") and p.pos[0] > median and any(q.net == "0" for q in p.pins):
                I.add("style", "INPUT_SOURCE_NOT_LEFT", f"signal source {p.ref} is right of centre; inputs belong on the left",
                      refs=[p.ref])


def _collinear_overlap(a, b, c, d):
    if a[1] == b[1] == c[1] == d[1]:
        lo = max(min(a[0], b[0]), min(c[0], d[0])); hi = min(max(a[0], b[0]), max(c[0], d[0]))
        if hi - lo > 0:
            return ((lo + hi) / 2, a[1])
    if a[0] == b[0] == c[0] == d[0]:
        lo = max(min(a[1], b[1]), min(c[1], d[1])); hi = min(max(a[1], b[1]), max(c[1], d[1]))
        if hi - lo > 0:
            return (a[0], (lo + hi) / 2)
    return None


def _crossings(wires):
    pts = []
    hs = [(a, b) for a, b, _ in wires if a[1] == b[1] and a[0] != b[0]]
    vs = [(a, b) for a, b, _ in wires if a[0] == b[0] and a[1] != b[1]]
    for (a, b) in hs:
        y = a[1]; x0, x1 = sorted((a[0], b[0]))
        for (c, d) in vs:
            x = c[0]; y0, y1 = sorted((c[1], d[1]))
            if x0 < x < x1 and y0 < y < y1:
                pts.append((x, y))
    return pts


def _inside(pt, box, margin=0):
    return box[0] + margin < pt[0] < box[2] - margin and box[1] + margin < pt[1] < box[3] - margin


def _shrink(b, s):
    return (b[0] + s, b[1] + s, b[2] - s, b[3] - s)


def _gap(a, b):
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _share_pin(A, B):
    pa = {q.net for q in A.pins if q.net}
    return any(q.net in pa for q in B.pins if q.net)


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _short(s, n=24):
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _who(t):
    if t.owner.startswith("label:"):
        return "net label"
    if t.owner in ("directive", "comment"):
        return t.owner
    return f"{t.owner} {t.role}"


def _text_fix(ti, tj):
    parts = [t.owner for t in (ti, tj) if not t.owner.startswith(("label", "dir", "com"))]
    if parts:
        return (f"fix_text_placement(refs={sorted(set(parts))}); if it persists move_component one of "
                f"them 200-300 units apart")
    if any(t.owner.startswith("dir") for t in (ti, tj)):
        return "set_directives(directives=[...same...], x=..., y=...) to move the directive block into free space"
    return "delete_items(labels_at=[[x,y]]) and add the label at a different pin of the same net"
