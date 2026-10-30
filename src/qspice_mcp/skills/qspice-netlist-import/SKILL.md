---
name: qspice-netlist-import
description: Turning SPICE netlists (QSPICE, LTspice, generic SPICE) into neat QSPICE schematics with netlist_to_schematic, simulating netlists directly, and differences from LTspice to watch for.
---

# Netlists -> schematics

## 1. Convert

```
netlist_to_schematic(netlist_text="""* RC filter
V1 in 0 SINE 0 1 1K AC 1
R1 in out 1K
C1 out 0 100n
.ac dec 50 10 1Meg
.end""", path='rc_from_netlist.qsch')
```

or `netlist_to_schematic(netlist_path='C:/work/amp.cir')` (writes `amp.qsch` next to it).

What happens:
- Each device line becomes a part on a standard QSPICE symbol (R, C, L, V, I, D, Q -> NPN/PNP by
  model type, M -> NMOS/PMOS, J, E, F, G, H, B, S, W, T, K ...).
- `X` instances of `.subckt`s become block symbols with the subcircuit's pin names.
- `.model`, `.subckt ... .ends`, `.param`, `.lib`, analyses and other dot-commands are kept as directives.
- The drawing is done by the auto-draw engine (same quality rules), validated and reviewed.

Then: `review_schematic(path)` -> fix -> `render_schematic(path)` -> `simulate(path)`.

## 2. Simulate without drawing

`simulate(netlist_text='* title\n...\n.end', name='test')` runs the netlist directly (first line is the
title; `.end` is added if missing). Good for quick experiments and checking a model.

## 3. LTspice -> QSPICE differences

| LTspice | QSPICE |
|---|---|
| `.meas TRAN name ...` | works, but `.meas name ...` is enough |
| `SINE(0 1 1k)` | `SINE 0 1 1K` (parentheses are accepted but not recommended) |
| `PULSE(0 5 0 1n 1n 5u 10u)` | `PULSE 0 5 0 1n 1n 5u 10u` |
| `Rser=` on capacitors/inductors | supported |
| `.step param X list ...` | same |
| LTspice symbols/`.asc` files | not readable; export the netlist (View > SPICE Netlist) and convert it |
| `U` op-amp macromodels from LTspice libraries (`.sub` with `LTC` encryption) | may not work; use the vendor's generic SPICE model or QSPICE's `OpAmp` |

## 4. Tips

- Give meaningful node names in the netlist (`in`, `out`, `vcc`) - they become net labels.
- If a device maps to the wrong symbol, edit the result with `delete_items` + `add_component`.
- Big netlists (> 40 parts) draw better split into sub-circuits (`.subckt`) and sub-sheets.
