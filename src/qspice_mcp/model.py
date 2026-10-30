"""High level, editable view over a QSPICE schematic tree."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import qsch
from .qsch import Node, Q, fmt_num, xform

GRID = 100


def pt(p) -> str:
    return f"({fmt_num(int(round(p[0])))},{fmt_num(int(round(p[1])))})"


def q(s: str) -> str:
    return '"' + s.replace("\n", "\\n") + '"'


# ------------------------------------------------------------- symbol -------

@dataclass
class PinInfo:
    name: str
    local: tuple
    index: int
    default_net: str | None = None


class Symbol:
    """Wrapper over a «symbol NAME» node (embedded in a component or a .qsym)."""

    def __init__(self, node: Node):
        self.node = node

    @property
    def name(self) -> str:
        return self.node.header.split(" ", 1)[1].strip() if " " in self.node.header else ""

    def _kv(self, key: str) -> str | None:
        n = self.node.find(key + ":")
        if n is None:
            return None
        return n.header.split(":", 1)[1].strip()

    @property
    def type(self) -> str:
        return self._kv("type") or ""

    @property
    def description(self) -> str:
        return self._kv("description") or ""

    @property
    def library(self) -> str | None:
        return self._kv("library file")

    def pins(self) -> list[PinInfo]:
        out = []
        for i, p in enumerate(self.node.findall("pin")):
            t = p.tokens
            strs = [x for x in t if isinstance(x, Q)]
            out.append(PinInfo(qsch.unq(strs[0]) if strs else str(i + 1), t[1], i,
                               qsch.unq(strs[1]) if len(strs) > 1 else None))
        return out

    def texts(self) -> list[Node]:
        return self.node.findall("text")

    def bbox(self, include_text: bool = False):
        xs, ys = [], []
        for c in self.node.children:
            if c.kind in ("line", "rect", "ellipse", "triangle", "zigzag", "coil", "arc", "arc3p", "pin") or (include_text and c.kind == "text"):
                for t in c.tokens:
                    if isinstance(t, tuple):
                        xs.append(t[0]); ys.append(t[1])
                        if c.kind == "pin":
                            break
        if not xs:
            return (-100, -100, 100, 100)
        return (min(xs), min(ys), max(xs), max(ys))


def text_string(n: Node) -> str:
    strs = [x for x in n.tokens if isinstance(x, Q)]
    return qsch.unq(strs[-1]) if strs else ""


def set_text_string(n: Node, s: str) -> None:
    h = n.header
    i = h.index('"')
    n.header = h[:i] + q(s)


def text_fields(n: Node):
    """«text (x,y) size just flag color a b "str"» -> dict."""
    t = n.tokens
    return {"pos": t[1], "size": float(t[2]), "just": int(t[3]), "flag": int(t[4]),
            "color": t[5], "str": text_string(n)}


def make_text_node(pos, s: str, size=1, just=7, flag=0, color="0x1000000") -> Node:
    return Node(f"text {pt(pos)} {fmt_num(size)} {just} {flag} {color} -1 -1 {q(s)}")


# ---------------------------------------------------------- component ------

class Component:
    def __init__(self, node: Node):
        self.node = node

    # header: component (x,y) code [mirror?]
    @property
    def _tok(self):
        return self.node.tokens

    @property
    def pos(self) -> tuple:
        return self._tok[1]

    @pos.setter
    def pos(self, p):
        t = self._tok
        rest = " ".join(str(x) for x in t[2:])
        self.node.header = f"component {pt(p)} {rest}".rstrip()

    @property
    def code(self) -> int:
        t = self._tok
        return int(t[2]) if len(t) > 2 else 0

    @code.setter
    def code(self, c: int):
        t = self._tok
        extra = " ".join(str(x) for x in t[3:]) or "0"
        self.node.header = f"component {pt(self.pos)} {int(c)} {extra}"

    @property
    def symbol(self) -> Symbol:
        return Symbol(self.node.find("symbol"))

    @property
    def texts(self) -> list[Node]:
        return self.symbol.texts()

    @property
    def refdes(self) -> str:
        tx = self.texts
        return text_string(tx[0]) if tx else ""

    @refdes.setter
    def refdes(self, v: str):
        set_text_string(self.texts[0], v)

    @property
    def value(self) -> str:
        tx = self.texts
        return text_string(tx[1]) if len(tx) > 1 else ""

    @value.setter
    def value(self, v: str):
        tx = self.texts
        if len(tx) > 1:
            set_text_string(tx[1], v)
        else:
            self.symbol.node.children.append(make_text_node((100, -150), v))

    @property
    def attributes(self) -> list[str]:
        return [text_string(t) for t in self.texts[1:]]

    def pin_positions(self) -> list[tuple[PinInfo, tuple]]:
        out = []
        for p in self.symbol.pins():
            w = xform(p.local, self.code, self.pos)
            out.append((p, (int(round(w[0])), int(round(w[1])))))
        return out

    def bbox(self, include_text=False):
        x0, y0, x1, y1 = self.symbol.bbox(include_text)
        pts = [xform(p, self.code, self.pos) for p in [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


# ----------------------------------------------------------- schematic -----

class Schematic:
    def __init__(self, root: Node | None = None, path: str | Path | None = None):
        if root is None:
            root = Node("schematic", [], leaf=False)
        self.root = root
        self.path = Path(path) if path else None

    @classmethod
    def load(cls, path) -> "Schematic":
        return cls(qsch.read_file(path), path)

    def save(self, path=None) -> Path:
        p = Path(path or self.path)
        qsch.write_file(self.root, p)
        self.path = p
        return p

    # --- accessors
    def components(self) -> list[Component]:
        return [Component(n) for n in self.root.findall("component")]

    def component(self, ref: str) -> Component | None:
        for c in self.components():
            if c.refdes.lower() == ref.lower():
                return c
        return None

    def wires(self) -> list[tuple[tuple, tuple, str]]:
        out = []
        for n in self.root.findall("wire"):
            t = n.tokens
            out.append((t[1], t[2], qsch.unq(t[3]) if len(t) > 3 else ""))
        return out

    def nets(self) -> list[tuple[tuple, str, int, int]]:
        out = []
        for n in self.root.findall("net"):
            t = n.tokens
            out.append((t[1], text_string(n), int(t[3]), int(t[4])))
        return out

    def junctions(self) -> list[tuple]:
        return [n.tokens[1] for n in self.root.findall("junction")]

    def texts(self) -> list[dict]:
        return [text_fields(n) for n in self.root.findall("text")]

    def directives(self) -> list[str]:
        return [t["str"] for t in self.texts() if t["flag"] == 0]

    # --- builders
    def _insert(self, node: Node, kind_order=("component", "net", "junction", "wire", "text")):
        # keep QSPICE's customary ordering: components, nets, junctions, wires, texts
        rank = {k: i for i, k in enumerate(kind_order)}
        r = rank.get(node.kind, len(rank))
        ch = self.root.children
        idx = len(ch)
        for i, c in enumerate(ch):
            if rank.get(c.kind, len(rank)) > r:
                idx = i
                break
        ch.insert(idx, node)
        return node

    def add_component(self, symbol_node: Node, pos, code=0) -> Component:
        n = Node(f"component {pt(pos)} {int(code)} 0", [symbol_node.copy()], leaf=False)
        self._insert(n)
        return Component(n)

    def add_wire(self, a, b, net: str = "") -> Node:
        return self._insert(Node(f"wire {pt(a)} {pt(b)} {q(net)}"))

    def add_net_label(self, p, name: str, just: int = 13, port: bool = False, size=1) -> Node:
        return self._insert(Node(f"net {pt(p)} {fmt_num(size)} {just} {1 if port else 0} {q(name)}"))

    def add_junction(self, p) -> Node:
        return self._insert(Node(f"junction {pt(p)}"))

    def add_text(self, p, s: str, directive=True, size=1, just=7) -> Node:
        return self._insert(make_text_node(p, s, size=size, just=just, flag=0 if directive else 1))

    def remove(self, node: Node):
        self.root.children = [c for c in self.root.children if c is not node]

    def clear_wiring(self):
        self.root.children = [c for c in self.root.children if c.kind not in ("wire", "junction", "net")]
