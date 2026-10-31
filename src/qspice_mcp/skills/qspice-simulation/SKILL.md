---
name: qspice-simulation
description: Running QSPICE simulations correctly - analysis directives (.tran .ac .dc .op .noise .bode .step .param .meas .four .ic .options), source syntax (DC, AC, SINE, PULSE, PWL, EXP), behavioural sources, choosing settings, and the simulate -> check -> read results workflow.
---

# Simulating with QSPICE

## 1. Workflow

1. Make sure the schematic is correct: `review_schematic(path)` has 0 errors (or `validate_schematic`).
2. Put the analysis on the sheet: `set_directives(path, ['.tran 10m', ...])` (or in the auto-draw spec).
   `set_directives` REPLACES the whole directive block by default - include every line you want to keep
   (models, .meas, .param ...). Use `replace=false` to add a separate block.
3. `simulate(path)`. Check `ok: true` and `errors: []`. If not -> `qspice_guide('troubleshooting')`.
4. The result lists `plots[].traces` (names you can plot) and `measurements` (results of `.meas`).
5. Look at waveforms: `plot_waveforms(path, ['V(out)', 'V(in)'])` - LOOK at the picture.
6. Get numbers: `measure(path, ['.meas vmax MAX V(out)'])` (no re-simulation) or `get_trace_data`.
7. Report numbers with units and compare with expectations (e.g. hand calculation).

A netlist can be simulated directly: `simulate(netlist_text='* title\nV1 in 0 1\nR1 in 0 1K\n.op\n.end')`.

## 2. Analyses

| Directive | Meaning | Example |
|---|---|---|
| `.tran TSTOP` | transient (time domain) for TSTOP seconds | `.tran 10m` |
| `.tran 0 TSTOP TSTART MAXSTEP` | Berkeley form: record from TSTART, limit the time step | `.tran 0 20m 10m 1u` |
| `.tran TSTOP UIC` | skip the bias point, start from `.ic` values | `.tran 5m UIC` |
| `.ac dec N F1 F2` | small-signal frequency sweep, N points/decade | `.ac dec 100 10 10Meg` |
| `.ac oct N F1 F2` / `.ac lin N F1 F2` / `.ac list F...` | other sweeps | `.ac lin 200 1K 100K` |
| `.dc SRC START STOP STEP` | sweep a source's DC value (nested: add more) | `.dc V1 0 5 10m` |
| `.dc temp T1 T2 STEP` | sweep temperature | `.dc temp -40 125 5` |
| `.op` | DC operating point (node voltages, currents) | `.op` |
| `.noise V(out) SRC dec N F1 F2` | noise referred to output and to source SRC | `.noise V(out) V1 dec 20 10 100K` |
| `.bode SRC TSETTLE [FSTART [FSTOP [AMP]]]` | loop gain of a switching converter (time-domain FRA) | `.bode Vpert 2m 1K 100K` |

Rules of thumb:
- `.tran`: simulate at least 5 time constants or 10-20 periods of the slowest signal. For switching
  converters, simulate until the output is settled (often 1-5 ms) and measure in the last part.
- To see fast edges set MAXSTEP (Berkeley form) to about 1/100 of the period.
- `.ac` needs an `AC 1` on the input source (e.g. value `AC 1` or `SINE 0 1 1K AC 1`).
- `.ac` and `.noise` linearize the circuit at its DC operating point - switching circuits need `.bode`.

## 3. Sources (value of a V or I part)

| Function | Syntax (no parentheses, no commas) | Example |
|---|---|---|
| DC | `<value>` | `12` |
| AC (for .ac) | `AC <mag> [phase]` | `AC 1` |
| Sine | `SINE Voffset Vamp Freq [Td [Theta [Phi [Ncycles]]]]` | `SINE 0 1 1K` |
| Pulse | `PULSE Voff Von Tdelay Trise Tfall Ton Tperiod [Ncycles]` | `PULSE 0 5 0 10n 10n 5u 10u` |
| Piece-wise linear | `PWL t1 v1 t2 v2 ...` | `PWL 0 0 1m 0 1.001m 5` |
| Exponential | `EXP V1 V2 Td1 Tau1 Td2 Tau2` | `EXP 0 1 1m 100u 5m 100u` |
| Combined | DC + AC + function | `SINE 0 1 1K AC 1` |

Instance parameters can follow, e.g. `12 Rser=10m`. Duty cycle of a PULSE = Ton/Tperiod.
A PULSE with Ton=4.2u, Tperiod=10u has 42% duty.

Current source `I`: same functions; for `I` and `B2` give pins by name (`{"+": ..., "-": ...}`).

## 4. Other directives

| Directive | Use | Example |
|---|---|---|
| `.param` | named values; use `{name}` in part values | `.param R=10K` and value `{R}` |
| `.step param` | repeat the analysis for several values | `.step param R list 1K 10K 100K` / `.step param C 1n 10n 1n` |
| `.step V1 ...` | step a source | `.step V1 list 5 12` |
| `.meas` | measure numbers from the result (see `qspice_guide('waveforms')`) | `.meas vmax MAX V(out)` |
| `.four F [N] expr` | Fourier/THD of the last period | `.four 1K V(out)` |
| `.ic` | initial conditions | `.ic V(out)=0 I(L1)=0` |
| `.nodeset` | initial guess for the bias point | `.nodeset V(out)=2.5` |
| `.model` | device model | `.model D1N D Is=1n Rs=.1` |
| `.lib file` / `.inc file` | include a model file | `.lib C:/models/opamp.lib` |
| `.options` | simulator options | `.options reltol=1e-4 gmin=1e-12` |
| `.temp` | circuit temperature(s) | `.temp 25 85` |
| `.func` | user function | `.func sq(x) {x*x}` |
| `.save` | limit stored traces (big circuits) | `.save V(out) I(L1)` |
| `.plot` | default plot suggestion in the GUI | `.plot V(out)` |

## 5. Behavioural sources and expressions

- `B1` (voltage) value: `V=<expression>`, `B2` (current) value: `I=<expression>`.
  Example: `V=V(a)*V(b)`, `V=if(V(in)>2.5, 5, 0)`, `I=1m*tanh(V(x)/.1)`.
- Expressions can use node voltages `V(n)`, `V(a,b)`, device currents `I(R1)`, `time`, parameters,
  functions `sin cos exp log sqrt abs min max pow if limit tanh`.
- Wrap parameter math in braces in part values: `{2*R}`.

## 6. Engines

`engine='QSPICE64'` (default, double precision) or `'QSPICE80'` (80-bit, for stiff/precision-critical
circuits). Most circuits: default.

## 7. Reading results quickly

| Question | Call |
|---|---|
| Which traces exist? | `list_traces(path)` |
| How does V(out) look? | `plot_waveforms(path, ['V(out)'])` |
| Peak / average / RMS? | `measure(path, ['.meas vpk MAX V(out)', '.meas vavg AVG V(out) FROM 1m TO 2m', '.meas vrms RMS V(out)'])` |
| -3 dB frequency? | `measure(path, ['.meas f3db WHEN mag(V(out))=.7071'])` (input `AC 1`) |
| Gain at 1 kHz? | `measure(path, ['.meas g FIND mag(V(out)) AT 1K'])` |
| Rise time? | `.meas tr TRIG V(out)=.1 RISE=1 TARG V(out)=.9 RISE=1` |
| Bias point? | `.op` then `get_trace_data(path, ['V(b)','V(c)','I(R1)'])` |
| Efficiency? | `.meas pin AVG -V(vin)*I(V1)` and `.meas pout AVG V(out)*I(R1)` -> pout/pin |

Operating point (`.op`): `simulate` returns the values directly in `operating_point`
(`{'V(out)': 3.19728, 'I(V1)': -0.00068, ...}`) - no extra call needed.
QSPICE does not store resistor currents: use `(V(a)-V(b))/R`, a source current `I(V1)`, or the
power `P(R1)`; capacitor/inductor/source currents (`I(C1)`, `I(L1)`, `I(V1)`) are stored.

Trace names: node voltage `V(out)` (only LABELLED nets have your names; unlabelled nets are `V(N03)` etc.);
differential `V(a,b)`; current through a two-terminal part `I(R1)`; transistor terminal currents
`Ic(Q1)`, `Ib(Q1)`, `Id(M1)`; inside a sub-circuit/sub-sheet `I(R1.X1)` (part R1 inside X1).
Always check exact names with `list_traces`. Plots and `get_trace_data` also accept arithmetic of
traces, e.g. `V(out)-V(in)`, `V(out)*I(R1)`, `mag(V(out))`.

## 8. Checklist before trusting a result

- [ ] `ok: true`, no errors, warnings understood.
- [ ] The waveform looks physically sensible (plot it).
- [ ] Settled? (measure in the final part of a transient).
- [ ] Results match a quick hand estimate (gain, cutoff, current, voltage).
