"""Lossless reader/writer for QSPICE .qsch (schematic) and .qsym (symbol) files.

File format facts (reverse engineered from the QSPICE install):
  * Encoding is Latin-1 (single byte). Structural delimiters are the Latin-1
    guillemets  « (0xAB) and » (0xBB).  µ is 0xB5, ¥ is 0xA5, Ã is 0xC3 ...
  * Optional 4-byte magic header FF D8 FF DB precedes the root «schematic / «symbol.
  * A node is  «header»  (leaf, one line) or  «header\n children... »  (branch).
  * Coordinates are integers in a y-UP system; the drafting grid is 100 units
    (pins sit on 100-unit multiples; half-grid 50 is used for fine text placement).
  * component code  = 2*rot90 (+1 for an extra 45 deg) (+8 for mirror).
  * text "justify" field: low 4 bits = justification
        bits 3..2 horizontal  01=left-justified 10=right-justified 11=centred
        bits 1..0 vertical    01=top-justified  10=bottom-justified 11=centred
    bits 5..6 = text rotation in 90 deg CCW steps (inside a symbol this is used to
    keep labels upright when the component is rotated).
  * schematic-level «text»: third numeric field 0 = SPICE directive, 1 = comment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

MAGIC = b"\xff\xd8\xff\xdb"
LQ, RQ = "\u00ab", "\u00bb"  # « »

# ---------------------------------------------------------------- tree ------

@dataclass
class Node:
    header: str
    children: list["Node"] = field(default_factory=list)
    leaf: bool = True

    # -- header helpers
    @property
    def kind(self) -> str:
        h = self.header
        # keys like "type:" / "library file:" / "shorted pins:"
        m = re.match(r"([A-Za-z][A-Za-z0-9_ ]*?):", h)
        if m and "(" not in m.group(1) and '"' not in m.group(1):
            return m.group(1) + ":"
        return h.split(" ", 1)[0]

    @property
    def tokens(self) -> list:
        return tokenize(self.header)

    def find(self, kind: str) -> "Node | None":
        for c in self.children:
            if c.kind == kind:
                return c
        return None

    def findall(self, kind: str) -> list["Node"]:
        return [c for c in self.children if c.kind == kind]

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def copy(self) -> "Node":
        return Node(self.header, [c.copy() for c in self.children], self.leaf)


_TOK = re.compile(r'\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)|"((?:[^"\\]|\\.)*)"|(\S+)')


class Q(str):
    """A quoted string token."""


def tokenize(header: str) -> list:
    out: list = []
    for m in _TOK.finditer(header):
        if m.group(1) is not None:
            x, y = m.group(1), m.group(2)
            out.append((_num(x), _num(y)))
        elif m.group(3) is not None:
            out.append(Q(m.group(3)))
        else:
            out.append(m.group(4))
    return out


def _num(s: str):
    try:
        return int(s)
    except ValueError:
        return float(s)


def fmt_num(v) -> str:
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    return str(v)


def fmt_pt(p) -> str:
    return f"({fmt_num(round(p[0]) if float(p[0]).is_integer() else p[0])},{fmt_num(round(p[1]) if float(p[1]).is_integer() else p[1])})"


def qstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"' if False else '"' + s.replace("\n", "\\n") + '"'


def unq(s: str) -> str:
    """Unescape a quoted token's content (QSPICE uses \\n for newlines)."""
    return s.replace("\\n", "\n")


def parse_text(s: str) -> Node:
    i = 0
    n = len(s)
    stack: list[Node] = []
    root: Node | None = None
    while i < n:
        c = s[i]
        if c == LQ:
            # read header
            j = i + 1
            inq = False
            nest = 0
            while j < n:
                ch = s[j]
                if inq:
                    if ch == "\\" and j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    if ch == '"':
                        inq = False
                    elif ch == "\n":
                        break
                elif ch == '"':
                    inq = True
                elif ch == LQ:
                    nest += 1
                elif ch == RQ:
                    if nest == 0:
                        break
                    nest -= 1
                elif ch == "\n":
                    break
                j += 1
            header = s[i + 1:j]
            node = Node(header)
            if stack:
                stack[-1].children.append(node)
            elif root is None:
                root = node
            if j < n and s[j] == RQ:
                node.leaf = True
                i = j + 1
            else:
                node.leaf = False
                stack.append(node)
                i = j + 1
        elif c == RQ:
            if stack:
                stack.pop()
            i += 1
        else:
            i += 1
    if root is None:
        raise ValueError("not a QSPICE file: no « root node found")
    return root


def serialize(node: Node, depth: int = 0) -> str:
    ind = "  " * depth
    if node.leaf:
        return f"{ind}{LQ}{node.header}{RQ}\n"
    out = [f"{ind}{LQ}{node.header}\n"]
    for c in node.children:
        out.append(serialize(c, depth + 1))
    out.append(f"{ind}{RQ}\n")
    return "".join(out)


def read_file(path: str | Path) -> Node:
    b = Path(path).read_bytes()
    if b.startswith(MAGIC):
        b = b[4:]
    return parse_text(b.decode("latin-1"))


def encode_text(txt: str) -> bytes:
    """Latin-1 for code points < 256 (structure bytes « » µ ...); anything
    beyond (Ω, ±...) is written as UTF-8, which QSPICE also understands."""
    try:
        return txt.encode("latin-1")
    except UnicodeEncodeError:
        out = bytearray()
        for ch in txt:
            o = ord(ch)
            out += bytes([o]) if o < 256 else ch.encode("utf-8")
        return bytes(out)


def display(s: str) -> str:
    """Human-readable form of a text read as Latin-1 that may carry UTF-8."""
    if not s:
        return s
    t = s
    if any(0xC2 <= ord(c) <= 0xF4 for c in s):
        try:
            t = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            t = s
    return t.replace("\ufeff", "")


def to_bytes(root: Node, magic: bool = True) -> bytes:
    txt = serialize(root, 0)
    data = encode_text(txt)
    return (MAGIC + data) if magic else data


def write_file(root: Node, path: str | Path, magic: bool = True) -> None:
    Path(path).write_bytes(to_bytes(root, magic))


# ------------------------------------------------------------ geometry -----

def code_parts(code: int) -> tuple[int, bool, bool]:
    """Return (rot90 steps CCW, mirrored, extra45)."""
    return (code >> 1) & 3, bool(code & 8), bool(code & 1)


def make_code(rot90: int, mirror: bool = False) -> int:
    return ((rot90 % 4) << 1) | (8 if mirror else 0)


def xform(pt, code: int, origin=(0, 0)):
    """Symbol-local point -> schematic point for a component with `code`.

    CCW rotation is applied first, then mirror (x -> -x), then translation
    (verified empirically against QUX's netlister for all 8 codes).
    """
    x, y = pt
    rot, mir, e45 = code_parts(code)
    for _ in range(rot):
        x, y = -y, x
    if e45:
        # QUX keeps 45-degree parts on the grid: (x - y, x + y), i.e. scaled by sqrt(2)
        # (verified against QUX's netlist of Examples/1N4148.qsch)
        x, y = x - y, x + y
    if mir:
        x = -x
    return (x + origin[0], y + origin[1])


def inv_xform(pt, code: int, origin=(0, 0)):
    x, y = pt[0] - origin[0], pt[1] - origin[1]
    rot, mir, e45 = code_parts(code)
    if mir:
        x = -x
    if e45:
        x, y = (x + y) / 2, (y - x) / 2
    for _ in range(rot):
        x, y = y, -x
    return (x, y)


# justification helpers -----------------------------------------------------
H_LEFT, H_RIGHT, H_CENTER = 1, 2, 3   # text extends right / left / both ways
V_TOP, V_BOTTOM, V_CENTER = 1, 2, 3   # text hangs below / sits above / centred


def make_just(h: int, v: int, rot90: int = 0) -> int:
    return ((rot90 & 3) << 5) | (h << 2) | v


def split_just(j: int) -> tuple[int, int, int]:
    return (j >> 2) & 3, j & 3, (j >> 5) & 3


def _rot_hv(h: int, v: int, steps: int) -> tuple[int, int]:
    """Rotate a justification (as anchor-direction vector) by steps*90 CCW."""
    # represent as a direction the text extends from the anchor
    dx = {H_LEFT: 1, H_RIGHT: -1, H_CENTER: 0}[h]
    dy = {V_TOP: -1, V_BOTTOM: 1, V_CENTER: 0}[v]
    for _ in range(steps % 4):
        dx, dy = -dy, dx
    hh = {1: H_LEFT, -1: H_RIGHT, 0: H_CENTER}[dx]
    vv = {-1: V_TOP, 1: V_BOTTOM, 0: V_CENTER}[dy]
    return hh, vv


def screen_to_local_just(h: int, v: int, code: int) -> int:
    """Justification code to store inside a symbol so that the text renders
    upright on screen with screen-justification (h, v) for a component `code`."""
    rot, mir, _ = code_parts(code)
    # text rotation field compensates component rotation (mirroring negates the
    # total angle, so the same compensation keeps text upright either way)
    trot = (-rot) % 4
    if mir:
        # mirroring flips horizontal justification of (upright) text
        h = {H_LEFT: H_RIGHT, H_RIGHT: H_LEFT, H_CENTER: H_CENTER}[h]
    return make_just(h, v, trot)


_FLIP = {1: 2, 2: 1, 3: 3, 0: 0}


def screen_just(local_just: int, code: int) -> tuple[int, int, int]:
    """(h, v, screen_rot90) of a symbol text as QUX renders it."""
    h, v, trot = split_just(local_just)
    rot, mir, _ = code_parts(code)
    th = (trot + rot) % 4
    if mir:
        if th % 2 == 0:
            h = _FLIP[h]
        else:
            v = _FLIP[v]
        return h, v, (-th) % 4
    return h, v, th
