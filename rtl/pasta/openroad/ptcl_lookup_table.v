// OpenROAD/Yosys-friendly Verilog implementation of the PASTA PTCL lookup
// table.  This is the PTCL Coverage Directory (PCD): a tagless locator table
// that stores only sector-present bits and ordinary TLB way locators.
//
// The table does not store PID tags, PTCL tags, PTEs, PPNs, or TLB set IDs.
// Downstream TLB logic must validate every returned candidate with exact
// PID + VAddr comparison before returning a translation.

`timescale 1ns/1ps

module ptcl_lookup_table #(
    parameter NUM_SETS       = 16,
    parameter NUM_PCD_WAYS   = 2,
    parameter TLB_WAYS       = 16,
    parameter PID_WIDTH      = 16,
    parameter VADDR_WIDTH    = 64,
    parameter LOG2_PAGE_SIZE = 12,
    parameter AGE_WIDTH      = 32,

    // Keep these explicit for conservative Verilog/OpenROAD flows.  If a
    // caller changes NUM_* parameters, override the matching width parameters.
    parameter SET_WIDTH      = 4,
    parameter PCD_WAY_WIDTH  = 1,
    parameter TLB_WAY_WIDTH  = 4,
    parameter HASH_WIDTH     = 64
) (
    input clk,
    input rst_n,

    input                         lookup_valid,
    output                        lookup_ready,
    input      [PID_WIDTH-1:0]    lookup_pid,
    input      [VADDR_WIDTH-1:0]  lookup_base_vaddr,
    input      [7:0]              lookup_bitmap,
    output                        lookup_resp_valid,
    output     [SET_WIDTH-1:0]    lookup_resp_set,
    output reg [NUM_PCD_WAYS-1:0] lookup_resp_row_valid,
    output reg [NUM_PCD_WAYS*8-1:0] lookup_resp_present_bitmap,
    output reg [NUM_PCD_WAYS*8*TLB_WAY_WIDTH-1:0] lookup_resp_way,

    input                         fill_valid,
    input      [PID_WIDTH-1:0]    fill_pid,
    input      [VADDR_WIDTH-1:0]  fill_vaddr,
    input      [TLB_WAY_WIDTH-1:0] fill_tlb_way,
    input                         fill_pcd_way_valid,
    input      [PCD_WAY_WIDTH-1:0] fill_pcd_way,

    input                         clear_valid,
    input      [PID_WIDTH-1:0]    clear_pid,
    input      [VADDR_WIDTH-1:0]  clear_base_vaddr,
    input      [PCD_WAY_WIDTH-1:0] clear_pcd_way,
    input      [7:0]              clear_bitmap,

    input                         touch_valid,
    input      [PID_WIDTH-1:0]    touch_pid,
    input      [VADDR_WIDTH-1:0]  touch_base_vaddr,
    input      [PCD_WAY_WIDTH-1:0] touch_pcd_way,

    input                         remove_valid,
    input      [PID_WIDTH-1:0]    remove_pid,
    input      [VADDR_WIDTH-1:0]  remove_vaddr,
    input      [TLB_WAY_WIDTH-1:0] remove_tlb_way,

    output     [31:0]             stat_entry_evictions,
    output     [31:0]             stat_stale_bits
);

    localparam NUM_SECTORS    = 8;
    localparam NUM_ROWS       = NUM_SETS * NUM_PCD_WAYS;
    localparam LOCATORS_WIDTH = NUM_SECTORS * TLB_WAY_WIDTH;
    localparam HASH_SHIFT     = (NUM_SETS <= 1) ? 0 : SET_WIDTH;

    reg [NUM_ROWS-1:0]             entry_valid_q;
    reg [7:0]                      present_q [0:NUM_ROWS-1];
    reg [LOCATORS_WIDTH-1:0]       locator_q [0:NUM_ROWS-1];
    reg [AGE_WIDTH-1:0]            last_visit_q [0:NUM_ROWS-1];

    reg [AGE_WIDTH-1:0] visit_counter_q;
    reg [31:0]          stat_entry_evictions_q;
    reg [31:0]          stat_stale_bits_q;

    wire [VADDR_WIDTH-1:0] lookup_line_base;
    wire [SET_WIDTH-1:0]   lookup_set;

    assign lookup_ready = 1'b1;
    assign lookup_resp_valid = lookup_valid;
    assign lookup_line_base = ptcl_base_vaddr(lookup_base_vaddr);
    assign lookup_set = pcd_set_id(lookup_pid, lookup_line_base);
    assign lookup_resp_set = lookup_set;
    assign stat_entry_evictions = stat_entry_evictions_q;
    assign stat_stale_bits = stat_stale_bits_q;

    function [VADDR_WIDTH-1:0] ptcl_base_vaddr;
        input [VADDR_WIDTH-1:0] vaddr;
        reg [VADDR_WIDTH-1:0] vpn;
        begin
            vpn = vaddr >> LOG2_PAGE_SIZE;
            ptcl_base_vaddr = ((vpn >> 3) << 3) << LOG2_PAGE_SIZE;
        end
    endfunction

    function [2:0] ptcl_sector;
        input [VADDR_WIDTH-1:0] vaddr;
        begin
            ptcl_sector = (vaddr >> LOG2_PAGE_SIZE) & 3'h7;
        end
    endfunction

    function [SET_WIDTH-1:0] pcd_set_id;
        input [PID_WIDTH-1:0] pid;
        input [VADDR_WIDTH-1:0] base_vaddr;
        reg [HASH_WIDTH-1:0] ptcl_id;
        reg [HASH_WIDTH-1:0] pid_ext;
        reg [HASH_WIDTH-1:0] pid_hash;
        reg [HASH_WIDTH-1:0] hashed;
        begin
            if (NUM_SETS <= 1) begin
                pcd_set_id = {SET_WIDTH{1'b0}};
            end else begin
                ptcl_id = base_vaddr >> (LOG2_PAGE_SIZE + 3);
                pid_ext = pid;
                pid_hash = pid_ext ^ (pid_ext >> HASH_SHIFT);
                hashed = ptcl_id ^ (ptcl_id >> HASH_SHIFT) ^ pid_hash;

                if ((NUM_SETS & (NUM_SETS - 1)) == 0) begin
                    pcd_set_id = hashed[SET_WIDTH-1:0];
                end else begin
                    pcd_set_id = hashed % NUM_SETS;
                end
            end
        end
    endfunction

    function [31:0] popcount8;
        input [7:0] value;
        integer i;
        begin
            popcount8 = 32'd0;
            for (i = 0; i < 8; i = i + 1) begin
                popcount8 = popcount8 + value[i];
            end
        end
    endfunction

    always @* begin : lookup_comb
        integer way;
        integer sector;
        integer row;
        reg [7:0] candidate_bitmap;

        lookup_resp_row_valid = {NUM_PCD_WAYS{1'b0}};
        lookup_resp_present_bitmap = {(NUM_PCD_WAYS*8){1'b0}};
        lookup_resp_way = {(NUM_PCD_WAYS*8*TLB_WAY_WIDTH){1'b0}};

        for (way = 0; way < NUM_PCD_WAYS; way = way + 1) begin
            row = (lookup_set * NUM_PCD_WAYS) + way;
            candidate_bitmap = present_q[row] & lookup_bitmap;
            lookup_resp_row_valid[way] =
                lookup_valid && entry_valid_q[row] && (candidate_bitmap != 8'b0);

            for (sector = 0; sector < NUM_SECTORS; sector = sector + 1) begin
                lookup_resp_present_bitmap[(way * NUM_SECTORS) + sector] =
                    lookup_valid && entry_valid_q[row] && candidate_bitmap[sector];
                lookup_resp_way[((way * NUM_SECTORS + sector) * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH] =
                    locator_q[row][(sector * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH];
            end
        end
    end

    always @(posedge clk or negedge rst_n) begin : table_seq
        integer row;
        integer way;
        integer sector;
        integer selected_row;
        reg [SET_WIDTH-1:0] set_id;
        reg [2:0] sector_id;
        reg [7:0] next_bitmap;
        reg [7:0] cleared_bitmap;
        reg [7:0] fill_present;
        reg [LOCATORS_WIDTH-1:0] fill_locator;
        reg [PCD_WAY_WIDTH-1:0] selected_way;
        reg [AGE_WIDTH-1:0] lru_age;
        reg found_invalid;
        reg fill_hint_in_range;
        reg fill_allocates_new_row;

        if (!rst_n) begin
            entry_valid_q <= {NUM_ROWS{1'b0}};
            visit_counter_q <= {AGE_WIDTH{1'b0}};
            stat_entry_evictions_q <= 32'd0;
            stat_stale_bits_q <= 32'd0;

            for (row = 0; row < NUM_ROWS; row = row + 1) begin
                present_q[row] <= 8'b0;
                locator_q[row] <= {LOCATORS_WIDTH{1'b0}};
                last_visit_q[row] <= {AGE_WIDTH{1'b0}};
            end
        end else begin
            if (remove_valid) begin
                set_id = pcd_set_id(remove_pid, ptcl_base_vaddr(remove_vaddr));
                sector_id = ptcl_sector(remove_vaddr);

                for (way = 0; way < NUM_PCD_WAYS; way = way + 1) begin
                    row = (set_id * NUM_PCD_WAYS) + way;
                    if (entry_valid_q[row] &&
                        present_q[row][sector_id] &&
                        (locator_q[row][(sector_id * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH] == remove_tlb_way)) begin
                        next_bitmap = present_q[row] & ~(8'b1 << sector_id);
                        present_q[row] <= next_bitmap;
                        locator_q[row][(sector_id * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH] <= {TLB_WAY_WIDTH{1'b0}};
                        if (next_bitmap == 8'b0) begin
                            entry_valid_q[row] <= 1'b0;
                        end
                    end
                end
            end else if (clear_valid && (clear_pcd_way < NUM_PCD_WAYS)) begin
                set_id = pcd_set_id(clear_pid, ptcl_base_vaddr(clear_base_vaddr));
                row = (set_id * NUM_PCD_WAYS) + clear_pcd_way;

                if (entry_valid_q[row]) begin
                    cleared_bitmap = present_q[row] & clear_bitmap;
                    next_bitmap = present_q[row] & ~clear_bitmap;
                    present_q[row] <= next_bitmap;
                    stat_stale_bits_q <= stat_stale_bits_q + popcount8(cleared_bitmap);

                    for (sector = 0; sector < NUM_SECTORS; sector = sector + 1) begin
                        if (clear_bitmap[sector]) begin
                            locator_q[row][(sector * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH] <= {TLB_WAY_WIDTH{1'b0}};
                        end
                    end

                    if (next_bitmap == 8'b0) begin
                        entry_valid_q[row] <= 1'b0;
                    end
                end
            end else if (fill_valid) begin
                set_id = pcd_set_id(fill_pid, ptcl_base_vaddr(fill_vaddr));
                sector_id = ptcl_sector(fill_vaddr);
                fill_hint_in_range = (fill_pcd_way < NUM_PCD_WAYS);

                selected_way = {PCD_WAY_WIDTH{1'b0}};
                found_invalid = 1'b0;

                if (fill_pcd_way_valid && fill_hint_in_range) begin
                    selected_way = fill_pcd_way;
                end else begin
                    for (way = 0; way < NUM_PCD_WAYS; way = way + 1) begin
                        row = (set_id * NUM_PCD_WAYS) + way;
                        if (!found_invalid && !entry_valid_q[row]) begin
                            selected_way = way[PCD_WAY_WIDTH-1:0];
                            found_invalid = 1'b1;
                        end
                    end

                    if (!found_invalid) begin
                        selected_way = {PCD_WAY_WIDTH{1'b0}};
                        lru_age = last_visit_q[set_id * NUM_PCD_WAYS];
                        for (way = 1; way < NUM_PCD_WAYS; way = way + 1) begin
                            row = (set_id * NUM_PCD_WAYS) + way;
                            if (last_visit_q[row] < lru_age) begin
                                selected_way = way[PCD_WAY_WIDTH-1:0];
                                lru_age = last_visit_q[row];
                            end
                        end
                    end
                end

                selected_row = (set_id * NUM_PCD_WAYS) + selected_way;
                fill_allocates_new_row =
                    (!fill_pcd_way_valid) || (!fill_hint_in_range) || (!entry_valid_q[selected_row]);

                if (fill_allocates_new_row) begin
                    if (((!fill_pcd_way_valid) || (!fill_hint_in_range)) &&
                        entry_valid_q[selected_row]) begin
                        stat_entry_evictions_q <= stat_entry_evictions_q + 32'd1;
                    end
                    fill_present = 8'b0;
                    fill_locator = {LOCATORS_WIDTH{1'b0}};
                end else begin
                    fill_present = present_q[selected_row];
                    fill_locator = locator_q[selected_row];
                end

                fill_present[sector_id] = 1'b1;
                fill_locator[(sector_id * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH] = fill_tlb_way;

                entry_valid_q[selected_row] <= 1'b1;
                present_q[selected_row] <= fill_present;
                locator_q[selected_row] <= fill_locator;
                visit_counter_q <= visit_counter_q + 1'b1;
                last_visit_q[selected_row] <= visit_counter_q + 1'b1;
            end else if (touch_valid && (touch_pcd_way < NUM_PCD_WAYS)) begin
                set_id = pcd_set_id(touch_pid, ptcl_base_vaddr(touch_base_vaddr));
                row = (set_id * NUM_PCD_WAYS) + touch_pcd_way;

                if (entry_valid_q[row]) begin
                    visit_counter_q <= visit_counter_q + 1'b1;
                    last_visit_q[row] <= visit_counter_q + 1'b1;
                end
            end
        end
    end

endmodule
