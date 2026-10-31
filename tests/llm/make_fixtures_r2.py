"""Starting files for LLM test round 2 (tasks T11-T20)."""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import sys
from pathlib import Path
from qspice_mcp import autodraw, canvas, runner
from qspice_mcp.model import Schematic

ROOT = Path(str(WORK))
FIX = ROOT / "fixtures2"
FIX.mkdir(parents=True, exist_ok=True)
M3904 = (".model 2N3904 NPN Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259 Ise=6.734f Ikf=66.78m "
         "Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 "
         "Vje=.75 Tr=239.5n Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10")

# T17: bridge rectifier, already simulated
autodraw.draw({"title": "Full-wave bridge rectifier", "components": [
    {"ref": "V1", "symbol": "V", "value": "SINE 0 17 50", "nodes": ["ac1", "ac2"]},
    {"ref": "D1", "symbol": "D", "value": "1N4007", "pins": {"A": "ac1", "K": "out"}},
    {"ref": "D2", "symbol": "D", "value": "1N4007", "pins": {"A": "ac2", "K": "out"}},
    {"ref": "D3", "symbol": "D", "value": "1N4007", "pins": {"A": "0", "K": "ac1"}},
    {"ref": "D4", "symbol": "D", "value": "1N4007", "pins": {"A": "0", "K": "ac2"}},
    {"ref": "C1", "symbol": "C", "value": "470u", "nodes": ["out", "0"]},
    {"ref": "R1", "symbol": "R", "value": "100", "nodes": ["out", "0"]}],
    "directives": [".tran 100m"]}, FIX / "bridge.qsch")
r = runner.simulate(FIX / "bridge.qsch")
print("bridge sim", r.get("errors"), r.get("qraw"))

# T20: common-emitter amplifier without output coupling / load
autodraw.draw({"title": "Common-emitter amplifier", "components": [
    {"ref": "V1", "symbol": "V", "value": "SINE 0 10m 1K", "nodes": ["in", "0"]},
    {"ref": "C1", "symbol": "C", "value": "10u", "nodes": ["in", "b"]},
    {"ref": "R1", "symbol": "R", "value": "47K", "nodes": ["vcc", "b"]},
    {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["b", "0"]},
    {"ref": "Q1", "symbol": "NPN", "value": "2N3904", "pins": {"C": "c", "B": "b", "E": "e"}},
    {"ref": "R3", "symbol": "R", "value": "4.7K", "nodes": ["vcc", "c"]},
    {"ref": "R4", "symbol": "R", "value": "1K", "nodes": ["e", "0"]},
    {"ref": "C2", "symbol": "C", "value": "100u", "nodes": ["e", "0"]},
    {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]}],
    "directives": [".tran 10m", M3904]}, FIX / "amp.qsch")

# T18: a two-stage RC ladder drawn with several mistakes
sch = Schematic()
P = canvas.place_component
P(sch, "V", 0, 0, ref="V1", value="AC 1")
P(sch, "R", 700, 500, ref="R1", value="1K", rotation=90)
P(sch, "C", 1300, 0, ref="C1", value="100n")
P(sch, "R", 1900, 500, ref="R2", value="10K", rotation=90)
P(sch, "C", 2600, 0, ref="C2", value="10n")


def pin(s):
    return list(canvas.find_pin(sch, s)[2])


def pins2(ref):
    a, b = pin(f"{ref}.1"), pin(f"{ref}.2")
    return (a, b) if (a[0], -a[1]) < (b[0], -b[1]) else (b, a)     # left/top first


def L(pa, pb):
    canvas.add_polyline(sch, [pa, [pb[0], pa[1]], pb])


v_top = pin("V1.+")
r1a, r1b = pins2("R1")
c1a, c1b = pins2("C1")
r2a, r2b = pins2("R2")
L(v_top, r1a)
L(r1b, c1a)
L(c1a, r2a)
canvas.add_polyline(sch, [r2b, [r2b[0] + 300, r2b[1]]])          # dangling: never reaches C2
canvas.add_label(sch, "V1.-", "GND", None, False, 1)
canvas.add_label(sch, "C1.2", "GND", None, False, 1)
sch.add_net_label((3300, 900), "out")                              # floating label
out = FIX / "broken_ladder.qsch"
sch.save(out)
t = out.read_text(encoding="latin-1")
assert t.count('"C2"') == 1, t.count('"C2"')
out.write_text(t.replace('"C2"', '"C1"'), encoding="latin-1")      # duplicate refdes
print("fixtures in", FIX)
for p in sorted(FIX.iterdir()):
    print(" ", p.name)
