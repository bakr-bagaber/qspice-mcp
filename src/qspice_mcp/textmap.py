"""Text views of the canvas for LLMs: an ASCII map with coordinate rulers and
a spatial summary (bounding boxes, neighbours, free space per direction)."""
from __future__ import annotations

import math

from .scene import Scene, on_segment_interior

LEGEND = ("Legend: # part body   o pin   - | wire   + bend/tee (connected)   * junction dot   "
          "x crossing (NOT connected)   G ground   [name] net label   REF text = reference designator.  "
          "Rows go DOWN the page = decreasing y.  Each character = {cell} units.")


def ascii_map(sc: Scene, cell: int = 100, region=None, max_cols: int = 160, show_values: bool = False) -> str:
    if region:
        x0, y0, x1, y1 = region
    else:
        x0, y0, x1, y1 = sc.bbox
        x0 -= cell; y0 -= cell; x1 += cell; y1 += cell
    while (x1 - x0) / cell + 1 > max_cols:
        cell += 100
    gx0 = math.floor(x0 / cell) * cell
    gy1 = math.ceil(y1 / cell) * cell
    cols = int(math.ceil((x1 - gx0) / cell)) + 1
    rows = int(math.ceil((gy1 - y0) / cell)) + 1
    grid = [[" "] * cols for _ in range(rows)]
    hor = [[0] * cols for _ in range(rows)]
    ver = [[0] * cols for _ in range(rows)]
    endp = [[False] * cols for _ in range(rows)]

    def rc(x, y):
        return int(round((gy1 - y) / cell)), int(round((x - gx0) / cell))

    def inside(r, c):
        return 0 <= r < rows and 0 <= c < cols

    for p in sc.parts:
        r0, c0 = rc(p.body[0], p.body[3])
        r1, c1 = rc(p.body[2], p.body[1])
        for r in range(min(r0, r1), max(r0, r1) + 1):
            for c in range(min(c0, c1), max(c0, c1) + 1):
                if inside(r, c):
                    grid[r][c] = "#"
    for a, b, n in sc.wires:
        ra, ca = rc(*a); rb, cb = rc(*b)
        if inside(ra, ca):
            endp[ra][ca] = True
        if inside(rb, cb):
            endp[rb][cb] = True
        if ra == rb:
            for c in range(min(ca, cb), max(ca, cb) + 1):
                if inside(ra, c):
                    hor[ra][c] += 1
        elif ca == cb:
            for r in range(min(ra, rb), max(ra, rb) + 1):
                if inside(r, ca):
                    ver[r][ca] += 1
    for r in range(rows):
        for c in range(cols):
            h, v = hor[r][c], ver[r][c]
            if not h and not v:
                continue
            if h and v:
                grid[r][c] = "+" if endp[r][c] else "x"
            elif h:
                grid[r][c] = "+" if (endp[r][c] and v) else "-"
            else:
                grid[r][c] = "|"
    for a, b, n in sc.wires:   # bends: endpoints shared by an H and a V wire
        for e in (a, b):
            r, c = rc(*e)
            if inside(r, c) and hor[r][c] and ver[r][c]:
                grid[r][c] = "+"
    for j in sc.junctions:
        r, c = rc(*j)
        if inside(r, c):
            grid[r][c] = "*"
    for q in sc.all_pins():
        if q.optional and not q.net:
            continue
        r, c = rc(*q.pos)
        if inside(r, c):
            grid[r][c] = "o"
    used = set()

    def put(r, c, s, force=False):
        for k, ch in enumerate(s):
            cc = c + k
            if not inside(r, cc):
                return
            if not force and grid[r][cc] not in (" ",):
                return
        for k, ch in enumerate(s):
            grid[r][c + k] = ch

    for lb in sc.labels:
        r, c = rc(*lb.pos)
        if lb.is_ground:
            d = {1: (1, 0), 2: (-1, 0)}.get(lb.just & 3)
            if d is None:
                d = (0, -1) if ((lb.just >> 2) & 3) == 2 else (0, 1)
            rr, cc = r + d[0], c + d[1]
            if inside(rr, cc):
                grid[rr][cc] = "G"
        else:
            s = f"[{lb.name}]"
            for (dr, dc) in ((-1, 0), (0, 1), (1, 0), (0, -len(s))):
                if _free(grid, r + dr, c + dc, len(s), inside):
                    put(r + dr, c + dc, s)
                    break
    for p in sc.parts:
        ref = p.ref
        tx = [t for t in p.texts if t.role == "refdes"]
        if tx:
            r, c = rc(tx[0].box[0], tx[0].box[3])
        else:
            r, c = rc(p.body[2] + cell, p.body[3])
        placed = False
        for dr in (0, 1, -1, 2):
            if _free(grid, r + dr, c, len(ref), inside):
                put(r + dr, c, ref); placed = True; break
        if not placed:
            r2, c2 = rc(p.body[2], p.body[3])
            put(r2, c2 + 1, ref, force=False)
        if show_values:
            vx = [t for t in p.texts if t.role == "value"]
            if vx:
                r, c = rc(vx[0].box[0], vx[0].box[3])
                put(r, c, vx[0].text[:20])
    # rulers
    lines = []
    ruler = [" "] * cols
    step = 5 if cell >= 200 else 10
    for c in range(0, cols, step):
        s = str(gx0 + c * cell)
        for k, ch in enumerate(s):
            if c + k < cols:
                ruler[c + k] = ch
    lw = max(len(str(gy1)), len(str(gy1 - (rows - 1) * cell))) + 1
    lines.append(" " * lw + "x=" + "".join(ruler))
    for r in range(rows):
        y = gy1 - r * cell
        lab = str(y) if (y % (cell * 5) == 0) else ""
        lines.append(lab.rjust(lw) + " |" + "".join(grid[r]).rstrip())
    lines.append("")
    lines.append(LEGEND.format(cell=cell))
    return "\n".join(lines)


def _free(grid, r, c, n, inside):
    for k in range(n):
        if not inside(r, c + k) or grid[r][c + k] != " ":
            return False
    return True


def spatial_summary(sc: Scene) -> list[dict]:
    """Per part: box, centre, pins, and nearest neighbour / free space on each side."""
    out = []
    obstacles = [(p.ref, p.body) for p in sc.parts]
    for p in sc.parts:
        b = p.body
        d = {"ref": p.ref, "symbol": p.symbol, "value": p.value, "x": p.pos[0], "y": p.pos[1],
             "rotation": p.rotation, "mirror": p.mirror,
             "body": [int(round(v)) for v in b],
             "pins": {q.name: [q.pos[0], q.pos[1], q.dir, q.net] for q in p.pins if not (q.optional and not q.net)}}
        free = {}
        for side in ("left", "right", "up", "down"):
            best = None
            for ref, ob in obstacles:
                if ref == p.ref:
                    continue
                if side in ("left", "right"):
                    if ob[3] < b[1] - 100 or ob[1] > b[3] + 100:
                        continue
                    gap = (b[0] - ob[2]) if side == "left" else (ob[0] - b[2])
                else:
                    if ob[2] < b[0] - 100 or ob[0] > b[2] + 100:
                        continue
                    gap = (ob[1] - b[3]) if side == "up" else (b[1] - ob[3])
                if gap < -1:
                    continue
                if best is None or gap < best[1]:
                    best = (ref, gap)
            free[side] = {"nearest": best[0], "gap": int(round(best[1]))} if best else {"nearest": None, "gap": None}
        d["free_space"] = free
        out.append(d)
    return out
