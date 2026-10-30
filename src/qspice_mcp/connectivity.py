"""Geometric connectivity extraction from a schematic (pins, wires, labels).

Rules (QSPICE/LTspice style): wires connect at their endpoints; an endpoint that
lands on the interior of another wire forms a T connection; plain crossings do
not connect; a pin connects to any wire endpoint/interior at its location; net
labels connect everything at their point and all labels with the same name are
the same net."""
from __future__ import annotations

import re
from collections import defaultdict

from .model import Schematic

GROUND_NAMES = {"0", "gnd", "ground"}


class UF:
    def __init__(self):
        self.p = {}

    def f(self, a):
        p = self.p.setdefault(a, a)
        while p != self.p[p]:
            self.p[p] = self.p[self.p[p]]
            p = self.p[p]
        self.p[a] = p
        return p

    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[ra] = rb


def _on_seg(p, a, b):
    (x, y), (x0, y0), (x1, y1) = p, a, b
    if x0 == x1 == x:
        return min(y0, y1) <= y <= max(y0, y1)
    if y0 == y1 == y:
        return min(x0, x1) <= x <= max(x0, x1)
    # diagonal wire
    if (x - x0) * (y1 - y0) != (y - y0) * (x1 - x0):
        return False
    return min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1)


def extract(sch: Schematic) -> dict:
    uf = UF()
    wires = sch.wires()
    points = set()
    for i, (a, b, _) in enumerate(wires):
        a, b = tuple(a), tuple(b)
        uf.u(("P", a), ("W", i)); uf.u(("P", b), ("W", i))
        points.add(a); points.add(b)
    pins = []
    for c in sch.components():
        for p, w in c.pin_positions():
            pins.append((c.refdes, p.name or str(p.index + 1), p.index, w, p.default_net))
            points.add(w)
    labels = sch.nets()
    for p, name, *_ in labels:
        points.add(tuple(p))
    # endpoints / pins / labels lying on wire interiors
    for pnt in points:
        for i, (a, b, _) in enumerate(wires):
            if _on_seg(pnt, tuple(a), tuple(b)):
                uf.u(("P", pnt), ("W", i))
    for p, name, *_ in labels:
        key = name.lower() if name.lower() not in GROUND_NAMES else "0"
        uf.u(("P", tuple(p)), ("L", key))
    pin_net = {}
    for ref, pname, idx, w, dflt in pins:
        pin_net[(ref, pname, idx)] = uf.f(("P", w))
    # name each net
    names = {}
    for p, name, *_ in labels:
        r = uf.f(("P", tuple(p)))
        nm = "0" if name.lower() in GROUND_NAMES else name
        if r not in names or nm == "0":
            names[r] = nm
    groups = defaultdict(list)
    for (ref, pname, idx), r in pin_net.items():
        groups[r].append(f"{ref}.{pname}")
    auto = 1
    out_pins = {}
    for r in sorted(groups, key=lambda r: str(r)):
        if r not in names:
            names[r] = f"N{auto:02d}"
            auto += 1
    for (ref, pname, idx), r in pin_net.items():
        out_pins.setdefault(ref, []).append({"pin": pname, "index": idx, "net": names[r]})
    unconnected = []
    for ref, pname, idx, w, dflt in pins:
        r = pin_net[(ref, pname, idx)]
        if len(groups[r]) == 1 and r not in {uf.f(("P", tuple(p))) for p, *_ in labels} and not dflt:
            # a lone pin: is any wire attached?
            touching = any(_on_seg(w, tuple(a), tuple(b)) for a, b, _ in wires)
            if not touching:
                unconnected.append(f"{ref}.{pname}")
    nets = defaultdict(list)
    for ref, lst in out_pins.items():
        for d in lst:
            nets[d["net"]].append(f"{ref}.{d['pin']}")
    dangling = []
    for i, (a, b, _) in enumerate(wires):
        for e in (tuple(a), tuple(b)):
            deg = sum(1 for j, (c, d, _) in enumerate(wires) if j != i and _on_seg(e, tuple(c), tuple(d)))
            on_pin = any(w == e for *_, w, _d in pins)
            on_lbl = any(tuple(p) == e for p, *_ in labels)
            if deg == 0 and not on_pin and not on_lbl:
                dangling.append(list(e))
    return {"pins": out_pins, "nets": dict(nets), "unconnected_pins": unconnected, "dangling_wire_ends": dangling}


# ------------------------------------------------- netlist line parsing ----

def parse_netlist_connections(text: str, pin_counts: dict[str, int]) -> dict[str, list[str]]:
    """Map refdes -> node list from a QUX generated netlist, given how many
    pins each refdes has in the schematic."""
    out = {}
    lines = _join_continuations(text)
    low = {k.lower(): k for k in pin_counts}
    depth = 0
    for line in lines:
        ll = line.lower()
        if ll.startswith(".subckt"):
            depth += 1
            continue
        if ll.startswith(".ends"):
            depth = max(0, depth - 1)
            continue
        if depth or not line or line[0] in "*.;":
            continue
        toks = line.replace("\u00ab", " ").replace("\u00bb", " ").split()
        ref = toks[0]
        cand = [ref]
        if "\u00b4" in ref:            # QUX: <device letter>´<refdes>
            cand.append(ref.split("\u00b4", 1)[1])
        cand.append(ref[1:])            # letter-prefixed without separator (e.g. ØX1)
        key = next((low[c.lower()] for c in cand if c.lower() in low), None)
        if key is None:
            continue
        n = pin_counts[key]
        nodes = [t.split("\u00b4", 1)[0].lower() for t in toks[1:1 + n]]
        out[key] = nodes
    return out


def _join_continuations(text: str) -> list[str]:
    res = []
    for l in text.splitlines():
        if l.startswith("+") and res:
            res[-1] += " " + l[1:]
        else:
            res.append(l.strip())
    return res


def compare_connectivity(intended: dict[str, list[str]], actual: dict[str, list[str]]) -> dict:
    """intended/actual: ref -> ordered node names.  Net names may differ; we check
    the induced partition of pins is identical (ground must map to ground)."""
    problems = []
    fwd, back = {}, {}
    for ref, nodes in intended.items():
        if ref not in actual:
            problems.append(f"{ref} missing from netlist")
            continue
        an = actual[ref]
        if len(an) < len(nodes):
            problems.append(f"{ref}: expected {len(nodes)} nodes, netlist has {len(an)}")
            continue
        for i, (n, a) in enumerate(zip(nodes, an)):
            nl = "0" if n.lower() in GROUND_NAMES else n.lower()
            if nl in fwd and fwd[nl] != a:
                problems.append(f"{ref} pin {i+1}: intended net '{n}' is split ('{fwd[nl]}' vs '{a}')")
            elif a in back and back[a] != nl:
                problems.append(f"{ref} pin {i+1}: nets '{back[a]}' and '{n}' are shorted together (as '{a}')")
            fwd.setdefault(nl, a); back.setdefault(a, nl)
            if nl == "0" and a != "0":
                problems.append(f"{ref} pin {i+1}: should be ground but is '{a}'")
            if re.match(r"^¥", a) or a.startswith("¥"):
                problems.append(f"{ref} pin {i+1}: unconnected in drawing")
    for ref in actual:
        if ref not in intended:
            problems.append(f"unexpected component {ref} in netlist")
    return {"ok": not problems, "problems": problems, "net_map": fwd}
