# QSPICE MCP Server

An MCP server that gives LLMs full access to **QSPICE** (Qorvo) — simulation, netlisting,
waveforms, measurements, help, symbol and model libraries — and adds what QSPICE doesn't
have: **automatic, human-readable schematic drawing** and an **LLM-friendly canvas API**.

## What it can do

| Area | Tools |
|---|---|
| Install & docs | `qspice_info`, `drawing_guide`, `help_search`, `help_topic`, `list_examples` |
| Libraries | `list_symbols`, `get_symbol` (with picture), `search_models`, `get_model_text`, `create_block_symbol` |
| Simulation | `simulate` (schematic, netlist file or inline netlist; QSPICE64/QSPICE80), `netlist_schematic`, `check_floating_nodes` |
| Results | `list_traces`, `get_trace_data` (stats + samples, `.step` aware, AC as dB/phase), `evaluate_expressions` (QSPICE's own expression engine), `export_waveforms` (CSV/ASCII/SPICE PWL/S2P), `measure` (`.meas`/`.four` via QPOST without re-simulating), `plot_waveforms` (PNG; Bode for AC; steps overlaid) |
| Other QSPICE features | `dll_variables` (C++/Verilog blocks), `protect_netlist` (encryption), `open_in_qspice`, `launch_tool` (DIODE/MOSFET/JFET model tools, LoopTrack, SoundScope, ToneGenerator) |
| **Auto drawing** | `auto_draw_schematic` (circuit description → neat .qsch), `netlist_to_schematic` (SPICE/LTspice netlist → .qsch), `beautify_schematic` (redraw a messy schematic) |
| **Canvas** | `read_schematic` (every pin's absolute x/y and net), `render_schematic` (PNG, optional coordinate grid), `validate_schematic`, `new_schematic`, `place_component`, `update_component`, `delete_items`, `connect_pins` / `connect_many` (auto-routed wires), `add_wire`, `add_net_label`, `add_ground`, `set_directives`, `add_comment` |

### How the automatic drawing works
1. The circuit spec is normalised against QSPICE's real symbols (harvested from the install:
   all `.qsym` files plus every symbol embedded in the bundled examples and help files).
2. Nets are classified: ground, supply rails (drawn as labels, never long wires), signal nets ranked
   along the signal path.
3. Placement starts from a layered left→right layout and is refined by simulated annealing whose cost
   function encodes the drafting conventions from Mike Engelhardt's *Empirically Found Drafting
   Guidelines* (shipped with QSPICE): signal flow left→right, current top→bottom, ground triangles,
   upright sources, refdes above value to the right of vertical parts, compactness, no overlapping
   text, no parts sitting on a net's straight path.
4. Several placements are routed with an orthogonal A* maze router on the 100-unit grid
   (bend & crossing costs, T-junction dots, never touching another net, never 4-way junctions);
   the best routed result wins. Unroutable nets fall back to named labels.
5. The .qsch is written and **validated with QUX.exe's own netlister** — the tool reports any
   mismatch between intended and drawn connectivity.

The `.qsch` format details (Latin-1 «» tree, y-up coordinates, rotation/mirror codes, text
justification bits) were reverse-engineered and verified against QUX; the parser round-trips all 294
installed QSPICE files byte-for-byte.

## Install / register

Requires Python ≥ 3.12 and QSPICE (auto-detected; override with `QSPICE_DIR`).

```powershell
cd C:\Users\bakrb\qspice-mcp
uv sync
```

Claude Desktop: *Settings → Developer → Edit Config*, and merge in the contents of
`claude_desktop_config.snippet.json` (shown here), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "qspice": {
      "command": "C:\\Users\\bakrb\\qspice-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "qspice_mcp.server"],
      "env": { "QSPICE_MCP_WORKDIR": "C:\\Users\\bakrb\\Documents\\QSPICE-MCP" }
    }
  }
}
```

Claude Code: `claude mcp add qspice -- C:\Users\bakrb\qspice-mcp\.venv\Scripts\python.exe -m qspice_mcp.server`

### Environment variables
* `QSPICE_DIR` – QSPICE install folder (default: auto-detect, e.g. `C:\Program Files\QSPICE`)
* `QSPICE_MCP_WORKDIR` – where relative file names are created (default `~/Documents/QSPICE-MCP`)
* `QSPICE_SYMBOL_PATH` – extra folders (`;`-separated) with your own `.qsym`/`.qsch`/model files

Caches (symbol library, help index, model index) live in `%LOCALAPPDATA%\qspice-mcp`.

## Example (what an LLM sends)

```json
{"spec": {
  "title": "Common-emitter amplifier",
  "components": [
    {"ref":"V1","symbol":"V","value":"SINE 0 10m 1K","nodes":["src","0"]},
    {"ref":"VCC","symbol":"V","value":"12","nodes":["vcc","0"]},
    {"ref":"C1","symbol":"C","value":"10µ","nodes":["src","b"]},
    {"ref":"R1","symbol":"R","value":"47K","nodes":["vcc","b"]},
    {"ref":"R2","symbol":"R","value":"10K","nodes":["b","0"]},
    {"ref":"RC","symbol":"R","value":"4.7K","nodes":["vcc","c"]},
    {"ref":"RE","symbol":"R","value":"1K","nodes":["e","0"]},
    {"ref":"CE","symbol":"C","value":"100µ","nodes":["e","0"]},
    {"ref":"Q1","symbol":"NPN","value":"2N3904","pins":{"C":"c","B":"b","E":"e"}},
    {"ref":"C2","symbol":"C","value":"10µ","nodes":["c","out"]},
    {"ref":"RL","symbol":"R","value":"10K","nodes":["out","0"]}],
  "directives": [".tran 5m", ".model 2N3904 NPN Is=6.7f Bf=416"]},
 "path": "ce_amp.qsch", "simulate_after": true}
```

## Verification
* `tests\smoke_test.py` drives the server through the real MCP stdio protocol (auto-draw → validate →
  simulate → `.meas` f₃dB check, netlist import, canvas editing) — `.venv\Scripts\python.exe tests\smoke_test.py`.
* Every auto-drawn sheet is netlisted by QUX.exe and compared pin-by-pin with the intended connectivity.
* Redrawing QSPICE's `NelsonPassAmp.qsch` example with `beautify_schematic` yields a schematic whose
  simulation is bit-identical to the original.

## Notes & limits
* QUX does not reload files changed on disk; re-open the schematic after edits.
* QSPICE prefixes the device letter to a refdes that doesn't start with it (an op-amp `U1` is
  netlisted as `Ã´U1`); the tools report this.
* The renderer is an approximation of QUX's drawing (fonts/sizes differ slightly).
* Very large circuits (> ~60 parts) take longer to place; use `options.effort = 1`, or draw
  sub-blocks separately / hierarchically.

## Layout
```
src/qspice_mcp/
  qsch.py            lossless .qsch/.qsym parser/writer + geometry/justification math
  model.py           schematic/component/symbol object model
  symbols.py         symbol library harvesting, aliases, generated block symbols
  runner.py          QUX / QSPICE64/80 / QPOST drivers, log & .meas parsing
  qraw.py            binary/ASCII .qraw reader (AC complex, .step)
  plots.py           waveform plots
  render.py          schematic & symbol renderer (PNG)
  connectivity.py    geometric netlister + validation against QUX netlists
  router.py          orthogonal grid maze router
  autodraw.py        auto placement / routing / label layout
  canvas.py          incremental canvas editing operations
  netlist_import.py  SPICE netlist -> drawing spec
  models.py          .model/.subckt index
  helpdocs.py        QSPICE.chm help index
  server.py          MCP server (tools)
```
