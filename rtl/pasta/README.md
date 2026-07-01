# PASTA RTL Blocks

This directory contains standalone SystemVerilog RTL sketches for the PASTA
hardware mechanisms.

For OpenROAD/Yosys, use the Verilog-2005 version in `openroad/`.

## `ptcl_lookup_table.sv`

`ptcl_lookup_table` implements the PTCL Coverage Directory used by the GMMU Flex
TLB path.  It is a tagless locator table:

- one PTCL line has 8 sectors;
- each PCD row stores `valid`, an 8-bit sector bitmap, and one ordinary TLB way
  locator per sector;
- a lookup returns every candidate row in the hashed PCD set;
- downstream TLB logic must validate each candidate with exact `PID + VAddr`
  tag comparison before returning a translation.

The module intentionally does not store PID tags, PTCL tags, PTEs, PPNs, or TLB
set IDs.  This matches the simulator implementation in
`akita/mem/vm/tlb_gmmu/pcd.go`.

### Main Operations

- `lookup_*`: combinationally reads the PCD set for a PTCL base and returns
  candidate sector locators for all PCD ways.
- `fill_*`: records that a sector was filled into an ordinary TLB way.  The
  caller can either specify the PCD way or let the table allocate an invalid/LRU
  row.
- `clear_*`: clears stale sector locators after exact downstream validation
  rejects them.
- `touch_*`: refreshes replacement metadata after exact downstream validation
  confirms a row belongs to the requested PTCL line.
- `remove_*`: clears a sector locator when the ordinary TLB entry in that way is
  evicted.

Only one table update is applied per cycle.  Priority is:

```text
remove > clear > fill > touch
```

## Quick Simulation

If Icarus Verilog is available:

```bash
iverilog -g2012 -o /tmp/ptcl_lookup_table_tb \
  rtl/pasta/ptcl_lookup_table.sv \
  rtl/pasta/ptcl_lookup_table_tb.sv
vvp /tmp/ptcl_lookup_table_tb
```
