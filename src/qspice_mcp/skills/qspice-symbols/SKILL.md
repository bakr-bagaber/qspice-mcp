---
name: qspice-symbols
description: When and how to make new symbols - block symbols for vendor SPICE models (symbol_from_subckt), symbols from scratch (create_symbol templates and custom graphics), hierarchical sub-sheets (create_subsheet), the rules a good symbol follows, and how to verify it.
---

# Symbols, vendor models and sub-sheets

## 1. Do I need a new symbol?

1. `list_symbols('<words>')` - QSPICE ships ~240 symbols (R C L V I D NPN PNP NMOS PMOS OpAmp SW E G
   B1 B2 T Y Transformer, logic gates, flops, EPC GaN FETs, gate drivers, ...).
2. A transistor/diode that only needs a different MODEL does NOT need a new symbol: use the standard
   symbol (`NPN`, `NMOS`, `D` ...) with `value` = model name and add the `.model`/`.lib` line.
3. A vendor IC / macro-model delivered as a `.subckt` (op-amps, regulators, drivers, sensors) DOES need
   a symbol -> section 2.
4. A reusable block you build from other parts (a filter stage, a bias network, a gate driver)
   -> a sub-sheet, section 4.

## 2. Symbol for a vendor model (.subckt) - one call

```
symbol_from_subckt(source='C:/models/LT1001.lib')               # a model file
symbol_from_subckt(source='.subckt MYAMP inp inn out vcc vee\n...\n.ends')   # pasted text
symbol_from_subckt(source='LT1001', subckt_name='LT1001')         # a name known to search_models
```

- Pins come from the `.subckt` line in the right order (this is what matters to SPICE).
- Sides are chosen by name: inputs left, outputs right, supplies (`vcc`, `vdd`, `v+`) top, ground and
  negative supplies (`gnd`, `vss`, `vee`, `v-`) bottom. Override: `sides={'EN': 'left', 'PG': 'right'}`.
- Pasted text is saved as `<name>.lib` next to the symbol and linked (QSPICE includes it automatically).
- Then use it: `symbol='LT1001'` in `add_component`, `place_component` or an auto-draw spec, with
  `pins={...}` by name.

## 3. Symbol from scratch - create_symbol

```
create_symbol(name='TPS7A47', pins=['IN', 'EN', 'GND', 'OUT', 'NR'],
              description='LDO regulator', library_file='C:/models/tps7a47.lib')
```

- `pins` are in **SPICE order** (the order of the nodes on the `.subckt` line). Getting this wrong
  silently connects the wrong nets! Each pin may be a name or `{"name": ..., "side": "left|right|top|bottom"}`.
- `kind`:
  - `block` (default): rectangle with pins on four sides. Right for ICs and modules.
  - `two_terminal`: 2 pins, top then bottom; `body_style`: `box`, `circle`, `resistor`, `inductor`,
    `capacitor`, `diode`; `label` writes a short text inside (e.g. `F` for a fuse).
  - `amplifier`: triangle; pins `IN+`, `IN-` (left), `OUT` (right), optional `V+` (top), `V-` (bottom).
  - `custom`: your own drawing (section 3.1).
- `spice_prefix`: `X` for subcircuits (default). Use the device letter for primitives with a `.model`
  (e.g. `D` for a diode model, `Q`... is better served by the standard symbols).
- `model`: the `.subckt`/`.model` name written on the symbol (default = `name`).
- `attributes`: extra instance parameters shown and passed on the netlist line, e.g. `['Rload=10K']`.
- The symbol is saved as `<name>.qsym` in your symbol folder and is immediately usable by name; it
  persists across sessions.

### 3.1 Custom graphics (kind='custom')

Coordinates are symbol-local, 100 grid, y up, origin (0, 0) in the middle of the body.

```
create_symbol(name='MIXER', kind='custom', spice_prefix='X',
  graphics=[{"circle": [0, 0, 200]}, {"line": [[-140, -140], [140, 140]]}, {"line": [[-140, 140], [140, -140]]}],
  pins=[{"name": "RF", "x": -400, "y": 0, "dir": "left"},
        {"name": "IF", "x": 400, "y": 0, "dir": "right"},
        {"name": "LO", "x": 0, "y": -400, "dir": "down"}])
```

Graphic items: `{"line": [[x1,y1],[x2,y2]]}`, `{"rect": [[x1,y1],[x2,y2]], "fill": false}`,
`{"circle": [cx, cy, r]}`, `{"ellipse": [[x1,y1],[x2,y2]]}`, `{"polygon": [[x,y], ...]}` (3 points =
filled triangle), `{"arc": [[x,y],[x,y],[x,y]]}` (3 points on the arc),
`{"text": "ABC", "at": [x, y], "size": 0.8, "align": "center"}`.

Pins: `x, y` = the pin's connection point (the OUTER end of its lead), `dir` = the direction the pin
points away from the body, `lead` = lead length drawn inward (default 200).

### 3.2 Rules of a good symbol (the validator checks these)

1. Every pin on the 100 grid, at the end of a short lead (200) OUTSIDE the body outline.
2. Inputs on the left, outputs on the right, positive supply on top, ground / negative supply at the bottom.
3. Pin names visible inside the body next to each lead (block template does this).
4. Pins spaced 200 apart on a side; body just big enough for the pin names.
5. First text = reference designator (`X1`), second text = model/value, both outside the body.
6. Unique pin names; pin order = SPICE node order.

## 4. Sub-sheets (hierarchical blocks)

A sub-sheet is a schematic `NAME.qsch` whose **port labels** are the block's pins. The parent sheet
uses it like any part. QSPICE inlines it as `.subckt NAME port1 port2 ...`.

```
create_subsheet(name='RCF', ports=['IN', 'OUT'], folder='C:/work/proj',
                spec={"components": [
                    {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["IN", "OUT"]},
                    {"ref": "C1", "symbol": "C", "value": "1u", "nodes": ["OUT", "0"]}]})
```

- Nets named like the ports become port labels. Ground (`0`) is global - not a port.
- Without `spec` you get an empty sheet with port labels and stubs; fill it with `add_component`
  (connect to `port_stub_ends`).
- Use it in a parent sheet **in the same folder**:
  `place_component(path='C:/work/proj/top.qsch', symbol='C:/work/proj/RCF.qsch', x=0, y=0)` or in a spec
  `{"ref": "X1", "symbol": "C:/work/proj/RCF.qsch", "pins": {"IN": "in", "OUT": "out"}}`.
- Check with `netlist_schematic(parent)`: you should see `X1 ... RCF` and `.subckt RCF IN OUT`.

## 5. Verify every new symbol

1. Look at the preview returned by the tool (or `get_symbol(name)`): pins `order:NAME (x,y)`, origin cross.
2. Place it in a tiny test sheet, `netlist_schematic` -> the `X...` line lists nets in the right order and a
   `.lib` line points to the model file.
3. `simulate` a minimal circuit (supply + load) to catch pin-order mistakes early.

## 6. Folders

- New symbols go to `<work folder>/symbols` (see `qspice_info`).
- `add_symbol_folder(folder)` makes another folder of `.qsym`/model files searchable (persists).
