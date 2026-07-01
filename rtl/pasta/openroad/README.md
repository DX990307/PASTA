# OpenROAD Verilog Version

This directory contains a conservative Verilog-2005 version of the PASTA PTCL
lookup table for OpenROAD/Yosys flows.

## Files

- `ptcl_lookup_table.v`: synthesizable Verilog top module.
- `ptcl_lookup_table_tb.v`: simple simulation testbench.
- `ptcl_lookup_table.sdc`: minimal clock/input/output constraints.

The top module is named `ptcl_lookup_table`.  Do not read this Verilog file and
the SystemVerilog version in `../ptcl_lookup_table.sv` in the same synthesis
run, because they intentionally share the same module name.

## Yosys Smoke Test

```bash
yosys -p "read_verilog rtl/pasta/openroad/ptcl_lookup_table.v; \
  hierarchy -top ptcl_lookup_table; proc; opt; stat"
```

## OpenROAD-Flow-Scripts Hook

For an OpenROAD-flow-scripts design, use this as the relevant config shape:

```make
export DESIGN_NAME = ptcl_lookup_table
export VERILOG_FILES = $(DESIGN_HOME)/src/ptcl_lookup_table.v
export SDC_FILE = $(DESIGN_HOME)/constraint.sdc
export CLOCK_PORT = clk
export CLOCK_PERIOD = 1.000
```

The default RTL parameters match the current simulator configuration:

```text
NUM_SETS      = 16
NUM_PCD_WAYS  = 2
TLB_WAYS      = 16
PID_WIDTH     = 16
VADDR_WIDTH   = 64
LOG2_PAGE_SIZE = 12
```

If `NUM_SETS`, `NUM_PCD_WAYS`, or `TLB_WAYS` are changed, override
`SET_WIDTH`, `PCD_WAY_WIDTH`, and `TLB_WAY_WIDTH` consistently.
