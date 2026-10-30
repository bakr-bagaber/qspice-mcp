---
name: qspice-recipes
description: Tested, ready-to-use auto_draw_schematic specs for common circuits (RC/RLC filters, op-amp amplifiers, Sallen-Key, BJT/MOSFET stages, current mirror, differential pair, rectifier, Zener regulator, buck and boost converters) with expected simulation results. Copy one and adapt it.
---

# Circuit recipes (tested)

Every spec below was drawn with `auto_draw_schematic` (review score 100, validation ok), simulated in
QSPICE, and its `.meas` results checked. To use one: copy the JSON, change values/parts, then
`auto_draw_schematic(spec=<json>, path='<name>.qsch')` -> `review_schematic` -> `simulate`.

Adapting tips:
- Keep the net names (`in`, `out`, `vcc`, `vee`, `0`) - they drive the layout and the labels.
- Change values freely; add parts by adding entries with new refdes and nets.
- Models: `2N3904`/`2N3906` need the `.model` line shown; `1N4148`, `1N4007`, `1N5819`, `IRF640`,
  `2N7002` are built into QSPICE.

## rc lowpass

RC low-pass filter, fc = 1/(2*pi*R1*C1) = 1.6 kHz.

Expected: `f3db` about 1.59 kHz.

```json
{
 "title": "RC low-pass filter, fc = 1/(2*pi*R1*C1) = 1.6 kHz",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 1 1K AC 1", "nodes": ["in", "0"]},
  {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["in", "out"]},
  {"ref": "C1", "symbol": "C", "value": "100n", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".ac dec 50 10 1Meg",
  ".meas f3db WHEN mag(V(out))=.7071"
 ]
}
```

## rc step

RC step response, tau = R1*C1 = 1 ms.

Expected: `trise` (10-90%) about 2.2 ms = 2.2*R*C.

```json
{
 "title": "RC step response, tau = R1*C1 = 1 ms",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "PULSE 0 1 1m 1u 1u 10m 20m", "nodes": ["in", "0"]},
  {"ref": "R1", "symbol": "R", "value": "10K", "nodes": ["in", "out"]},
  {"ref": "C1", "symbol": "C", "value": "100n", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".tran 8m",
  ".meas trise TRIG V(out)=.1 RISE=1 TARG V(out)=.9 RISE=1"
 ]
}
```

## divider dc

Voltage divider with DC sweep.

Expected: `vout12` = 12*4.7/14.7 = 3.84 V.

```json
{
 "title": "Voltage divider with DC sweep",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "12", "nodes": ["vin", "0"]},
  {"ref": "R1", "symbol": "R", "value": "10K", "nodes": ["vin", "out"]},
  {"ref": "R2", "symbol": "R", "value": "4.7K", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".dc V1 0 12 .1",
  ".meas vout12 FIND V(out) AT 12"
 ]
}
```

## inverting amp

Inverting amplifier, gain = -R2/R1 = -10.

Expected: output 1 V peak (gain -10), inverted.

```json
{
 "title": "Inverting amplifier, gain = -R2/R1 = -10",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 .1 1K", "nodes": ["in", "0"]},
  {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["in", "inv"]},
  {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["inv", "out"]},
  {"ref": "U1", "symbol": "OpAmp", "pins": {"IN+": "0", "IN-": "inv", "OUT": "out", "Vdd": "vcc", "Vss": "vee"}},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
  {"ref": "V3", "symbol": "V", "value": "-12", "nodes": ["vee", "0"]},
  {"ref": "R3", "symbol": "R", "value": "10K", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".tran 5m",
  ".meas vpk MAX V(out)"
 ]
}
```

## noninverting amp

Non-inverting amplifier, gain = 1 + R2/R1 = 11.

Expected: output 1.1 V peak (gain 11).

```json
{
 "title": "Non-inverting amplifier, gain = 1 + R2/R1 = 11",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 .1 1K", "nodes": ["in", "0"]},
  {"ref": "U1", "symbol": "OpAmp", "pins": {"IN+": "in", "IN-": "fb", "OUT": "out", "Vdd": "vcc", "Vss": "vee"}},
  {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["fb", "0"]},
  {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["out", "fb"]},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
  {"ref": "V3", "symbol": "V", "value": "-12", "nodes": ["vee", "0"]}
 ],
 "directives": [
  ".tran 5m",
  ".meas vpk MAX V(out)"
 ]
}
```

## ce amplifier

Common-emitter amplifier (2N3904).

Expected: `vpp` about 3 V (gain about 150 with the emitter bypassed).

```json
{
 "title": "Common-emitter amplifier (2N3904)",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 10m 1K", "nodes": ["in", "0"]},
  {"ref": "C1", "symbol": "C", "value": "10u", "nodes": ["in", "b"]},
  {"ref": "R1", "symbol": "R", "value": "47K", "nodes": ["vcc", "b"]},
  {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["b", "0"]},
  {"ref": "Q1", "symbol": "NPN", "value": "2N3904", "pins": {"C": "c", "B": "b", "E": "e"}},
  {"ref": "R3", "symbol": "R", "value": "4.7K", "nodes": ["vcc", "c"]},
  {"ref": "R4", "symbol": "R", "value": "1K", "nodes": ["e", "0"]},
  {"ref": "C2", "symbol": "C", "value": "100u", "nodes": ["e", "0"]},
  {"ref": "C3", "symbol": "C", "value": "10u", "nodes": ["c", "out"]},
  {"ref": "R5", "symbol": "R", "value": "10K", "nodes": ["out", "0"]},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]}
 ],
 "directives": [
  ".tran 10m",
  ".model 2N3904 NPN Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259 Ise=6.734f Ikf=66.78m Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 Vje=.75 Tr=239.5n Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10",
  ".meas vpp PP V(out) FROM 5m TO 10m"
 ]
}
```

## nmos switch

NMOS low-side switch driving a resistive load.

Expected: drain swings between 12 V and about 0.2 V.

```json
{
 "title": "NMOS low-side switch driving a resistive load",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "PULSE 0 10 0 100n 100n 5u 10u", "nodes": ["gate_drv", "0"]},
  {"ref": "R1", "symbol": "R", "value": "10", "nodes": ["gate_drv", "g"]},
  {"ref": "M1", "symbol": "NMOS", "value": "IRF640", "pins": {"D": "d", "G": "g", "S": "0"}},
  {"ref": "R2", "symbol": "R", "value": "12", "nodes": ["vcc", "d"]},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]}
 ],
 "directives": [
  ".tran 50u",
  ".meas vdmin MIN V(d)"
 ]
}
```

## bridge rectifier

Full-wave bridge rectifier with filter capacitor.

Expected: `vavg` about 9.9 V (12 V peak minus two diode drops, with ripple).

```json
{
 "title": "Full-wave bridge rectifier with filter capacitor",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 12 50", "nodes": ["ac1", "ac2"]},
  {"ref": "D1", "symbol": "D", "value": "1N4007", "pins": {"A": "ac1", "K": "pos"}},
  {"ref": "D2", "symbol": "D", "value": "1N4007", "pins": {"A": "ac2", "K": "pos"}},
  {"ref": "D3", "symbol": "D", "value": "1N4007", "pins": {"A": "0", "K": "ac1"}},
  {"ref": "D4", "symbol": "D", "value": "1N4007", "pins": {"A": "0", "K": "ac2"}},
  {"ref": "C1", "symbol": "C", "value": "1000u", "nodes": ["pos", "0"]},
  {"ref": "R1", "symbol": "R", "value": "100", "nodes": ["pos", "0"]}
 ],
 "directives": [
  ".tran 100m",
  ".meas vavg AVG V(pos) FROM 60m TO 100m"
 ]
}
```

## buck converter

Buck converter 12 V -> about 5 V (ideal switch, 100 kHz).

Expected: `vout` about 4.6 V (12 V x 42 % duty minus diode/ripple losses).

```json
{
 "title": "Buck converter 12 V -> about 5 V (ideal switch, 100 kHz)",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "12", "nodes": ["vin", "0"]},
  {"ref": "S1", "symbol": "SW", "pins": {"1": "vin", "2": "sw", "+": "ctrl", "-": "0"}, "params": {"Ron": "10m", "Roff": "1Meg", "Vt": "2.5", "Vh": "-.5"}},
  {"ref": "V2", "symbol": "V", "value": "PULSE 0 5 0 10n 10n 4.2u 10u", "nodes": ["ctrl", "0"]},
  {"ref": "D1", "symbol": "D", "value": "1N5819", "pins": {"A": "0", "K": "sw"}},
  {"ref": "L1", "symbol": "L", "value": "47u", "nodes": ["sw", "out"]},
  {"ref": "C1", "symbol": "C", "value": "100u", "nodes": ["out", "0"]},
  {"ref": "R1", "symbol": "R", "value": "5", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".tran 2m",
  ".meas vout AVG V(out) FROM 1.5m TO 2m"
 ]
}
```

## rlc series

Series RLC resonance, f0 = 1/(2*pi*sqrt(L1*C1)) = 5 kHz.

Expected: peak at `fpk` about 5.0 kHz.

```json
{
 "title": "Series RLC resonance, f0 = 1/(2*pi*sqrt(L1*C1)) = 5 kHz",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "AC 1", "nodes": ["in", "0"]},
  {"ref": "R1", "symbol": "R", "value": "10", "nodes": ["in", "a"]},
  {"ref": "L1", "symbol": "L", "value": "10m", "nodes": ["a", "out"]},
  {"ref": "C1", "symbol": "C", "value": "100n", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".ac dec 100 100 100K",
  ".meas vmax MAX mag(V(out))",
  ".meas fpk WHEN mag(V(out))=vmax"
 ]
}
```

## zener regulator

Zener shunt regulator.

Expected: V(out) about 5.1 V at the operating point.

```json
{
 "title": "Zener shunt regulator",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "12", "nodes": ["vin", "0"]},
  {"ref": "R1", "symbol": "R", "value": "470", "nodes": ["vin", "out"]},
  {"ref": "D1", "symbol": "Zener", "value": "DZ51", "pins": {"A": "0", "K": "out"}},
  {"ref": "R2", "symbol": "R", "value": "2.2K", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".model DZ51 D Bv=5.1 Ibv=5m Rs=2",
  ".op"
 ]
}
```

## sallen key lp

Sallen-Key 2nd-order low-pass, fc about 1 kHz.

Expected: `f3db` about 1.1 kHz.

```json
{
 "title": "Sallen-Key 2nd-order low-pass, fc about 1 kHz",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "AC 1", "nodes": ["in", "0"]},
  {"ref": "R1", "symbol": "R", "value": "10K", "nodes": ["in", "a"]},
  {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["a", "b"]},
  {"ref": "C1", "symbol": "C", "value": "22n", "nodes": ["a", "out"]},
  {"ref": "C2", "symbol": "C", "value": "10n", "nodes": ["b", "0"]},
  {"ref": "U1", "symbol": "OpAmp", "pins": {"IN+": "b", "IN-": "out", "OUT": "out", "Vdd": "vcc", "Vss": "vee"}},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
  {"ref": "V3", "symbol": "V", "value": "-12", "nodes": ["vee", "0"]}
 ],
 "directives": [
  ".ac dec 50 10 100K",
  ".meas f3db WHEN mag(V(out))=.7071"
 ]
}
```

## current mirror

NPN current mirror, Iout about 1 mA.

Expected: I(R2) about 1 mA (copy of I(R1)).

```json
{
 "title": "NPN current mirror, Iout about 1 mA",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
  {"ref": "R1", "symbol": "R", "value": "11.3K", "nodes": ["vcc", "ref"]},
  {"ref": "Q1", "symbol": "NPN", "value": "2N3904", "pins": {"C": "ref", "B": "ref", "E": "0"}},
  {"ref": "Q2", "symbol": "NPN", "value": "2N3904", "pins": {"C": "out", "B": "ref", "E": "0"}},
  {"ref": "R2", "symbol": "R", "value": "2K", "nodes": ["vcc", "out"]}
 ],
 "directives": [
  ".op",
  ".model 2N3904 NPN Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259 Ise=6.734f Ikf=66.78m Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 Vje=.75 Tr=239.5n Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10"
 ]
}
```

## diff pair

BJT differential pair with tail resistor.

Expected: `vpp` of V(outp,outn) about 2 V for 10 mVpp input.

```json
{
 "title": "BJT differential pair with tail resistor",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 5m 1K", "nodes": ["inp", "0"]},
  {"ref": "Q1", "symbol": "NPN", "value": "2N3904", "pins": {"C": "outn", "B": "inp", "E": "tail"}},
  {"ref": "Q2", "symbol": "NPN", "value": "2N3904", "pins": {"C": "outp", "B": "0", "E": "tail"}},
  {"ref": "R1", "symbol": "R", "value": "10K", "nodes": ["vcc", "outn"]},
  {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["vcc", "outp"]},
  {"ref": "R3", "symbol": "R", "value": "10K", "nodes": ["tail", "vee"]},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
  {"ref": "V3", "symbol": "V", "value": "-12", "nodes": ["vee", "0"]}
 ],
 "directives": [
  ".tran 3m",
  ".model 2N3904 NPN Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259 Ise=6.734f Ikf=66.78m Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 Vje=.75 Tr=239.5n Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10",
  ".meas vpp PP V(outp,outn)"
 ]
}
```

## boost converter

Boost converter 5 V -> about 12 V (ideal switch).

Expected: `vout` about 12.5 V (5 V / (1 - 58 % duty) minus losses).

```json
{
 "title": "Boost converter 5 V -> about 12 V (ideal switch)",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "5", "nodes": ["vin", "0"]},
  {"ref": "L1", "symbol": "L", "value": "22u", "nodes": ["vin", "sw"]},
  {"ref": "S1", "symbol": "SW", "pins": {"1": "sw", "2": "0", "+": "ctrl", "-": "0"}, "params": {"Ron": "10m", "Roff": "1Meg", "Vt": "2.5", "Vh": "-.5"}},
  {"ref": "V2", "symbol": "V", "value": "PULSE 0 5 0 10n 10n 5.8u 10u", "nodes": ["ctrl", "0"]},
  {"ref": "D1", "symbol": "D", "value": "1N5819", "pins": {"A": "sw", "K": "out"}},
  {"ref": "C1", "symbol": "C", "value": "47u", "nodes": ["out", "0"]},
  {"ref": "R1", "symbol": "R", "value": "50", "nodes": ["out", "0"]}
 ],
 "directives": [
  ".tran 3m",
  ".meas vout AVG V(out) FROM 2.5m TO 3m"
 ]
}
```

