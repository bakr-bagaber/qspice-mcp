"""QSPICE MCP server.

Exposes QSPICE's simulator, netlister, post-processor, waveform exporter, GUI
tools, help, symbol & model libraries — plus LLM-native schematic drawing:
automatic neat layout from a circuit description, incremental canvas editing
with auto-routing, and rendered previews so the model can *see* the canvas.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import traceback
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image

from . import autodraw, canvas, helpdocs, models, netlist_import, plots, qraw, render, runner
from .config import qspice_dir, work_dir
from .connectivity import extract
from .model import Schematic
from .symbols import library, make_block_symbol

DRAWING_GUIDE = r"""
QSPICE MCP — DRAWING GUIDE (read once before drawing)

TWO WAYS TO DRAW
1) auto_draw_schematic(spec)  — describe the circuit, the server places, routes and
   labels it neatly (signal left->right, current top->bottom, ground triangles,
   supply rails as labels, refdes above value, T-junction dots, no 4-way joins),
   writes the .qsch, validates it against QUX's own netlister, and returns a PNG.
   Use this for any new circuit.  netlist_to_schematic() does the same from SPICE text.
2) Canvas tools for surgical edits: place_component, connect_pins (auto-routed wire),
   add_wire (explicit polyline), add_net_label / add_ground, update_component,
   delete_items, set_directives, then render_schematic to look at the result and
   read_schematic to get exact coordinates.

SPEC FORMAT (auto_draw_schematic)
{
 "title": "optional comment shown on the sheet",
 "components": [
   {"ref":"R1", "symbol":"R", "value":"10K", "nodes":["in","out"]},          # nodes = SPICE pin order
   {"ref":"Q1", "symbol":"NPN", "value":"2N3904", "pins":{"C":"vc","B":"b","E":"0"}},
   {"ref":"U1", "symbol":"OpAmp", "pins":{"IN+":"p","IN-":"m","OUT":"out","Vdd":"vcc","Vss":"vee"},
               "params":{"GBW":"10Meg"}},
   {"ref":"X1", "symbol":"X", "subckt":"MYBLK", "subckt_pins":["IN","OUT","VCC","GND"],
               "pins":{"IN":"a","OUT":"b","VCC":"vcc","GND":"0"}}
 ],
 "directives": [".tran 1m", ".model 2N3904 NPN Is=6.7f Bf=416"],
 "inputs": ["in"], "outputs": ["out"],          # optional signal-flow hints
 "rails": {"vcc":"+", "vee":"-"},               # optional; names like vcc/vdd/vee/vss are detected
 "label_nets": "auto" | "all" | "none" | ["net1", ...],
 "options": {"effort": 1-3, "style": "compact|normal|spacious", "seed": 1, "use_rail_labels": true}
}
* Ground is net "0" (also "gnd"). Use plain SPICE values without units: 3.3 not 3.3V, 1µ / 1u, 10K.
* Symbols: R C L V I D Zener Schottky NPN PNP NMOS PMOS NJF PJF OpAmp SW CSW E F G H B1 B2 T Y
  Transformer, logic gates (AND2, INV, ...), flops, and every installed .qsym (list_symbols).
* Pins can be given by name ("pins") or in order ("nodes"); optional pins (e.g. OpAmp MULT+) may be omitted.
* QSPICE identifies device type by the refdes' first letter; the server renames when needed
  (e.g. an op-amp U1 becomes Ã1) and reports it in "notes".
* Hints per component: "position":[x,y] (fixes it), "rotation":0/90/180/270, "mirror":true.

CANVAS COORDINATES
* Units: 100 per grid step, y grows UPWARD.  Pins sit on multiples of 100; keep wires on the grid.
* rotation is counter-clockwise degrees; mirror flips left/right after rotation.
* A wire connects only at its endpoints or where an endpoint touches another wire; crossings don't connect.
* Net labels with equal names are connected; "GND" draws a ground triangle.
* After editing, call validate_schematic (QUX netlist + geometric checks) and render_schematic.
"""

INSTRUCTIONS = ("Tools for the QSPICE circuit simulator (Qorvo). Simulate schematics/netlists, read "
                "waveforms, run .meas, plot, browse symbols/models/help, and DRAW schematics. For new "
                "circuits prefer auto_draw_schematic; call drawing_guide first if unsure of the spec. "
                "Files are created in the work folder unless an absolute path is given.")

mcp = MCPServer("qspice", instructions=INSTRUCTIONS)


# ------------------------------------------------------------- helpers ----

def _p(path: str | None, suffix: str | None = None, must_exist=False) -> Path:
    if not path:
        raise ValueError("path is required")
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.is_absolute():
        p = work_dir() / p
    if suffix and p.suffix == "":
        p = p.with_suffix(suffix)
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


def _img(png: bytes) -> Image:
    return Image(data=png, format="png")


def _js(d: Any) -> str:
    return json.dumps(d, indent=1, default=str, ensure_ascii=False)


def _load(path) -> Schematic:
    return Schematic.load(_p(path, ".qsch", must_exist=True))


def _save_and_show(sch: Schematic, path, info: dict, show=True, grid=False):
    p = sch.save(_p(path, ".qsch"))
    info = dict(info)
    info["file"] = str(p)
    out: list = [_js(info)]
    if show:
        out.append(_img(render.render(sch, width=1200, grid=grid)))
    return out


def _qraw_for(path: str) -> Path:
    p = _p(path)
    if p.suffix.lower() != ".qraw":
        p = p.with_suffix(".qraw")
    if not p.exists():
        raise FileNotFoundError(f"{p} not found — run simulate first")
    return p


# ========================================================= info & help ====

@mcp.tool()
def qspice_info() -> str:
    """Installation details: QSPICE folder, executables, latest revision notes,
    the server's work folder and symbol/model library sizes."""
    d = runner.version()
    d["work_dir"] = str(work_dir())
    try:
        d["symbols"] = len(library().entries)
    except Exception as e:
        d["symbols"] = f"error: {e}"
    return _js(d)


@mcp.tool()
def drawing_guide() -> str:
    """How to describe circuits for auto_draw_schematic and how the QSPICE canvas
    works (coordinates, pins, labels).  Read before drawing."""
    return DRAWING_GUIDE


@mcp.tool()
def help_search(query: str, limit: int = 8) -> str:
    """Full-text search of the official QSPICE help (devices, dot-commands, .meas,
    behavioural sources, C++/Verilog DLL blocks, waveform viewer...)."""
    return _js(helpdocs.search(query, limit))


@mcp.tool()
def help_topic(topic: str, max_chars: int = 12000) -> str:
    """Return a QSPICE help page, e.g. 'TRAN', '.meas', 'R-device', 'M-device',
    'Ø-device'/'Oslash-device' (DLL block), 'WaveformExpressions', 'STEP'.
    Use topic='index' to list all topics."""
    if topic.strip().lower() in ("index", "list", "topics", ""):
        return "\n".join(helpdocs.topics())
    return helpdocs.get(topic)[:max_chars]


@mcp.tool()
def list_examples(query: str = "") -> str:
    """List example schematics shipped with QSPICE (optionally filtered)."""
    out = []
    for p in sorted(qspice_dir().rglob("*.qsch")):
        rel = str(p.relative_to(qspice_dir()))
        if query.lower() in rel.lower():
            out.append({"path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)})
    return _js(out)


# ============================================================== library ===

@mcp.tool()
def list_symbols(query: str = "", type_filter: str | None = None, limit: int = 80) -> str:
    """Search the symbol library (built-in primitives + every installed .qsym +
    QSPICE_SYMBOL_PATH folders). Returns name, SPICE type, description and pin names."""
    return _js(library().search(query, type_filter, limit))


@mcp.tool()
def get_symbol(name: str, preview: bool = True) -> list:
    """Pins (local coordinates, SPICE order, outward direction hint), bounding box
    and a picture of one symbol."""
    e = library().resolve(name)
    out: list = [_js(e.summary())]
    if preview:
        out.append(_img(render.render_symbol(e.node())))
    return out


@mcp.tool()
def search_models(query: str = "", kind: str | None = None, model_type: str | None = None, limit: int = 50) -> str:
    """Find .model / .subckt definitions in QSPICE's bundled libraries (Diode.txt,
    NPN.txt, NMOS.txt, vendor folders...). kind = 'model'|'subckt'; model_type e.g. NPN, NMOS, D."""
    return _js(models.search(query, kind, model_type, limit))


@mcp.tool()
def get_model_text(name: str, library_file: str | None = None) -> str:
    """Return the SPICE text of a .model or .subckt found by search_models."""
    return models.get_text(name, library_file)


# =========================================================== simulation ===

def _sim_summary(res: dict, max_vars=60) -> dict:
    out = {k: res.get(k) for k in ("netlist", "qraw", "log", "returncode", "elapsed_s", "wall_time_s", "errors", "warnings", "steps")}
    out["log_tail"] = res.get("log_text", "")[-2500:]
    if res.get("measurements"):
        out["measurements"] = res["measurements"]["results"]
        out["measurements_text"] = res["measurements"]["text"][-4000:]
    if res.get("qraw"):
        try:
            pl = qraw.read(res["qraw"])
            out["plots"] = []
            for p in pl:
                out["plots"].append({"plotname": p.plotname, "flags": p.flags, "points": p.n_points,
                                     "traces": p.names[:max_vars] + (["..."] if len(p.names) > max_vars else []),
                                     "steps": p.step_info()[:30], "plot_suggestions": p.suggestions})
        except Exception as e:
            out["plots"] = f"could not read waveform file: {e}"
    return out


@mcp.tool()
def simulate(path: str | None = None, netlist_text: str | None = None, name: str = "sim",
             engine: str = "QSPICE64", ascii_output: bool = False, check_floating: bool = False,
             extra_args: list[str] | None = None, timeout_s: int = 900) -> str:
    """Run a QSPICE simulation.
    * path: a .qsch (netlisted with QUX first) or a netlist (.cir/.net/.sp).
    * or netlist_text: SPICE text (first line is the title) saved as <work>/<name>.cir.
    engine: QSPICE64 (double precision) or QSPICE80 (80-bit extended precision).
    Returns errors/warnings, step list, the trace names in the .qraw file and any
    .meas/.four results (QPOST runs automatically)."""
    if netlist_text:
        p = _p(name, ".cir")
        txt = netlist_text if netlist_text.lstrip().startswith("*") else "* " + name + "\n" + netlist_text
        if not re.search(r"^\s*\.end\s*$", txt, re.I | re.M):
            txt += "\n.end\n"
        p.write_text(txt, encoding="latin-1", errors="replace")
    else:
        p = _p(path, must_exist=True)
    res = runner.simulate(p, engine=engine, ascii=ascii_output, floaters=check_floating,
                          extra_args=extra_args, timeout=timeout_s)
    return _js(_sim_summary(res))


@mcp.tool()
def netlist_schematic(path: str) -> str:
    """Generate the SPICE netlist of a .qsch with QUX.exe -Netlist and return it."""
    cir = runner.netlist(_p(path, ".qsch", must_exist=True))
    return f"# {cir}\n" + runner.read_text(cir)


@mcp.tool()
def check_floating_nodes(path: str) -> str:
    """Parse a schematic/netlist with QSPICE's -floaters check (reports nodes with no DC path)."""
    res = runner.simulate(_p(path, must_exist=True), floaters=True)
    return _js({k: res.get(k) for k in ("errors", "warnings", "log_text")})


@mcp.tool()
def list_traces(path: str) -> str:
    """Trace names, analysis type, point counts and .step values in a .qraw
    (path may be the schematic/netlist; its .qraw is used)."""
    return _js(qraw.summarize(qraw.read(_qraw_for(path))))


@mcp.tool()
def get_trace_data(path: str, traces: list[str], step: int = 1, max_points: int = 200,
                   x_min: float | None = None, x_max: float | None = None, plot_index: int = 0) -> str:
    """Numbers from a .qraw: statistics (min/max/avg/rms/first/last, x at extremes)
    and up to max_points evenly decimated samples per trace. Supports V(a,b).
    AC data is returned as magnitude_dB / phase_deg. For arbitrary expressions use evaluate_expressions."""
    import numpy as np
    pl = qraw.read(_qraw_for(path))[plot_index]
    x = pl.axis(step)
    sel = np.ones(len(x), bool)
    if x_min is not None:
        sel &= np.real(x) >= x_min
    if x_max is not None:
        sel &= np.real(x) <= x_max
    xs = x[sel]
    idx = np.linspace(0, len(xs) - 1, min(max_points, len(xs))).round().astype(int) if len(xs) else []
    out = {"plot": pl.plotname, "x_name": pl.variables[0][0], "step": step, "n_points": int(sel.sum()),
           "x": [float(np.real(v)) for v in xs[idx]], "traces": {}}
    for t in traces:
        try:
            y = pl.trace(t, step)[sel]
        except KeyError:
            out["traces"][t] = {"error": f"unknown trace. Available: {pl.names[:50]}"}
            continue
        d = {"stats": qraw.stats(y, xs)}
        if np.iscomplexobj(y):
            d["magnitude_dB"] = [float(20 * np.log10(abs(v) + 1e-300)) for v in y[idx]]
            d["phase_deg"] = [float(np.angle(v, deg=True)) for v in y[idx]]
        else:
            d["y"] = [float(v) for v in y[idx]]
        out["traces"][t] = d
    return _js(out)


@mcp.tool()
def evaluate_expressions(path: str, expressions: list[str], n_points: int = 101) -> str:
    """Evaluate waveform expressions with QSPICE's own engine (QUX -Export), e.g.
    'V(out)/V(in)', 'I(R1)*V(n1)', 'mag(V(out))'. Returns CSV rows (all steps)."""
    txt = runner.export(_qraw_for(path), expressions, n_points, "CSV")
    txt = "\n".join(l for l in txt.splitlines() if l.strip())
    return txt if len(txt) < 60000 else txt[:60000] + "\n...(truncated: lower n_points)"


@mcp.tool()
def export_waveforms(path: str, expressions: list[str], out_file: str, fmt: str = "CSV",
                     n_points: int | None = None) -> str:
    """Write waveforms/expressions to a file. fmt: CSV | ASCII | SPICE (PWL) | S2P."""
    out = _p(out_file)
    runner.export(_qraw_for(path), expressions, n_points, fmt, out=out)
    return _js({"file": str(out), "bytes": out.stat().st_size})


@mcp.tool()
def measure(path: str, statements: list[str]) -> str:
    """Run .meas/.four statements on an existing simulation result with QPOST,
    without re-simulating. Example: ['.meas tran vpk MAX V(out)', '.meas ac f3db WHEN mag(V(out))=.707'].
    path: the simulated schematic/netlist or its .qraw."""
    raw = _qraw_for(path)
    tmp = raw.with_name(raw.stem + "_meas.cir")
    tmp.write_text("* measurements\n" + "\n".join(statements) + "\n.end\n", encoding="latin-1")
    res = runner.qpost(tmp, raw)
    return _js(res)


@mcp.tool()
def plot_waveforms(path: str, traces: list[str], steps: list[int] | None = None, title: str | None = None,
                   x_min: float | None = None, x_max: float | None = None, separate_panes: bool = False,
                   plot_index: int = 0, save_png: str | None = None) -> list:
    """Plot traces from a .qraw (transient/DC: value vs x; AC: Bode magnitude+phase;
    .step runs are overlaid). Returns an image."""
    pl = qraw.read(_qraw_for(path))[plot_index]
    xl = (x_min, x_max) if x_min is not None or x_max is not None else None
    png = plots.plot_traces(pl, traces, steps, title, xl, separate_panes)
    out: list = []
    if save_png:
        sp = _p(save_png, ".png")
        sp.write_bytes(png)
        out.append(f"saved {sp}")
    out.append(_img(png))
    return out


@mcp.tool()
def dll_variables(path: str) -> str:
    """Variable declarations for the C++/Verilog .DLL (Ø-device) blocks in a schematic (QUX -DLLvariables)."""
    return runner.dll_variables(_p(path, ".qsch", must_exist=True))


@mcp.tool()
def protect_netlist(path: str, mode: str = "subcircuits") -> str:
    """Encrypt a netlist with QSPICE: mode 'subcircuits' (bodies of .subckt) or
    'selections' (.prot/.unprot regions). The original is backed up first."""
    return _js(runner.protect(_p(path, must_exist=True), mode))


@mcp.tool()
def open_in_qspice(path: str | None = None, run: bool = False) -> str:
    """Open a schematic/netlist/waveform in the QSPICE GUI (QUX.exe); run=True also starts the simulation."""
    return _js(runner.open_gui(_p(path, must_exist=True) if path else None, run))


@mcp.tool()
def launch_tool(tool: str, path: str | None = None) -> str:
    """Start a bundled QSPICE utility: DIODE, MOSFET, JFET (model extraction tools),
    LoopTrack, SoundScope, ToneGenerator, SCPIenumerate."""
    name = tool if tool.lower().endswith(".exe") else tool + ".exe"
    found = [p.name for p in qspice_dir().glob("*.exe") if p.name.lower() == name.lower()]
    if not found:
        raise ValueError(f"unknown tool {tool}")
    return _js(runner.open_gui(_p(path) if path else None, False, tool=found[0]))


# ======================================================= schematic: read ===

@mcp.tool()
def read_schematic(path: str, include_netlist: bool = False) -> str:
    """Structured view of a .qsch canvas: every component (ref, symbol, value,
    position, rotation, mirror, bbox, pins with absolute x/y and the net each pin is on),
    wires, net labels, junctions, directives, comments, the net list, and problems
    (unconnected pins, dangling wire ends)."""
    sch = _load(path)
    d = canvas.describe(sch)
    if include_netlist:
        try:
            d["qux_netlist"] = runner.read_text(runner.netlist(sch.path))
        except Exception as e:
            d["qux_netlist"] = f"error: {e}"
    return _js(d)


@mcp.tool()
def render_schematic(path: str, grid: bool = False, show_pins: bool = False, highlight: list[str] | None = None,
                     width: int = 1400, region: list[int] | None = None, save_png: str | None = None) -> list:
    """Picture of a schematic. grid=True overlays the 100-unit grid with coordinates
    (use it to plan edits); show_pins marks pin endpoints; highlight=['R1',..];
    region=[xmin,ymin,xmax,ymax] zooms."""
    sch = _load(path)
    png = render.render(sch, width=width, grid=grid, show_pins=show_pins, highlight=highlight,
                        bbox=tuple(region) if region else None)
    out: list = []
    if save_png:
        sp = _p(save_png, ".png"); sp.write_bytes(png); out.append(f"saved {sp}")
    out.append(_img(png))
    return out


@mcp.tool()
def validate_schematic(path: str) -> str:
    """Check a schematic: QUX netlists it (authoritative), plus geometric checks for
    unconnected pins, dangling wires, overlapping parts, and labels referenced by
    directives that don't exist."""
    sch = _load(path)
    d = canvas.describe(sch)
    probs = []
    if d["unconnected_pins"]:
        probs.append({"unconnected_pins": d["unconnected_pins"]})
    if d["dangling_wire_ends"]:
        probs.append({"dangling_wire_ends": d["dangling_wire_ends"]})
    comps = d["components"]
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i]["bbox"], comps[j]["bbox"]
            if min(a[2], b[2]) - max(a[0], b[0]) > 40 and min(a[3], b[3]) - max(a[1], b[1]) > 40:
                probs.append({"overlap": [comps[i]["ref"], comps[j]["ref"]]})
    netnames = {n.lower() for n in d["nets"]}
    for dv in d["directives"]:
        for m in re.finditer(r"\bV\(\s*([^(),]+)", dv, re.I):
            n = m.group(1).strip().lower()
            if n not in netnames and n not in ("0", "gnd"):
                probs.append({"directive_references_unknown_net": m.group(1).strip()})
    try:
        cir = runner.netlist(sch.path)
        net = runner.read_text(cir)
    except Exception as e:
        net = f"netlisting failed: {e}"
        probs.append({"netlist": net})
    return _js({"ok": not probs, "problems": probs, "netlist": net})


# ===================================================== schematic: create ===

@mcp.tool()
def auto_draw_schematic(spec: dict, path: str = "autodraw.qsch", simulate_after: bool = False,
                        show: bool = True) -> list:
    """Draw a complete, neat, readable QSPICE schematic from a circuit description
    (see drawing_guide for the spec). Placement follows professional drafting
    conventions; wires are auto-routed without overlaps; the result is validated
    against QUX's netlister. Returns metrics, notes, validation and a preview image."""
    out_p = _p(path, ".qsch")
    res = autodraw.draw(spec, out_p, validate=True)
    sch = res.pop("_schematic")
    v = res.get("validation", {})
    if "netlist" in v and len(v["netlist"]) > 4000:
        v["netlist"] = v["netlist"][:4000] + "\n..."
    if simulate_after:
        res["simulation"] = _sim_summary(runner.simulate(out_p))
    out: list = [_js(res)]
    if show:
        out.append(_img(render.render(sch, width=1300)))
    return out


@mcp.tool()
def netlist_to_schematic(netlist_text: str | None = None, netlist_path: str | None = None,
                         path: str | None = None, options: dict | None = None, show: bool = True) -> list:
    """Turn a SPICE netlist (QSPICE / LTspice / generic) into a neatly drawn .qsch.
    Device types are mapped to QSPICE symbols, .subckt instances become block symbols,
    and all dot-commands/.model/.subckt text is kept as directives."""
    if netlist_path:
        txt = Path(_p(netlist_path, must_exist=True)).read_text(encoding="latin-1", errors="replace")
        path = path or str(_p(netlist_path).with_suffix(".qsch"))
    elif netlist_text:
        txt = netlist_text
    else:
        raise ValueError("give netlist_text or netlist_path")
    spec = netlist_import.parse(txt)
    if options:
        spec["options"] = options
    notes = spec.pop("_import_notes", [])
    r = auto_draw_schematic(spec, path or "from_netlist.qsch", show=show)
    if notes:
        d = json.loads(r[0]); d["import_notes"] = notes; r[0] = _js(d)
    return r


@mcp.tool()
def beautify_schematic(path: str, out_path: str | None = None, options: dict | None = None, show: bool = True) -> list:
    """Re-draw an existing (messy) schematic neatly: keeps every component, value,
    symbol, connection and directive, but recomputes placement, wiring and labels.
    Writes to out_path (default: <name>_neat.qsch) so the original is untouched."""
    src = _p(path, ".qsch", must_exist=True)
    sch = Schematic.load(src)
    conn = extract(sch)
    comps = []
    for c in sch.components():
        pins = {}
        for d in conn["pins"].get(c.refdes, []):
            pins[str(d["index"] + 1)] = d["net"]
        sym = c.symbol
        names = [p.name or str(p.index + 1) for p in sym.pins()]
        pmap = {}
        for d in conn["pins"].get(c.refdes, []):
            # skip optional pins that are not wired to anything
            if sym.pins()[d["index"]].default_net and d["net"].startswith("N") and \
                    len(conn["nets"].get(d["net"], [])) <= 1:
                continue
            pmap[names[d["index"]] if names.count(names[d["index"]]) == 1 else str(d["index"] + 1)] = d["net"]
        attrs = c.attributes[1:] if len(c.attributes) > 1 else []
        comps.append({"ref": c.refdes, "symbol": sym.name, "_symbol_node": sym.node, "value": c.value,
                      "attributes": attrs, "pins": pmap})
    spec = {"components": comps, "directives": sch.directives(), "options": options or {}}
    comments = [t["str"] for t in sch.texts() if t["flag"] != 0]
    if comments:
        spec["title"] = comments[0]
    dst = _p(out_path) if out_path else src.with_name(src.stem + "_neat.qsch")
    # keep the original symbols' attribute texts: autodraw rewrites refdes/value only
    for c in comps:
        c["attributes"] = []
    return auto_draw_schematic(spec, str(dst), show=show)


@mcp.tool()
def new_schematic(path: str, overwrite: bool = False) -> str:
    """Create an empty .qsch canvas."""
    p = _p(path, ".qsch")
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} exists (pass overwrite=true)")
    Schematic().save(p)
    return _js({"file": str(p)})


# ===================================================== schematic: edit ====

@mcp.tool()
def place_component(path: str, symbol: str, x: int, y: int, ref: str | None = None, value: str = "",
                    rotation: int = 0, mirror: bool = False, params: dict | None = None,
                    attributes: list[str] | None = None, show: bool = False) -> list:
    """Place a symbol on the canvas at (x, y) (snapped to the 100 grid; y up).
    rotation 0/90/180/270 CCW; mirror flips left-right. Refdes/value texts are
    positioned by drafting convention. Returns the absolute pin coordinates."""
    sch = _load(path)
    info = canvas.place_component(sch, symbol, x, y, ref, value, rotation, mirror, params, attributes)
    return _save_and_show(sch, sch.path, info, show)


@mcp.tool()
def update_component(path: str, ref: str, value: str | None = None, params: dict | None = None,
                     new_ref: str | None = None, x: int | None = None, y: int | None = None,
                     rotation: int | None = None, mirror: bool | None = None, show: bool = False) -> list:
    """Change a component's value/params/refdes or move/rotate it (wires are not
    dragged along — reconnect with connect_pins afterwards)."""
    sch = _load(path)
    info = canvas.update_component(sch, ref, value, params, new_ref, x, y, rotation, mirror)
    return _save_and_show(sch, sch.path, info, show)


@mcp.tool()
def delete_items(path: str, refs: list[str] | None = None, wires_at: list[list[int]] | None = None,
                 labels_at: list[list[int]] | None = None, show: bool = False) -> list:
    """Delete components (and the wire stubs left dangling), wires passing through
    given points, and net labels at given points."""
    sch = _load(path)
    info = canvas.delete(sch, refs or [], wires_at or [], labels_at or [])
    return _save_and_show(sch, sch.path, info, show)


@mcp.tool()
def connect_pins(path: str, from_: str, to: str | list[int], show: bool = False) -> list:
    """Auto-route a clean orthogonal wire from a pin ('R1.2', 'U1.IN-', 'Q1.B') to
    another pin or an [x, y] point. The router avoids component bodies and never
    touches or overlaps other nets; T-junction dots are added automatically."""
    sch = _load(path)
    info = canvas.connect(sch, from_, to)
    return _save_and_show(sch, sch.path, info, show)


@mcp.tool()
def connect_many(path: str, connections: list[list], show: bool = True) -> list:
    """Route several connections at once: [["R1.2","C1.1"], ["C1.2",[400,-300]], ...].
    Failed ones are reported (use labels for those)."""
    sch = _load(path)
    done, failed = [], []
    for a, b in connections:
        try:
            done.append({"from": a, "to": b, **canvas.connect(sch, a, b)})
        except Exception as e:
            failed.append({"from": a, "to": b, "error": str(e)})
    return _save_and_show(sch, sch.path, {"routed": done, "failed": failed}, show)


@mcp.tool()
def add_wire(path: str, points: list[list[int]], show: bool = False) -> list:
    """Draw an explicit polyline wire through the given [x, y] points (snapped to grid).
    Wires are split at tees and junction dots refreshed automatically."""
    sch = _load(path)
    n = canvas.add_polyline(sch, points)
    return _save_and_show(sch, sch.path, {"segments_added": n}, show)


@mcp.tool()
def add_net_label(path: str, at: str | list[int], name: str, direction: str | None = None,
                  stub: int = 1, port: bool = False, show: bool = False) -> list:
    """Attach a net label at a pin ('R1.1') or point [x,y]. With stub=1 a 100-unit
    wire is drawn first in `direction` (U/D/L/R, default: the pin's outward direction).
    Same-named labels are connected. Name 'GND' draws a ground triangle.
    port=True marks it as a hierarchical port."""
    sch = _load(path)
    info = canvas.add_label(sch, at, name, direction, port, stub)
    return _save_and_show(sch, sch.path, info, show)


@mcp.tool()
def add_ground(path: str, at: str | list[int], direction: str = "D", stub: int = 1, show: bool = False) -> list:
    """Attach a ground symbol at a pin or point (direction the triangle points, default down)."""
    sch = _load(path)
    info = canvas.add_label(sch, at, "GND", direction, False, stub)
    return _save_and_show(sch, sch.path, info, show)


@mcp.tool()
def set_directives(path: str, directives: list[str], replace: bool = True, x: int | None = None,
                   y: int | None = None) -> str:
    """Set the SPICE directives on the sheet (.tran/.ac/.op/.step/.meas/.param/.model/.lib/.plot ...).
    replace=True removes existing directive texts first."""
    sch = _load(path)
    if replace:
        for t in sch.root.findall("text"):
            if int(t.tokens[4]) == 0:
                sch.remove(t)
    from .render import schematic_bbox
    bb = schematic_bbox(sch)
    pos = (x if x is not None else int(bb[0]) + 200, y if y is not None else int(bb[1]) - 100)
    sch.add_text(pos, "\n".join(directives), directive=True, size=1, just=5)
    sch.save()
    return _js({"file": str(sch.path), "directives": sch.directives()})


@mcp.tool()
def add_comment(path: str, text: str, x: int, y: int, size: float = 1.0) -> str:
    """Place a non-SPICE comment text on the sheet."""
    sch = _load(path)
    sch.add_text((x, y), text, directive=False, size=size, just=5)
    sch.save()
    return _js({"file": str(sch.path)})


@mcp.tool()
def create_block_symbol(name: str, pins: list[str], out_path: str | None = None, library_file: str | None = None,
                        left: list[str] | None = None, right: list[str] | None = None,
                        top: list[str] | None = None, bottom: list[str] | None = None,
                        description: str | None = None) -> list:
    """Generate a tidy rectangular .qsym for a subcircuit (pins in SPICE order;
    optional side assignment). It's added to the symbol library for this session."""
    node = make_block_symbol(name, pins, library_file, left, right, top, bottom, description)
    p = _p(out_path or f"{name}.qsym", ".qsym")
    from . import qsch
    qsch.write_file(node, p)
    library().add_qsym(p)
    return [_js({"file": str(p), "pins": pins}), _img(render.render_symbol(node))]


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
