"""Create LLM test run folders with starting files and a TASK.md each.

usage: setup_runs.py <round-name> [models ...] [--tasks T1,T5,T12]
Run make_fixtures.py and make_fixtures_r2.py first. Then give each agent the prompt:
"Load your shell/file tools, read <folder>\\TASK.md with read_file and carry it out."
"""
import sys as _sys
from pathlib import Path as _P
REPO = _P(__file__).resolve().parents[2]
WORK = REPO / "_research" / "llmtest"          # fixtures, runs and results (git-ignored)
WORK.mkdir(parents=True, exist_ok=True)
_sys.path.insert(0, str(REPO / "src"))
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import shutil, sys
from pathlib import Path

ROOT = Path(str(WORK))
args = sys.argv[1:]
ONLY = None
if "--tasks" in args:
    i = args.index("--tasks")
    ONLY = set(args[i + 1].split(","))
    args = args[:i] + args[i + 2:]
RND = args[0] if args else "r3"
MODELS = args[1:] or ["haiku", "sonnet"]
Q = str(REPO / "qmcp.cmd")

HEAD = r"""# Your assignment

You are evaluating an MCP server for the QSPICE circuit simulator, acting like an ordinary AI assistant
that has this server's tools. You reach the tools through a command-line bridge on a Windows PC.

## Calling the server's tools
- List tools:            & {Q} tools
- Tool parameters:       & {Q} help <tool>
- Read a guide (skill):  & {Q} guide <name>     (no name = list of guides)
- Call a tool with simple arguments:  & {Q} call review_schematic path=C:\full\path\file.qsch
- Call a tool with complex arguments (lists, dicts, specs): write the arguments as ONE JSON object to a
  .json file with write_file, then pass the file path:
      & {Q} call auto_draw_schematic C:\full\path\args_draw.json
Run these with the PowerShell tool (always pass timeout=60; use 120 for simulations or auto-drawing).
Tools that return pictures print "[image saved: C:\...png]" - open that path with read_file to SEE the picture.
Use only these commands plus reading/writing files in your work folder. Do not read or change the
server's source code, and do not write your own Python/SPICE tooling around it.

## What the server tells its users (its MCP instructions)
QSPICE MCP server v0.2.0 - simulate circuits with QSPICE and draw clean schematics.
START HERE: call qspice_guide() once - it lists the skills (step-by-step guides). Before drawing read
qspice_guide('drafting'); before simulating read qspice_guide('simulation').
GOLDEN WORKFLOW FOR SCHEMATICS
1. auto_draw_schematic(spec) for any new circuit (it places, wires, labels and validates).
2. review_schematic(path) -> fix every error and warning with the suggested tool calls.
3. render_schematic(path) and LOOK at the picture. Repeat 2-3 until score >= 90 and it looks clean.
4. simulate(path) -> plot_waveforms / get_trace_data / measure.
RULES: coordinates are QSPICE units, grid = 100, y grows UPWARD. Pins are 'REF.PIN' (e.g. 'R1.1', 'Q1.B',
'U1.OUT'). Ground net is '0' or 'GND'. Values without units: 10K, 100n, 3.3 (not 10kOhm, 100nF, 3.3V);
mega = Meg (M = milli). Every edit tool saves the file and returns a review summary; use undo_last_edit if
an edit made things worse. Files go to the work folder unless an absolute path is given.
(qspice_guide(topic) corresponds to the CLI command: qmcp.cmd guide <topic>)

## Your work folder
{W}  (already exists). Save every file there (including any symbol or library you create) and always
use absolute paths.

## Task
"""

TAIL = r"""

## When done
Write a short report to {W}\report.md (with write_file) AND give the same report as your final reply:
final file path(s), the final review score and error count, the results asked for (with units), which
guides/tools you used, any tool errors or confusing tool behaviour you hit, and anything you could not
do. Be honest; do not claim things you did not verify. Keep the work efficient - do not repeat checks
that already passed.
"""

M3904 = (".model 2N3904 NPN Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259 Ise=6.734f Ikf=66.78m "
         "Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 "
         "Vje=.75 Tr=239.5n Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10")
CLEAN = ("The drawing must be clean: review score of at least 90 with 0 errors, and it must look neat when you "
         "look at the rendered picture.")

TASKS1 = {
 "T1": ("Design and draw an RC low-pass filter with a -3 dB cutoff frequency of about 1 kHz, driven by a voltage "
        "source suitable for AC analysis. Save it as {W}\\T1.qsch. " + CLEAN + " Run an AC analysis with QSPICE "
        "and report the -3 dB frequency that QSPICE measures.", []),
 "T2": ("Draw a non-inverting amplifier with a gain of 5 using QSPICE's generic op-amp (symbol OpAmp), powered "
        "from +15 V and -15 V supplies, with a 1 kHz sine input of 100 mV amplitude. Save it as {W}\\T2.qsch. "
        + CLEAN + " Run a transient simulation of at least 5 ms and report the peak output voltage.", []),
 "T3": ("Draw a buck converter that steps 24 V down to about 12 V, switching at 100 kHz, using an ideal "
        "voltage-controlled switch (symbol SW) driven by a pulse source, a Schottky diode, an inductor, an output "
        "capacitor and a 10 ohm load. Save it as {W}\\T3.qsch. " + CLEAN + " Simulate long enough for the output "
        "to settle and report the average output voltage over the last 0.5 ms of the simulation.", []),
 "T4": ("{W}\\rc_stage.qsch contains a single RC low-pass stage (V1, R1, C1; the node between R1 and C1 is called "
        "mid). Edit this file in place (do not redraw it from scratch): add R2 = 1K from the R1/C1 node to a new node "
        "named out, C2 = 100n from out to ground, and a 10K load resistor from out to ground. Keep the drawing neat: "
        "review score of at least 90 with 0 errors, and it must look neat in the rendered picture. Then run the AC "
        "analysis and report the gain at 1 kHz at node out, as a ratio and in dB.", ["rc_stage.qsch"]),
 "T5": ("{W}\\messy_ce.qsch is a common-emitter transistor amplifier that was drawn very badly. Make it clean and "
        "professional: review score of at least 90 with 0 errors, and it must look tidy in the rendered picture. Do "
        "not change the circuit, with one exception: the emitter bypass capacitor C2 has one pin that is not "
        "connected - connect that pin to ground. The final result must be saved as {W}\\T5.qsch (you may work on a "
        "copy or produce a new file, as long as T5.qsch is the final clean version). Then simulate it and report the "
        "peak-to-peak voltage at Q1's collector.", ["messy_ce.qsch"]),
 "T6": ("A vendor gave you the model file {W}\\ACME_LDO.lib, which contains a 3.3 V LDO regulator subcircuit named "
        "ACME_LDO with pins IN, EN, GND, OUT. QSPICE has no symbol for it. Create a proper symbol for it, then draw "
        "a test circuit: a 5 V DC supply connected to IN, EN tied to IN, GND to ground, a 1u capacitor from IN to "
        "ground, a 10u capacitor from OUT to ground, and a 33 ohm load from OUT to ground. Save the circuit as "
        "{W}\\T6.qsch. " + CLEAN + " Run a 1 ms transient simulation and report the output voltage at the end of "
        "the simulation.", ["ACME_LDO.lib"]),
 "T7": ("Build a hierarchical design. First create a sub-sheet named RCSTAGE with ports IN and OUT that contains a "
        "1K resistor from IN to OUT and a 100n capacitor from OUT to ground; save it in your work folder. Then draw a "
        "parent schematic {W}\\T7.qsch in the same folder: an AC voltage source driving two RCSTAGE blocks in "
        "series (X1 then X2); name the nets in, mid and out. The parent drawing must be clean: review score of at "
        "least 90 with 0 errors, and it must look neat in the rendered picture. Run an AC analysis and report the "
        "-3 dB frequency at out.", []),
 "T8": ("Convert the SPICE netlist in {W}\\t8_netlist.cir into a QSPICE schematic saved as {W}\\T8.qsch. Make "
        "sure the drawing is clean: review score of at least 90 with 0 errors, and it must look neat in the rendered "
        "picture. Simulate it and report the DC voltage at node out.", ["t8_netlist.cir"]),
 "T9": ("{W}\\rlc_step.qsch has already been simulated (results in rlc_step.qraw next to it). Without changing "
        "the circuit, use the server's measurement tools to report: (a) the peak value of V(out), (b) the overshoot "
        "in percent relative to the final value of 1 V, (c) the 10%-90% rise time of V(out) for the first rising "
        "edge, (d) the frequency of the ringing. Also create a plot of V(in) and V(out) and look at it.",
        ["rlc_step.qsch", "rlc_step.qraw"]),
 "T10": ("Find a Schottky diode model that ships with QSPICE and use it in a half-wave rectifier: a 10 V peak, "
         "50 Hz sine source, the Schottky diode, a 100u smoothing capacitor and a 1K load. Save it as "
         "{W}\\T10.qsch. " + CLEAN + " Simulate 100 ms and report the average output voltage over the last 40 ms, "
         "and the diode model you used.", []),
}

TASKS = {
 "T11": ("Using ONLY the canvas editing tools (new_schematic, add_component or place_component, connect_pins, "
         "add_ground, add_net_label, set_directives, move_component, fix_text_placement ...) - do NOT use "
         "auto_draw_schematic, netlist_to_schematic or beautify_schematic - build this circuit in a new file "
         "{W}\\T11.qsch: a 10 V DC source V1 whose + terminal is net 'in', R1 = 10K from in to net 'out', "
         "R2 = 4.7K from out to ground, and C1 = 100n from out to ground. " + CLEAN +
         " Run an operating-point (.op) simulation and report V(out).", []),
 "T12": ("Draw this two-stage common-emitter amplifier and save it as {W}\\T12.qsch (use exactly these reference "
         "designators and values): V1 = SINE 0 100u 1K driving net 'in'; C1 = 10u from in to b1; R1 = 47K from "
         "vcc to b1; R2 = 10K from b1 to ground; Q1 = 2N3904 NPN (collector c1, base b1, emitter e1); R3 = 4.7K "
         "from vcc to c1; R4 = 1K from e1 to ground; C2 = 100u from e1 to ground; C3 = 10u from c1 to b2; "
         "R5 = 47K from vcc to b2; R6 = 10K from b2 to ground; Q2 = 2N3904 (collector c2, base b2, emitter e2); "
         "R7 = 4.7K from vcc to c2; R8 = 1K from e2 to ground; C4 = 100u from e2 to ground; C5 = 10u from c2 to "
         "net 'out'; RL = 10K from out to ground; V2 = 12 V supply (net vcc). The 2N3904 model is not in QSPICE's "
         "library - add this directive: " + M3904 + "  " + CLEAN + " Run a 20 ms transient and report the "
         "peak-to-peak output voltage V(out) between 15 ms and 20 ms and the overall voltage gain.", []),
 "T13": ("Draw an RC low-pass filter: R1 = 1K from the input (net 'in', driven by an AC voltage source V1) to "
         "net 'out', and C1 from out to ground. Make C1's value a parameter and use .step to sweep it over "
         "10n, 100n and 1u. Save as {W}\\T13.qsch. " + CLEAN + " Run an AC analysis from 1 Hz to 1 MHz, report "
         "the -3 dB frequency for each of the three C1 values, and create ONE Bode plot of V(out) that shows "
         "all three curves - look at it.", []),
 "T14": ("A colleague pasted this shunt-regulator model (there is NO .lib file - this text is all you have):\n\n"
         "```\n.subckt SHUNT431 K A REF\n* behavioural 2.495 V shunt reference (TL431-like)\n"
         "B1 K A I=0.1*(1+tanh(1000*(V(REF,A)-2.495)))\nRleak K A 10Meg\n.ends SHUNT431\n```\n\n"
         "Create a proper QSPICE symbol for it (pins K, A, REF), then draw a 5 V shunt regulator and save it as "
         "{W}\\T14.qsch: a 12 V DC source V1 (net vin), R1 = 470 from vin to net 'out', the SHUNT431 as X1 with "
         "K on out, A on ground and REF on net 'ref', R2 = 10K from out to ref, R3 = 10K from ref to ground, and "
         "a 1K load RL from out to ground. " + CLEAN + " Run an operating point and report V(out) and the current "
         "through R1.", []),
 "T15": ("Use the server's help tools (help_search / help_topic) to find out how QSPICE's behavioral voltage "
         "source works. Then draw an analog multiplier test and save it as {W}\\T15.qsch: V1 = sine, 1 V "
         "amplitude, 1 kHz (net 'a'); V2 = sine, 1 V amplitude, 10 kHz (net 'b'); a behavioral voltage source B1 "
         "whose output (net 'out', referenced to ground) equals V(a)*V(b); and a 1K load R1 from out to ground. "
         + CLEAN + " Simulate 2 ms with a maximum time step of 1 us, and report the RMS value and the peak value "
         "of V(out) over the 2 ms.", []),
 "T16": ("Find an N-channel power MOSFET model that ships with QSPICE and can switch 1 A at 12 V. Draw a "
         "low-side switch and save it as {W}\\T16.qsch: a 12 V DC supply V1 (net vcc), a 12 ohm load R1 from vcc "
         "to the drain (net 'd'), the MOSFET M1 with source to ground, and a 10 ohm gate resistor R2 from a pulse "
         "source V2 (0 to 10 V, 10 kHz, 50 % duty, 10 ns edges; net 'drv') to the gate. " + CLEAN + " Simulate "
         "300 us and report the drain voltage while the MOSFET is on, the drain voltage while it is off, and the "
         "model you used.", []),
 "T17": ("{W}\\bridge.qsch is a full-wave bridge rectifier that has ALREADY been simulated (results in "
         "bridge.qraw next to it). Do not change or redraw the circuit. Use the server's waveform tools to report: "
         "(a) the average of V(out) between 60 ms and 100 ms, (b) the peak-to-peak ripple of V(out) in that "
         "window, (c) the RMS current of the source V1 in that window. Also (d) export V(out) and I(V1) to a CSV "
         "file {W}\\bridge_out.csv and (e) plot V(out) and look at the plot.", ["bridge.qsch", "bridge.qraw"]),
 "T18": ("{W}\\broken_ladder.qsch was meant to be this two-stage RC low-pass filter: V1 (an AC source, AC 1) "
         "drives net 'in'; R1 = 1K from in to net 'a'; C1 = 100n from a to ground; R2 = 10K from a to net 'out'; "
         "C2 = 10n from out to ground. Whoever drew it made several mistakes. Use review_schematic to find them "
         "and repair the drawing so the circuit is exactly as intended (reference designators V1, R1, R2, C1, C2). "
         "Save the repaired schematic as {W}\\T18.qsch. " + CLEAN + " Then run an AC analysis and report the "
         "-3 dB frequency at out.", ["broken_ladder.qsch"]),
 "T19": ("Draw a unity-gain Sallen-Key low-pass filter with QSPICE's generic op-amp (symbol OpAmp, U1) powered "
         "from +12 V and -12 V: AC source V1 drives net 'in'; R1 = 4.7K from in to n1; R2 = 4.7K from n1 to n2; "
         "C1 = 47n from n1 to the op-amp output (net 'out'); C2 = 22n from n2 to ground; n2 goes to the "
         "non-inverting input and the inverting input is tied to out. Save as {W}\\T19.qsch. " + CLEAN +
         " Run an AC analysis from 10 Hz to 100 kHz, report the -3 dB frequency and the phase of V(out) at "
         "1 kHz in degrees, and create a Bode plot (magnitude in dB and phase) and look at it.", []),
 "T20": ("{W}\\amp.qsch is a common-emitter amplifier. Edit this file in place (do not redraw it from scratch): "
         "(1) change the collector resistor R3 from 4.7K to 3.3K; (2) add an output coupling capacitor C3 = 10u "
         "from Q1's collector to a new net named 'out'; (3) add a 10K load resistor RL from out to ground. Keep "
         "the drawing neat: review score of at least 90 with 0 errors, and it must look neat in the rendered "
         "picture. Then simulate and report the peak-to-peak voltage at out between 5 ms and 10 ms.", ["amp.qsch"]),
}

ALL = {**{k: (v[0], v[1], "fixtures") for k, v in TASKS1.items()},
       **{k: (v[0], v[1], "fixtures2") for k, v in TASKS.items()}}
for model in MODELS:
    for t, (text, files, fixdir) in ALL.items():
        if ONLY and t not in ONLY:
            continue
        FIX = ROOT / fixdir
        w = ROOT / "runs" / RND / f"{t}_{model}"
        if w.exists():
            shutil.rmtree(w)
        w.mkdir(parents=True)
        for f in files:
            shutil.copy2(FIX / f, w / f)
        body = HEAD.format(Q=Q, W=w) + text.format(W=w) + TAIL.format(W=w)
        (w / "TASK.md").write_text(body, encoding="utf-8")
        print(w)
