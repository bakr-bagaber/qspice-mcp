# Testing with small models

The server is meant to be used by *any* LLM, not only the strongest ones. v0.2 was therefore tested by
letting **Claude Haiku 5.5** (`claude-haiku-5-5`) and **Claude Sonnet 5.5** (`claude-sonnet-5-5`) do real
tasks with it, each run in a fresh context with nothing but the server's own instructions, tool
descriptions and skills to go on. The author's model (Opus) did not take part in any test run; it only
built the tasks, the starting files, the reference answers and the grader, and fixed what the runs
exposed.

## How a run works

- Each run gets a work folder with a `TASK.md` (the assignment) and any starting files.
- The agent reaches the server through `qmcp.cmd`, the CLI bridge that exposes the same tools, help
  text and skills as the MCP interface. Pictures returned by tools are saved as PNG files and the agent
  opens them, so the visual feedback loop (draw -> render -> look -> fix) is the same as over MCP.
- `TASK.md` contains the server's MCP instructions verbatim. The agent may not read the server's source
  code or write its own tooling around it.
- When done the agent writes `report.md`: results, final review score, tools and guides used, and every
  tool error or confusing behaviour it met.

## How runs are graded

`tests/llm/grade.py` (round 1) and `tests/llm/grade2.py` (round 2) grade the files the agent produced,
not what it claims:

- **Drawing** passes when the server's reviewer scores the final sheet >= 90 with 0 errors. (The agents
  were also told to look at the picture; their own judgement of neatness is in the reports.)
- **Function** passes when the circuit is the one asked for and simulates to the right answer: the grader
  netlists the sheet with QSPICE, matches the topology node-for-node against the task (two-pin passives
  may be either way round), re-simulates, re-measures with QSPICE and compares with reference values
  computed beforehand (`reference.py`, `reference_r2.py`).
- Answers that are only numbers from an existing result (T9, T17) are checked against the references.

## Round 1 - drawing, editing, symbols, hierarchy, import, measurements

| Task | What it exercises | Haiku | Sonnet |
|---|---|---|---|
| T1 | RC low-pass for 1 kHz, AC analysis, -3 dB point | 100 / 994.7 Hz | 100 / 994.8 Hz |
| T2 | Non-inverting op-amp (gain 5), +-15 V, transient | 100 / 0.500 V pk | 100 / 0.500 V pk |
| T3 | Buck converter 24 V -> 12 V, ideal switch, Schottky | 96 / 11.84 V | 100 / 12.10 V |
| T4 | Extend an existing RC stage in place (3 parts) | 100 / gain 0.4745 | 100 / gain 0.4745 |
| T5 | Clean up a deliberately messy CE amplifier (+1 missing wire) | 100 / topology ok | 100 / topology ok |
| T6 | Symbol for a vendor LDO `.subckt` + test circuit | 100 / 3.295 V | 100 / 3.295 V |
| T7 | Hierarchical design: sub-sheet RCSTAGE used twice | 100 / 595.6 Hz | 100 / 595.6 Hz |
| T8 | SPICE netlist -> schematic, DC operating point | 100 / 5.495 V | 100 / 5.495 V |
| T9 | Measurements on an existing result (peak, overshoot, rise time, ringing) | all 4 correct | all 4 correct |
| T10 | Find a Schottky model, half-wave rectifier | 100 / 8.98 V | 100 / 8.98 V |

Every run passed both drawing and function (numbers: review score / measured result).

## Round 2 - harder and broader (after fixing the round-1 findings)

| Task | What it exercises | Haiku | Sonnet |
|---|---|---|---|
| T11 | Build a circuit with the canvas tools only (no auto-draw), `.op` | 100 / 3.197 V | 100 / 3.197 V |
| T12 | 18-part two-stage BJT amplifier from a parts list | 100 / 2.337 Vpp | 100 / 2.337 Vpp |
| T13 | `.step` sweep of C1, -3 dB point per step, one Bode plot | 100 / 3 values ok | 100 / 3 values ok |
| T14 | Symbol from pasted `.subckt` text (no file), shunt regulator | 100 / 4.987 V | 100 / 4.987 V |
| T15 | Help lookup + behavioural source (multiplier), RMS and peak | 100 / 0.500 V rms | 100 / 0.500 V rms |
| T16 | Pick a power MOSFET from the library, low-side switch | 100 / IRF640 ok | 100 / IRF640 ok |
| T17 | Post-processing: average, ripple, RMS current, CSV export | all correct | all correct |
| T18 | Repair a broken sheet (duplicate designator, floating label, dangling wire, unconnected part, no analysis) | 100 / 956.1 Hz | 100 / 956.1 Hz |
| T19 | Sallen-Key low-pass with op-amp, f(-3 dB) and phase | 100 / 1087 Hz, -85.7 deg | 100 / 1087 Hz, -85.7 deg |
| T20 | Edit an amplifier in place (value change, coupling cap, load) | 100 / 2.393 Vpp | 100 / 2.393 Vpp |

Again every run passed both drawing and function. (Several round-2 runs hit an API rate limit after
the work was done; they were resumed only to write their reports.)

## What the runs found, and what changed

Round 1 (fixed before round 2): tool errors hid their messages; auto-draw left named nets unlabelled
(so `V(name)` failed); deleting a label left its stub; automatic net names could collide with labels;
plots did not accept expressions; edits did not report nets they split or shorted; text placement
around switches and op-amps; parts added with `add_component` did not continue the signal path.

Round 2:
- `connect_pins` drew up-over-down "bumps" between the tops of neighbouring shunt parts -> the router
  now enters/leaves the end pins of two-terminal parts sideways when that gives a straight bus.
- Wiring tools left texts under new wires -> they now re-place the texts they cross.
- `search_models(model_type='NMOS')` missed power MOSFETs (typed VDMOS) -> included by polarity, with
  ratings (Vds, Ids, Ron, mfg) in every hit and duplicates merged.
- `export_waveforms` wrote CR CR LF line ends -> fixed.
- `get_trace_data` window statistics differed slightly from `.meas` -> window edges interpolated.
- `.meas` results of `.step` runs were an unlabelled list -> labelled by step; `simulate` returns
  `.op` values directly.
- `measure` left helper files next to the user's schematic -> temp folder.
- Renaming a part made its old problems look new in the review delta -> issues compared by location.
- Auto-draw put a shunt load sideways and staggered two signal sources -> shunt parts stand vertical,
  sources share a baseline, net labels avoid part texts, and the final pick weighs drafting rules more.
- CLI: `value=10` arrived as a number; a bare path or a missing `call` word was rejected -> fixed.
- New `copy_schematic` ("save as"); help search ranks pages properly; QSPICE netlist notation
  (`Ã´U1`, `¥`) is explained in tool output.

## Reproducing

```
.venv\Scripts\python.exe tests\llm\make_fixtures.py        # round-1 starting files
.venv\Scripts\python.exe tests\llm\make_fixtures_r2.py     # round-2 starting files
.venv\Scripts\python.exe tests\llm\reference.py            # reference answers
.venv\Scripts\python.exe tests\llm\reference_r2.py
.venv\Scripts\python.exe tests\llm\setup_runs.py r3 haiku sonnet          # all 20 tasks
.venv\Scripts\python.exe tests\llm\setup_runs.py r3 haiku --tasks T11,T18 # a subset
```
Then start one agent per run folder with the prompt *"load your shell and file tools, read
`<folder>\TASK.md` and carry it out"*, and grade:
```
.venv\Scripts\python.exe tests\llm\grade.py r3      # T1-T10
.venv\Scripts\python.exe tests\llm\grade2.py r3     # T11-T20
```
Fixtures, runs and results go to `_research\llmtest\` (not committed).
