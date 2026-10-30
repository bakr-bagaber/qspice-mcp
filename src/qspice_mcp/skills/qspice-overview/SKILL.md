---
name: qspice-overview
description: START HERE. What the QSPICE MCP server can do, which tool to use for which job, the golden workflows and the 12 rules that prevent most mistakes.
---

# QSPICE MCP server - overview (read this first)

QSPICE is a free, fast SPICE circuit simulator by Qorvo. This server lets you:

1. **Draw schematics** (.qsch files) that look like a careful engineer drew them.
2. **Check and improve drawings** with a reviewer that scores them and tells you how to fix each problem.
3. **Simulate** (.tran, .ac, .dc, .op, .noise, .step, .meas ...) and **read results** as numbers or pictures.
4. **Make new symbols** for parts QSPICE does not have, and **sub-sheets** (hierarchical blocks).
5. Search QSPICE's **help**, **symbols**, **models** and **examples**.

## Which tool do I use?

| I want to ... | Use |
|---|---|
| Draw a new circuit | `auto_draw_schematic(spec)` (read guide `auto-draw`) |
| Convert a SPICE netlist into a drawing | `netlist_to_schematic(netlist_text=...)` |
| Check how good a drawing is | `review_schematic(path)` |
| See the drawing | `render_schematic(path)` (`annotate='pins'` shows pin names) |
| Understand where things are (text) | `canvas_map(path)` and `read_schematic(path)` |
| Add one part and wire it | `add_component(path, symbol, connections={...})` |
| Move / rotate a part (wires follow) | `move_component(path, ref, dx=.., dy=..)` |
| Change a value | `update_component(path, ref, value=...)` |
| Tidy all wires | `reroute_schematic(path)` |
| Fix overlapping texts | `fix_text_placement(path)` |
| Undo a bad edit | `undo_last_edit(path)` |
| Redraw a messy sheet completely | `beautify_schematic(path)` |
| Add analysis commands | `set_directives(path, ['.tran 10m'])` |
| Simulate | `simulate(path)` |
| See waveforms | `plot_waveforms(path, ['V(out)'])` |
| Get exact numbers | `measure(path, ['.meas vmax MAX V(out)'])`, `get_trace_data` |
| Find a symbol | `list_symbols('opamp')`, then `get_symbol('OpAmp')` |
| Find a transistor/diode model | `search_models('2N3904')` |
| Make a symbol for a vendor model | `symbol_from_subckt(source=<file or .subckt text>)` |
| Make a symbol from scratch | `create_symbol(name, pins, kind='block')` |
| Make a reusable sub-circuit block | `create_subsheet(name, ports, spec)` |
| Look something up in QSPICE help | `help_search('pulse source')`, `help_topic('V-device')` |

## Golden workflow: a new schematic

1. Read `qspice_guide('drafting')` (how a good schematic looks) and `qspice_guide('auto-draw')` (spec format).
2. Write the spec: every part with `ref`, `symbol`, `value` and its nets. Use net `0` for ground and
   meaningful names for important nets (`in`, `out`, `vcc`, `fb`, `sw`).
3. `auto_draw_schematic(spec, path='name.qsch')`. Check: `validation.ok` is `true`, `review.score` >= 90.
4. `review_schematic(path)`. For every **error** and **warning** apply the `fix` it suggests.
5. `render_schematic(path)` and **look at the picture** (checklist in the drafting guide).
6. Repeat 4-5 until: score >= 90, 0 errors, and the picture looks clean. Never more than ~8 rounds;
   if stuck, `beautify_schematic` or change the spec (position hints) and redraw.
7. `simulate(path)` -> `plot_waveforms` / `measure`. Report numbers with units.

## Golden workflow: editing an existing schematic

1. `render_schematic(path, annotate='pins')` + `canvas_map(path)` to understand the sheet.
2. Make ONE change at a time with the editing tools. Each edit returns `review` (score before/after and
   `new_problems`). If the score dropped or new errors appeared: fix them, or `undo_last_edit(path)`.
3. Finish with `review_schematic` and `render_schematic`.

## The 12 rules

1. Coordinates: 100 units = 1 grid step. **y grows UPWARD** (bigger y = higher on the page).
2. Every pin and wire end must be on a multiple of 100.
3. Pins are named `REF.PIN`: `R1.1`, `R1.2`, `C1.1`, `V1.+`, `V1.-`, `D1.A`, `D1.K`, `Q1.C`, `Q1.B`, `Q1.E`,
   `M1.D`, `M1.G`, `M1.S`, `U1.IN+`, `U1.IN-`, `U1.OUT`. Pin numbers work too (`C1.1` = first pin).
4. Ground is the net `0` (in specs) or `GND` (in tools). Every circuit needs ground.
5. Values have NO units: `10K` not `10kOhm`, `100n` not `100nF`, `3.3` not `3.3V`, `1Meg` for mega
   (`1M` means 1 milli!). Multipliers: f p n u m K Meg G T.
6. Reference designators are unique and start with the device letter: R, C, L, D, Q, M, J, V, I, E, F,
   G, H, B, S, W, T, X (subcircuits), U (ICs/op-amps are fine; QSPICE adds the device letter itself).
7. A simulation needs an analysis directive: `.tran`, `.ac`, `.dc`, `.op` or `.noise`.
8. Prefer `auto_draw_schematic` over placing parts one by one. Prefer `add_component` over
   `place_component` + manual wires.
9. Never draw wires through parts, never let different nets touch, never leave a pin unconnected.
10. After drawing, ALWAYS review and LOOK at the render. The reviewer's `fix` field tells you the exact call.
11. Relative paths go to the server's work folder (see `qspice_info`). Use absolute paths when the user
    names a folder.
12. When a tool returns an error message, read it: it usually names the valid options (pins, symbols, files).

## Guides (skills)

Call `qspice_guide(name)`:

- `drafting` - the drafting standard and the review/visual-feedback loop (most important)
- `auto-draw` - full spec format with examples
- `canvas` - editing an existing sheet, coordinates, recipes
- `symbols` - new symbols, vendor models, sub-sheets (hierarchy)
- `simulation` - analyses, sources, directives, running simulations
- `waveforms` - traces, plots, .meas, expressions, exporting
- `models` - .model/.subckt/.lib, finding and using device models
- `netlist-import` - SPICE/LTspice netlist to schematic
- `troubleshooting` - error messages and how to fix them
- `recipes` - tested specs for common circuits (copy and adapt)
