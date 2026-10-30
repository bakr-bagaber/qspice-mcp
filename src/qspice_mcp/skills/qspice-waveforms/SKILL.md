---
name: qspice-waveforms
description: Reading simulation results - trace names, plots, exact numbers with .meas (all forms), statistics, waveform expressions, .step runs, AC magnitude/phase, Fourier/THD, exporting CSV.
---

# Waveforms, measurements and results

After `simulate(path)` the results live in `<name>.qraw` next to the schematic. Every tool below takes
the schematic/netlist path (or the .qraw path).

## 1. What is in the result?

`list_traces(path)` -> analysis type (Transient / AC / DC / Operating Point / Noise), number of points,
`.step` values and all trace names, e.g. `V(out)`, `V(in)`, `I(R1)`, `Ic(Q1)`, `Id(M1)`.

## 2. Look at it: plot_waveforms

```
plot_waveforms(path, traces=['V(in)', 'V(out)'])                 # overlaid
plot_waveforms(path, traces=['V(out)', 'I(L1)'], separate_panes=True)
plot_waveforms(path, traces=['V(out)'], x_min=1e-3, x_max=2e-3)   # zoom (seconds / Hz / volts)
plot_waveforms(path, traces=['V(out)'], steps=[1, 3])             # some .step runs only
```

AC results are drawn as Bode plots (magnitude in dB + phase). ALWAYS look at the picture and describe
what you see (settling, ringing, clipping, ripple, oscillation).

## 3. Exact numbers: measure (.meas)

`measure(path, statements=[...])` runs `.meas` lines on the existing result (no re-simulation).
`NAME` is any word; the analysis keyword (`tran`, `ac`, `dc`) is optional.

| Want | Statement |
|---|---|
| maximum / minimum | `.meas vmax MAX V(out)` / `.meas vmin MIN V(out)` |
| average, RMS, peak-to-peak | `.meas vavg AVG V(out)`, `.meas vrms RMS V(out)`, `.meas vpp PP V(out)` |
| over a window | `.meas vavg AVG V(out) FROM 1m TO 2m` |
| integral (charge, energy) | `.meas q INTEG I(C1) FROM 0 TO 1m` |
| value at a time / frequency | `.meas v1 FIND V(out) AT 1m`  (AC: `.meas g FIND mag(V(out)) AT 1K`) |
| when something crosses a level | `.meas t50 WHEN V(out)=2.5 RISE=1` (`FALL=1`, `CROSS=LAST`) |
| value of A when B crosses | `.meas vx FIND V(a) WHEN V(b)=1` |
| time between two events | `.meas tr TRIG V(out)=.1 RISE=1 TARG V(out)=.9 RISE=1` |
| -3 dB bandwidth (input AC 1) | `.meas f3db WHEN mag(V(out))=.7071` |
| frequency of the peak | `.meas vpk MAX mag(V(out))` then `.meas fpk WHEN mag(V(out))=vpk` |
| phase at a frequency | `.meas p FIND ph(V(out)) AT 1K` (degrees) |
| derivative | `.meas sr DERIV V(out) AT 1u` |
| use earlier results | `.meas ratio PARAM vout/vin` |

Put `.meas` lines in the schematic's directives to get them on every run (`simulate` returns
`measurements`). AC values are complex: use `mag()` for magnitude and `ph()` for phase.
For dB use `20*log10(mag(V(out)))`.

## 4. Numbers for many points: get_trace_data

`get_trace_data(path, traces=['V(out)'], max_points=50)` -> statistics (min, max, avg, rms, first, last,
x at min/max) and evenly spaced samples. Options: `x_min`, `x_max`, `step` (which .step run).
AC traces come as `magnitude_dB` and `phase_deg`.

## 5. Expressions: evaluate_expressions

QSPICE evaluates any waveform expression: `evaluate_expressions(path, ['V(out)/V(in)', 'V(out)*I(R1)'])`
-> CSV rows. Useful functions: `mag()`, `ph()`, `re()`, `im()`, `abs()`, `sqrt()`, `log10()`, `d()`
(derivative), arithmetic `+ - * /`.

## 6. .step runs

With `.step param R list 1K 10K 100K` the result holds one run per value:
- `simulate` lists `steps` (index -> parameter values).
- `plot_waveforms` overlays all runs (or `steps=[2]`).
- `.meas` results come out per step.
- `get_trace_data(..., step=2)` reads one run.

## 7. Fourier / THD

`.four 1K V(out)` (fundamental 1 kHz) -> harmonics and THD of the last period.
Simulate an integer number of periods, settled; e.g. `.tran 0 20m 10m 1u` for 1 kHz.
Or `measure(path, ['.four 1K 9 1 V(out)'])` on an existing result.

## 8. Export

`export_waveforms(path, ['V(out)', 'I(L1)'], out_file='data.csv', fmt='CSV')` (also `ASCII`, `SPICE` for a
PWL file, `S2P`).

## 9. Reporting results

- State numbers with units and the condition (`V(out) peak = 1.00 V at 1 kHz, gain = 10.0`).
- Compare with the expected value and explain differences.
- Mention the plot you looked at and what it shows.
