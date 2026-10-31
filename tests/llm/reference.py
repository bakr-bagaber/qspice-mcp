"""Reference values for grading (computed by simulating known-good circuits)."""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import json, sys
from pathlib import Path
from qspice_mcp import runner
R = Path(str(WORK / "ref")); R.mkdir(exist_ok=True)

def sim(name, text):
    p = R / f"{name}.cir"
    p.write_text(text, encoding="latin-1")
    res = runner.simulate(p)
    return (res.get("measurements") or {}).get("results", {}), res.get("errors")

out = {}
out["T4"] = sim("t4", """* two-stage RC + 10K load
V1 in 0 SINE 0 1 1K AC 1
R1 in mid 1K
C1 mid 0 100n
R2 mid out 1K
C2 out 0 100n
R3 out 0 10K
.ac dec 50 10 1Meg
.meas g1k FIND mag(V(out)) AT 1K
.meas g1kdb FIND 20*log10(mag(V(out))) AT 1K
.end
""")
out["T7"] = sim("t7", """* two cascaded RC stages
V1 in 0 AC 1
R1 in mid 1K
C1 mid 0 100n
R2 mid out 1K
C2 out 0 100n
.ac dec 100 10 1Meg
.meas f3db WHEN mag(V(out))=.7071
.end
""")
T8 = """* Zener-referenced emitter-follower regulator
V1 vin 0 15
R1 vin ref 1K
D1 0 ref DZ6V2
Q1 vin ref out QNPN
R2 out 0 100
C1 out 0 10u
.model DZ6V2 D Bv=6.2 Ibv=5m Rs=5
.model QNPN NPN Bf=100 Is=1e-14
.op
.meas vout FIND V(out) AT 0
.end
"""
(R / "t8_netlist.cir").write_text(T8, encoding="latin-1")
out["T8"] = sim("t8", T8)
out["T9"] = sim("t9", """* RLC step
V1 in 0 PULSE 0 1 100u 1u 1u 5m 10m
R1 in a 20
L1 a out 1m
C1 out 0 1u
.tran 2m
.meas vpk MAX V(out)
.meas tr TRIG V(out)=.1 RISE=1 TARG V(out)=.9 RISE=1
.meas t1 WHEN V(out)=1 RISE=1
.meas t2 WHEN V(out)=1 RISE=2
.end
""")
print(json.dumps(out, indent=1, default=str))
(R / "reference.json").write_text(json.dumps(out, indent=1, default=str))
