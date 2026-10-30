---
name: qspice-auto-draw
description: How to describe a circuit for auto_draw_schematic (the spec format, pin names of every common symbol, position hints, options), how to read its result and how to improve a drawing.
---

# auto_draw_schematic - draw a whole circuit from a description

`auto_draw_schematic(spec, path)` turns a list of parts and nets into a finished, neat .qsch:
it chooses positions and orientations (signal left->right, supplies up, ground down), routes wires
without overlaps, adds ground symbols, supply/net labels and junction dots, places refdes/value texts,
validates the drawing against QSPICE's own netlister and scores it with the reviewer.

## Step by step

1. List every part of the circuit: refdes, symbol, value, and the net each pin connects to.
2. Name nets: `0` = ground; `in`, `out` for the signal input/output; `vcc`/`vdd`/`5v`/`12v` for positive
   supplies, `vee`/`vss` for negative supplies; anything else short (`b`, `e`, `fb`, `sw`, `inv`).
3. Add directives: an analysis (`.tran`, `.ac`, `.dc`, `.op`), `.model` lines for models not in QSPICE's
   library, `.meas` lines for numbers you want.
4. Call `auto_draw_schematic(spec=..., path='my_circuit.qsch')`.
5. Read the result (below), then follow the loop in `qspice_guide('drafting')`.

## The spec

```json
{
  "title": "Inverting amplifier, gain -10",
  "components": [
    {"ref": "V1", "symbol": "V", "value": "SINE 0 .1 1K", "nodes": ["in", "0"]},
    {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["in", "inv"]},
    {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["inv", "out"]},
    {"ref": "U1", "symbol": "OpAmp",
     "pins": {"IN+": "0", "IN-": "inv", "OUT": "out", "Vdd": "vcc", "Vss": "vee"}},
    {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
    {"ref": "V3", "symbol": "V", "value": "-12", "nodes": ["vee", "0"]}
  ],
  "directives": [".tran 5m"]
}
```

### Component fields

| Field | Required | Meaning |
|---|---|---|
| `ref` | yes | unique reference designator (`R1`, `C3`, `Q2`, `U1`, `X1`) |
| `symbol` | yes | symbol name (table below, or `list_symbols`), a `.qsym` path, or a sub-sheet `.qsch` path |
| `value` | usually | value / model / source function: `10K`, `100n`, `2N3904`, `PULSE 0 5 0 1u 1u 5u 10u` |
| `nodes` | one of these | list of nets **in the symbol's pin order** (see table) |
| `pins` | one of these | `{"pin name": "net"}` - safer for transistors/op-amps/ICs |
| `params` | no | instance parameters, e.g. `{"Rser": "10m"}`, op-amp `{"GBW": "10Meg"}`, switch `{"Ron": "10m"}` |
| `attributes` | no | extra text lines passed to the netlist |
| `position` | no | `[x, y]` fixes the part there (use sparingly) |
| `rotation` / `mirror` | no | force an orientation (0/90/180/270 counter-clockwise; mirror = flip left-right) |

Every required pin must get a net. Optional pins (marked `(opt)` below) may be left out.

### Top-level fields

| Field | Meaning |
|---|---|
| `components` | the parts (required) |
| `directives` | list of SPICE commands placed on the sheet (`.tran 10m`, `.model ...`, `.meas ...`) |
| `title` | a comment shown at the top-left of the sheet |
| `inputs` / `outputs` | net names that are the signal input/output (normally detected automatically) |
| `rails` | `{"vcc": "+", "vee": "-"}` marks supply nets explicitly (normally detected from names) |
| `label_nets` | `"auto"` (default: label in/out/fb/... and nets used in directives), `"all"`, `"none"`, or a list of names |
| `ports` | for sub-sheets: nets that become hierarchical ports |
| `options` | `{"effort": 1-3, "style": "compact" or "normal" or "spacious", "seed": 1}` |

`effort` 3 tries more layouts (slower, often nicer for big circuits). Change `seed` to get a different
layout. `style: "spacious"` leaves more room between parts.

### Pin names of common symbols (SPICE order)

| symbol | what | pins in order |
|---|---|---|
| `R` | resistor | `1`, `2` |
| `C` | capacitor | `+`, `-` (or `1`, `2`) |
| `C2` | polarized capacitor | `+`, `-` |
| `L` | inductor | `1`, `2` |
| `V` | voltage source | `+`, `-` |
| `I` | current source | `-`, `+` (use names: `{"+": ..., "-": ...}`) |
| `D`, `Zener`, `Schottky`, `LED` | diodes | `A` (anode), `K` (cathode) |
| `NPN`, `PNP` | BJTs | `C`, `B`, `E` |
| `NMOS`, `PMOS` | MOSFETs | `D`, `G`, `S` |
| `NJF`, `PJF` | JFETs | `D`, `G`, `S` |
| `OpAmp` | generic op-amp | `Vdd`, `Vss`, `OUT`, `IN-`, `IN+` (+ optional `MULT+`, `MULT-`, `IN--`, `IN++`, `EN`) |
| `SW` | voltage-controlled switch | `1`, `2` (switch), `+`, `-` (control) |
| `CSW` | current-controlled switch | `1`, `2` |
| `E` | voltage-controlled voltage source | `+`, `-`, `+C`, `-C` |
| `G` | voltage-controlled current source | `-`, `+`, `+C`, `-C` |
| `F`, `H` | current-controlled sources | `+`, `-` (value: `<Vsense> <gain>`) |
| `B1` / `B2` | behavioral voltage / current source | `+`, `-` / `-`, `+` (value `V=...` / `I=...`) |
| `T` | transmission line | `L1`, `L2`, `R1`, `R2` |
| `Y` | crystal | `+`, `-` |
| `Transformer` | 2-winding transformer | `PRI+`, `PRI-`, `SEC+`, `SEC-` (value `Turns=1 1`) |
| `AND2`, `OR2`, `XOR2`, `INV`, ... | logic gates | `Vdd`, `Vss`, `Q`, `¬Q`, inputs `b0`, `b1`, ... |

Pin names are case-insensitive and common aliases work (`anode`, `cathode`, `collector`, `base`,
`emitter`, `drain`, `gate`, `source`, `inp`/`non_inverting`, `inn`/`inverting`, `output`, `vcc` for
`Vdd`, `vee` for `Vss`). If unsure: `get_symbol('NAME')`.

### Values and models

- Passive values: `4.7K`, `100n`, `22u`, `1Meg`, `.5` (no units; `M` = milli, `Meg` = mega).
  The server rewrites `10kΩ` / `100nF` / `3.3V` into `10k` / `100n` / `3.3` and tells you in `notes`.
- Sources: `12` (DC), `AC 1`, `SINE 0 1 1K`, `PULSE 0 5 0 10n 10n 5u 10u`, `PWL 0 0 1m 5`,
  combined `SINE 0 1 1K AC 1`. See `qspice_guide('simulation')`.
- Transistors/diodes: the value is the model name. Bundled models (no .model needed): diodes `1N4148`,
  `1N4007`, `1N5819` (Schottky); MOSFETs `2N7002`, `IRF640`; JFETs `2N5457`, `J310`; many Zetex BJTs
  (`search_models`). Others (e.g. `2N3904`) need a `.model` line in `directives`.
- Op-amp `OpAmp`: a generic rail-to-rail model; set `params` `GBW`, `Slew`, `Avol`, `Rload`, `Phi`.
- Switch `SW`: set `params` `Ron`, `Roff`, `Vt` (threshold), `Vh` (hysteresis).

## Reading the result

- `validation.ok: true` -> QSPICE's netlister sees exactly the connections you described. If `false`,
  read `validation.problems`.
- `review.score` (0-100) and `review.issues` -> drawing quality. >= 90 is good.
- `notes` -> value rewrites, refdes renames, label fallbacks.
- `label_fallbacks` -> nets the router could not wire cleanly, so they were connected with net labels
  (electrically fine).
- The picture -> LOOK at it (visual checklist in `qspice_guide('drafting')`).

## Making it nicer

1. Review first: `review_schematic(path)`, then apply the fixes.
2. Want a different arrangement? Re-run with `options: {"effort": 3}` or another `seed`.
3. Want a part somewhere specific? Add `"position": [x, y]` to it (and maybe to 1-2 neighbours).
   Leave most parts free so the engine can arrange them.
4. Too crowded? `"style": "spacious"`. Too spread out? `"style": "compact"`.
5. Many parts (> 25)? Split into sub-sheets (`qspice_guide('symbols')`) or draw stages separately.
6. To change a single part afterwards use the canvas tools (`qspice_guide('canvas')`), not a full redraw.

## Common mistakes

- Forgetting a pin -> error "pins [...] are not connected". Give every pin a net.
- Using `nodes` in the wrong order for a transistor -> use `pins` with names instead.
- Two parts with the same `ref` -> error. Refdes must be unique.
- Using `gnd`/`GND` is fine - it is the same as `0`.
- Writing `1M` for a megaohm resistor (it means 1 milliohm) -> write `1Meg`.
- Missing `.model` for a transistor that is not in QSPICE's library -> simulation error
  "unknown model". Add the `.model` line or pick a bundled model (`search_models`).
