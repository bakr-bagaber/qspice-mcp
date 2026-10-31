"""Objective grading of the LLM test runs.   usage: grade.py <round> [run ...]"""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import json, re, sys, traceback
from pathlib import Path
from qspice_mcp import runner, qraw
from qspice_mcp.model import Schematic
from qspice_mcp.review import review
from qspice_mcp.connectivity import extract

ROOT = Path(str(WORK / "runs"))


def netlist(p):
    return runner.read_text(runner.netlist(p))


def devices(txt):
    """{REF: (nodes, rest)} from a QUX netlist (top level only)."""
    out, in_sub = {}, 0
    for line in txt.splitlines():
        l = line.strip()
        if not l or l.startswith("*"):
            continue
        if l.lower().startswith(".subckt"):
            in_sub += 1; continue
        if l.lower().startswith(".ends"):
            in_sub -= 1; continue
        if in_sub or l.startswith(".") or l.startswith("+"):
            continue
        toks = l.split()
        ref = toks[0]
        ref = ref.split("\u00b4")[-1]
        out[ref.upper()] = toks[1:]
    return out


def meas(p, stmts):
    raw = Path(p).with_suffix(".qraw")
    tmp = raw.with_name(raw.stem + "_grade.cir")
    tmp.write_text("* g\n" + "\n".join(stmts) + "\n.end\n", encoding="latin-1")
    r = runner.qpost(tmp, raw)
    res = {}
    for k, v in r.get("results", {}).items():
        res[k] = v["value"] if isinstance(v, dict) else v
    return res


def sim(p):
    r = runner.simulate(p)
    return bool(r.get("qraw")) and not r.get("errors"), r.get("errors")


def base(p):
    d = {"file": str(p), "exists": Path(p).exists()}
    if not d["exists"]:
        return d
    sch = Schematic.load(p)
    rv = review(sch)
    d["score"] = rv["score"]
    d["errors"] = rv["counts"]["error"]
    d["warnings"] = rv["counts"]["warning"]
    d["issues"] = [f"{i['severity']}:{i['code']}" for i in rv["issues"]][:12]
    try:
        d["netlist"] = netlist(p)
    except Exception as e:
        d["netlist"] = f"ERR {e}"
    return d


def opamp_out(devs):
    for ref, toks in devs.items():
        if "RROPAMP" in " ".join(toks).upper():
            return toks[2]  # Vdd Vss OUT IN- IN+
    return None


def node_of_value(devs, letter, value, other="0"):
    for ref, toks in devs.items():
        if ref.startswith(letter) and len(toks) >= 3 and toks[2].upper() == value.upper():
            a, b = toks[0], toks[1]
            return b if a == other else a
    return None


def g_T1(d, p):
    devs = devices(d["netlist"])
    rs = [t for r, t in devs.items() if r.startswith("R")]
    cs = [t for r, t in devs.items() if r.startswith("C")]
    out = None
    for t in cs:
        if "0" in t[:2]:
            out = t[0] if t[1] == "0" else t[1]
    ok, err = sim(p)
    m = meas(p, [f".meas f3db WHEN mag(V({out}))=.7071"]) if out and ok else {}
    d["sim_ok"], d["metric"] = ok, m
    f = m.get("f3db")
    d["pass_func"] = f is not None and 850 <= f <= 1150
    return d


def g_T2(d, p):
    devs = devices(d["netlist"])
    out = opamp_out(devs)
    ok, err = sim(p)
    m = meas(p, [f".meas vpk MAX V({out})"]) if out and ok else {}
    d["sim_ok"], d["metric"], d["out"] = ok, m, out
    v = m.get("vpk")
    d["pass_func"] = v is not None and 0.45 <= v <= 0.55
    return d


def g_T3(d, p):
    devs = devices(d["netlist"])
    out = node_of_value(devs, "R", "10")
    ok, err = sim(p)
    m = {}
    if out and ok:
        pl = qraw.read(Path(p).with_suffix(".qraw"))[0]
        import numpy as np
        x = np.real(pl.axis(1)); tend = float(x[-1])
        m = meas(p, [f".meas vavg AVG V({out}) FROM {tend - .5e-3} TO {tend}"])
        m["tend"] = tend
    d["sim_ok"], d["metric"], d["out"] = ok, m, out
    v = m.get("vavg")
    d["pass_func"] = v is not None and 10.5 <= v <= 13.5
    return d


def g_T4(d, p):
    devs = devices(d["netlist"])
    ok, err = sim(p)
    m = meas(p, [".meas g FIND mag(V(out)) AT 1K"]) if ok else {}
    d["sim_ok"], d["metric"] = ok, m
    v = m.get("g")
    d["pass_func"] = v is not None and abs(v - .474459) < .015
    return d


def _partition(devs, spec):
    """spec: {ref: [node-class per pin]} -> True if node classes map 1:1 to netlist nodes."""
    fwd, back = {}, {}
    for ref, classes in spec.items():
        toks = devs.get(ref)
        if not toks:
            return False, f"missing {ref}"
        for k, cls in enumerate(classes):
            n = toks[k]
            if cls == "0":
                if n != "0":
                    return False, f"{ref} pin {k + 1} should be ground, is {n}"
                continue
            if fwd.setdefault(cls, n) != n or back.setdefault(n, cls) != cls:
                return False, f"{ref} pin {k + 1}: {cls} -> {n} conflicts"
    return True, ""


def g_T5(d, p):
    devs = devices(d["netlist"])
    spec = {"V1": ["in", "0"], "C1": ["in", "b"], "R1": ["vcc", "b"], "R2": ["b", "0"], "Q1": ["c", "b", "e"],
            "R3": ["vcc", "c"], "R4": ["e", "0"], "C2": ["e", "0"], "V2": ["vcc", "0"]}
    ok_t, why = _partition(devs, spec)
    d["topology_ok"], d["topology_note"] = ok_t, why
    ok, err = sim(p)
    c = devs.get("Q1", [None])[0]
    m = meas(p, [f".meas vpp PP V({c}) FROM 5m TO 10m"]) if ok and c else {}
    d["sim_ok"], d["metric"] = ok, m
    d["pass_func"] = ok_t and ok
    return d


def g_T6(d, p):
    devs = devices(d["netlist"])
    x = [(r, t) for r, t in devs.items() if any("ACME_LDO" in s.upper() for s in t)]
    d["x_line"] = x
    ok, err = sim(p)
    out = x[0][1][3] if x else None
    m = {}
    if ok and out:
        m = meas(p, [f".meas vend FIND V({out}) AT .99m"])
    d["sim_ok"], d["metric"], d["sim_err"] = ok, m, err
    v = m.get("vend")
    d["pass_func"] = bool(x) and v is not None and 3.2 <= v <= 3.35 and ".lib" in d["netlist"].lower()
    return d


def g_T7(d, p):
    txt = d["netlist"]
    xs = [l for l in txt.splitlines() if l.strip().upper().startswith("X") and "RCSTAGE" in l.upper()]
    d["x_lines"] = xs
    ok, err = sim(p)
    m = meas(p, [".meas f3db WHEN mag(V(out))=.7071"]) if ok else {}
    d["sim_ok"], d["metric"] = ok, m
    f = m.get("f3db")
    d["pass_func"] = len(xs) == 2 and ".subckt rcstage" in txt.lower() and f is not None and abs(f - 595.6) < 20
    return d


def g_T8(d, p):
    devs = devices(d["netlist"])
    spec = {"V1": ["vin", "0"], "R1": ["vin", "ref"], "D1": ["0", "ref"], "Q1": ["vin", "ref", "out"],
            "R2": ["out", "0"], "C1": ["out", "0"]}
    ok_t, why = _partition(devs, spec)
    d["topology_ok"], d["topology_note"] = ok_t, why
    ok, err = sim(p)
    v = None
    if ok:
        pl = qraw.read(Path(p).with_suffix(".qraw"))[0]
        q1 = devs.get("Q1")
        try:
            v = float(complex(pl.trace(f"V({q1[2]})", 1)[0]).real) if q1 else None
        except Exception:
            v = None
    d["sim_ok"], d["metric"] = ok, {"vout": v}
    d["pass_func"] = ok_t and v is not None and abs(v - 5.495) < .06
    return d


def g_T10(d, p):
    devs = devices(d["netlist"])
    diodes = [(r, t) for r, t in devs.items() if r.startswith("D")]
    d["diode"] = diodes
    out = node_of_value(devs, "R", "1K")
    ok, err = sim(p)
    m = meas(p, [f".meas vavg AVG V({out}) FROM 60m TO 100m"]) if ok and out else {}
    d["sim_ok"], d["metric"] = ok, m
    v = m.get("vavg")
    d["pass_func"] = v is not None and 7.5 <= v <= 9.9
    return d


GRADERS = {f"T{k}": globals().get(f"g_T{k}") for k in range(1, 11)}


def grade_run(run: Path):
    t = run.name.split("_")[0]
    if t not in GRADERS:
        return None                     # not a round-1 task (see grade2.py)
    p = run / ("rc_stage.qsch" if t == "T4" else f"{t}.qsch")
    if t == "T9":
        return {"run": run.name, "note": "graded from the agent's report"}
    d = base(p)
    if not d.get("exists"):
        cands = sorted(run.glob("*.qsch"))
        d["note"] = f"missing {p.name}; files: {[c.name for c in cands]}"
        return {"run": run.name, **d}
    try:
        d = GRADERS[t](d, p)
    except Exception as e:
        d["grader_error"] = traceback.format_exc()[-600:]
    d.pop("netlist", None)
    d["pass_draw"] = (d.get("score") or 0) >= 90 and d.get("errors", 1) == 0
    return {"run": run.name, **d}


if __name__ == "__main__":
    rnd = sys.argv[1]
    runs = [ROOT / rnd / r for r in sys.argv[2:]] or sorted((ROOT / rnd).iterdir())
    allr = []
    for r in runs:
        if not r.is_dir():
            continue
        g = grade_run(r)
        if g is None:
            continue
        allr.append(g)
        print(json.dumps(g, default=str))
    (ROOT / rnd / "grades.json").write_text(json.dumps(allr, indent=1, default=str))
