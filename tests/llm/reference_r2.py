"""Ground-truth numbers for round-2 tasks (simulated with QSPICE through the server code)."""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import json, sys
from pathlib import Path
from qspice_mcp import autodraw, runner

ROOT = Path(str(WORK))
OUT = ROOT / "ref2"
OUT.mkdir(exist_ok=True)
M3904 = (".model 2N3904 NPN Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259 Ise=6.734f Ikf=66.78m "
         "Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 "
         "Vje=.75 Tr=239.5n Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10")
R, C, V, D = "R", "C", "V", "D"


def comp(ref, sym, val, nodes=None, pins=None):
    d = {"ref": ref, "symbol": sym, "value": val}
    if nodes:
        d["nodes"] = nodes
    if pins:
        d["pins"] = pins
    return d


CASES = {
    "T11": ([comp("V1", V, "10", ["in", "0"]), comp("R1", R, "10K", ["in", "out"]), comp("R2", R, "4.7K", ["out", "0"]),
             comp("C1", C, "100n", ["out", "0"])], [".op"]),
    "T12": ([comp("V1", V, "SINE 0 100u 1K", ["in", "0"]), comp("C1", C, "10u", ["in", "b1"]),
             comp("R1", R, "47K", ["vcc", "b1"]), comp("R2", R, "10K", ["b1", "0"]),
             comp("Q1", "NPN", "2N3904", pins={"C": "c1", "B": "b1", "E": "e1"}),
             comp("R3", R, "4.7K", ["vcc", "c1"]), comp("R4", R, "1K", ["e1", "0"]), comp("C2", C, "100u", ["e1", "0"]),
             comp("C3", C, "10u", ["c1", "b2"]), comp("R5", R, "47K", ["vcc", "b2"]), comp("R6", R, "10K", ["b2", "0"]),
             comp("Q2", "NPN", "2N3904", pins={"C": "c2", "B": "b2", "E": "e2"}),
             comp("R7", R, "4.7K", ["vcc", "c2"]), comp("R8", R, "1K", ["e2", "0"]), comp("C4", C, "100u", ["e2", "0"]),
             comp("C5", C, "10u", ["c2", "out"]), comp("RL", R, "10K", ["out", "0"]), comp("V2", V, "12", ["vcc", "0"])],
            [".tran 20m", M3904, ".meas vpp PP V(out) FROM 15m TO 20m", ".meas vin PP V(in) FROM 15m TO 20m"]),
    "T13": ([comp("V1", V, "AC 1", ["in", "0"]), comp("R1", R, "1K", ["in", "out"]), comp("C1", C, "{Cx}", ["out", "0"])],
            [".param Cx=100n", ".step param Cx list 10n 100n 1u", ".ac dec 50 1 1Meg",
             ".meas f3db WHEN mag(V(out))=.7071"]),
    "T14": None,   # needs the subckt text (below)
    "T15": ([comp("V1", V, "SINE 0 1 1K", ["a", "0"]), comp("V2", V, "SINE 0 1 10K", ["b", "0"]),
             comp("B1", "B1", "V=V(a)*V(b)", ["out", "0"]), comp("R1", R, "1K", ["out", "0"])],
            [".tran 0 2m 0 1u", ".meas vrms RMS V(out)", ".meas vpk MAX V(out)"]),
    "T18": ([comp("V1", V, "AC 1", ["in", "0"]), comp("R1", R, "1K", ["in", "a"]), comp("C1", C, "100n", ["a", "0"]),
             comp("R2", R, "10K", ["a", "out"]), comp("C2", C, "10n", ["out", "0"])],
            [".ac dec 100 10 1Meg", ".meas f3db WHEN mag(V(out))=.7071"]),
    "T19": ([comp("V1", V, "AC 1", ["in", "0"]), comp("R1", R, "4.7K", ["in", "n1"]), comp("R2", R, "4.7K", ["n1", "n2"]),
             comp("C1", C, "47n", ["n1", "out"]), comp("C2", C, "22n", ["n2", "0"]),
             comp("U1", "OpAmp", "", pins={"IN+": "n2", "IN-": "out", "OUT": "out", "Vdd": "vcc", "Vss": "vee"}),
             comp("V2", V, "12", ["vcc", "0"]), comp("V3", V, "-12", ["vee", "0"])],
            [".ac dec 100 10 100K", ".meas f3db WHEN mag(V(out))=.7071", ".meas ph1k FIND ph(V(out)) AT 1K",
             ".meas g1k FIND mag(V(out)) AT 1K"]),
    "T20": ([comp("V1", V, "SINE 0 10m 1K", ["in", "0"]), comp("C1", C, "10u", ["in", "b"]),
             comp("R1", R, "47K", ["vcc", "b"]), comp("R2", R, "10K", ["b", "0"]),
             comp("Q1", "NPN", "2N3904", pins={"C": "c", "B": "b", "E": "e"}),
             comp("R3", R, "3.3K", ["vcc", "c"]), comp("R4", R, "1K", ["e", "0"]), comp("C2", C, "100u", ["e", "0"]),
             comp("C3", C, "10u", ["c", "out"]), comp("RL", R, "10K", ["out", "0"]), comp("V2", V, "12", ["vcc", "0"])],
            [".tran 10m", M3904, ".meas vpp PP V(out) FROM 5m TO 10m"]),
}
SHUNT = """.subckt SHUNT431 K A REF
* behavioural 2.495 V shunt reference (TL431-like)
B1 K A I=0.1*(1+tanh(1000*(V(REF,A)-2.495)))
Rleak K A 10Meg
.ends SHUNT431
"""
res = {}
for name, case in CASES.items():
    if case is None:
        continue
    comps, dirs = case
    p = OUT / f"{name}.qsch"
    autodraw.draw({"title": name, "components": comps, "directives": dirs}, p)
    r = runner.simulate(p)
    res[name] = {"errors": r.get("errors"), "meas": r.get("measurements")}
    print(name, json.dumps(res[name], default=str)[:400], flush=True)

# T14 as a plain netlist (no drawing needed for the number)
cir = OUT / "T14.cir"
cir.write_text("* T14\nV1 vin 0 12\nR1 vin out 470\nX1 out 0 ref SHUNT431\nR2 out ref 10K\nR3 ref 0 10K\nRL out 0 1K\n"
               + SHUNT + ".op\n.end\n", encoding="latin-1")
r = runner.simulate(cir)
print("T14 sim", r.get("errors"), r.get("qraw"))
from qspice_mcp import qraw
pl = qraw.read(Path(r["qraw"]))[0]
v_out = float(complex(pl.trace("V(out)", 1)[0]).real)
print("T14 traces", pl.names if hasattr(pl, "names") else "")
i_r1 = (12 - v_out) / 470
res["T14"] = {"v_out": v_out, "i_r1": i_r1}
print("T14", res["T14"])

# T17: numbers from the fixture's existing results
fix = ROOT / "fixtures2" / "bridge.qsch"
tmp = fix.with_name("bridge_ref.cir")
tmp.write_text("* ref\n.meas vavg AVG V(out) FROM 60m TO 100m\n.meas vpp PP V(out) FROM 60m TO 100m\n"
               ".meas irms RMS I(V1) FROM 60m TO 100m\n.end\n", encoding="latin-1")
q = runner.qpost(tmp, fix.with_suffix(".qraw"))
res["T17"] = q.get("results")
print("T17", res["T17"])
(OUT / "reference.json").write_text(json.dumps(res, indent=1, default=str))
