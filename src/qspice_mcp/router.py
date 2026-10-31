"""Orthogonal maze router on QSPICE's 100-unit drafting grid.

Guarantees (by construction) that different nets never touch:
  * no two nets share a grid edge,
  * a grid point is either owned by a single net, or is a *clean crossing*
    where one net passes straight horizontally and another straight vertically
    (QSPICE does not connect crossing wires),
  * wires never pass through foreign pins or component keep-out areas.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

G = 100
DIRS = {"R": (1, 0), "L": (-1, 0), "U": (0, 1), "D": (0, -1)}
OPP = {"R": "L", "L": "R", "U": "D", "D": "U"}
AXIS = {"R": "H", "L": "H", "U": "V", "D": "V"}


def step(p, d, n=1):
    dx, dy = DIRS[d]
    return (p[0] + dx * G * n, p[1] + dy * G * n)


@dataclass
class Terminal:
    key: str            # e.g. "R1.1"
    pos: tuple
    dir: str | None     # outward direction from the component body


@dataclass
class NetRoute:
    net: str
    points: set = field(default_factory=set)
    edges: set = field(default_factory=set)
    failed: list = field(default_factory=list)


class Router:
    def __init__(self, bounds, blocked: set, pins: dict, soft: dict | None = None,
                 bend_cost=3.0, cross_cost=6.0, soft_cost=1.5):
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.blocked = set(blocked)        # points no wire may use
        self.pins = dict(pins)             # point -> net
        self.soft = soft or {}             # point -> extra cost
        self.owner: dict = {}              # point -> net   (non-crossing use)
        self.through: dict = {}            # point -> (net, axis) straight pass-through
        self.cross: set = set()            # points that already host a crossing
        self.edges: dict = {}              # frozenset(p,q) -> net
        self.bend_cost, self.cross_cost, self.soft_cost = bend_cost, cross_cost, soft_cost
        self.routes: dict[str, NetRoute] = {}
        # pins at the far end of a two-terminal part's lead: a wire may leave/enter them
        # sideways at little cost (a straight bus across the tops of shunt parts instead
        # of an up-over-down bump)
        self.easy: set = set()
        # pins own their point
        for p, n in self.pins.items():
            self.owner[p] = n

    # ------------------------------------------------------------- helpers --
    def inb(self, p):
        return self.x0 <= p[0] <= self.x1 and self.y0 <= p[1] <= self.y1

    def can_enter(self, net, p, axis_in, will_turn_or_end):
        """May `net` occupy point p, arriving along axis_in?"""
        if not self.inb(p) or p in self.blocked:
            return False, 0
        pn = self.pins.get(p)
        if pn is not None and pn != net:
            return False, 0
        o = self.owner.get(p)
        if o is not None and o != net:
            return False, 0
        t = self.through.get(p)
        if t is not None and t[0] != net:
            # clean crossing only: perpendicular, straight, no existing crossing
            if will_turn_or_end or t[1] == axis_in or p in self.cross:
                return False, 0
            return True, self.cross_cost
        return True, 0

    def edge_free(self, net, p, q):
        e = frozenset((p, q))
        n = self.edges.get(e)
        return n is None or n == net

    # ---------------------------------------------------------------- A* ---
    def _search(self, net, start: Terminal, targets: dict, max_expand=150000):
        """targets: point -> (kind, extra) where kind is 'pin' with an approach
        direction or 'tree'.  Returns list of points from start to a target."""
        tx = [p for p in targets]
        def h(p):
            return min(abs(p[0] - t[0]) + abs(p[1] - t[1]) for t in tx) / G
        # state: (point, direction of travel into point)
        sdir = start.dir
        openq = []
        cnt = 0
        g0 = {}
        prev = {}
        s_state = (start.pos, sdir)
        g0[s_state] = 0.0
        heapq.heappush(openq, (h(start.pos), 0.0, cnt, s_state))
        expanded = 0
        while openq:
            f, g, _, st = heapq.heappop(openq)
            p, d = st
            if g > g0.get(st, 1e18) + 1e-9:
                continue
            expanded += 1
            if expanded > max_expand:
                break
            if p in targets and p != start.pos:
                path = [p]
                cur = st
                while cur in prev:
                    cur = prev[cur]
                    path.append(cur[0])
                return path[::-1], g
            for nd, (dx, dy) in DIRS.items():
                if d is not None and nd == OPP[d] and p != start.pos:
                    continue
                if p == start.pos and start.dir is not None and nd == OPP[start.dir]:
                    continue  # never leave a pin back into its own body
                q = (p[0] + dx * G, p[1] + dy * G)
                if not self.edge_free(net, p, q):
                    continue
                is_target = q in targets
                # we don't know if we will turn at q yet: be conservative and
                # allow crossings only when q is not a target; turning at a
                # crossing is filtered when expanding from q.
                ok, extra = self.can_enter(net, q, AXIS[nd], is_target)
                if not ok:
                    continue
                if q in self.through and self.through[q][0] != net:
                    # arriving at a crossing point: must continue straight
                    pass
                cost = 1.0 + extra + self.soft.get(q, 0) * self.soft_cost
                if d is not None and nd != d:
                    cost += self.bend_cost
                    # cannot turn at a point that is a foreign crossing
                    if p in self.through and self.through[p][0] != net:
                        continue
                if p == start.pos and start.dir is not None and nd != start.dir:
                    cost += self.bend_cost * (0.4 if p in self.easy else 1.5)
                if is_target:
                    kind, appr = targets[q]
                    if kind == "pin" and appr is not None and nd != OPP[appr]:
                        cost += self.bend_cost * (0.4 if q in self.easy else 1.5)
                    if kind == "deg3":
                        cost += 4.0
                ng = g + cost
                ns = (q, nd)
                if ng < g0.get(ns, 1e18):
                    g0[ns] = ng
                    prev[ns] = st
                    cnt += 1
                    heapq.heappush(openq, (ng + h(q), ng, cnt, ns))
        return None, None

    # ---------------------------------------------------------- commit ----
    def _commit(self, net, path):
        r = self.routes.setdefault(net, NetRoute(net))
        for i, p in enumerate(path):
            r.points.add(p)
        for a, b in zip(path, path[1:]):
            self.edges[frozenset((a, b))] = net
            r.edges.add(frozenset((a, b)))
        # classify points: straight pass-through vs owned
        for i, p in enumerate(path):
            if i == 0 or i == len(path) - 1:
                self._own(net, p)
                continue
            a, b = path[i - 1], path[i + 1]
            if a[0] == p[0] == b[0]:
                ax = "V"
            elif a[1] == p[1] == b[1]:
                ax = "H"
            else:
                ax = None
            if ax is None:
                self._own(net, p)
            else:
                t = self.through.get(p)
                if t is not None and t[0] != net:
                    self.cross.add(p)
                elif self.owner.get(p) not in (None, net):
                    pass
                else:
                    if p in self.owner:
                        continue
                    self.through[p] = (net, ax)

    def _own(self, net, p):
        t = self.through.get(p)
        if t is not None and t[0] == net:
            del self.through[p]
        self.owner[p] = net

    # ----------------------------------------------------------- public ---
    def route_net(self, net: str, terms: list[Terminal]) -> NetRoute:
        r = self.routes.setdefault(net, NetRoute(net))
        if len(terms) < 2:
            for t in terms:
                r.points.add(t.pos)
            return r
        # Prim-like growth from the terminal nearest to the centroid
        cx = sum(t.pos[0] for t in terms) / len(terms)
        cy = sum(t.pos[1] for t in terms) / len(terms)
        rest = sorted(terms, key=lambda t: abs(t.pos[0] - cx) + abs(t.pos[1] - cy))
        tree_pts = {rest[0].pos}
        pin_dir = {t.pos: t.dir for t in terms}
        r.points.add(rest[0].pos)
        remaining = rest[1:]
        while remaining:
            remaining.sort(key=lambda t: min(abs(t.pos[0] - p[0]) + abs(t.pos[1] - p[1]) for p in tree_pts))
            t = remaining.pop(0)
            if t.pos in tree_pts:
                continue
            targets = {}
            for p in tree_pts:
                if p in self.cross:
                    continue  # joining here would short the crossing net
                if p in pin_dir:
                    targets[p] = ("pin", pin_dir[p])
                else:
                    deg = sum(1 for e in r.edges if p in e)
                    targets[p] = ("deg3" if deg >= 3 else "tree", None)
            path, cost = self._search(net, t, targets)
            if path is None:
                r.failed.append(t.key)
                continue
            self._commit(net, path)
            tree_pts.update(path)
        return r

    def segments(self, net: str, pin_points: set) -> tuple[list, list]:
        """Convert a routed net into maximal straight wire segments, split at
        corners, pins, and junctions.  Returns (segments, junction_points)."""
        r = self.routes.get(net)
        if not r:
            return [], []
        adj: dict = {}
        for e in r.edges:
            a, b = tuple(e)
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        def is_node(p):
            nb = adj.get(p, set())
            if p in pin_points or len(nb) != 2:
                return True
            a, b = list(nb)
            return not (a[0] == p[0] == b[0] or a[1] == p[1] == b[1])
        segs = []
        seen = set()
        for p in adj:
            if not is_node(p):
                continue
            for q in adj[p]:
                e = frozenset((p, q))
                if e in seen:
                    continue
                cur, nxt = p, q
                seen.add(e)
                guard = 0
                while not is_node(nxt) and nxt != p and guard < 100000:
                    nb = [x for x in adj[nxt] if x != cur]
                    if not nb:
                        break
                    cur, nxt = nxt, nb[0]
                    seen.add(frozenset((cur, nxt)))
                    guard += 1
                segs.append((p, nxt))
        junctions = []
        for p, nb in adj.items():
            deg = len(nb) + (1 if p in pin_points else 0)
            if deg >= 3:
                junctions.append(p)
        return segs, junctions
