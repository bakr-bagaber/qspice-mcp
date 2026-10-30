"""Convert a SPICE netlist (QSPICE / LTspice / generic SPICE) into an
autodraw circuit spec, so netlists can be turned into neat schematics."""
from __future__ import annotations

import re


def _logical_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if line.lstrip().startswith("+") and out:
            out[-1] += " " + line.lstrip()[1:].strip()
        else:
            out.append(line.strip())
    return out


def _strip_comment(l: str) -> str:
    # ';' and '$ ' style inline comments (not inside braces/quotes)
    m = re.search(r"\s;|\s\$\s", l)
    return l[:m.start()].rstrip() if m else l


def parse(text: str) -> dict:
    lines = _logical_lines(text)
    title = None
    if lines and lines[0].startswith("*"):
        title = lines[0].lstrip("* ").strip() or None
        lines = lines[1:]
    models: dict[str, str] = {}
    subckts: dict[str, list[str]] = {}
    directives: list[str] = []
    devices: list[str] = []
    in_sub = 0
    block: list[str] = []
    for l in lines:
        if not l or l.startswith("*"):
            if in_sub and l:
                block.append(l)
            continue
        l = _strip_comment(l)
        low = l.lower()
        if low.startswith(".subckt"):
            in_sub += 1
            toks = l.split()
            if len(toks) >= 2:
                pins = []
                for t in toks[2:]:
                    if "=" in t or t.lower() in ("params:",):
                        break
                    pins.append(t)
                subckts[toks[1].lower()] = [toks[1]] + pins
            block.append(l)
            continue
        if in_sub:
            block.append(l)
            if low.startswith(".ends"):
                in_sub -= 1
                if in_sub == 0:
                    directives.append("\n".join(block))
                    block = []
            continue
        if low.startswith(".end") and not low.startswith(".ends"):
            continue
        if low.startswith(".model"):
            toks = re.split(r"[\s(]+", l)
            if len(toks) >= 3:
                models[toks[1].lower()] = toks[2].upper()
            directives.append(l)
            continue
        if l.startswith("."):
            directives.append(l)
            continue
        devices.append(l)
    comps = []
    notes = []
    for l in devices:
        c = _device(l, models, subckts, notes)
        if c is None:
            directives.append(l)
        else:
            comps.append(c)
    spec = {"components": comps, "directives": directives}
    if title:
        spec["title"] = title
    if notes:
        spec["_import_notes"] = notes
    return spec


def _split_value(toks, n):
    nodes = toks[1:1 + n]
    rest = " ".join(toks[1 + n:])
    return nodes, rest


def _device(l: str, models: dict, subckts: dict, notes: list) -> dict | None:
    toks = l.split()
    if not toks:
        return None
    ref = toks[0]
    k = ref[0].upper()
    def mk(sym, nnodes, pins=None):
        nodes, rest = _split_value(toks, nnodes)
        d = {"ref": ref, "symbol": sym, "value": rest}
        if pins:
            d["pins"] = dict(zip(pins, nodes))
        else:
            d["nodes"] = nodes
        return d
    if k in "RCL":
        return mk(k, 2)
    if k in "VI":
        return mk(k, 2)
    if k == "D":
        nodes, rest = _split_value(toks, 2)
        model = rest.split()[0].lower() if rest else ""
        sym = "D"
        if re.search(r"zener|bz|1n47|1n52", model):
            sym = "Zener"
        return {"ref": ref, "symbol": sym, "value": rest, "pins": {"A": nodes[0], "K": nodes[1]}}
    if k == "Q":
        # Q c b e [s] model
        n = 3
        if len(toks) > 5 and toks[4].lower() not in models and not re.match(r"^[\d.]", toks[4]) and toks[5].lower() in models:
            n = 4
        nodes, rest = _split_value(toks, n)
        model = rest.split()[0].lower() if rest else ""
        typ = models.get(model, "NPN")
        sym = "PNP" if typ.startswith("PNP") or re.search(r"pnp", model) else "NPN"
        if n == 4 and nodes[3] not in ("0", "gnd", "GND"):
            notes.append(f"{ref}: substrate node {nodes[3]} dropped (QSPICE BJT symbol has 3 pins)")
        return {"ref": ref, "symbol": sym, "value": rest, "pins": {"C": nodes[0], "B": nodes[1], "E": nodes[2]}}
    if k == "M":
        # M d g s [b] model
        n = 4 if len(toks) > 5 or (len(toks) == 5 and toks[4].lower() not in models) else 3
        if len(toks) >= 6 and toks[4].lower() in models:
            n = 3
        nodes, rest = _split_value(toks, n)
        model = rest.split()[0].lower() if rest else ""
        typ = models.get(model, "")
        pch = typ.startswith("PMOS") or "pchan" in l.lower() or re.search(r"^p|pmos", model) and not typ.startswith("NMOS")
        sym = "PMOS" if pch else "NMOS"
        if n == 4 and nodes[3] != nodes[2]:
            notes.append(f"{ref}: bulk {nodes[3]} differs from source {nodes[2]}; drawn with bulk tied to source"
                         " (QSPICE's MOSFET symbol is 3-terminal)")
        return {"ref": ref, "symbol": sym, "value": rest, "pins": {"D": nodes[0], "G": nodes[1], "S": nodes[2]}}
    if k == "J":
        nodes, rest = _split_value(toks, 3)
        model = rest.split()[0].lower() if rest else ""
        sym = "PJF" if models.get(model, "").startswith("PJF") else "NJF"
        return {"ref": ref, "symbol": sym, "value": rest, "pins": {"D": nodes[0], "G": nodes[1], "S": nodes[2]}}
    if k in "EG":
        nodes, rest = _split_value(toks, 4)
        return {"ref": ref, "symbol": k, "value": rest, "pins": dict(zip(["+", "-", "+C", "-C"], nodes))}
    if k in "FH":
        return mk(k, 2, ["+", "-"])
    if k == "B":
        nodes, rest = _split_value(toks, 2)
        sym = "B2" if re.match(r"^\s*i\s*=", rest, re.I) else "B1"
        return {"ref": ref, "symbol": sym, "value": rest, "pins": {"+": nodes[0], "-": nodes[1]}}
    if k == "S":
        nodes, rest = _split_value(toks, 4)
        return {"ref": ref, "symbol": "SW", "value": rest, "pins": dict(zip(["1", "2", "+", "-"], nodes))}
    if k == "W":
        return mk("CSW", 2, ["1", "2"])
    if k in "TO":
        nodes, rest = _split_value(toks, 4)
        return {"ref": ref, "symbol": k, "value": rest, "pins": dict(zip(["L1", "L2", "R1", "R2"], nodes))}
    if k == "U":
        return mk("U", 3, ["1", "2", "COM"])
    if k == "Y":
        return mk("Y", 2, ["+", "-"])
    if k == "X":
        # X n1 n2 ... subname [params]
        body = toks[1:]
        params = [t for t in body if "=" in t]
        plain = [t for t in body if "=" not in t and t.lower() != "params:"]
        if not plain:
            return None
        sub = plain[-1]
        nodes = plain[:-1]
        pins = subckts.get(sub.lower(), [sub] + [f"P{i+1}" for i in range(len(nodes))])[1:]
        if len(pins) != len(nodes):
            pins = [f"P{i+1}" for i in range(len(nodes))]
        return {"ref": ref, "symbol": "X", "subckt": sub, "subckt_pins": pins,
                "value": " ".join([sub] + params), "pins": dict(zip(pins, nodes))}
    if k == "K":
        return None  # mutual inductance -> directive text
    notes.append(f"{ref}: unrecognised device letter; kept as a directive line")
    return None
