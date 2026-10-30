"""Reference-designator / value text placement.

Drafting convention: vertical parts carry refdes above value, both to the
right; horizontal parts carry refdes above and value below the body.  For
every part we generate the conventional layouts plus fall-backs and keep the
one with the fewest collisions (wires, bodies, labels, other texts)."""
from __future__ import annotations

from .model import Component, Schematic, set_text_string, text_string
from .qsch import inv_xform, screen_to_local_just
from .scene import CHAR_W, LINE_H, Scene, boxes_overlap, build, seg_hits_box, text_box


def _kind(p) -> str:
    req = [q for q in p.pins if not q.optional]
    t = p.type or ""
    if len(req) == 2 and len(p.pins) <= 3:
        dirs = {q.dir for q in req}
        return "v2" if dirs <= {"U", "D"} else "h2" if dirs <= {"L", "R"} else "v2"
    if t[:1] in ("Q", "M", "J", "Z") and len(req) <= 4:
        return "tr"
    return "block"


def _movable_texts(p):
    return [t for t in p.texts]  # visible texts in symbol order (refdes, value, attrs)


def candidates(p) -> list[tuple[str, float, list]]:
    """[(name, preference_penalty, [(anchor, h, v) per text])]"""
    kind = _kind(p)
    texts = _movable_texts(p)
    n = len(texts)
    if n == 0 or kind == "block":
        return []
    b = p.body
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    sizes = [_size(t) for t in texts]
    out = []
    if kind in ("v2", "tr"):
        if n == 2:
            ys = [cy + 150, cy - 150] if kind == "v2" else [cy + 160, cy - 90]
        else:
            top = cy + (n - 1) * LINE_H / 2
            ys = [top - k * LINE_H for k in range(n)]
        control_right = any(q.dir == "R" and q.name.upper() in ("B", "G") for q in p.pins)
        first = "L" if control_right else "R"
        for side, pref in ((first, 0.0), ("L" if first == "R" else "R", 1.5)):
            for extra, epref in ((20, 0.0), (120, 0.8), (220, 1.6)):
                x = b[2] + extra if side == "R" else b[0] - extra
                h = 1 if side == "R" else 2
                out.append((f"{side}{extra}", pref + epref, [((x, ys[k]), h, 3) for k in range(n)]))
                # slide the text block up/down to dodge a label or wire
                if extra <= 120:
                    for dy, dpref in ((100, 1.1), (-100, 1.1), (200, 2.0), (-200, 2.0)):
                        out.append((f"{side}{extra}{dy:+d}", pref + epref + dpref,
                                    [((x, ys[k] + dy), h, 3) for k in range(n)]))
        # stacked above / below as last resorts
        out.append(("above", 3.0, [((cx, b[3] + 10 + (n - 1 - k) * LINE_H), 3, 2) for k in range(n)]))
        out.append(("below", 3.0, [((cx, b[1] - 10 - k * LINE_H), 3, 1) for k in range(n)]))
    else:  # h2
        lay = [((cx, b[3] + 10), 3, 2)] + [((cx, b[1] - 10 - k * LINE_H), 3, 1) for k in range(n - 1)]
        out.append(("ab", 0.0, lay))
        out.append(("ab_far", 0.8, [((cx, b[3] + 60), 3, 2)] + [((cx, b[1] - 60 - k * LINE_H), 3, 1) for k in range(n - 1)]))
        for dx, dpref in ((100, 1.2), (-100, 1.2), (200, 2.0), (-200, 2.0)):
            out.append((f"ab{dx:+d}", dpref, [((cx + dx, b[3] + 10), 3, 2)] +
                        [((cx + dx, b[1] - 10 - k * LINE_H), 3, 1) for k in range(n - 1)]))
        out.append(("above", 1.5, [((cx, b[3] + 10 + (n - 1 - k) * LINE_H), 3, 2) for k in range(n)]))
        out.append(("below", 1.5, [((cx, b[1] - 10 - k * LINE_H), 3, 1) for k in range(n)]))
        x = b[2] + 20
        out.append(("right", 2.5, [((x, cy + (n - 1) * LINE_H / 2 - k * LINE_H), 1, 3) for k in range(n)]))
    return out


def _size(t) -> float:
    try:
        return float(t.node.tokens[2])
    except Exception:
        return 1.0


def _boxes(p, layout):
    out = []
    for t, (anchor, h, v) in zip(_movable_texts(p), layout):
        out.append(text_box(anchor, t.text, _size(t), h, v, 0))
    return out


def _cost(boxes, p, sc: Scene, placed: list) -> float:
    c = 0.0
    for bx in boxes:
        for a, b, _ in sc.wires:
            if seg_hits_box(a, b, bx, shrink=12):
                c += 10
        for q in sc.parts:
            if boxes_overlap(_sh(bx, 10), _sh(q.body, 8)):
                c += 14
        for ob in placed:
            if boxes_overlap(_sh(bx, 8), _sh(ob, 8)):
                c += 9
        for q in sc.all_pins():
            if q.ref != p.ref and bx[0] + 5 < q.pos[0] < bx[2] - 5 and bx[1] + 5 < q.pos[1] < bx[3] - 5:
                c += 6
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes_overlap(_sh(boxes[i], 5), _sh(boxes[j], 5)):
                c += 9
    return c


def _sh(b, s):
    return (b[0] + s, b[1] + s, b[2] - s, b[3] - s)


def apply_layout(comp: Component, p, layout):
    for t, (anchor, h, v) in zip(_movable_texts(p), layout):
        node = t.node
        local = inv_xform(anchor, comp.code, comp.pos)
        just = screen_to_local_just(h, v, comp.code)
        s = text_string(node)
        rest = node.header.split(") ", 1)[1].split(" ", 2)
        size = rest[0]
        tail = rest[2] if len(rest) > 2 else '0 0x1000000 -1 -1 ""'
        node.header = f"text ({int(round(local[0]))},{int(round(local[1]))}) {size} {just} {tail}"
        set_text_string(node, s)


def optimize(sch: Schematic, refs: list[str] | None = None, passes: int = 2) -> dict:
    """Re-place texts of the given parts (default: every part with a conventional
    layout) to minimise collisions.  Returns {ref: chosen layout}."""
    want = {r.lower() for r in refs} if refs else None
    chosen = {}
    for _ in range(passes):
        sc = build(sch, with_keepout=False)
        targets = [p for p in sc.parts if (want is None or p.ref.lower() in want) and candidates(p)]
        if not targets:
            break
        fixed_boxes = [t.box for t in sc.texts if t.owner not in {p.ref for p in targets}]
        # most constrained first: the part whose current texts collide most
        def cur_cost(p):
            return _cost([t.box for t in p.texts], p, sc, fixed_boxes)
        targets.sort(key=cur_cost, reverse=True)
        placed = list(fixed_boxes)
        others = {p.ref: [t.box for t in p.texts] for p in targets}
        changed = False
        for p in targets:
            others.pop(p.ref, None)
            pool = placed + [b for bs in others.values() for b in bs]
            best = None
            for name, pref, lay in candidates(p):
                bx = _boxes(p, lay)
                c = _cost(bx, p, sc, pool) + pref
                if best is None or c < best[0]:
                    best = (c, name, lay, bx)
            if best is None:
                continue
            apply_layout(p.comp, p, best[2])
            chosen[p.ref] = {"layout": best[1], "collisions": round(best[0], 1)}
            placed += best[3]
            changed = True
        if not changed:
            break
    return chosen
