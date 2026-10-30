---
name: qspice-schematic-drafting
description: The schematic drafting standard (layout, orientation, wiring, grounds, labels, names, values, text placement) and the mandatory render -> review -> fix loop that makes any schematic clean. Read before drawing or editing.
---

# Drafting standard for QSPICE schematics + the visual feedback loop

A schematic is good when another engineer understands how the circuit works **at a glance**.
These rules follow the conventions of Mike Engelhardt (author of QSPICE and LTspice) and classic
industry practice. The server's reviewer (`review_schematic`) checks most of them automatically.

## Part A - The canvas

- Units: **100 = one grid step**. **y grows upward** (bigger y = higher on the page). x grows to the right.
- Everything electrical (pins, wire ends, wire corners, labels) sits on multiples of 100.
- Typical sizes: a resistor/capacitor/source is 400 long (pins 200 above and below its center); a
  transistor is about 400 tall; an op-amp about 800 x 800.
- A wire connects ONLY at its end points (and where an end point lands on another wire or pin).
  Two wires that cross do **not** connect. A wire running straight over a pin end DOES connect (avoid it).
- Same-named net labels are the same net. `GND` / net `0` is ground.

## Part B - Layout (where things go)

1. **Signal flows left to right.** Input source / input connector at the far left, output at the far right.
   Each stage sits to the right of the stage that drives it.
2. **Current flows top to bottom.** Positive supply at the top, ground at the bottom, negative supply
   below the circuit (or as a label at the bottom). Supply sources stand upright at the left or as labels.
3. **Ground symbols, not ground wires.** Put a ground symbol (triangle, pointing DOWN) directly below each
   part that returns to ground. Never route long wires back to one ground point.
4. **Group by function.** Keep the parts of one stage together (e.g. a transistor with its bias resistors);
   leave a little more space between stages than inside a stage.
5. **Align.** Put parts that share a node on the same horizontal or vertical line; keep the main signal
   path on one straight horizontal line where possible.
6. **Feedback paths go above (or below) the forward path,** flowing right-to-left back to the input
   (e.g. an op-amp's feedback resistor drawn above the op-amp).
7. **Symmetry shows function.** Differential pairs, bridges, push-pull stages and current mirrors are
   drawn mirror-symmetric.
8. **Compact but not crowded.** Leave at least 2-3 grid steps (200-300 units) between part bodies so texts
   fit; do not spread parts far apart with long wires. Big sheets: split into sub-sheets
   (`create_subsheet`) or use net labels between areas.
9. **No overlaps of any kind**: part over part, text over part, text over wire, text over text.

## Part C - Orientation of each kind of part

| Part | Draw it |
|---|---|
| Resistor / capacitor / inductor **in series with the signal** | horizontal |
| Resistor / capacitor / inductor **from a node to ground or supply (shunt)** | vertical, ground at the bottom |
| Voltage source | upright, `+` on top, `-` at the bottom to ground |
| Current source / behavioral source to ground | upright, ground at the bottom |
| Diode | points the way conventional current flows (anode -> cathode): top-to-bottom or left-to-right |
| NPN / NMOS | collector/drain up, emitter/source down, base/gate on the left (mirror for gate on the right) |
| PNP / PMOS | emitter/source up (toward the positive supply), collector/drain down |
| Op-amp / comparator | inputs on the left, output on the right; feedback network above it |
| ICs / blocks | inputs left, outputs right, supply pins top, ground pins bottom |

`auto_draw_schematic` does all of this automatically. When editing by hand use `rotation`
(0/90/180/270, counter-clockwise) and `mirror` (left-right flip).

## Part D - Wiring

1. Only horizontal and vertical wires, on the grid.
2. Never run a wire through a part body or over a pin of another part.
3. Never let wires of different nets touch or overlap. Never put two wires on top of each other.
4. Minimize crossings; a crossing has NO dot. If wires cross, they are not connected.
5. Every T-junction gets a dot (the tools add dots automatically). A 4-way joint is acceptable only
   with a dot; prefer T-junctions.
6. Few bends, short wires. A long wire across the sheet -> replace it with two net labels.
7. No dangling wire ends, no zero-length wires, no unconnected pins.
8. Use `connect_pins` (auto-router) or `add_component` instead of hand-drawn polylines.

## Part E - Grounds, supplies and net labels

1. Ground: one ground symbol per return point, always pointing down (`add_ground` does this).
2. Supplies: name the rail (`vcc`, `vdd`, `vee`, `5v`, `12v`) and attach a net label to each pin that needs
   it, at the top of the part. Draw the supply source once (usually at the left) with the same label.
3. Name every important net: `in`, `out`, `fb`, `sw`, `vref`, `bias`, `clk`. Plots and `.meas` refer to these
   names (`V(out)`), so naming them makes results readable.
4. Label names: short, start with a letter, no spaces or special characters (`vout_1`, not `V out!`).
   Do not use `0` or `gnd` for anything except ground.
5. A label must sit on a wire end or a pin. A label floating in space connects nothing.

## Part F - Reference designators and values

1. Every part has a unique refdes starting with its device letter:
   R resistor, C capacitor, L inductor, D diode, Q BJT, M MOSFET, J JFET, V voltage source,
   I current source, B behavioral source, E/F/G/H controlled sources, S/W switches, T transmission line,
   K coupling, X sub-circuit/IC, U op-amp/IC (QSPICE adds its own letter; that's fine).
2. Number them in reading order: left to right, top to bottom, along the signal path (R1, R2, R3 ...).
3. Values without units and without unnecessary characters:
   - `3.3` not `3.3V`; `10K` not `10kOhm`/`10 k`; `100n` not `100nF`/`0.1uF`; `.5` not `0.5`.
   - Use metric multipliers: f p n u m K **Meg** G T. `M` is MILLI. `2.2F` means 2.2 femto!
   - No spaces between number and multiplier (`1u`, not `1 u`).
   - Source functions without parentheses or commas: `PULSE 0 5 0 1u 1u .5m 1m`, `SINE 0 1 1K`.
4. Models: the value of a diode/transistor is its model name (`1N4148`, `2N3904`, `IRF540`); the
   `.model`/`.lib` text goes into the directives.

## Part G - Text placement

1. Vertical part: refdes **above** the value, both to the **right** of the symbol.
2. Horizontal part: refdes **above** the symbol, value **below** it.
3. Texts must not touch wires, other parts or other texts, and must stay close to their own part.
4. `fix_text_placement` puts every refdes/value at the best conventional spot automatically.
5. The directive block (`.tran ...`) goes below or beside the circuit, never over it. A title comment goes
   at the top-left.

## Part H - THE FEEDBACK LOOP (do this every time)

You cannot see the canvas while you edit. The reviewer and the renderer are your eyes. Run this loop
after drawing and after every few edits:

```
round = 0
repeat:
    round += 1
    r = review_schematic(path)            # score, issues (with 'fix'), picture with numbered circles
    if r.counts.error == 0 and r.score >= 90 and picture looks clean: STOP (done)
    fix issues in this order:
        1. connectivity errors  (UNCONNECTED_PIN, DANGLING_WIRE, FLOATING_LABEL, NO_GROUND, SHORTED_PART)
        2. PARTS_OVERLAP / PARTS_CROWDED  -> move_component(... dx/dy ...)   (wires follow)
        3. wiring problems (WIRE_THROUGH_PART, WIRES_OVERLAP, WIRE_OVER_PIN, FOUR_WAY_NO_DOT,
           MISSING_JUNCTION_DOT, DIAGONAL_WIRE) -> reroute_schematic()
        4. text problems (TEXT_OVERLAP, TEXT_ON_WIRE, TEXT_ON_PART, TEXT_FAR_FROM_PART)
           -> fix_text_placement()
        5. style (GROUND_NOT_DOWN, SOURCE_UPSIDE_DOWN, FLOW_RIGHT_TO_LEFT, ...) -> as the 'fix' says
    render_schematic(path) and LOOK (checklist below)
    if score went DOWN after an edit: undo_last_edit(path) and try a different fix
    if round > 8: beautify_schematic(path) (fresh layout) and review that file instead
```

Tips:
- Each editing tool already returns `review.score`, `score_before` and `new_problems`. Read them.
- Copy the `fix` text of an issue literally - it names the tool and its arguments.
- Fix several text problems at once with one `fix_text_placement()`; fix all wiring with one
  `reroute_schematic()`.
- When a part is in the wrong place, move it; don't add wires around it.

### Visual checklist (look at the rendered picture and answer each)

1. Can I follow the signal from the input at the left to the output at the right?
2. Is the positive supply at the top and ground at the bottom? Do all ground triangles point down?
3. Does any wire pass through a part or through text? Do any texts overlap?
4. Is every refdes/value right next to its own part (not floating between parts)?
5. Are there wires that cross where a connection was intended (no dot)? Or dots where wires only cross?
6. Are there long wires across the sheet that should be net labels?
7. Are sources upright with `+` on top? Diodes pointing in the current direction?
8. Is the drawing compact - no huge empty gaps, no parts squeezed together?
9. Are the important nets labelled (`in`, `out`, ...)?
10. Is the directive block readable and outside the circuit?

If any answer is wrong, fix it (move_component, reroute_schematic, fix_text_placement, add_net_label),
then review and render again.

## Part I - Reviewer issue codes

| Code | Meaning | Fix |
|---|---|---|
| UNCONNECTED_PIN (error) | a pin touches nothing | `connect_pins(from_pin='R1.2', to=...)` |
| DANGLING_WIRE (error) | a wire end touches nothing | `delete_items(wires_at=[[x,y]])` or connect it |
| FLOATING_LABEL (error) | label not on a wire/pin | delete it, `add_net_label(at='REF.PIN', ...)` |
| NO_GROUND (error) | no ground at all | `add_ground(at='REF.PIN')` |
| PARTS_OVERLAP (error) | two parts on top of each other | `move_component(ref, dx, dy)` |
| WIRE_THROUGH_PART (error) | wire crosses a body | `reroute_schematic()` |
| DUPLICATE_REFDES (error) | same name twice | `update_component(ref, new_ref=...)` |
| SHORTED_PART (warning) | both pins on one net | check wiring |
| WIRES_OVERLAP (warning) | wires on top of each other | `reroute_schematic()` |
| WIRE_OVER_PIN (warning) | wire passes over a pin | `reroute_schematic()` |
| FOUR_WAY_NO_DOT (warning) | 4 wires meet, no dot | `reroute_schematic()` |
| TEXT_OVERLAP / TEXT_ON_WIRE / TEXT_ON_PART / TEXT_FAR_FROM_PART (warning) | text placement | `fix_text_placement()` |
| SOURCE_UPSIDE_DOWN (warning) | ground pin on top | `move_component(ref, rotation=0)` |
| NO_ANALYSIS (warning) | no .tran/.ac/... | `set_directives([...])` |
| DIRECTIVE_UNKNOWN_NET (warning) | V(x) but no net x | `add_net_label(at=..., name='x')` |
| OFF_GRID_* / DIAGONAL_WIRE (warning) | geometry | `move_component` / redraw wire |
| MISSING_JUNCTION_DOT, EXTRA_JUNCTION_DOT, WIRE_CROSSING, PIN_TO_PIN, PARTS_CROWDED, GROUND_NOT_DOWN, SOURCE_SIDEWAYS, FLOW_RIGHT_TO_LEFT, INPUT_SOURCE_NOT_LEFT, LONG_WIRE (style) | cosmetic | follow `fix`; low priority |

Score: 100 minus 12 per error, 4 per warning, 1 per style issue (each category capped). A >= 90.

## Part J - Final checklist before you say "done"

- [ ] `review_schematic`: 0 errors, score >= 90.
- [ ] `render_schematic`: visual checklist passes.
- [ ] `netlist_schematic` or `validate_schematic`: the netlist has every part on the intended nets.
- [ ] Directives present (analysis + models), and `simulate` returns `ok: true`.
