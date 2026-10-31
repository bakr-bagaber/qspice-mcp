"""Symbol library: harvested from the QSPICE installation (.qsym files plus the
symbols embedded in every installed example schematic), user symbol folders,
and auto-generated block symbols for subcircuits."""
from __future__ import annotations

import collections
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import qsch
from .config import cache_dir, qspice_dir, extra_symbol_dirs
from .model import Symbol, q, pt
from .qsch import Node

# SPICE device letter / QSPICE symbol type -> preferred symbol name
TYPE_DEFAULT = {
    "R": "R", "C": "C", "L": "L", "V": "V", "I": "I", "D": "D",
    "QN": "NPN", "QP": "PNP", "MN": "NMOS", "MP": "PMOS", "JN": "NJF", "JP": "PJF",
    "S": "SW", "W": "CSW", "E": "E", "F": "F", "G": "G", "H": "H",
    "T": "T", "O": "O", "U": "U", "Y": "Y", "ZN": "NMF",
}
# friendly aliases an LLM may use
ALIASES = {
    "resistor": "R", "res": "R", "capacitor": "C", "cap": "C", "inductor": "L", "ind": "L",
    "vsource": "V", "voltage": "V", "voltage_source": "V", "isource": "I", "current_source": "I",
    "current": "I", "diode": "D", "zener": "Zener", "schottky": "Schottky", "led": "D",
    "npn": "NPN", "pnp": "PNP", "nmos": "NMOS", "pmos": "PMOS", "njf": "NJF", "pjf": "PJF",
    "opamp": "OpAmp", "op_amp": "OpAmp", "op-amp": "OpAmp", "switch": "SW", "vcsw": "SW",
    "polcap": "C2", "polarized_capacitor": "C2", "electrolytic": "C2", "crystal": "Y",
    "vcvs": "E", "cccs": "F", "vccs": "G", "ccvs": "H", "bv": "B1", "bi": "B2",
    "behavioral_voltage": "B1", "behavioral_current": "B2", "transformer": "Transformer",
    "tline": "T", "gnd": "GND", "ground": "GND",
}

@dataclass
class SymEntry:
    name: str
    type: str
    description: str
    source: str
    text: str                  # serialized «symbol …» node (latin-1 str)
    pins: list = field(default_factory=list)
    bbox: tuple = (0, 0, 0, 0)

    def node(self) -> Node:
        return qsch.parse_text(self.text)

    def summary(self) -> dict:
        return {"name": self.name, "type": self.type, "description": self.description,
                "pins": [p for p in self.pins], "bbox": list(self.bbox), "source": self.source}


def _entry(node: Node, source: str) -> SymEntry:
    s = Symbol(node)
    pins = []
    for p in s.pins():
        d = {"name": p.name, "x": p.local[0], "y": p.local[1], "order": p.index}
        if p.default_net:
            d["optional"] = True
        pins.append(d)
    return SymEntry(s.name, s.type, s.description, source, qsch.serialize(node, 0), pins, s.bbox())


def _shape_key(node: Node) -> str:
    parts = []
    for c in node.children:
        if c.kind == "text":
            parts.append("text " + " ".join(str(t) for t in c.tokens[1:4]))
        else:
            parts.append(c.header)
    return "\n".join(parts)


class SymbolLibrary:
    def __init__(self):
        self.entries: dict[str, SymEntry] = {}
        self._loaded = False

    # ---------------------------------------------------------- loading ----
    def _scan_sources(self):
        root = qspice_dir()
        dirs = [root] + extra_symbol_dirs()
        try:
            from .helpdocs import ensure_help, chm_html_dir
            ensure_help()
            dirs.append(chm_html_dir())
        except Exception:
            pass
        qsyms, qschs = [], []
        for d in dirs:
            if not d.exists():
                continue
            for p in d.rglob("*.qsym"):
                qsyms.append(p)
            for p in d.rglob("*.qsch"):
                qschs.append(p)
        return qsyms, qschs

    def _signature(self, files) -> str:
        # NOTE: must be stable across processes (str hash() is randomized)
        import zlib
        h = 0
        for p in sorted(files, key=str):
            try:
                st = p.stat()
                h = zlib.crc32(f"{p}|{int(st.st_mtime)}|{st.st_size}".encode("utf-8", "replace"), h)
            except OSError:
                pass
        return f"{len(files)}:{h:08x}:v5"

    def load(self, force: bool = False):
        if self._loaded and not force:
            return self
        qsyms, qschs = self._scan_sources()
        sig = self._signature(qsyms + qschs)
        cfile = cache_dir() / "symbols.json"
        if not force and cfile.exists():
            try:
                data = json.loads(cfile.read_text(encoding="utf-8"))
                if data.get("sig") == sig:
                    self.entries = {k: SymEntry(**v) for k, v in data["entries"].items()}
                    for e in self.entries.values():
                        e.bbox = tuple(e.bbox)
                    self._loaded = True
                    return self
            except Exception:
                pass
        entries: dict[str, SymEntry] = {}
        # 1) harvested from schematics: choose the most common shape per name
        variants: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        nodes: dict[tuple, tuple] = {}
        for p in qschs:
            try:
                root = qsch.read_file(p)
            except Exception:
                continue
            for comp in root.findall("component"):
                sn = comp.find("symbol")
                if sn is None:
                    continue
                s = Symbol(sn)
                key = (s.name, s.description)
                sk = _shape_key(sn)
                variants[s.name][(s.description, sk)] += 1
                nodes.setdefault((s.name, s.description, sk), (sn, p))
        for name, cnt in variants.items():
            if not name:
                continue
            # most common description first, then most common shape
            desc_cnt = collections.Counter()
            for (d, _), n in cnt.items():
                desc_cnt[d] += n
            best_desc = desc_cnt.most_common(1)[0][0]
            best = max(((k, n) for k, n in cnt.items() if k[0] == best_desc), key=lambda kv: kv[1])[0]
            sn, p = nodes[(name, best[0], best[1])]
            if name.upper() in ("B1", "B2"):
                # behavioural sources: don't show one example's expression as the default
                try:
                    from .model import Symbol as _S, set_text_string as _sts
                    tx = _S(sn).texts()
                    if len(tx) > 1:
                        _sts(tx[1], "V=0" if name.upper() == "B1" else "I=0")
                except Exception:
                    pass
            entries[name] = _entry(sn, f"example:{p.name}")
        # 2) .qsym files override (pristine definitions)
        for p in qsyms:
            try:
                root = qsch.read_file(p)
            except Exception:
                continue
            if root.kind != "symbol":
                continue
            e = _entry(root, str(p))
            if not e.name:
                e.name = p.stem
            entries[e.name] = e
        # 3) synthesized symbols for devices missing from the install
        self.entries = entries
        self._synthesize_missing()
        cache_dir().mkdir(parents=True, exist_ok=True)
        cfile.write_text(json.dumps({"sig": sig, "entries": {k: v.__dict__ for k, v in self.entries.items()}}), encoding="utf-8")
        self._loaded = True
        return self

    def _synthesize_missing(self):
        e = self.entries
        # give nameless pins of controlled sources sensible names
        names_for = {"E": ["+", "-", "+C", "-C"], "F": ["+", "-"], "H": ["+", "-"]}
        for sname, names in names_for.items():
            if sname in e and any(not p["name"] for p in e[sname].pins):
                node = e[sname].node()
                for pn, nm in zip(node.findall("pin"), names):
                    if pn.header.endswith('""'):
                        pn.header = pn.header[:-2] + q(nm)
                e[sname] = _entry(node, e[sname].source)
        if "PJF" not in e and "NJF" in e:
            txt = e["NJF"].text.replace("type: JN", "type: JP").replace("N-Channel", "P-Channel").replace("symbol NJF", "symbol PJF")
            node = qsch.parse_text(txt)
            # reverse the gate arrow: mirror triangle x-coordinates around the gate line midpoint
            for c in node.children:
                if c.kind == "triangle":
                    t = c.tokens
                    pts = [x for x in t if isinstance(x, tuple)]
                    xs = [p[0] for p in pts]
                    mid = (min(xs) + max(xs)) / 2
                    newpts = [(int(2 * mid - p[0]), p[1]) for p in pts]
                    rest = c.header.split(") ")[-1]
                    c.header = "triangle " + " ".join(pt(p) for p in newpts) + " " + rest
            self.entries["PJF"] = _entry(node, "synthesized")
        if "PMOS" not in e and "NMOS" in e:
            txt = e["NMOS"].text.replace("type: MN", "type: MP").replace("N-Channel", "P-Channel").replace("symbol NMOS", "symbol PMOS")
            self.entries["PMOS"] = _entry(qsch.parse_text(txt), "synthesized")

    # ------------------------------------------------------------ queries --
    def resolve(self, name: str) -> SymEntry:
        self.load()
        if name in self.entries:
            return self.entries[name]
        low = name.lower()
        if low in ALIASES and ALIASES[low] in self.entries:
            return self.entries[ALIASES[low]]
        for k, v in self.entries.items():
            if k.lower() == low:
                return v
        if name.upper() in TYPE_DEFAULT and TYPE_DEFAULT[name.upper()] in self.entries:
            return self.entries[TYPE_DEFAULT[name.upper()]]
        # file path to a .qsym
        p = Path(name)
        if p.suffix.lower() == ".qsym" and p.exists():
            return self.add_qsym(p)
        # a child schematic -> hierarchical block symbol
        if p.suffix.lower() == ".qsch" and p.exists():
            from .symbol_builder import block_for_child
            node = block_for_child(p)
            e = _entry(node, str(p))
            e.name = p.stem
            e.description = f"Hierarchical block (child schematic {p.name})"
            return e
        import difflib
        close = difflib.get_close_matches(name, list(self.entries), n=5, cutoff=0.6)
        for a in difflib.get_close_matches(low, list(ALIASES), n=3, cutoff=0.6):
            tgt = ALIASES[a]
            if tgt in self.entries and tgt not in close:
                close.insert(0, f"{tgt} ({a})")
        close += [k for k in self.entries if low in k.lower() and k not in close][:5]
        hint = f" Did you mean: {', '.join(close[:6])}?" if close else ""
        raise KeyError(f"unknown symbol '{name}'.{hint} Use list_symbols to browse the {len(self.entries)} "
                       f"available symbols, or create_symbol / symbol_from_subckt to make a new one.")

    def add_qsym(self, path) -> SymEntry:
        root = qsch.read_file(path)
        e = _entry(root, str(path))
        if not e.name:
            e.name = Path(path).stem
        self.entries[e.name] = e
        return e

    def search(self, query: str = "", type_filter: str | None = None, limit: int = 60) -> list[dict]:
        self.load()
        out = []
        ql = query.lower()
        for e in self.entries.values():
            if type_filter and e.type != type_filter:
                continue
            hay = f"{e.name} {e.description} {e.type}".lower()
            if ql and not all(w in hay for w in ql.split()):
                continue
            out.append({"name": e.name, "type": e.type, "description": e.description,
                        "pins": [p["name"] for p in e.pins], "source": Path(e.source).name if e.source else ""})
        out.sort(key=lambda d: (0 if d["name"] in TYPE_DEFAULT.values() else 1, d["name"]))
        return out[:limit]


# ------------------------------------------------ generated block symbol ---

def make_block_symbol(name: str, pins: list[str], library: str | None = None,
                      left: list[str] | None = None, right: list[str] | None = None,
                      top: list[str] | None = None, bottom: list[str] | None = None,
                      description: str | None = None, type_: str = "X") -> Node:
    """Create a neat rectangular subcircuit symbol.  Pin ORDER in the file is the
    SPICE node order (`pins`); side assignment only changes where pins are drawn."""
    if not any([left, right, top, bottom]):
        left, right, top, bottom = _auto_sides(pins)
    left, right, top, bottom = left or [], right or [], top or [], bottom or []
    nside = max(len(left), len(right), 1)
    ntb = max(len(top), len(bottom), 0)
    longest = max([len(p) for p in left + right] + [len(name) // 2, 3])
    half_w = max(400, ((longest * 60 + 250) // 100) * 100, ((ntb + 1) * 200) // 2 // 100 * 100)
    half_h = max(300, ((nside + 1) * 200) // 2 // 100 * 100)
    pos = {}
    def spread(n, span):
        if n == 0:
            return []
        step = 200
        start = ((n - 1) * step) // 2
        return [start - i * step for i in range(n)]
    for p, y in zip(left, spread(len(left), half_h)):
        pos[p] = ((-half_w - 200), y, "L")
    for p, y in zip(right, spread(len(right), half_h)):
        pos[p] = ((half_w + 200), y, "R")
    for p, x in zip(top, spread(len(top), half_w)):
        pos[p] = (-x, half_h + 200, "T")
    for p, x in zip(bottom, spread(len(bottom), half_w)):
        pos[p] = (-x, -half_h - 200, "B")
    ch = [Node(f"type: {type_}"), Node(f"description: {description or 'Subcircuit ' + name}")]
    if library:
        ch.append(Node(f"library file: {library}"))
    ch.append(Node("shorted pins: false"))
    ch.append(Node(f"rect ({-half_w},{half_h}) ({half_w},{-half_h}) 0 0 0 0x1000000 0x1000000 -1 0 -1"))
    for p in pins:
        x, y, side = pos[p]
        if side == "L":
            ch.append(Node(f"line ({x},{y}) ({-half_w},{y}) 0 0 0x1000000 -1 -1"))
        elif side == "R":
            ch.append(Node(f"line ({half_w},{y}) ({x},{y}) 0 0 0x1000000 -1 -1"))
        elif side == "T":
            ch.append(Node(f"line ({x},{y}) ({x},{half_h}) 0 0 0x1000000 -1 -1"))
        else:
            ch.append(Node(f"line ({x},{-half_h}) ({x},{y}) 0 0 0x1000000 -1 -1"))
    ch.append(Node(f'text (0,{half_h + 50}) 1 14 0 0x1000000 -1 -1 "X1"') if not top else Node(f'text ({-half_w},{half_h + 50}) 1 6 0 0x1000000 -1 -1 "X1"'))
    ch.append(Node(f'text (0,{-half_h - 50}) 1 13 0 0x1000000 -1 -1 {q(name)}') if not bottom else Node(f'text ({-half_w},{-half_h - 50}) 1 5 0 0x1000000 -1 -1 {q(name)}'))
    for p in pins:
        x, y, side = pos[p]
        # pin label drawn inside the body
        if side == "L":
            ch.append(Node(f"pin ({x},{y}) ({200 + 40},0) 1 7 0 0x1000000 -1 {q(p)}"))
        elif side == "R":
            ch.append(Node(f"pin ({x},{y}) ({-200 - 40},0) 1 11 0 0x1000000 -1 {q(p)}"))
        elif side == "T":
            ch.append(Node(f"pin ({x},{y}) (0,{-200 - 40}) 1 13 0 0x1000000 -1 {q(p)}"))
        else:
            ch.append(Node(f"pin ({x},{y}) (0,{200 + 40}) 1 14 0 0x1000000 -1 {q(p)}"))
    return Node(f"symbol {name}", ch, leaf=False)


_IN = re.compile(r"^(in|inp|inn|in\+|in-|vin|en|enable|clk|d|set|reset|rst|ref|fb|sense|ctrl|ss|rt|ct|iset|ilim|uvlo|pg|sync|mode|a\d*|b\d*|x\d*)$", re.I)
_OUT = re.compile(r"^(out|vout|q|qb|q_|sw|lx|drv|gate|hg|lg|hsg|lsg|ho|lo|o\d*|y\d*|pgood)$", re.I)
_PWR = re.compile(r"^(vcc|vdd|v\+|vp|vin|vs\+|vbat|boot|bst|vpp|avdd|dvdd|pvin|v12|v5|v3v3)$", re.I)
_GND = re.compile(r"^(gnd|vss|v-|vee|agnd|pgnd|dgnd|vs-|vn|com|0|ep)$", re.I)


def _auto_sides(pins):
    left, right, top, bottom = [], [], [], []
    for p in pins:
        if _GND.match(p):
            bottom.append(p)
        elif _PWR.match(p) and p.lower() != "vin":
            top.append(p)
        elif _OUT.match(p):
            right.append(p)
        else:
            left.append(p)
    # balance: don't let one side be crowded when the other is empty
    while len(left) > len(right) + 2:
        right.append(left.pop())
    while len(right) > len(left) + 2:
        left.append(right.pop(0))
    if len(top) > 4:
        left = top[4:] + left
        top = top[:4]
    if len(bottom) > 4:
        left = left + bottom[4:]
        bottom = bottom[:4]
    return left, right, top, bottom


_LIB: SymbolLibrary | None = None


def library() -> SymbolLibrary:
    global _LIB
    if _LIB is None:
        _LIB = SymbolLibrary().load()
    return _LIB
