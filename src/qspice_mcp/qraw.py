"""Reader for QSPICE .qraw waveform files (binary and ASCII), incl. .step runs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Plot:
    title: str = ""
    date: str = ""
    plotname: str = ""
    flags: str = ""
    n_vars: int = 0
    n_points: int = 0
    variables: list = field(default_factory=list)   # [(name, kind)]
    params: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)
    suggestions: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    data: np.ndarray | None = None                 # shape (n_points, n_vars), complex or real
    step_bounds: list = field(default_factory=list)  # [(start, stop)]

    @property
    def complex(self) -> bool:
        return "complex" in self.flags

    @property
    def names(self) -> list[str]:
        return [v[0] for v in self.variables]

    def index(self, name: str) -> int:
        n = name.strip()
        low = [x.lower() for x in self.names]
        if n.lower() in low:
            return low.index(n.lower())
        # V(node) shorthand: bare node name
        if f"v({n.lower()})" in low:
            return low.index(f"v({n.lower()})")
        raise KeyError(name)

    @property
    def n_steps(self) -> int:
        return max(1, len(self.step_bounds))

    def step_info(self) -> list[dict]:
        out = []
        pidx = [i for i, v in enumerate(self.variables) if v[1] == "parameter"]
        for k, (a, b) in enumerate(self.step_bounds or [(0, self.n_points)]):
            d = {"step": k + 1, "points": b - a}
            for i in pidx:
                val = self.data[a, i]
                d[self.variables[i][0]] = float(np.real(val))
            out.append(d)
        return out

    def axis(self, step: int | None = None):
        a, b = self._bounds(step)
        return np.real(self.data[a:b, 0])

    def _bounds(self, step):
        if step is None or not self.step_bounds:
            return (0, self.n_points) if step is None else self.step_bounds[0] if self.step_bounds else (0, self.n_points)
        return self.step_bounds[step - 1]

    def trace(self, expr: str, step: int | None = 1):
        """Return samples for a variable name or simple V(a,b) / V(a)-V(b) expression."""
        a, b = self._bounds(step)
        m = re.fullmatch(r"\s*V\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)\s*", expr, re.I)
        if m:
            return self.trace(f"V({m.group(1)})", step) - self.trace(f"V({m.group(2)})", step)
        if expr.strip().lower() in ("v(0)", "v(gnd)"):
            return np.zeros(b - a)
        return self.data[a:b, self.index(expr)]


def _split_steps(x: np.ndarray) -> list[tuple[int, int]]:
    if len(x) < 2:
        return [(0, len(x))]
    xr = np.real(x)
    starts = [0] + [i for i in range(1, len(xr)) if xr[i] < xr[i - 1] or (xr[i] == xr[0] and xr[i] == xr[i - 1] and i > 1 and xr[i - 1] != xr[i - 2])]
    starts = sorted(set(starts))
    return [(s, (starts[k + 1] if k + 1 < len(starts) else len(xr))) for k, s in enumerate(starts)]


def read(path: str | Path) -> list[Plot]:
    raw = Path(path).read_bytes()
    pos = 0
    plots = []
    while pos < len(raw):
        p = Plot()
        mode = None
        # header
        while pos < len(raw):
            nl = raw.find(b"\n", pos)
            if nl < 0:
                nl = len(raw)
            line = raw[pos:nl].decode("latin-1").rstrip("\r")
            pos = nl + 1
            if line.startswith("Binary:"):
                mode = "binary"; break
            if line.startswith("Values:"):
                mode = "ascii"; break
            if line.startswith("Variables:"):
                for _ in range(p.n_vars):
                    nl = raw.find(b"\n", pos)
                    vl = raw[pos:nl].decode("latin-1").strip().split("\t")
                    pos = nl + 1
                    vl = [x for x in vl if x != ""]
                    if len(vl) >= 3:
                        p.variables.append((vl[1], vl[2]))
                    elif len(vl) == 2:
                        p.variables.append((vl[1], ""))
                continue
            if ":" in line and not line.startswith("."):
                k, v = line.split(":", 1)
                k = k.strip(); v = v.strip()
                if k == "Title": p.title = v
                elif k == "Date": p.date = v
                elif k == "Plotname": p.plotname = v
                elif k == "Flags": p.flags = v
                elif k == "No. Variables": p.n_vars = int(v)
                elif k == "No. Points": p.n_points = int(v)
                elif k.startswith("Plot Suggestion"):
                    p.suggestions = re.findall("«(.*?)»", v)
                else: p.extra[k] = v
            elif line.startswith(".param"):
                p.params.append(line[6:].strip())
            elif line.startswith(".alias"):
                parts = line[6:].strip().split(None, 1)
                if len(parts) == 2:
                    p.aliases[parts[0]] = parts[1]
        if mode is None:
            break
        nv, npnt = p.n_vars, p.n_points
        if mode == "binary":
            if p.complex:
                dt = np.dtype([("x", "<f8"), ("y", "<c16", (nv - 1,))])
            else:
                dt = np.dtype([("x", "<f8"), ("y", "<f8", (nv - 1,))])
            nbytes = dt.itemsize * npnt
            arr = np.frombuffer(raw, dtype=dt, count=npnt, offset=pos)
            pos += nbytes
            data = np.empty((npnt, nv), dtype=np.complex128 if p.complex else np.float64)
            data[:, 0] = arr["x"]
            data[:, 1:] = arr["y"].reshape(npnt, nv - 1)
        else:
            text = raw[pos:].decode("latin-1")
            # consume until next "Title:" (another plot) or EOF
            nxt = text.find("\nTitle:")
            body = text if nxt < 0 else text[:nxt]
            pos += len(body.encode("latin-1")) + (1 if nxt >= 0 else 0)
            toks = body.split()
            data = np.empty((npnt, nv), dtype=np.complex128 if p.complex else np.float64)
            i = 0
            for r in range(npnt):
                i += 1  # point index
                for c in range(nv):
                    t = toks[i]; i += 1
                    if "," in t:
                        re_, im = t.split(",")
                        data[r, c] = complex(float(re_), float(im))
                    else:
                        data[r, c] = float(t)
        p.data = data
        p.step_bounds = _split_steps(data[:, 0]) if "stepped" in p.flags else [(0, npnt)]
        plots.append(p)
        # skip whitespace between plots
        while pos < len(raw) and raw[pos:pos + 1] in (b"\n", b"\r", b" "):
            pos += 1
    return plots


def summarize(plots: list[Plot]) -> list[dict]:
    out = []
    for p in plots:
        d = {"plotname": p.plotname, "flags": p.flags, "points": p.n_points,
             "variables": [{"name": n, "kind": k} for n, k in p.variables],
             "aliases": p.aliases, "plot_suggestions": p.suggestions, "steps": p.step_info()}
        out.append(d)
    return out


def stats(y: np.ndarray, x: np.ndarray) -> dict:
    yr = np.real(y)
    d = {"min": float(np.min(yr)), "max": float(np.max(yr)), "first": float(yr[0]), "last": float(yr[-1])}
    if len(x) > 1 and np.all(np.diff(np.real(x)) >= 0):
        xr = np.real(x)
        span = xr[-1] - xr[0]
        if span > 0:
            d["avg"] = float(np.trapezoid(yr, xr) / span)
            d["rms"] = float(np.sqrt(np.trapezoid(yr ** 2, xr) / span))
    d["x_at_max"] = float(np.real(x[int(np.argmax(yr))]))
    d["x_at_min"] = float(np.real(x[int(np.argmin(yr))]))
    if np.iscomplexobj(y):
        mag = np.abs(y)
        d["max_mag_db"] = float(20 * np.log10(np.max(mag) + 1e-300))
    return d
