---
name: qspice-canvas-editing
description: How to understand and edit an existing QSPICE schematic - coordinates, reading the canvas (canvas_map, annotated renders), every editing tool with examples, step-by-step recipes (add/insert/replace/move parts, labels, grounds), undo, and pitfalls.
---

# Editing a schematic on the canvas

## 1. Understand the canvas first

- Units: 100 = one grid step. **y grows UPWARD**. Pins, wire ends and labels are on multiples of 100.
- A part's `x, y` is its origin (the green cross in `get_symbol` pictures); its pins are at fixed
  offsets from it. `read_schematic` lists every pin's absolute position and net.
- Rotation is counter-clockwise in steps of 90; `mirror` flips left-right.

Three ways to "see" (use all three when a sheet is new to you):

| Tool | Gives you |
|---|---|
| `render_schematic(path, annotate='pins')` | picture with every pin labelled `REF.PIN` |
| `render_schematic(path, annotate='coords', grid=True)` | picture with exact pin coordinates and the grid |
| `canvas_map(path)` | text picture of the grid + per part: pins (x, y, direction, net) and **free space** left/right/up/down |
| `read_schematic(path)` | full structured data (parts, pins, nets, wires, labels, directives, problems) |
| `review_schematic(path)` | the problems, each with the exact fixing call; picture with numbered circles |

`canvas_map` legend: `#` part body, `o` pin, `-` `|` wires, `+` corner/tee, `*` junction dot,
`x` crossing (not connected), `G` ground, `[name]` net label. Rows go down the page (y decreasing).

## 2. The editing tools

Every editing tool saves the file, keeps an undo history and returns a `review` delta:
`score`, `score_before`, `errors`, `warnings`, `new_problems`. Read it after each call.
If an edit changed which pins are connected, the result also contains `connectivity_changes`
(`SPLIT: net 'out' is now broken into 2 separate nets ...` or `SHORT: nets [...] are now connected
together`). Unless you meant to do exactly that, call `undo_last_edit` immediately.

Net names: only nets with a **label** have a name in the simulation. An unlabelled net gets an
automatic name like `N03` (shown by `read_schematic` / `netlist_schematic`). Before using `V(mid)` in a
plot or `.meas`, make sure a label `mid` exists (`add_net_label`).

| Tool | Use for | Example |
|---|---|---|
| `add_component` | add a part AND wire it (best) | `add_component(path, symbol='C', value='10u', connections={'1':'Q1.E','2':'GND'})` |
| `place_component` | put a part at exact x,y (no wires) | `place_component(path, symbol='R', x=1200, y=400, rotation=90)` |
| `connect_pins` | wire pin -> pin / point / ground / net | `connect_pins(path, from_pin='R1.2', to='C1.1')` |
| `connect_many` | several connections | `connect_many(path, [['R1.2','C1.1'], ['C1.2','GND']])` |
| `add_net_label` | name a net / connect by name | `add_net_label(path, at='U1.OUT', name='out')` |
| `add_ground` | ground symbol (points down) | `add_ground(path, at='C1.2')` |
| `move_component` | move/rotate, wires follow | `move_component(path, ref='R3', dx=300)` |
| `update_component` | value / params / rename | `update_component(path, ref='R1', value='4.7K')` |
| `delete_items` | delete parts, wires, labels | `delete_items(path, refs=['R5'])`, `wires_at=[[x,y]]` (a point in the MIDDLE of a segment deletes only that segment; a corner/junction point deletes every segment ending there), `labels_at=[[x,y]]` (also removes the label's stub) |
| `add_wire` | explicit polyline (rarely needed) | `add_wire(path, points=[[0,0],[0,400],[600,400]])` |
| `set_directives` | analysis/model/meas commands | `set_directives(path, ['.tran 10m', '.meas vmax MAX V(out)'])` |
| `add_comment` | a note or title | `add_comment(path, 'Bias network', x=-400, y=1200)` |
| `reroute_schematic` | redraw ALL wires neatly, parts stay | `reroute_schematic(path)` |
| `fix_text_placement` | re-place refdes/value texts | `fix_text_placement(path)` |
| `undo_last_edit` | go back one (or n) edits | `undo_last_edit(path)` |

Connection targets (in `add_component.connections` and `connect_pins.to`):
`'R1.2'` a pin, `'GND'` ground, `'out'` a net name (net label), `[x, y]` a point (e.g. a free wire end).

## 3. Recipes

### Add a part between two existing nodes
`add_component(path, symbol='R', value='10K', connections={'1': 'Q1.B', '2': 'GND'})`
The server picks a free spot near Q1.B, orients the resistor (vertical to ground), wires it, adds the
ground symbol and places the texts.

### Insert a part INTO an existing wire (series)
1. `read_schematic` -> find the wire between the two pins (e.g. R1.2 -> C1.1 at points ...).
2. `delete_items(path, wires_at=[[x, y]])` with a point on that wire.
3. `add_component(path, symbol='L', value='10u', connections={'1': 'R1.2', '2': 'C1.1'})`.
4. `review_schematic(path)`.

### Replace a part with a different symbol
1. Note its connections (`read_schematic` -> its pins' nets).
2. `delete_items(path, refs=['D1'])` (dangling stubs are removed automatically).
3. `add_component(path, symbol='Schottky', value='1N5819', ref='D1', connections={'A': ..., 'K': ...})`
   using pins of neighbouring parts or net names as targets.

### Change only a value
`update_component(path, ref='C2', value='47u')` - nothing else moves.

### Move or rotate a part
`move_component(path, ref='Q1', dx=200, dy=-100)` or `move_component(path, ref='V1', rotation=0)`.
All wires are re-routed, connections are kept. If the target overlaps another part, the nearest
free spot is used (`adjusted` in the result).

### Connect two far-apart nodes
Prefer net labels over long wires:
`add_net_label(path, at='U1.OUT', name='vout')` and `add_net_label(path, at='R7.1', name='vout')`.

### Add a supply rail to a pin
`add_net_label(path, at='R1.1', name='vcc')` (the supply source elsewhere has the same label).

### Ground a pin
`add_ground(path, at='C3.2')` - always points down, with a short stub.

### Mark a node for plots / .meas
`add_net_label(path, at='C1.1', name='out')` then `.meas vmax MAX V(out)`.

### Clean up a messy sheet
1. `review_schematic` -> fix connectivity errors first.
2. Overlapping parts -> `move_component` (see each issue's `fix`).
3. `reroute_schematic(path)` -> all wires redrawn cleanly.
4. `fix_text_placement(path)`.
5. If still bad: `beautify_schematic(path)` writes a completely re-laid-out copy `<name>_neat.qsch`.

## 4. Choosing coordinates yourself (place_component)

1. `canvas_map(path)` -> look at `free_space` of the neighbouring part, e.g. `right: gap 900`.
2. Pick a position at least 300 units away from other bodies, on the 100 grid, in the signal-flow
   direction (to the right of what drives it, above ground symbols, below supply labels).
3. After placing, wire it with `connect_pins` / `add_ground` / `add_net_label`.
4. If the result says `WARNING ... overlaps`, use the suggested `move_component`.

## 5. Pitfalls

- Crossing wires do NOT connect. Use `connect_pins` (it creates proper T-junctions).
- A label must touch a wire end or pin, otherwise it is floating (FLOATING_LABEL).
- Don't hand-draw wires through parts; let the router do it.
- `place_component` does not connect anything; unconnected pins are errors.
- One change at a time, then read the returned `review`. Use `undo_last_edit` when something got worse.
- Refdes letters matter: a resistor must be `R...`, a capacitor `C...` (QSPICE uses the first letter).
