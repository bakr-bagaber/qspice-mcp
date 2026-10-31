# Changelog

## 0.2.0 - 2026-10-31

Focus: make *any* LLM (including small models) able to draw clean schematics and use QSPICE well.

### Seeing the canvas
- **`review_schematic`** - a drafting reviewer: scores a sheet 0-100 (A >= 90) against professional
  drafting rules (connectivity, wiring, placement, text, conventions; ~35 issue types) and gives the
  exact tool call that fixes each problem. Returns a picture with numbered issue circles.
- Every editing tool now returns a **review delta** (`score`, `score_before`, `new_problems`).
- **`canvas_map`** - a text picture of the grid plus, per part, its pins (x, y, direction, net) and the
  free space on each side.
- `render_schematic(annotate='pins'|'coords'|'full', show_issues=True)` - pin names / coordinates and
  reviewer marks drawn on the picture.

### Editing
- **`add_component`** - place a part *and* wire it in one call (`connections={'1':'R1.2','2':'GND'}`);
  chooses a free, tidy spot and orientation, routes wires, adds grounds/labels and places texts.
- **`move_component`** - move/rotate; wires follow (connectivity is kept and the sheet re-routed).
- **`reroute_schematic`** - rip up and redraw all wiring neatly, parts stay.
- **`fix_text_placement`** - optimizer that places refdes/value texts conventionally without collisions.
- **`undo_last_edit`** - 30-step history per file.
- Ground symbols always point down (L-shaped stubs for sideways pins); labels pick a free stub direction;
  `connect_pins` accepts `GND` or a net name as target and never draws on top of existing wiring.
- `place_component` warns (with a fix) when a part lands on another part.

### Symbols and hierarchy
- **`create_symbol`** - block / two-terminal / amplifier / custom-graphics templates following QSPICE
  conventions (pins on grid at lead ends, refdes + value texts), validated, saved and registered.
- **`symbol_from_subckt`** - symbol for a vendor model from `.subckt` text, a model file or a known name
  (pin order from the `.subckt` line; pasted text is saved and linked with `.lib`).
- **`create_subsheet`** - hierarchical sub-schematics with port labels; usable as `symbol='<child>.qsch'`
  in `place_component`/`add_component`/auto-draw specs.
- `add_symbol_folder` - persistent extra symbol/model folders.

### Auto-draw
- Text-placement optimizer and reviewer run on every drawing (`review` in the result).
- Values are normalised (`10kΩ` -> `10k`, `100nF` -> `100n`, `1MΩ` -> `1Meg`) with notes; `1M` on a
  resistor is flagged (milli!).
- Pin-name aliases (`inp`, `non_inverting`, `vcc` for `Vdd`, `anode`, ...).
- `ports` for sub-sheets; long `.model` lines wrapped into `+` continuation lines.
- Helpful "did you mean" suggestions for unknown symbols.

### Skills (guides)
11 skills shipped with the server and exposed through `qspice_guide`, MCP prompts and resources:
overview, schematic drafting standard + visual feedback loop, auto-draw spec, canvas editing,
symbols & hierarchy, simulation, waveforms & measurements, models, netlist import, troubleshooting,
and 15 tested circuit recipes. `qmcp export-skills` copies them into `~/.claude/skills`.

### Tooling
- `qmcp` command-line bridge (`tools`, `help`, `call`, `guide`, `export-skills`): the same tools for
  shell-based agents and for testing.
- Symbol library cache now uses a stable signature (startup 5 s -> 0.05 s).
- Smoke test covers the new tools through the real MCP protocol.

### Found and fixed by testing with Claude Haiku and Sonnet (see TESTING.md)
- Tool errors reach the model with their real message (the MCP SDK hid the text of most exceptions).
- Edit results report `connectivity_changes` (nets an edit split or shorted).
- Auto-draw labels every net you named, so `V(name)` works in plots and `.meas`; the result lists
  nets that stayed unlabelled and reports layout stats instead of router internals.
- `delete_items`: a point inside a wire deletes only that segment; deleting a label removes its stub.
- Automatic net names never collide with label names; `beautify_schematic` names unnamed supply nets.
- Plots and trace data accept expressions (`V(out)-V(in)`, `mag(V(out))`); hierarchical current
  names (`I(R1.X1)`) work in `measure`.
- `.meas` results of `.step` runs come back per step.
- Parts sharing a designator are addressed as `C1#2` in review messages and every editing tool.
- `connect_pins` accepts a point `[x, y]` as target (e.g. the loose end of a dangling wire).
- `help_search` ranks pages properly (BM25 + page titles + synonyms: "behavioral voltage source" ->
  B-device, "sweep" -> .step); `help_topic('index')` lists page titles.
- Text placement: designed symbol texts move as a group; long source values hang off the body end;
  net labels pick the least cluttered side; the directive block never covers labels or parts.
- Reviewer: proportional text widths; text beside an op-amp triangle is no longer "on the part".
- `add_component`: series parts continue the signal path; net-name targets land near that net.
- Auto-draw is 3-4x faster (cached placement costs): a 21-part sheet takes 3 s (effort 1) / 12 s
  (effort 2), with identical results.
- Round 2: the router no longer draws up-over-down bumps between shunt parts (sideways entry into the
  end pins of two-terminal parts); wiring tools re-place texts a new wire crosses; `search_models`
  includes VDMOS power MOSFETs in NMOS/PMOS searches and shows ratings; CSV export line ends fixed;
  `get_trace_data` statistics over exactly the requested window; `.op` values returned by `simulate`;
  `measure` leaves no files behind; review deltas survive renames; shunt loads stand vertical and signal
  sources share a baseline in auto-draw; net labels avoid part texts; `copy_schematic`; CLI accepts
  `value=10` as text, a bare path, and tool names without `call`; netlist notation explained.
- `tests/llm`: the LLM test harness (20 tasks, fixtures, reference answers, graders).

## 0.1.0 - 2026-10-30
Initial release: simulation, netlisting, waveforms, QPOST measurements, help/symbol/model search,
renderer, auto-draw engine and canvas editing.
