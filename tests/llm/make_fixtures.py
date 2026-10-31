"""Create the starting files for the LLM test campaign."""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import json, shutil, sys
from pathlib import Path
from qspice_mcp import autodraw, canvas, runner
from qspice_mcp.model import Schematic

ROOT = Path(str(WORK))
FIX = ROOT / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

# T4: single RC stage to extend
autodraw.draw({"title": "RC filter stage 1", "components": [
    {"ref": "V1", "symbol": "V", "value": "SINE 0 1 1K AC 1", "nodes": ["in", "0"]},
    {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["in", "mid"]},
    {"ref": "C1", "symbol": "C", "value": "100n", "nodes": ["mid", "0"]}],
    "directives": [".ac dec 50 10 1Meg"]}, FIX / "rc_stage.qsch")

# T5: a deliberately messy common-emitter amplifier (same circuit, bad drawing)
sch = Schematic()
P = canvas.place_component
P(sch, "V", -2000, 0, ref="V1", value="SINE 0 10m 1K")
P(sch, "C", -1500, 800, ref="C1", value="10u", rotation=90)
P(sch, "R", -1000, 1200, ref="R1", value="47K")
P(sch, "R", -1000, 300, ref="R2", value="10K")          # overlaps area near Q1
P(sch, "NPN", -800, 400, ref="Q1", value="2N3904")       # overlapping R2
P(sch, "R", -700, 1500, ref="R3", value="4.7K")
P(sch, "R", -700, -500, ref="R4", value="1K")
P(sch, "C", 400, -400, ref="C2", value="100u")
P(sch, "V", 1500, 1500, ref="V2", value="12")
W = canvas.add_polyline
def pin(s):
    return list(canvas.find_pin(sch, s)[2])
def L(a, b):
    pa, pb = pin(a) if isinstance(a, str) else a, pin(b) if isinstance(b, str) else b
    W(sch, [pa, [pb[0], pa[1]], pb])
L("V1.+", "C1.+"); L("C1.-", "R2.1"); L("R1.2", "R2.1"); L("R2.1", "Q1.B")
L("R3.2", "Q1.C"); L("Q1.E", "R4.1"); L("R4.1", "C2.+")
L("R1.1", "R3.1"); L("R3.1", "V2.+")
canvas.add_label(sch, "V1.-", "GND", None, False, 1)
canvas.add_label(sch, "R2.2", "GND", None, False, 1)
canvas.add_label(sch, "R4.2", "GND", None, False, 1)
canvas.add_label(sch, "V2.-", "GND", None, False, 1)
# C2.- deliberately left unconnected
sch.add_text((-2000, -1500), ".tran 10m\n.model 2N3904 NPN Is=6.734f Bf=416.4 Vaf=74.03 Ikf=66.78m Rb=10 Rc=1 Cjc=3.638p Cje=4.493p Tf=301.2p Tr=239.5n", directive=True, just=5)
sch.save(FIX / "messy_ce.qsch")

# T9: an RLC step response to analyse
autodraw.draw({"title": "RLC step response", "components": [
    {"ref": "V1", "symbol": "V", "value": "PULSE 0 1 100u 1u 1u 5m 10m", "nodes": ["in", "0"]},
    {"ref": "R1", "symbol": "R", "value": "20", "nodes": ["in", "a"]},
    {"ref": "L1", "symbol": "L", "value": "1m", "nodes": ["a", "out"]},
    {"ref": "C1", "symbol": "C", "value": "1u", "nodes": ["out", "0"]}],
    "directives": [".tran 2m"]}, FIX / "rlc_step.qsch")
runner.simulate(FIX / "rlc_step.qsch")

# T6: vendor-like subcircuit text
(FIX / "ACME_LDO.lib").write_text(""".subckt ACME_LDO IN EN GND OUT
* simple behavioural 3.3 V LDO (dropout 200 mV), enabled when EN > 1.2 V
B1 OUT GND V=if(V(EN,GND)>1.2, min(3.3, max(V(IN,GND)-.2, 0)), 0) Rser=50m
Rin IN GND 100K
Ren EN GND 1Meg
.ends ACME_LDO
""", encoding="latin-1")
# T8: a SPICE netlist to turn into a schematic
(FIX / "t8_netlist.cir").write_text("""* Zener-referenced emitter-follower regulator
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
""", encoding="latin-1")
print("fixtures in", FIX)
for p in sorted(FIX.iterdir()):
    print(" ", p.name)
