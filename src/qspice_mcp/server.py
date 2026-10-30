"""QSPICE MCP server (v0.2).

Exposes QSPICE's simulator, netlister, post-processor, waveform exporter, GUI
tools, help, symbol & model libraries - plus LLM-native schematic drawing:

* auto_draw_schematic: circuit description -> neat, validated drawing
* a reviewer that scores a sheet against professional drafting rules and says
  exactly which tool call fixes each problem (the "eye" for any model)
* canvas_map / annotated renders: text + picture views of the 2-D canvas
* smart editing: add_component (place + wire in one call), move_component
  (wires follow), reroute_schematic, fix_text_placement, undo
* symbol creation: create_symbol, symbol_from_subckt, create_subsheet
* skills: step-by-step guides (qspice_guide) that teach any model the workflow
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image

from . import __version__, autodraw, canvas, helpdocs, models, netlist_import, plots, qraw, render, runner, skills
from .config import add_symbol_dir, qspice_dir, work_dir
from .connectivity import extract
from .model import Schematic
from .symbols import library

INSTRUCTIONS = f"""QSPICE MCP server v{__version__} - simulate circuits with QSPICE and draw clean schematics.

START HERE: call qspice_guide() once - it lists the skills (step-by-step guides). Before drawing read
qspice_guide('drafting'); before simulating read qspice_guide('simulation').

GOLDEN WORKFLOW FOR SCHEMATICS
1. auto_draw_schematic(spec) for any new circuit (it places, wires, labels and validates).
2. review_schematic(path) -> fix every error and warning with the suggested tool calls.
3. render_schematic(path) and LOOK at the picture. Repeat 2-3 until score >= 90 and it looks clean.
4. simulate(path) -> plot_waveforms / get_trace_data / measure.

RULES: coordinates are QSPICE units, grid = 100, y grows UPWARD. Pins are 'REF.PIN' (e.g. 'R1.1', 'Q1.B',
'U1.OUT'). Ground net is '0' or 'GND'. Values without units: 10K, 100n, 3.3 (not 10kOhm, 100nF, 3.3V);
mega = Meg (M = milli). Every edit tool saves the file and returns a review summary; use undo_last_edit
if an edit made things worse. Files go to the work folder unless an absolute path is given."""

mcp = MCPServer("qspice", instructions=INSTRUCTIONS)


# The MCP SDK hides the text of any exception that is not a ToolError ("Error
# executing tool X" only). Our error messages are written for the model (valid
# pin names, "did you mean", files in the folder ...), so every tool converts its
# exceptions into ToolErrors that carry the message.
def _explain(e: Exception) -> str:
    msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
    if isinstance(e, (ValueError, KeyError, FileNotFoundError, FileExistsError, RuntimeError,
                      NotADirectoryError, PermissionError)):
        return str(msg)
    return f"{type(e).__name__}: {msg}"


def _friendly(fn):
    import functools
    from mcp.server.mcpserver.exceptions import ToolError

    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolError(_explain(e)) from e
    return wrapper


_sdk_tool = mcp.tool


def _tool(*dargs, **dkw):
    deco = _sdk_tool(*dargs, **dkw)

    def apply(fn):
        return deco(_friendly(fn))
    return apply


mcp.tool = _tool


# ------------------------------------------------------------- helpers ----

def _p(path: str | None, suffix: str | None = None, must_exist=False) -> Path:
    if not path:
        raise ValueError("path is required")
    p = Path(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    if not p.is_absolute():
        p = work_dir() / p
    if suffix and p.suffix == "":
        p = p.with_suffix(suffix)
    if must_exist and not p.exists():
        near = sorted(x.name for x in p.parent.glob(f"*{p.suffix}"))[:15] if p.parent.exists() else []
        raise FileNotFoundError(f"{p} does not exist." + (f" Files in that folder: {near}" if near else ""))
    return p


def _img(png: bytes) -> Image:
    return Image(data=png, format="png")


def _js(d: Any) -> str:
    return json.dumps(d, indent=1, default=str, ensure_ascii=False)


def _load(path) -> Schematic:
    return Schematic.load(_p(path, ".qsch", must_exist=True))


def _history_dir(p: Path) -> Path:
    d = work_dir() / ".history" / re.sub(r"[^\w.-]+", "_", str(p.resolve()).lower())[-120:]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup(p: Path, keep: int = 30):
    if not p.exists():
        return
    d = _history_dir(p)
    shutil.copy2(p, d / f"{time.time():.3f}.qsch")
    old = sorted(d.glob("*.qsch"))
    for f in old[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass


def _review(sch: Schematic) -> dict:
    from .review import review
    try:
        return review(sch)
    except Exception as e:  # never break an edit because of the reviewer
        return {"score": None, "grade": "?", "counts": {"error": 0, "warning": 0, "style": 0},
                "issues": [], "error": str(e)}


def _marks(rv: dict, limit: int = 40) -> list:
    out = []
    for k, it in enumerate([i for i in rv.get("issues", []) if i.get("at")][:limit]):
        out.append({"at": it["at"], "severity": it["severity"], "label": f"{k + 1}"})
    return out


def _render(sch: Schematic, width=1300, grid=False, annotate=None, marks=None, highlight=None, region=None):
    return render.render(sch, width=width, grid=grid, annotate=annotate, marks=marks, highlight=highlight,
                         bbox=tuple(region) if region else None)


def _pin_nets(sch: Schematic) -> dict:
    conn = extract(sch)
    return {f"{ref}.{d['pin']}": d["net"] for ref, lst in conn["pins"].items() for d in lst}


def _conn_changes(before: dict, after: dict) -> list[str]:
    """Human-readable list of nets that an edit split apart or shorted together
    (pins of parts present before and after)."""
    from collections import defaultdict
    common = set(before) & set(after)
    out = []
    by_b = defaultdict(set)
    for p in common:
        by_b[before[p]].add(p)
    for net, pins in by_b.items():
        groups = defaultdict(list)
        for p in pins:
            groups[after[p]].append(p)
        if len(pins) >= 2 and len(groups) > 1:
            parts = " | ".join(", ".join(sorted(v)[:5]) for v in groups.values())
            out.append(f"SPLIT: net '{net}' is now broken into {len(groups)} separate nets: {parts}")
    by_a = defaultdict(set)
    for p in common:
        by_a[after[p]].add(p)
    for net, pins in by_a.items():
        groups = defaultdict(list)
        for p in pins:
            groups[before[p]].append(p)
        real = {k: v for k, v in groups.items() if len(v) >= 2}
        if len(real) > 1:
            out.append(f"SHORT: nets {sorted(real)} are now connected together (net '{net}')")
    return out


def _edit(path, fn, show: bool = False, grid: bool = False, annotate: str | None = None) -> list:
    """Load -> backup -> fn(sch) -> save -> review delta + connectivity check (+ picture)."""
    from .edit import issues_delta
    p = _p(path, ".qsch", must_exist=True)
    sch = Schematic.load(p)
    before = _review(sch)
    try:
        nets_before = _pin_nets(sch)
    except Exception:
        nets_before = None
    info = fn(sch) or {}
    _backup(p)
    sch.save(p)
    after = _review(sch)
    info = dict(info)
    info["file"] = str(p)
    if nets_before is not None:
        try:
            changes = _conn_changes(nets_before, _pin_nets(sch))
        except Exception:
            changes = []
        if changes:
            info["connectivity_changes"] = changes
            info["hint"] = ("CHECK: this edit changed which pins are connected (see connectivity_changes). "
                            "If that was not intended, call undo_last_edit now.")
    if after.get("score") is not None and before.get("score") is not None:
        d = issues_delta(before, after)
        d["score_before"] = before["score"]
        info["review"] = d
        if d["new_problems"] and "hint" not in info:
            info["hint"] = ("This edit introduced problems (see review.new_problems). Fix them or call "
                            "undo_last_edit.")
    out: list = [_js(info)]
    if show:
        out.append(_img(_render(sch, grid=grid, annotate=annotate,
                                marks=[m for m in _marks(after) if m["severity"] == "error"])))
    return out


def _qraw_for(path: str) -> Path:
    p = _p(path)
    if p.suffix.lower() != ".qraw":
        p = p.with_suffix(".qraw")
    if not p.exists():
        raise FileNotFoundError(f"{p} not found - run simulate first")
    return p


def _short_issue(i: dict) -> dict:
    return {k: i[k] for k in ("severity", "code", "message", "at", "fix") if k in i}


# ========================================================= info & help ====

@mcp.tool()
def qspice_guide(topic: str = "") -> str:
    """READ THIS FIRST. Step-by-step guides (skills) for using this server well.
    topic='' lists all guides. Examples: 'overview', 'drafting' (how a clean
    schematic must look + the review loop), 'auto-draw' (spec format), 'canvas'
    (editing), 'symbols' (new symbols / subcircuits / hierarchy), 'simulation',
    'waveforms', 'models', 'netlist-import', 'troubleshooting', 'recipes'."""
    if not topic.strip():
        return skills.index_text()
    return skills.get(topic)


@mcp.tool()
def drawing_guide() -> str:
    """Same as qspice_guide('drafting'): the schematic drafting standard and the
    render -> review -> fix loop. Read before drawing or editing a schematic."""
    return skills.get("drafting")


@mcp.tool()
def qspice_info() -> str:
    """Installation details: QSPICE folder and executables, revision notes, the
    server's work folder (where relative paths go) and library sizes."""
    d = runner.version()
    d["server_version"] = __version__
    d["work_dir"] = str(work_dir())
    d["symbols_dir"] = str(work_dir() / "symbols")
    try:
        d["symbols"] = len(library().entries)
    except Exception as e:
        d["symbols"] = f"error: {e}"
    return _js(d)


@mcp.tool()
def help_search(query: str, limit: int = 8) -> str:
    """Full-text search of the official QSPICE help (devices, dot-commands, .meas,
    behavioural sources, C++/Verilog DLL blocks, waveform viewer ...).
    Then read a page with help_topic(topic)."""
    return _js(helpdocs.search(query, limit))


@mcp.tool()
def help_topic(topic: str, max_chars: int = 12000) -> str:
    """Return a QSPICE help page, e.g. 'TRAN', '.meas', 'R-device', 'M-device',
    'Oslash-device' (C++/Verilog DLL block), 'WaveformExpressions', 'STEP'.
    topic='index' lists all pages."""
    if topic.strip().lower() in ("index", "list", "topics", ""):
        return "\n".join(helpdocs.topics())
    return helpdocs.get(topic)[:max_chars]


@mcp.tool()
def list_examples(query: str = "") -> str:
    """List the example schematics shipped with QSPICE (optionally filtered by a
    word in the path). Good starting points to copy from."""
    out = []
    for p in sorted(qspice_dir().rglob("*.qsch")):
        rel = str(p.relative_to(qspice_dir()))
        if query.lower() in rel.lower():
            out.append({"path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)})
    return _js(out)


# ============================================================== library ===

@mcp.tool()
def list_symbols(query: str = "", type_filter: str | None = None, limit: int = 80) -> str:
    """Search the symbol library: built-in primitives (R C L V I D NPN PNP NMOS PMOS
    OpAmp SW E F G H B1 B2 ...), every installed .qsym and your own symbols.
    query: words to match (e.g. 'gate', 'opamp', 'schottky'). Returns name, SPICE
    type, description and pin names. Use the 'name' as `symbol` in other tools."""
    return _js(library().search(query, type_filter, limit))


@mcp.tool()
def get_symbol(name: str, preview: bool = True) -> list:
    """Everything about one symbol: pins in SPICE order with local x/y and the
    direction each pin points, texts, and an annotated picture
    ('order:NAME (x,y)' at every pin; green cross = origin = the point you place).
    name: a library name ('R', 'NPN', 'OpAmp'), a .qsym path, or a child .qsch
    path (hierarchical block)."""
    from . import symbol_builder as sb
    e = library().resolve(name)
    node = e.node()
    d = sb.describe(node)
    d["source"] = e.source
    d["how_to_use"] = (f"place_component(symbol='{name}', x=.., y=..) or add_component(symbol='{name}', "
                       f"connections={{'<pin>': '<REF.PIN | net name | GND>'}}). Pin positions in the "
                       f"sheet = (x, y) + local pin coordinates (rotation 0).")
    out: list = [_js(d)]
    if preview:
        out.append(_img(render.render_symbol(node)))
    return out


@mcp.tool()
def search_models(query: str = "", kind: str | None = None, model_type: str | None = None, limit: int = 50) -> str:
    """Find .model / .subckt definitions in QSPICE's bundled libraries and your
    symbol folders. kind = 'model' | 'subckt'; model_type e.g. 'NPN', 'NMOS', 'D'.
    The 'name' goes into a component's value (e.g. value='2N3904')."""
    return _js(models.search(query, kind, model_type, limit))


@mcp.tool()
def get_model_text(name: str, library_file: str | None = None) -> str:
    """Return the SPICE text of a .model or .subckt found by search_models."""
    return models.get_text(name, library_file)


# ============================================================== symbols ===

@mcp.tool()
def create_symbol(name: str, pins: list, kind: str = "block", description: str | None = None,
                  spice_prefix: str = "X", model: str | None = None, library_file: str | None = None,
                  attributes: list[str] | None = None, body_style: str = "box", label: str | None = None,
                  graphics: list | None = None, refdes: str | None = None, pin_pitch: int = 200,
                  folder: str | None = None) -> list:
    """Create a new QSPICE symbol (.qsym) the right way and add it to the library,
    so it can be used by name in place_component / add_component / auto_draw_schematic.
    Use it when list_symbols has no suitable symbol (e.g. a vendor IC model).

    pins: SPICE node order! e.g. ["VIN","EN","GND","VOUT"] or
          [{"name":"VIN","side":"left"}, {"name":"VOUT","side":"right"}, ...].
          side = left|right|top|bottom (default by name: inputs left, outputs
          right, supplies top, ground bottom).
    kind: 'block' (rectangle IC, the usual choice) | 'two_terminal' (body_style box,
          circle, resistor, inductor, capacitor, diode; pins top then bottom) |
          'amplifier' (triangle; pins IN+, IN-, OUT, optional V+ top / V- bottom) |
          'custom' (your own graphics; pins need x, y, dir).
    spice_prefix: device letter: 'X' subcircuit (default), or R C L D Q M ... for primitives.
    model: the .subckt/.model name written on the symbol (default = name).
    library_file: path of the file containing the .subckt/.model (QSPICE adds .lib for it).
    attributes: extra texts passed on the netlist line, e.g. ["Rload=1K"].
    graphics (custom only): [{"rect":[[-200,200],[200,-200]]}, {"line":[[x,y],[x,y]]},
          {"circle":[cx,cy,r]}, {"polygon":[[x,y],...]}, {"arc":[[x,y],[x,y],[x,y]]},
          {"text":"ABC","at":[0,0],"size":0.8}]. Coordinates: 100 grid, y up.
    Returns the symbol description (pins with coordinates) and an annotated preview."""
    from . import symbol_builder as sb
    spec = {"name": name, "kind": kind, "pins": pins, "description": description, "spice_prefix": spice_prefix,
            "model": model, "library": library_file, "attributes": attributes or [], "body_style": body_style,
            "label": label, "graphics": graphics or [], "refdes": refdes, "pin_pitch": pin_pitch}
    node, warn = sb.build(spec)
    path = sb.save(node, _p(folder) if folder else None)
    d = sb.describe(node)
    d["file"] = str(path)
    d["warnings"] = warn
    d["next"] = (f"Use symbol='{name}' in place_component/add_component/auto_draw_schematic. "
                 + ("" if library_file else "If it is a subcircuit, make sure its .subckt text is reachable: "
                    "pass library_file, or add '.lib <file>' with set_directives."))
    return [_js(d), _img(render.render_symbol(node))]


@mcp.tool()
def symbol_from_subckt(source: str, name: str | None = None, sides: dict | None = None,
                       library_file: str | None = None, subckt_name: str | None = None,
                       description: str | None = None) -> list:
    """Make a block symbol for a SPICE subcircuit in one step (pins are taken from
    the .subckt line, in the right order).
    source: the .subckt text itself, OR a path to a model file (.lib/.sub/.txt/.cir),
            OR the name of a subcircuit known to search_models.
    sides: optional {"PIN": "left|right|top|bottom"} to override pin placement.
    subckt_name: which .subckt to use when the file contains several.
    Pasted text is saved next to the symbol and linked automatically."""
    from . import symbol_builder as sb
    node, warn, lib = sb.make_from_source(source, name, sides, library_file, description, subckt_name)
    path = sb.save(node)
    d = sb.describe(node)
    d.update({"file": str(path), "library_file": lib, "warnings": warn,
              "next": f"Use symbol='{d['name']}' in place_component/add_component/auto_draw_schematic."})
    return [_js(d), _img(render.render_symbol(node))]


@mcp.tool()
def create_subsheet(name: str, ports: list, spec: dict | None = None, folder: str | None = None,
                    show: bool = True) -> list:
    """Create a hierarchical sub-schematic NAME.qsch (a reusable block made of other
    components) and its block symbol.
    ports: the nets that connect to the parent, in pin order, e.g. ["IN","OUT","VCC"]
           or [{"name":"IN","side":"left"}, ...].
    spec: optional auto_draw_schematic spec for the inside (nets named like the
          ports become port labels). Without spec an empty sheet with the port
          labels is created - then edit it with the canvas tools.
    Use it in a parent sheet (MUST be in the same folder) with
    place_component(symbol='<full path of NAME.qsch>') or in an auto_draw spec
    {"ref":"X1","symbol":"<path of NAME.qsch>","pins":{"IN":"a",...}}."""
    from . import symbol_builder as sb
    d = _p(folder) if folder else work_dir()
    d.mkdir(parents=True, exist_ok=True)
    child = d / f"{name}.qsch"
    pnames = [p["name"] if isinstance(p, dict) else str(p) for p in ports]
    info: dict = {"file": str(child)}
    if spec:
        spec = dict(spec)
        spec["ports"] = pnames
        res = autodraw.draw(spec, child, validate=False)
        sch = res.pop("_schematic")
        info["notes"] = res.get("notes")
        info["review"] = res.get("review")
    else:
        sch = Schematic()
        sides = {}
        for p in ports:
            nm = p["name"] if isinstance(p, dict) else str(p)
            sides[nm] = (p.get("side") if isinstance(p, dict) else None) or sb.auto_side(nm)
        ys = {"left": 0, "right": 0, "top": 0, "bottom": 0}
        ends = {}
        for nm, s in sides.items():
            if s in ("left", "right"):
                x = -2000 if s == "left" else 2000
                y = 1000 - ys[s] * 400
                end = (x + (300 if s == "left" else -300), y)
                sch.add_net_label((x, y), nm, 11 if s == "left" else 7, port=True)
            else:
                x = -400 + ys[s] * 400
                y = 2000 if s == "top" else -2000
                end = (x, y + (-300 if s == "top" else 300))
                sch.add_net_label((x, y), nm, 14 if s == "top" else 13, port=True)
            sch.add_wire((x, y), end)
            ends[nm] = list(end)
            ys[s] += 1
        sch.add_text((-2000, 2400), f"Sub-sheet {name}: draw the circuit between the port labels", directive=False)
        sch.save(child)
        info["port_stub_ends"] = ends
        info["note"] = ("Empty sub-sheet created: each port label has a short wire stub. Add parts with "
                        "add_component and connect pins to a stub end point, e.g. connections={'1': [x, y]} "
                        "using port_stub_ends.")
    blk = sb.block_for_child(child)
    from .model import Symbol as _Sym
    info["block_pins"] = [p.name for p in _Sym(blk).pins()]
    info["use_in_parent"] = f"place_component(path=<parent in {d}>, symbol=r'{child}', x=.., y=..)"
    out: list = [_js(info)]
    if show:
        out.append(_img(_render(sch, width=900)))
        out.append(_img(render.render_symbol(blk)))
    return out


@mcp.tool()
def add_symbol_folder(folder: str) -> str:
    """Remember a folder that contains your own .qsym symbols / model files; they
    become searchable with list_symbols and search_models (persists across restarts)."""
    p = _p(folder)
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    dirs = add_symbol_dir(p)
    library().load(force=True)
    return _js({"symbol_folders": dirs, "symbols": len(library().entries)})


# =========================================================== simulation ===

def _sim_summary(res: dict, max_vars=60) -> dict:
    out = {k: res.get(k) for k in ("netlist", "qraw", "log", "returncode", "elapsed_s", "wall_time_s", "errors", "warnings", "steps")}
    out["ok"] = bool(res.get("qraw")) and not res.get("errors")
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
    if not out["ok"]:
        out["hint"] = ("Simulation failed or produced no data. Read 'errors'/'log_tail', then "
                       "qspice_guide('troubleshooting').")
    return out


@mcp.tool()
def simulate(path: str | None = None, netlist_text: str | None = None, name: str = "sim",
             engine: str = "QSPICE64", ascii_output: bool = False, check_floating: bool = False,
             extra_args: list[str] | None = None, timeout_s: int = 900) -> str:
    """Run a QSPICE simulation.
    path: a .qsch schematic (netlisted with QUX first) or a netlist (.cir/.net/.sp).
    netlist_text: alternatively, SPICE text (first line = title); saved as <work>/<name>.cir.
    The sheet needs an analysis directive (.tran/.ac/.dc/.op/.noise) - add one with set_directives.
    engine: QSPICE64 (double precision, default) or QSPICE80 (80-bit, for tough convergence).
    Returns ok, errors/warnings, .step list, the trace names in the result (.qraw) and
    the results of any .meas/.four statements. Then use plot_waveforms / get_trace_data / measure."""
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
    """The SPICE netlist QSPICE generates for a .qsch (QUX.exe -Netlist). Use it to
    check what the simulator will actually see (node names, device lines, .lib)."""
    cir = runner.netlist(_p(path, ".qsch", must_exist=True))
    return f"# {cir}\n" + runner.read_text(cir)


@mcp.tool()
def check_floating_nodes(path: str) -> str:
    """Run QSPICE's floating-node check (-floaters): reports nodes without a DC path
    to ground - a common cause of 'singular matrix' failures."""
    res = runner.simulate(_p(path, must_exist=True), floaters=True)
    return _js({k: res.get(k) for k in ("errors", "warnings", "log_text")})


@mcp.tool()
def list_traces(path: str) -> str:
    """Trace names, analysis type, point counts and .step values of a simulation
    result (path = the simulated schematic/netlist or its .qraw)."""
    return _js(qraw.summarize(qraw.read(_qraw_for(path))))


@mcp.tool()
def get_trace_data(path: str, traces: list[str], step: int = 1, max_points: int = 200,
                   x_min: float | None = None, x_max: float | None = None, plot_index: int = 0) -> str:
    """Numbers from a result: statistics (min/max/avg/rms/first/last, x at extremes)
    and up to max_points evenly spaced samples per trace, e.g. traces=['V(out)','I(R1)'].
    V(a,b) = differential voltage. AC data comes as magnitude_dB / phase_deg.
    step: which .step run (1-based). For math on traces use evaluate_expressions."""
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
    """Run .meas/.four statements on an existing result WITHOUT re-simulating (QPOST).
    Examples: ['.meas tran vpk MAX V(out)', '.meas tran vavg AVG V(out) FROM 1m TO 2m',
    '.meas ac f3db WHEN mag(V(out))=0.707', '.four 1K V(out)'].
    path: the simulated schematic/netlist or its .qraw."""
    raw = _qraw_for(path)
    try:  # accept dotted hierarchical names (I(B1.X1)) for QSPICE's bullet-separated ones
        names = [n for pl in qraw.read(raw) for n in pl.raw_names]
        statements = [qraw.to_raw_names(s, names) for s in statements]
    except Exception:
        pass
    tmp = raw.with_name(raw.stem + "_meas.cir")
    tmp.write_text("* measurements\n" + "\n".join(statements) + "\n.end\n", encoding="latin-1", errors="replace")
    res = runner.qpost(tmp, raw)
    return _js(res)


@mcp.tool()
def plot_waveforms(path: str, traces: list[str], steps: list[int] | None = None, title: str | None = None,
                   x_min: float | None = None, x_max: float | None = None, separate_panes: bool = False,
                   plot_index: int = 0, save_png: str | None = None) -> list:
    """Plot traces of a result (transient/DC: value vs x; AC: Bode magnitude+phase;
    .step runs are overlaid). Returns a picture - look at it."""
    pl = qraw.read(_qraw_for(path))[plot_index]
    for t in traces:
        try:
            pl.trace(t, 1)
        except Exception as e:
            raise ValueError(f"cannot plot '{t}' ({e}). Use trace names from list_traces (e.g. V(out), I(R1)) "
                             f"or arithmetic of them (V(out)-V(in), mag(V(out))). Available: {pl.names[:40]}")
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
    """Variable declarations for the C++/Verilog DLL (Ø-device) blocks of a schematic (QUX -DLLvariables)."""
    return runner.dll_variables(_p(path, ".qsch", must_exist=True))


@mcp.tool()
def protect_netlist(path: str, mode: str = "subcircuits") -> str:
    """Encrypt a netlist with QSPICE: mode 'subcircuits' (.subckt bodies) or
    'selections' (.prot/.unprot regions). The original is backed up first."""
    return _js(runner.protect(_p(path, must_exist=True), mode))


@mcp.tool()
def open_in_qspice(path: str | None = None, run: bool = False) -> str:
    """Open a schematic/netlist/waveform in the QSPICE GUI for the human user;
    run=True also starts the simulation there."""
    return _js(runner.open_gui(_p(path, must_exist=True) if path else None, run))


@mcp.tool()
def launch_tool(tool: str, path: str | None = None) -> str:
    """Start a bundled QSPICE utility: DIODE, MOSFET, JFET (model extraction),
    LoopTrack, SoundScope, ToneGenerator, SCPIenumerate."""
    name = tool if tool.lower().endswith(".exe") else tool + ".exe"
    found = [p.name for p in qspice_dir().glob("*.exe") if p.name.lower() == name.lower()]
    if not found:
        raise ValueError(f"unknown tool {tool}")
    return _js(runner.open_gui(_p(path) if path else None, False, tool=found[0]))


# ================================================= schematic: look & check =

@mcp.tool()
def read_schematic(path: str, include_netlist: bool = False) -> str:
    """Structured view of a .qsch: every component (ref, symbol, value, x, y,
    rotation, mirror, bbox, pins with absolute x/y and the net of each pin),
    wires, net labels, junctions, directives, comments, nets, and problems
    (unconnected pins, dangling wire ends). For a picture use render_schematic;
    for a text picture + free space around parts use canvas_map."""
    sch = _load(path)
    d = canvas.describe(sch)
    if include_netlist:
        try:
            d["qux_netlist"] = runner.read_text(runner.netlist(sch.path))
        except Exception as e:
            d["qux_netlist"] = f"error: {e}"
    return _js(d)


@mcp.tool()
def render_schematic(path: str, annotate: str = "none", grid: bool = False, show_issues: bool = False,
                     highlight: list[str] | None = None, region: list[int] | None = None, width: int = 1400,
                     save_png: str | None = None) -> list:
    """Picture of a schematic - LOOK at it after every few edits.
    annotate: 'none' (clean drawing) | 'pins' (every pin labelled REF.PIN) |
              'coords' (every pin labelled with its exact (x,y)) | 'full' (both).
    grid: overlay the 100-unit grid with coordinates (helps planning positions).
    show_issues: circle the reviewer's problems (red = error, orange = warning;
                 numbers match review_schematic's issue list).
    highlight: refs to shade, e.g. ['R1','Q2']. region: [xmin,ymin,xmax,ymax] to zoom."""
    sch = _load(path)
    marks = _marks(_review(sch)) if show_issues else None
    png = _render(sch, width=width, grid=grid, annotate=None if annotate in ("none", "", None) else annotate,
                  marks=marks, highlight=highlight, region=region)
    out: list = []
    if save_png:
        sp = _p(save_png, ".png"); sp.write_bytes(png); out.append(f"saved {sp}")
    out.append(_img(png))
    return out


@mcp.tool()
def review_schematic(path: str, intended: dict | None = None, show: bool = True, max_issues: int = 40) -> list:
    """The drafting reviewer: scores the sheet 0-100 (A >= 90) against professional
    schematic rules and lists every problem with the exact tool call that fixes it.
    Checks connectivity (unconnected pins, dangling wires, floating labels, shorted
    parts), wiring (wires through parts, overlapping wires, 4-way joins, missing or
    extra junction dots, crossings, off-grid/diagonal wires), placement (overlapping
    or crowded parts, flow right-to-left, sources upside down, ground not pointing
    down) and text (overlapping/far-away refdes and values, text on wires).
    intended: optional {ref: {pin: net}} to also verify the connections are right, e.g.
      {"R1": {"1": "in", "2": "out"}, "C1": {"1": "out", "2": "0"}} - pins that should share a net
      must be connected, different nets must not touch, and labelled nets must carry the given name
      (issues NET_SPLIT / NET_SHORT / WRONG_NET / MISSING_PART / UNKNOWN_PIN).
    Loop: review -> fix (follow 'fix') -> review again, until score >= 90 and 0 errors.
    The picture circles each issue with its number (red = error, orange = warning)."""
    from .review import review
    sch = _load(path)
    rv = review(sch, intended=intended)
    issues = rv["issues"]
    numbered = []
    k = 0
    for it in issues[:max_issues]:
        d = _short_issue(it)
        if it.get("at"):
            k += 1
            d["mark"] = k
        numbered.append(d)
    res = {"file": str(sch.path), "score": rv["score"], "grade": rv["grade"], "counts": rv["counts"],
           "issues": numbered, "more_issues": max(0, len(issues) - max_issues), "stats": rv["stats"],
           "next_steps": rv["next_steps"]}
    out: list = [_js(res)]
    if show:
        out.append(_img(_render(sch, marks=_marks(rv, max_issues))))
    return out


@mcp.tool()
def canvas_map(path: str, region: list[int] | None = None, cell: int = 100, show_values: bool = False,
               parts: bool = True) -> str:
    """A TEXT picture of the canvas (one character per grid cell) plus, for every
    part, its pins (x, y, direction, net) and how much FREE SPACE there is on each
    side. Use it to understand the spatial layout and to choose positions for new
    parts without overlaps. Legend: '#' part body, 'o' pin, '-' '|' wires, '+'
    wire corner/tee, '*' junction dot, 'x' crossing (not connected), 'G' ground,
    '[name]' net label. Rulers show x (every 10 cells) and y (every 5 rows).
    region=[xmin,ymin,xmax,ymax] to zoom; cell=50 for more detail, 200 for big sheets."""
    from . import scene, textmap
    sch = _load(path)
    sc = scene.build(sch)
    txt = textmap.ascii_map(sc, cell=cell, region=tuple(region) if region else None, show_values=show_values)
    out = {"map": txt}
    if parts:
        out["parts"] = textmap.spatial_summary(sc)
        out["extent"] = [int(v) for v in sc.bbox]
    return txt + "\n\n" + _js({k: v for k, v in out.items() if k != "map"})


@mcp.tool()
def validate_schematic(path: str) -> str:
    """Electrical sanity check: QUX netlists the sheet (authoritative), plus checks for
    unconnected pins, dangling wires, overlapping parts and directives that refer to
    nets that don't exist. For drawing quality use review_schematic."""
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

def _draw_result(res: dict, sch: Schematic, show: bool) -> list:
    res.pop("attempt_scores", None)          # internal layout costs - not meaningful to users
    v = res.get("validation", {})
    if "netlist" in v and len(v["netlist"]) > 4000:
        v["netlist"] = v["netlist"][:4000] + "\n..."
    # names the simulator will use for nets that are not labelled on the sheet
    nm = v.pop("net_map", None) or {}
    renamed = {k: str(a).upper() if str(a).lower().startswith("n") and str(a)[1:].isdigit() else a
               for k, a in nm.items() if str(a).lower() != str(k).lower() and k != "0"}
    if renamed:
        res["unlabelled_nets"] = {
            "simulation_names": renamed,
            "note": ("These spec nets are not labelled on the sheet, so QSPICE gives them automatic names "
                     "(use V(N03) etc. in plots/.meas), or label them: add_net_label(at='<REF.PIN>', name='<net>') "
                     "or list them in the spec's label_nets.")}
    rv = res.get("review") or {}
    if rv.get("score") is not None:
        res["next"] = ("Look at the picture. " + ("Score >= 90: good. " if rv["score"] >= 90 else
                       "Score < 90: call review_schematic and apply the fixes. ") +
                       "Then simulate(path).")
    out: list = [_js(res)]
    if show:
        marks = None
        if rv.get("issues"):
            marks = [{"at": i["at"], "severity": i["severity"], "label": ""} for i in rv["issues"]
                     if i.get("at") and i["severity"] == "error"] or None
        out.append(_img(_render(sch, width=1300, marks=marks)))
    return out


@mcp.tool()
def auto_draw_schematic(spec: dict, path: str = "autodraw.qsch", simulate_after: bool = False,
                        show: bool = True) -> list:
    """DRAW A WHOLE CIRCUIT from a description - the best way to create any new
    schematic. The server places parts (signal left->right, supplies top, ground
    bottom), routes wires without overlaps, adds ground symbols / net labels /
    junction dots, positions refdes+value texts, validates the result against
    QSPICE's netlister and reviews it (score). Read qspice_guide('auto-draw') for
    the full spec format. Minimal spec:
    {"components": [
       {"ref":"V1","symbol":"V","value":"SINE 0 1 1K","nodes":["in","0"]},
       {"ref":"R1","symbol":"R","value":"1K","nodes":["in","out"]},
       {"ref":"C1","symbol":"C","value":"100n","nodes":["out","0"]}],
     "directives": [".tran 5m"]}
    nodes = nets in the symbol's pin order; or "pins": {"C":"vc","B":"b","E":"0"} by pin name.
    Net "0" = ground. Optional: "title", "label_nets", "ports" (sub-sheets),
    per part "position":[x,y], "rotation", "mirror"; "options": {"effort":1-3}.
    Returns notes, validation (must be ok), review score, and a picture."""
    out_p = _p(path, ".qsch")
    if out_p.exists():
        _backup(out_p)
    res = autodraw.draw(spec, out_p, validate=True)
    sch = res.pop("_schematic")
    if simulate_after:
        res["simulation"] = _sim_summary(runner.simulate(out_p))
    return _draw_result(res, sch, show)


@mcp.tool()
def netlist_to_schematic(netlist_text: str | None = None, netlist_path: str | None = None,
                         path: str | None = None, options: dict | None = None, show: bool = True) -> list:
    """Turn a SPICE netlist (QSPICE / LTspice / generic) into a neatly drawn .qsch.
    Device types map to QSPICE symbols, .subckt instances become block symbols,
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


def _rail_names(sch: Schematic, conn: dict) -> dict:
    """Meaningful names for unnamed supply nets (a DC source between the net and
    ground): 'vcc' / 'vee' (or 'v12', 'vn5' when there are several)."""
    from .autodraw import AUTO_NET
    labels = {n.lower() for _, n, _, _ in sch.nets()}
    rails = []
    for c in sch.components():
        if c.symbol.type != "V":
            continue
        pins = {d["pin"]: d["net"] for d in conn["pins"].get(c.refdes, [])}
        plus, minus = pins.get("+"), pins.get("-")
        val = c.value.strip().split()
        if not val or val[0].upper() in ("SINE", "SIN", "PULSE", "PWL", "AC", "EXP", "SFFM"):
            continue
        try:
            v = float(re.sub(r"[^0-9.eE+-].*$", "", val[-1] if val[0].upper() == "DC" else val[0]) or "nan")
        except ValueError:
            continue
        if minus == "0" and plus and AUTO_NET.match(plus) and plus.lower() not in labels:
            rails.append((plus, v))
        elif plus == "0" and minus and AUTO_NET.match(minus) and minus.lower() not in labels:
            rails.append((minus, -v))
    out, used = {}, set(labels) | {n.lower() for n in conn["nets"]}
    for net, v in rails:
        if net in out:
            continue
        base = "vcc" if v >= 0 else "vee"
        if base in used or sum(1 for _, w in rails if (w >= 0) == (v >= 0)) > 1:
            mag = f"{abs(v):g}".replace(".", "v")
            base = ("v" if v >= 0 else "vn") + mag
        name, k = base, 2
        while name in used:
            name = f"{base}_{k}"; k += 1
        used.add(name)
        out[net] = name
    return out


@mcp.tool()
def beautify_schematic(path: str, out_path: str | None = None, options: dict | None = None, show: bool = True) -> list:
    """Re-draw a messy schematic from scratch: keeps every component, value, symbol,
    connection and directive, but recomputes placement, wiring, labels and texts.
    Writes to out_path (default <name>_neat.qsch) so the original is untouched.
    (To keep the current placement and only tidy wires, use reroute_schematic.)"""
    src = _p(path, ".qsch", must_exist=True)
    sch = Schematic.load(src)
    conn = extract(sch)
    rename = _rail_names(sch, conn)
    comps = []
    for c in sch.components():
        sym = c.symbol
        names = [p.name or str(p.index + 1) for p in sym.pins()]
        pmap = {}
        for d in conn["pins"].get(c.refdes, []):
            if sym.pins()[d["index"]].default_net and d["net"].startswith("N") and \
                    len(conn["nets"].get(d["net"], [])) <= 1:
                continue
            pmap[names[d["index"]] if names.count(names[d["index"]]) == 1 else str(d["index"] + 1)] = \
                rename.get(d["net"], d["net"])
        comps.append({"ref": c.refdes, "symbol": sym.name, "_symbol_node": sym.node, "value": c.value,
                      "attributes": [], "pins": pmap})
    spec = {"components": comps, "directives": sch.directives(), "options": options or {}}
    comments = [t["str"] for t in sch.texts() if t["flag"] != 0]
    if comments:
        spec["title"] = comments[0]
    ports = sorted({n for _, n, _, port in sch.nets() if port})
    if ports:
        spec["ports"] = ports
    dst = _p(out_path, ".qsch") if out_path else src.with_name(src.stem + "_neat.qsch")
    return auto_draw_schematic(spec, str(dst), show=show)


@mcp.tool()
def new_schematic(path: str, overwrite: bool = False) -> str:
    """Create an empty .qsch canvas (then add parts with add_component /
    place_component). For whole circuits prefer auto_draw_schematic."""
    p = _p(path, ".qsch")
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} exists (pass overwrite=true)")
    if p.exists():
        _backup(p)
    Schematic().save(p)
    return _js({"file": str(p)})


# ===================================================== schematic: edit ====

@mcp.tool()
def add_component(path: str, symbol: str, connections: dict, value: str = "", ref: str | None = None,
                  rotation: int | None = None, mirror: bool | None = None, params: dict | None = None,
                  prefer_side: str | None = None, show: bool = False) -> list:
    """ADD A PART AND WIRE IT IN ONE CALL - the easiest way to extend a schematic.
    The server finds a free, tidy spot near the things it connects to, chooses the
    orientation, draws the wires, ground symbols and labels, and places the texts.
    connections: {pin: target} for EVERY pin, where pin is a name or number of the
    new part ('1','2','B','C','E','IN+','OUT') and target is
      'R1.2'     an existing pin (wired with a clean route)
      'GND'      a ground symbol
      'out'      a net name (attached with a net label of that name)
      [x, y]     a point on the canvas (e.g. the free end of a wire)
    Example: add_component(path='amp.qsch', symbol='C', value='10u',
             connections={'1':'Q1.E', '2':'GND'})
    prefer_side: 'left'|'right'|'up'|'down' relative to its connections (optional).
    Returns the chosen position, what was drawn and a review delta."""
    from . import edit
    return _edit(path, lambda sch: edit.add_and_connect(sch, symbol, connections, value, ref, rotation, mirror,
                                                        params, prefer_side), show)


@mcp.tool()
def place_component(path: str, symbol: str, x: int, y: int, ref: str | None = None, value: str = "",
                    rotation: int = 0, mirror: bool = False, params: dict | None = None,
                    attributes: list[str] | None = None, show: bool = False) -> list:
    """Place a symbol at an exact (x, y) WITHOUT wiring it (then connect_pins /
    add_net_label / add_ground). Prefer add_component, which also wires.
    x, y snap to the 100 grid (y grows upward); rotation 0/90/180/270 counter-clockwise;
    mirror flips left-right. Check free space first with canvas_map.
    symbol may also be the full path of a sub-sheet .qsch (hierarchical block).
    Returns the absolute pin coordinates."""
    from .autodraw import normalize_value
    from .edit import nearest_free_position

    def fn(sch):
        v, note = normalize_value(value or "")
        info = canvas.place_component(sch, symbol, x, y, ref, v, rotation, mirror, params, attributes)
        if note:
            info.setdefault("notes", []).append(note)
        c = sch.component(info["ref"])
        free = nearest_free_position(sch, c.refdes, c.pos, c.code)
        if free != tuple(c.pos):
            info.setdefault("notes", []).append(
                f"WARNING: at ({c.pos[0]},{c.pos[1]}) {c.refdes} overlaps another part; nearest free position is "
                f"{list(free)} - use move_component(ref='{c.refdes}', x={free[0]}, y={free[1]})")
        return info
    return _edit(path, fn, show)


@mcp.tool()
def move_component(path: str, ref: str, x: int | None = None, y: int | None = None, dx: int | None = None,
                   dy: int | None = None, rotation: int | None = None, mirror: bool | None = None,
                   reroute: bool = True, show: bool = False) -> list:
    """Move and/or rotate a part; its wires FOLLOW (the sheet is re-routed keeping all
    connections). Give x,y (absolute) or dx,dy (relative, e.g. dx=300 moves right 3
    grid steps). If the target spot overlaps another part, the nearest free spot is
    used (reported in 'adjusted'). rotation 0/90/180/270 CCW; mirror flips left-right."""
    from . import edit
    return _edit(path, lambda sch: edit.move(sch, ref, x, y, dx, dy, rotation, mirror, reroute), show)


@mcp.tool()
def update_component(path: str, ref: str, value: str | None = None, params: dict | None = None,
                     new_ref: str | None = None, show: bool = False) -> list:
    """Change a part's value (e.g. '4.7K', 'SINE 0 1 1K', '2N3904'), parameters
    (params={'Rser':'10m'}) and/or reference designator. To move or rotate use
    move_component."""
    from .autodraw import normalize_value

    def fn(sch):
        v, note = (normalize_value(value) if value is not None else (None, None))
        info = canvas.update_component(sch, ref, v, params, new_ref)
        info.pop("note", None)
        if note:
            info["notes"] = [note]
        return info
    return _edit(path, fn, show)


@mcp.tool()
def delete_items(path: str, refs: list[str] | None = None, wires_at: list[list[int]] | None = None,
                 labels_at: list[list[int]] | None = None, show: bool = False) -> list:
    """Delete parts (and the wire stubs they leave dangling), wires that pass
    through given [x,y] points, and net labels / ground symbols at given points."""
    return _edit(path, lambda sch: canvas.delete(sch, refs or [], wires_at or [], labels_at or []), show)


def _connect(sch, a, b):
    if isinstance(b, str) and "." not in b:
        gnd = b.upper() in ("GND", "0", "GROUND")
        known = gnd or any(n.lower() == b.lower() for _, n, _, _ in sch.nets()) or \
            any(n.lower() == b.lower() for n in extract(sch)["nets"])
        r = canvas.add_label(sch, a, "GND" if gnd else b, None, False, 1)
        if not known:
            r["note"] = (f"net '{b}' did not exist before: {a} now starts a NEW net with that name. To join an "
                         f"existing node, connect to one of its pins instead (e.g. to='C1.1').")
        return r
    return canvas.connect(sch, a, b)


@mcp.tool()
def connect_pins(path: str, from_pin: str, to: str | list[int], show: bool = False) -> list:
    """Draw a clean auto-routed wire from a pin to another pin, a point, or a net.
    from_pin: 'R1.2' (REF.PIN). to: 'C1.1' (pin) | [x, y] (point) | 'GND' (ground
    symbol) | 'out' (a net label named out). The router avoids part bodies, never
    touches other nets, and adds T-junction dots. If no route is found, connect
    with net labels (to='<net name>') on both pins instead."""
    return _edit(path, lambda sch: _connect(sch, from_pin, to), show)


@mcp.tool()
def connect_many(path: str, connections: list[list], show: bool = True) -> list:
    """Several connections at once: [["R1.2","C1.1"], ["C1.2","GND"], ["U1.OUT","out"],
    ["R2.1",[400,-300]]]. Failed ones are listed - connect those with net labels."""
    def fn(sch):
        done, failed = [], []
        for pair in connections:
            a, b = pair[0], pair[1]
            try:
                done.append({"from": a, "to": b, **_connect(sch, a, b)})
            except Exception as e:
                failed.append({"from": a, "to": b, "error": str(e)})
        return {"routed": done, "failed": failed}
    return _edit(path, fn, show)


@mcp.tool()
def add_wire(path: str, points: list[list[int]], show: bool = False) -> list:
    """Draw an explicit wire through [x, y] points (orthogonal segments, snapped to the
    grid). Wires connect only at their END points (or where an end touches another
    wire); crossing wires do NOT connect. Prefer connect_pins (auto-routed)."""
    return _edit(path, lambda sch: {"segments_added": canvas.add_polyline(sch, points)}, show)


@mcp.tool()
def add_net_label(path: str, at: str | list[int], name: str, direction: str | None = None,
                  stub: int = 1, port: bool = False, show: bool = False) -> list:
    """Attach a net label to a pin ('R1.1') or point [x,y]. All labels with the same
    name are connected (use this instead of long wires, for supply rails and for
    nets referenced in .meas/plots). stub=1 draws a short wire first in `direction`
    (U/D/L/R; default = the way the pin points). port=True makes a hierarchical port
    (sub-sheets). name 'GND' draws a ground symbol."""
    return _edit(path, lambda sch: canvas.add_label(sch, at, name, direction, port, stub), show)


@mcp.tool()
def add_ground(path: str, at: str | list[int], direction: str | None = None, stub: int = 1,
               show: bool = False) -> list:
    """Attach a ground symbol to a pin ('C1.2') or point. It always points down
    (sideways pins get a short L-shaped stub). Use one ground symbol per return
    point instead of long ground wires."""
    return _edit(path, lambda sch: canvas.add_label(sch, at, "GND", direction, False, stub), show)


@mcp.tool()
def set_directives(path: str, directives: list[str], replace: bool = True, x: int | None = None,
                   y: int | None = None) -> list:
    """Set the SPICE commands on the sheet: analyses (.tran 10m, .ac dec 100 1 1Meg,
    .dc V1 0 5 0.1, .op, .noise), .step, .param, .meas, .model, .lib/.include, .ic,
    .options. replace=True removes the existing directive block first.
    Placed below-left of the circuit unless x, y are given."""
    def fn(sch):
        if replace:
            for t in sch.root.findall("text"):
                if int(t.tokens[4]) == 0:
                    sch.remove(t)
        from .render import schematic_bbox
        bb = schematic_bbox(sch)
        pos = (x if x is not None else int(bb[0]) + 200, y if y is not None else int(bb[1]) - 100)
        from .autodraw import wrap_directives
        sch.add_text(pos, "\n".join(wrap_directives(directives)), directive=True, size=1, just=5)
        return {"directives": sch.directives()}
    return _edit(path, fn, False)


@mcp.tool()
def add_comment(path: str, text: str, x: int, y: int, size: float = 1.0) -> list:
    """Place a comment (non-SPICE text, e.g. a title or a note) on the sheet;
    (x, y) is its top-left corner."""
    return _edit(path, lambda sch: (sch.add_text((x, y), text, directive=False, size=size, just=5), {})[1], False)


@mcp.tool()
def reroute_schematic(path: str, show: bool = True) -> list:
    """Tidy ALL wiring: rips up every wire, label and junction and redraws them
    cleanly with the router, keeping every part where it is and every connection
    as it was. Also re-places refdes/value texts. Use it when review_schematic
    reports wires through parts, overlapping wires, 4-way joins or messy routing."""
    from . import edit
    return _edit(path, lambda sch: edit.reroute(sch), show)


@mcp.tool()
def fix_text_placement(path: str, refs: list[str] | None = None, show: bool = False) -> list:
    """Re-place reference designators and values (and parameters) of the given parts
    (default: all) at the best conventional spot that doesn't overlap wires, parts
    or other texts. Fixes TEXT_OVERLAP / TEXT_ON_WIRE / TEXT_ON_PART / TEXT_FAR_FROM_PART."""
    from . import labels as textplace
    return _edit(path, lambda sch: {"texts": textplace.optimize(sch, refs)}, show)


@mcp.tool()
def undo_last_edit(path: str, steps: int = 1) -> list:
    """Restore the schematic to how it was before the last edit (or `steps` edits).
    Every editing tool keeps a history (30 versions per file)."""
    p = _p(path, ".qsch", must_exist=True)
    d = _history_dir(p)
    hist = sorted(d.glob("*.qsch"))
    if len(hist) < steps or steps < 1:
        raise ValueError(f"only {len(hist)} earlier version(s) available")
    target = hist[-steps]
    shutil.copy2(target, p)
    for f in hist[-steps:]:
        f.unlink()
    sch = Schematic.load(p)
    rv = _review(sch)
    return [_js({"file": str(p), "restored_version_from": time.ctime(float(target.stem)),
                 "versions_left": len(hist) - steps, "score": rv.get("score"), "grade": rv.get("grade")}),
            _img(_render(sch, width=1100))]


# ================================================================ prompts ==

@mcp.prompt()
def draw_circuit(description: str) -> str:
    """Workflow prompt: draw a circuit from a plain-language description."""
    return (f"Draw this circuit as a QSPICE schematic: {description}\n\n"
            "1. Call qspice_guide('drafting') and qspice_guide('auto-draw') and follow them.\n"
            "2. Write the auto_draw_schematic spec: every part with ref, symbol, value and nets; net '0' is ground;"
            " meaningful net names (in, out, vcc...); an analysis directive.\n"
            "3. Call auto_draw_schematic. validation.ok must be true.\n"
            "4. Call review_schematic; apply each fix; repeat until score >= 90 with 0 errors.\n"
            "5. render_schematic and look at the picture; describe anything that looks wrong and fix it.\n"
            "6. simulate and report the key results.")


@mcp.prompt()
def review_and_fix(path: str) -> str:
    """Workflow prompt: iterate review -> fix until the schematic is clean."""
    return (f"Make the schematic {path} clean and professional.\n"
            "Loop (max 8 rounds): review_schematic(path) -> for each error/warning apply its 'fix' tool call "
            "(connectivity first, then overlaps/moves, then reroute_schematic, then fix_text_placement) -> "
            "render_schematic(path, show_issues=true) and look. Stop when score >= 90, 0 errors, and the picture "
            "looks tidy. If an edit makes the score worse, call undo_last_edit.")


@mcp.prompt()
def simulate_and_report(path: str, goal: str = "") -> str:
    """Workflow prompt: simulate a schematic and report the results."""
    return (f"Simulate {path}. {goal}\n"
            "1. qspice_guide('simulation'). Make sure the sheet has the right analysis directive (set_directives).\n"
            "2. simulate(path). If ok is false, follow qspice_guide('troubleshooting').\n"
            "3. plot_waveforms for the important nodes and LOOK at the plot; use measure for exact numbers.\n"
            "4. Report the numbers with units and say whether they match expectations.")


# ============================================================== resources ==

@mcp.resource("qspice://skills")
def skills_index() -> str:
    """Index of the QSPICE skills (guides)."""
    return skills.index_text()


@mcp.resource("qspice://skills/{name}")
def skill_resource(name: str) -> str:
    """One QSPICE skill (guide) as markdown."""
    return skills.get(name)


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
