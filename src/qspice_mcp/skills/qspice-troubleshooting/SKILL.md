---
name: qspice-troubleshooting
description: What to do when a tool or simulation fails - tool error messages, simulation errors (singular matrix, timestep too small, unknown model/subcircuit, floating nodes), wrong-looking results, and drawing problems that will not go away.
---

# Troubleshooting

Always read the error text first - most tool errors name the valid options.

## 1. Tool errors

| Error says | Do this |
|---|---|
| `unknown symbol 'X'. Did you mean: ...` | use one of the suggestions, or `list_symbols('words')`; for vendor parts `symbol_from_subckt` |
| `REF: symbol S has no pin 'P'. Pins: [...]` | use one of the listed pin names (or `get_symbol('S')`) |
| `pins [...] are not connected` | every required pin needs a net in `nodes`/`pins` |
| `duplicate reference designator` | make refdes unique |
| `... does not exist. Files in that folder: [...]` | fix the path; relative paths are inside the work folder |
| `no component R7` | check refs with `read_schematic` |
| `no clean route found` | connect with net labels (`connect_pins(..., to='<net name>')` on both pins) or move a part |
| `only N earlier version(s) available` | undo history is exhausted |

## 2. Simulation errors (`simulate` -> `ok: false`)

| Symptom / message | Likely cause | Fix |
|---|---|---|
| no analysis / nothing simulated | no `.tran/.ac/.dc/.op` | `set_directives` |
| unknown / undefined model | `.model` or `.lib` missing, typo in value | add the model; `search_models` |
| unknown subcircuit | the `.subckt` file is not included | `.lib <file>`; or recreate the symbol with `library_file` |
| singular matrix / floating node | a node has no DC path to ground (capacitor-only node, open input, missing ground) | `check_floating_nodes(path)`; add ground; add a large resistor (e.g. 1G) to ground |
| timestep too small / convergence failure | ideal switches with no resistance, huge dv/dt, unrealistic models | add `Rser` to sources/capacitors, finite `Ron/Roff`, rise times > 0, `.options reltol=1e-3`, try `engine='QSPICE80'` |
| voltage source loop / inductor-only loop | two V sources or V + L in parallel | add a small series resistance (`Rser=1m`) |
| very slow simulation | MAXSTEP too small, very long `.tran` | larger MAXSTEP, shorter time, `.save` only needed traces |
| `.meas` result missing | condition never met (e.g. WHEN level never reached) | plot the trace, adjust the level/window |

## 3. Results look wrong

1. Plot the inputs first - is the source what you intended? (`PULSE` Ton/Tperiod, `AC 1` for `.ac`)
2. Check the netlist: `netlist_schematic(path)` - are parts on the expected nodes? Op-amp supplies?
3. Check pin order of custom symbols (`get_symbol`) against the `.subckt` line.
4. Units: `1M` is 1 milli, `1Meg` is mega; `2.2F` is femto; `10u` vs `10n`.
5. Transistor polarity: NPN vs PNP, NMOS vs PMOS, source/drain swapped.
6. Not settled yet: measure later in the transient, or simulate longer.

## 4. Drawing problems that won't go away

1. `undo_last_edit` back to the last good state.
2. `reroute_schematic(path)` - redraws all wires.
3. Move the crowded part: `move_component(ref, dx=300)`.
4. `beautify_schematic(path)` - fresh layout in `<name>_neat.qsch`; review that one.
5. Redraw from the spec with `options: {"effort": 3}` or another `seed`, or add `position` hints.
6. Too many parts on one sheet: split into sub-sheets (`create_subsheet`).

## 5. Environment

- `qspice_info()` shows the QSPICE folder, work folder and library sizes.
- The QSPICE GUI: `open_in_qspice(path)` lets the human user see the sheet in QSPICE itself.
