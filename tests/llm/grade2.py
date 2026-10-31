"""Objective grading of round-2 LLM runs (T11-T20).   usage: grade2.py <round> [run ...]"""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import json, sys, traceback
from pathlib import Path
from grade import base, devices, meas, sim, ROOT          # noqa
from qspice_mcp import qraw, runner

REF = json.loads((ROOT.parent / "ref2" / "reference.json").read_text())
SWAP = set("RCL")


def match(devs, spec):
    """Unify expected node classes with actual netlist nodes; 2-pin R/C/L may be
    reversed. Returns {class: node} or None."""
    items = list(spec.items())

    def rec(k, fwd, back):
        if k == len(items):
            return fwd
        ref, classes = items[k]
        toks = devs.get(ref.upper())
        if not toks:
            return None
        orders = [toks[:len(classes)]]
        if ref[0].upper() in SWAP and len(classes) == 2:
            orders.append([toks[1], toks[0]])
        for nodes in orders:
            f2, b2, ok = dict(fwd), dict(back), True
            for cls, n in zip(classes, nodes):
                if cls == "0":
                    if n != "0":
                        ok = False
                        break
                    continue
                if f2.setdefault(cls, n) != n or b2.setdefault(n, cls) != cls:
                    ok = False
                    break
            if ok:
                r = rec(k + 1, f2, b2)
                if r is not None:
                    return r
        return None
    return rec(0, {}, {})


def op_v(p, node):
    pl = qraw.read(Path(p).with_suffix(".qraw"))[0]
    return float(complex(pl.trace(f"V({node})", 1)[0]).real)


def near(v, ref, rel):
    return v is not None and abs(v - ref) <= abs(ref) * rel


def used_tools(run):
    return sorted({f.name.split("_")[-3] if f.name.count("_") >= 3 else f.name for f in (run / "images").glob("*.png")}) \
        if (run / "images").exists() else []


def g_T11(d, p, run):
    devs = devices(d["netlist"])
    m = match(devs, {"V1": ["in", "0"], "R1": ["in", "out"], "R2": ["out", "0"], "C1": ["out", "0"]})
    d["topology_ok"] = m is not None
    ok, err = sim(p)
    v = op_v(p, m["out"]) if ok and m else None
    imgs = [f.name for f in (run / "images").glob("*.png")] if (run / "images").exists() else []
    d["forbidden_tools"] = [n for n in imgs if any(k in n for k in ("auto_draw", "netlist_to_schematic", "beautify"))]
    d["sim_ok"], d["metric"] = ok, {"vout": v}
    d["pass_func"] = d["topology_ok"] and near(v, 3.19728, .005) and not d["forbidden_tools"]
    return d


def g_T12(d, p, run):
    devs = devices(d["netlist"])
    spec = {"V1": ["in", "0"], "C1": ["in", "b1"], "R1": ["vcc", "b1"], "R2": ["b1", "0"], "Q1": ["c1", "b1", "e1"],
            "R3": ["vcc", "c1"], "R4": ["e1", "0"], "C2": ["e1", "0"], "C3": ["c1", "b2"], "R5": ["vcc", "b2"],
            "R6": ["b2", "0"], "Q2": ["c2", "b2", "e2"], "R7": ["vcc", "c2"], "R8": ["e2", "0"], "C4": ["e2", "0"],
            "C5": ["c2", "out"], "RL": ["out", "0"], "V2": ["vcc", "0"]}
    m = match(devs, spec)
    d["topology_ok"] = m is not None
    ok, err = sim(p)
    mm = meas(p, [f".meas vpp PP V({m['out']}) FROM 15m TO 20m"]) if ok and m else {}
    d["sim_ok"], d["metric"] = ok, mm
    d["pass_func"] = d["topology_ok"] and near(mm.get("vpp"), REF["T12"]["meas"]["results"]["vpp"], .03)
    return d


def g_T13(d, p, run):
    devs = devices(d["netlist"])
    m = match(devs, {"R1": ["in", "out"], "C1": ["out", "0"]})
    d["topology_ok"] = m is not None
    d["has_step"] = ".step" in d["netlist"].lower()
    ok, err = sim(p)
    mm = {}
    if ok and m:
        tmp = Path(p).with_name(Path(p).stem + "_grade.cir")
        tmp.write_text(f"* g\n.meas f3db WHEN mag(V({m['out']}))=.7071\n.end\n", encoding="latin-1")
        q = runner.qpost(tmp, Path(p).with_suffix(".qraw"))
        v = q.get("results", {}).get("f3db")
        mm["f3db"] = [x["value"] if isinstance(x, dict) else x for x in v] if isinstance(v, list) else v
    d["sim_ok"], d["metric"] = ok, mm
    ref = REF["T13"]["meas"]["results"]["f3db"]
    got = mm.get("f3db")
    d["pass_func"] = bool(d["topology_ok"] and d["has_step"] and isinstance(got, list) and len(got) == 3 and
                          all(near(g, r, .02) for g, r in zip(sorted(got), sorted(ref))))
    return d


def g_T14(d, p, run):
    devs = devices(d["netlist"])
    xs = [(r, t) for r, t in devs.items() if any("SHUNT431" in s.upper() for s in t)]
    d["x_line"] = xs
    ok, err = sim(p)
    v = op_v(p, xs[0][1][0]) if ok and xs else None
    d["sim_ok"], d["metric"], d["sim_err"] = ok, {"v_out": v}, err
    good_pins = bool(xs) and xs[0][1][1] == "0"
    d["pass_func"] = good_pins and near(v, REF["T14"]["v_out"], .005) and ".subckt shunt431" in d["netlist"].lower() \
        or (good_pins and near(v, REF["T14"]["v_out"], .005) and "shunt431" in d["netlist"].lower())
    return d


def g_T15(d, p, run):
    devs = devices(d["netlist"])
    bs = [(r, t) for r, t in devs.items() if r.startswith("B")]
    d["b_line"] = bs
    ok, err = sim(p)
    mm = meas(p, [f".meas vrms RMS V({bs[0][1][0]})"]) if ok and bs else {}
    d["sim_ok"], d["metric"] = ok, mm
    d["pass_func"] = bool(bs) and near(mm.get("vrms"), 0.5, .02)
    return d


def g_T16(d, p, run):
    devs = devices(d["netlist"])
    ms = [(r, t) for r, t in devs.items() if r.startswith("M")]
    d["m_line"] = ms
    ok, err = sim(p)
    mm = {}
    if ok and ms:
        dn = ms[0][1][0]
        mm = meas(p, [f".meas vmin MIN V({dn}) FROM 100u TO 300u", f".meas vmax MAX V({dn}) FROM 100u TO 300u"])
    d["sim_ok"], d["metric"] = ok, mm
    nm = Path(r"C:\Program Files\QSPICE\NMOS.txt").read_text(encoding="latin-1", errors="replace").upper()
    toks = [t.upper() for t in (ms[0][1] if ms else [])]
    lib = [t for t in toks if f".MODEL {t} " in nm or f".MODEL {t}\t" in nm]
    d["model"], d["model_in_library"] = (lib[0] if lib else (toks[-1] if toks else "")), bool(lib)
    d["pass_func"] = bool(ms) and (mm.get("vmin") or 99) < 0.6 and (mm.get("vmax") or 0) > 11.5 and d["model_in_library"]
    return d


def g_T17(d, p, run):
    csvs = sorted(run.glob("*.csv"))
    d["csv"] = [c.name for c in csvs]
    rows = 0
    if csvs:
        rows = sum(1 for _ in open(csvs[0], encoding="utf-8", errors="replace"))
    d["csv_rows"] = rows
    d["pass_func"] = rows > 50
    d["note"] = "numbers graded from report"
    return d


def g_T18(d, p, run):
    devs = devices(d["netlist"])
    m = match(devs, {"V1": ["in", "0"], "R1": ["in", "a"], "C1": ["a", "0"], "R2": ["a", "out"], "C2": ["out", "0"]})
    d["topology_ok"] = m is not None
    ok, err = sim(p)
    mm = meas(p, [f".meas f3db WHEN mag(V({m['out']}))=.7071"]) if ok and m else {}
    d["sim_ok"], d["metric"] = ok, mm
    d["values_ok"] = [devs.get(k, ["", "", ""])[2].upper() for k in ("R1", "C1", "R2", "C2")] == ["1K", "100N", "10K", "10N"]
    d["pass_func"] = d["topology_ok"] and near(mm.get("f3db"), REF["T18"]["meas"]["results"]["f3db"], .02)
    return d


def g_T19(d, p, run):
    devs = devices(d["netlist"])
    m = match(devs, {"R1": ["in", "n1"], "R2": ["n1", "n2"], "C1": ["n1", "out"], "C2": ["n2", "0"],
                     "U1": ["vcc", "vee", "out", "out", "n2"]})
    d["topology_ok"] = m is not None
    ok, err = sim(p)
    mm = meas(p, [f".meas f3db WHEN mag(V({m['out']}))=.7071"]) if ok and m else {}
    d["sim_ok"], d["metric"] = ok, mm
    d["pass_func"] = d["topology_ok"] and near(mm.get("f3db"), REF["T19"]["meas"]["results"]["f3db"], .02)
    return d


def g_T20(d, p, run):
    devs = devices(d["netlist"])
    spec = {"V1": ["in", "0"], "C1": ["in", "b"], "R1": ["vcc", "b"], "R2": ["b", "0"], "Q1": ["c", "b", "e"],
            "R3": ["vcc", "c"], "R4": ["e", "0"], "C2": ["e", "0"], "C3": ["c", "out"], "RL": ["out", "0"], "V2": ["vcc", "0"]}
    m = match(devs, spec)
    d["topology_ok"] = m is not None
    d["r3"] = devs.get("R3", ["", "", ""])[2]
    ok, err = sim(p)
    mm = meas(p, [f".meas vpp PP V({m['out']}) FROM 5m TO 10m"]) if ok and m else {}
    d["sim_ok"], d["metric"] = ok, mm
    d["pass_func"] = d["topology_ok"] and d["r3"].upper() in ("3.3K", "3K3") and \
        near(mm.get("vpp"), REF["T20"]["meas"]["results"]["vpp"], .03)
    return d


FILES = {"T17": "bridge.qsch", "T20": "amp.qsch"}


def grade_run(run: Path):
    t = run.name.split("_")[0]
    if f"g_{t}" not in globals():
        return None                     # not a round-2 task (see grade.py)
    p = run / FILES.get(t, f"{t}.qsch")
    d = base(p)
    if not d.get("exists"):
        d["note"] = f"missing {p.name}; files: {[c.name for c in sorted(run.glob('*.qsch'))]}"
        return {"run": run.name, **d}
    try:
        d = globals()[f"g_{t}"](d, p, run)
    except Exception:
        d["grader_error"] = traceback.format_exc()[-700:]
    d.pop("netlist", None)
    d["pass_draw"] = (d.get("score") or 0) >= 90 and d.get("errors", 1) == 0
    rep = run / "report.md"
    d["has_report"] = rep.exists()
    return {"run": run.name, **d}


if __name__ == "__main__":
    rnd = sys.argv[1]
    runs = [ROOT / rnd / r for r in sys.argv[2:]] or sorted(x for x in (ROOT / rnd).iterdir() if x.is_dir())
    allr = []
    for r in runs:
        g = grade_run(r)
        if g is None:
            continue
        allr.append(g)
        print(json.dumps(g, default=str))
    out = ROOT / rnd / ("grades.json" if not sys.argv[2:] else "grades_partial.json")
    out.write_text(json.dumps(allr, indent=1, default=str))
