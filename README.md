# QSPICE MCP Server

An MCP server that gives LLMs full access to **QSPICE** (Qorvo) - simulation, netlisting, waveforms,
measurements, help, symbol and model libraries - and adds what QSPICE doesn't have:
**schematic drawing that any LLM can do well**, with a reviewer, spatial views, smart editing tools,
symbol creation and step-by-step skills.

## Highlights (v0.2)

- **auto_draw_schematic**: circuit description -> neat, validated .qsch (signal left->right, supplies up,
  ground down, no overlaps, labels, junction dots, conventional refdes/value placement).
- **review_schematic**: scores a sheet 0-100 against professional drafting rules and gives the exact
  tool call that fixes each issue - the "eye" that lets a model iterate *render -> review -> fix*.
- **canvas_map / annotated renders**: the canvas as text (grid map, pins, free space) and as pictures
  with pin names or coordinates.
- **Smart editing**: `add_component` (place + wire in one call), `move_component` (wires follow),
  `reroute_schematic`, `fix_text_placement`, `undo_last_edit`; every edit returns a review delta.
- **Symbols**: `create_symbol` (block, two-terminal, amplifier, custom graphics), `symbol_from_subckt`
  (vendor models), `create_subsheet` (hierarchical blocks).
- **Skills**: 11 guides (drafting standard + feedback loop, auto-draw spec, canvas editing, symbols,
  simulation, waveforms, models, netlist import, troubleshooting, 15 tested recipes) served by
  `qspice_guide`, MCP prompts and resources, and exportable as Claude skills.

## Tools (51)

| Area | Tools |
|---|---|
| Guides & docs | `qspice_guide`, `drawing_guide`, `qspice_info`, `help_search`, `help_topic`, `list_examples` |
| Libraries | `list_symbols`, `get_symbol`, `search_models`, `get_model_text` |
| Symbols | `create_symbol`, `symbol_from_subckt`, `create_subsheet`, `add_symbol_folder` |
| Simulation | `simulate`, `netlist_schematic`, `check_floating_nodes` |
| Results | `list_traces`, `get_trace_data`, `evaluate_expressions`, `export_waveforms`, `measure`, `plot_waveforms` |
| QSPICE extras | `dll_variables`, `protect_netlist`, `open_in_qspice`, `launch_tool` |
| See & check | `read_schematic`, `render_schematic`, `review_schematic`, `canvas_map`, `validate_schematic` |
| Create | `auto_draw_schematic`, `netlist_to_schematic`, `beautify_schematic`, `new_schematic` |
| Edit | `add_component`, `place_component`, `move_component`, `update_component`, `delete_items`, `connect_pins`, `connect_many`, `add_wire`, `add_net_label`, `add_ground`, `set_directives`, `add_comment`, `reroute_schematic`, `fix_text_placement`, `undo_last_edit` |

Prompts: `draw_circuit`, `review_and_fix`, `simulate_and_report`. Resources: `qspice://skills`,
`qspice://skills/{name}`.

## Install / register

Requires Windows, Python >= 3.12 and QSPICE (auto-detected; override with `QSPICE_DIR`).

```powershell
git clone https://github.com/bakr-bagaber/qspice-mcp
cd qspice-mcp
uv sync
```

Claude Desktop: *Settings -> Developer -> Edit Config*, merge in `claude_desktop_config.snippet.json`
(adjust the path), restart:

```json
{
  "mcpServers": {
    "qspice": {
      "command": "C:\\path\\to\\qspice-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "qspice_mcp.server"],
      "env": { "QSPICE_MCP_WORKDIR": "C:\\Users\\<you>\\Documents\\QSPICE-MCP" }
    }
  }
}
```

Claude Code: `claude mcp add qspice -- C:\path\to\qspice-mcp\.venv\Scripts\python.exe -m qspice_mcp.server`

Skills for Claude Code / Claude Desktop (optional - the server also serves them itself):
`.venv\Scripts\python.exe -m qspice_mcp.cli export-skills` (copies them to `~/.claude/skills`).

### Command-line bridge

`qmcp.cmd` (or `python -m qspice_mcp.cli`) exposes the same tools without MCP - handy for scripts,
shell-based agents and testing:

```
qmcp tools                                   list tools
qmcp help auto_draw_schematic                parameters of a tool
qmcp guide drafting                          read a skill
qmcp call review_schematic path=C:\work\amp.qsch
qmcp call auto_draw_schematic C:\work\args.json      (complex arguments from a JSON file)
```

### Environment variables
* `QSPICE_DIR` - QSPICE install folder (default: auto-detect, e.g. `C:\Program Files\QSPICE`)
* `QSPICE_MCP_WORKDIR` - where relative file names are created (default `~/Documents/QSPICE-MCP`)
* `QSPICE_SYMBOL_PATH` - extra folders (`;`-separated) with your own `.qsym`/`.qsch`/model files

Caches (symbol library, help index, model index, settings) live in `%LOCALAPPDATA%\qspice-mcp`.
Symbols you create go to `<work folder>\symbols`; edit history to `<work folder>\.history`.

## How an LLM should use it

1. `qspice_guide()` -> read `overview`, then `drafting` before drawing.
2. `auto_draw_schematic(spec)` for a new circuit (spec format: `qspice_guide('auto-draw')`, tested
   examples: `qspice_guide('recipes')`).
3. `review_schematic(path)` -> apply the suggested fixes -> `render_schematic(path)` and look ->
   repeat until score >= 90 with 0 errors.
4. `simulate(path)` -> `plot_waveforms` / `measure`.

Example spec:

```json
{"title": "Inverting amplifier, gain -10",
 "components": [
  {"ref": "V1", "symbol": "V", "value": "SINE 0 .1 1K", "nodes": ["in", "0"]},
  {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["in", "inv"]},
  {"ref": "R2", "symbol": "R", "value": "10K", "nodes": ["inv", "out"]},
  {"ref": "U1", "symbol": "OpAmp", "pins": {"IN+": "0", "IN-": "inv", "OUT": "out", "Vdd": "vcc", "Vss": "vee"}},
  {"ref": "V2", "symbol": "V", "value": "12", "nodes": ["vcc", "0"]},
  {"ref": "V3", "symbol": "V", "value": "-12", "nodes": ["vee", "0"]}],
 "directives": [".tran 5m", ".meas vpk MAX V(out)"]}
```

## How the drawing engine works
1. The spec is normalised against QSPICE's real symbols (all `.qsym` files plus every symbol embedded
   in the bundled examples and help files), values and pin names are normalised.
2. Nets are classified (ground, supply rails drawn as labels, signal nets ranked along the signal path).
3. Placement: layered left->right start, refined by simulated annealing whose cost encodes the drafting
   conventions of Mike Engelhardt's *Empirically Found Drafting Guidelines* (shipped with QSPICE).
4. Several placements are routed with an orthogonal A* maze router (bends, crossings, T-junction dots,
   never touching another net); the best wins; unroutable nets fall back to labels.
5. Texts are placed by an optimizer; the sheet is **validated with QUX.exe's own netlister** and scored
   by the reviewer.

The `.qsch` format (Latin-1 «» tree, y-up coordinates, rotation/mirror codes incl. 45°, text
justification bits, hierarchical blocks) was reverse-engineered and verified against QUX; the parser
round-trips all installed QSPICE files byte-for-byte.

## Verification
* `tests\smoke_test.py` drives the server through the real MCP stdio protocol (drawing, review,
  editing, symbols, hierarchy, simulation, measurements).
* 15 recipe circuits draw with review score 100, validate, simulate and meet their expected `.meas`
  results.
* LLM test campaign with Claude Haiku and Claude Sonnet using only the tools and skills: see
  `TESTING.md`.

## Layout
```
src/qspice_mcp/
  server.py          MCP server (tools, prompts, resources)
  cli.py             qmcp command-line bridge
  skills/            the 11 skills (SKILL.md each); skills.py serves them
  qsch.py            lossless .qsch/.qsym parser/writer + geometry/justification math
  model.py           schematic/component/symbol object model
  symbols.py         symbol library (harvested + user folders + hierarchical blocks)
  symbol_builder.py  create/validate symbols, subckt symbols, sub-sheet blocks
  scene.py           geometric scene model (bodies, shapes, pins, texts, labels)
  review.py          drafting reviewer (score, issues, fixes)
  textmap.py         ASCII canvas map + spatial summary
  labels.py          refdes/value text placement optimizer
  edit.py            reroute / move / add_and_connect
  autodraw.py        auto placement / routing / label layout
  router.py          orthogonal grid maze router
  canvas.py          low-level canvas editing operations
  connectivity.py    geometric netlister + validation against QUX netlists
  render.py          schematic & symbol renderer (PNG, annotations, issue marks)
  runner.py          QUX / QSPICE64/80 / QPOST drivers, log & .meas parsing
  qraw.py, plots.py  waveform reader and plots
  netlist_import.py  SPICE netlist -> drawing spec
  models.py          .model/.subckt index
  helpdocs.py        QSPICE help index
```

## Notes & limits
* QUX does not reload files changed on disk; re-open the schematic in QSPICE after edits.
* QSPICE prefixes the device letter to a refdes that doesn't start with it (op-amp `U1` -> `Ã´U1`).
* The renderer approximates QUX's drawing (fonts/sizes differ slightly).
* Very large circuits (> ~60 parts) take longer to place; use hierarchy (`create_subsheet`).
