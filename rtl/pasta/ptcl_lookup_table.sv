// PTCL lookup table for PASTA/Flex.
//
// This module implements the PTCL Coverage Directory (PCD) locator table used
// by the simulator's GMMU Flex TLB path.  It is intentionally tagless: entries
// contain only an 8-bit sector-present mask and a TLB-way locator per sector.
// The downstream TLB data/tag array must still validate every candidate with an
// exact PID + VAddr comparison before returning a translation.

`timescale 1ns/1ps

module ptcl_lookup_table #(
    parameter int unsigned NUM_SETS       = 16,
    parameter int unsigned NUM_PCD_WAYS   = 2,
    parameter int unsigned TLB_WAYS       = 16,
    parameter int unsigned PID_WIDTH      = 16,
    parameter int unsigned VADDR_WIDTH    = 64,
    parameter int unsigned LOG2_PAGE_SIZE = 12,
    parameter int unsigned AGE_WIDTH      = 32,

    // Derived widths.  These are parameters so they can be used in the port
    // list by tools that do not allow localparams in ANSI-style ports.
    parameter int unsigned SET_WIDTH      = (NUM_SETS <= 1) ? 1 : $clog2(NUM_SETS),
    parameter int unsigned PCD_WAY_WIDTH  = (NUM_PCD_WAYS <= 1) ? 1 : $clog2(NUM_PCD_WAYS),
    parameter int unsigned TLB_WAY_WIDTH  = (TLB_WAYS <= 1) ? 1 : $clog2(TLB_WAYS),
    parameter int unsigned HASH_WIDTH     = (VADDR_WIDTH > PID_WIDTH) ? VADDR_WIDTH : PID_WIDTH
) (
    input  logic clk,
    input  logic rst_n,

    // Combinational lookup. lookup_base_vaddr may be any address in the PTCL
    // line; the module aligns it internally to an 8-sector PTCL base.
    input  logic                         lookup_valid,
    output logic                         lookup_ready,
    input  logic [PID_WIDTH-1:0]         lookup_pid,
    input  logic [VADDR_WIDTH-1:0]       lookup_base_vaddr,
    input  logic [7:0]                   lookup_bitmap,
    output logic                         lookup_resp_valid,
    output logic [SET_WIDTH-1:0]         lookup_resp_set,
    output logic [NUM_PCD_WAYS-1:0]      lookup_resp_row_valid,
    output logic [NUM_PCD_WAYS*8-1:0]    lookup_resp_present_bitmap,
    output logic [NUM_PCD_WAYS*8*TLB_WAY_WIDTH-1:0] lookup_resp_way,

    // Fill/update a sector locator.  If fill_pcd_way_valid is high, the caller
    // chooses the PCD row to update.  Otherwise this module allocates an
    // invalid row or the LRU row in the hashed PCD set.
    input  logic                         fill_valid,
    input  logic [PID_WIDTH-1:0]         fill_pid,
    input  logic [VADDR_WIDTH-1:0]       fill_vaddr,
    input  logic [TLB_WAY_WIDTH-1:0]     fill_tlb_way,
    input  logic                         fill_pcd_way_valid,
    input  logic [PCD_WAY_WIDTH-1:0]     fill_pcd_way,

    // Clear stale sector bits in one PCD row after downstream exact TLB tag
    // validation rejects the locator.
    input  logic                         clear_valid,
    input  logic [PID_WIDTH-1:0]         clear_pid,
    input  logic [VADDR_WIDTH-1:0]       clear_base_vaddr,
    input  logic [PCD_WAY_WIDTH-1:0]     clear_pcd_way,
    input  logic [7:0]                   clear_bitmap,

    // Refresh replacement metadata after downstream exact validation confirms
    // that a candidate row really belongs to the requested PTCL line.
    input  logic                         touch_valid,
    input  logic [PID_WIDTH-1:0]         touch_pid,
    input  logic [VADDR_WIDTH-1:0]       touch_base_vaddr,
    input  logic [PCD_WAY_WIDTH-1:0]     touch_pcd_way,

    // Remove a locator when the corresponding ordinary TLB way is evicted.
    // This scans every PCD row in the hashed set and clears the sector only if
    // the stored locator matches remove_tlb_way.
    input  logic                         remove_valid,
    input  logic [PID_WIDTH-1:0]         remove_pid,
    input  logic [VADDR_WIDTH-1:0]       remove_vaddr,
    input  logic [TLB_WAY_WIDTH-1:0]     remove_tlb_way,

    output logic [31:0]                  stat_entry_evictions,
    output logic [31:0]                  stat_stale_bits
);

    localparam int unsigned NUM_SECTORS = 8;
    localparam int unsigned HASH_SHIFT  = (NUM_SETS <= 1) ? 0 : $clog2(NUM_SETS);

    logic                         entry_valid_q [NUM_SETS][NUM_PCD_WAYS];
    logic                         entry_valid_d [NUM_SETS][NUM_PCD_WAYS];
    logic [7:0]                   present_q     [NUM_SETS][NUM_PCD_WAYS];
    logic [7:0]                   present_d     [NUM_SETS][NUM_PCD_WAYS];
    logic [TLB_WAY_WIDTH-1:0]     locator_q     [NUM_SETS][NUM_PCD_WAYS][NUM_SECTORS];
    logic [TLB_WAY_WIDTH-1:0]     locator_d     [NUM_SETS][NUM_PCD_WAYS][NUM_SECTORS];
    logic [AGE_WIDTH-1:0]         last_visit_q  [NUM_SETS][NUM_PCD_WAYS];
    logic [AGE_WIDTH-1:0]         last_visit_d  [NUM_SETS][NUM_PCD_WAYS];

    logic [AGE_WIDTH-1:0] visit_counter_q;
    logic [AGE_WIDTH-1:0] visit_counter_d;
    logic [31:0]          stat_entry_evictions_q;
    logic [31:0]          stat_entry_evictions_d;
    logic [31:0]          stat_stale_bits_q;
    logic [31:0]          stat_stale_bits_d;

    logic [SET_WIDTH-1:0]    lookup_set;
    logic [VADDR_WIDTH-1:0]  lookup_line_base;

    logic [SET_WIDTH-1:0]    fill_set;
    logic [2:0]              fill_sector;
    logic [PCD_WAY_WIDTH-1:0] fill_selected_way;
    logic                    fill_hint_in_range;

    logic [SET_WIDTH-1:0]    clear_set;
    logic [VADDR_WIDTH-1:0]  clear_line_base;
    logic                    clear_way_in_range;

    logic [SET_WIDTH-1:0]    touch_set;
    logic [VADDR_WIDTH-1:0]  touch_line_base;
    logic                    touch_way_in_range;

    logic [SET_WIDTH-1:0]    remove_set;
    logic [2:0]              remove_sector;

    function automatic logic [VADDR_WIDTH-1:0] ptcl_base_vaddr(
        input logic [VADDR_WIDTH-1:0] vaddr
    );
        logic [VADDR_WIDTH-1:0] vpn;
        begin
            vpn = vaddr >> LOG2_PAGE_SIZE;
            ptcl_base_vaddr = ((vpn >> 3) << 3) << LOG2_PAGE_SIZE;
        end
    endfunction

    function automatic logic [2:0] ptcl_sector(
        input logic [VADDR_WIDTH-1:0] vaddr
    );
        begin
            ptcl_sector = (vaddr >> LOG2_PAGE_SIZE) & 3'h7;
        end
    endfunction

    function automatic logic [SET_WIDTH-1:0] pcd_set_id(
        input logic [PID_WIDTH-1:0]   pid,
        input logic [VADDR_WIDTH-1:0] base_vaddr
    );
        logic [HASH_WIDTH-1:0] ptcl_id;
        logic [HASH_WIDTH-1:0] pid_ext;
        logic [HASH_WIDTH-1:0] pid_hash;
        logic [HASH_WIDTH-1:0] hashed;
        begin
            if (NUM_SETS <= 1) begin
                pcd_set_id = '0;
            end else begin
                ptcl_id  = HASH_WIDTH'((base_vaddr >> (LOG2_PAGE_SIZE + 3)));
                pid_ext  = HASH_WIDTH'(pid);
                pid_hash = pid_ext ^ (pid_ext >> HASH_SHIFT);
                hashed   = ptcl_id ^ (ptcl_id >> HASH_SHIFT) ^ pid_hash;

                if ((NUM_SETS & (NUM_SETS - 1)) == 0) begin
                    pcd_set_id = hashed[SET_WIDTH-1:0];
                end else begin
                    pcd_set_id = hashed % NUM_SETS;
                end
            end
        end
    endfunction

    function automatic logic [31:0] popcount8(input logic [7:0] value);
        logic [31:0] count;
        int unsigned i;
        begin
            count = 32'd0;
            for (i = 0; i < 8; i++) begin
                count = count + {31'd0, value[i]};
            end
            popcount8 = count;
        end
    endfunction

    assign lookup_ready          = 1'b1;
    assign lookup_line_base      = ptcl_base_vaddr(lookup_base_vaddr);
    assign lookup_set            = pcd_set_id(lookup_pid, lookup_line_base);
    assign lookup_resp_set       = lookup_set;
    assign lookup_resp_valid     = lookup_valid;
    assign stat_entry_evictions  = stat_entry_evictions_q;
    assign stat_stale_bits       = stat_stale_bits_q;

    always_comb begin
        lookup_resp_row_valid      = '0;
        lookup_resp_present_bitmap = '0;
        lookup_resp_way            = '0;

        for (int unsigned way = 0; way < NUM_PCD_WAYS; way++) begin
            logic [7:0] candidate_bitmap;
            candidate_bitmap = present_q[lookup_set][way] & lookup_bitmap;
            lookup_resp_row_valid[way] = lookup_valid &&
                                         entry_valid_q[lookup_set][way] &&
                                         (candidate_bitmap != 8'b0);

            for (int unsigned sector = 0; sector < NUM_SECTORS; sector++) begin
                lookup_resp_present_bitmap[(way * NUM_SECTORS) + sector] =
                    lookup_valid &&
                    entry_valid_q[lookup_set][way] &&
                    candidate_bitmap[sector];
                lookup_resp_way[((way * NUM_SECTORS + sector) * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH] =
                    locator_q[lookup_set][way][sector];
            end
        end
    end

    assign fill_set           = pcd_set_id(fill_pid, ptcl_base_vaddr(fill_vaddr));
    assign fill_sector        = ptcl_sector(fill_vaddr);
    assign fill_hint_in_range = (int'(fill_pcd_way) < NUM_PCD_WAYS);

    always_comb begin
        logic found_invalid;
        logic [AGE_WIDTH-1:0] lru_age;

        fill_selected_way = '0;
        found_invalid     = 1'b0;
        lru_age           = last_visit_q[fill_set][0];

        if (fill_pcd_way_valid && fill_hint_in_range) begin
            fill_selected_way = fill_pcd_way;
        end else begin
            for (int unsigned way = 0; way < NUM_PCD_WAYS; way++) begin
                if (!found_invalid && !entry_valid_q[fill_set][way]) begin
                    fill_selected_way = PCD_WAY_WIDTH'(way);
                    found_invalid = 1'b1;
                end
            end

            if (!found_invalid) begin
                fill_selected_way = '0;
                lru_age = last_visit_q[fill_set][0];
                for (int unsigned way = 1; way < NUM_PCD_WAYS; way++) begin
                    if (last_visit_q[fill_set][way] < lru_age) begin
                        fill_selected_way = PCD_WAY_WIDTH'(way);
                        lru_age = last_visit_q[fill_set][way];
                    end
                end
            end
        end
    end

    assign clear_line_base    = ptcl_base_vaddr(clear_base_vaddr);
    assign clear_set          = pcd_set_id(clear_pid, clear_line_base);
    assign clear_way_in_range = (int'(clear_pcd_way) < NUM_PCD_WAYS);

    assign touch_line_base    = ptcl_base_vaddr(touch_base_vaddr);
    assign touch_set          = pcd_set_id(touch_pid, touch_line_base);
    assign touch_way_in_range = (int'(touch_pcd_way) < NUM_PCD_WAYS);

    assign remove_set    = pcd_set_id(remove_pid, ptcl_base_vaddr(remove_vaddr));
    assign remove_sector = ptcl_sector(remove_vaddr);

    always_comb begin
        logic [7:0] next_bitmap;
        logic [7:0] cleared_bitmap;
        logic       fill_allocates_new_row;

        for (int unsigned set = 0; set < NUM_SETS; set++) begin
            for (int unsigned way = 0; way < NUM_PCD_WAYS; way++) begin
                entry_valid_d[set][way] = entry_valid_q[set][way];
                present_d[set][way] = present_q[set][way];
                last_visit_d[set][way] = last_visit_q[set][way];
                for (int unsigned sector = 0; sector < NUM_SECTORS; sector++) begin
                    locator_d[set][way][sector] = locator_q[set][way][sector];
                end
            end
        end

        visit_counter_d = visit_counter_q;
        stat_entry_evictions_d = stat_entry_evictions_q;
        stat_stale_bits_d = stat_stale_bits_q;

        // Only one table update is applied per cycle.  Eviction removal wins
        // over stale-bit cleanup; fill wins over a metadata-only touch because
        // fill also refreshes last_visit.
        if (remove_valid) begin
            for (int unsigned way = 0; way < NUM_PCD_WAYS; way++) begin
                if (entry_valid_q[remove_set][way] &&
                    present_q[remove_set][way][remove_sector] &&
                    (locator_q[remove_set][way][remove_sector] == remove_tlb_way)) begin
                    next_bitmap = present_q[remove_set][way] & ~(8'b1 << remove_sector);
                    present_d[remove_set][way] = next_bitmap;
                    locator_d[remove_set][way][remove_sector] = '0;
                    if (next_bitmap == 8'b0) begin
                        entry_valid_d[remove_set][way] = 1'b0;
                    end
                end
            end
        end else if (clear_valid && clear_way_in_range) begin
            if (entry_valid_q[clear_set][clear_pcd_way]) begin
                cleared_bitmap = present_q[clear_set][clear_pcd_way] & clear_bitmap;
                next_bitmap = present_q[clear_set][clear_pcd_way] & ~clear_bitmap;
                present_d[clear_set][clear_pcd_way] = next_bitmap;
                stat_stale_bits_d = stat_stale_bits_q + popcount8(cleared_bitmap);

                for (int unsigned sector = 0; sector < NUM_SECTORS; sector++) begin
                    if (clear_bitmap[sector]) begin
                        locator_d[clear_set][clear_pcd_way][sector] = '0;
                    end
                end

                if (next_bitmap == 8'b0) begin
                    entry_valid_d[clear_set][clear_pcd_way] = 1'b0;
                end
            end
        end else if (fill_valid) begin
            fill_allocates_new_row = !fill_pcd_way_valid ||
                                     !fill_hint_in_range ||
                                     !entry_valid_q[fill_set][fill_selected_way];

            visit_counter_d = visit_counter_q + AGE_WIDTH'(1);
            entry_valid_d[fill_set][fill_selected_way] = 1'b1;
            last_visit_d[fill_set][fill_selected_way] = visit_counter_q + AGE_WIDTH'(1);

            if (fill_allocates_new_row) begin
                if ((!fill_pcd_way_valid || !fill_hint_in_range) &&
                    entry_valid_q[fill_set][fill_selected_way]) begin
                    stat_entry_evictions_d = stat_entry_evictions_q + 32'd1;
                end

                present_d[fill_set][fill_selected_way] = 8'b0;
                for (int unsigned sector = 0; sector < NUM_SECTORS; sector++) begin
                    locator_d[fill_set][fill_selected_way][sector] = '0;
                end
            end

            present_d[fill_set][fill_selected_way][fill_sector] = 1'b1;
            locator_d[fill_set][fill_selected_way][fill_sector] = fill_tlb_way;
        end else if (touch_valid && touch_way_in_range) begin
            if (entry_valid_q[touch_set][touch_pcd_way]) begin
                visit_counter_d = visit_counter_q + AGE_WIDTH'(1);
                last_visit_d[touch_set][touch_pcd_way] = visit_counter_q + AGE_WIDTH'(1);
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            visit_counter_q <= '0;
            stat_entry_evictions_q <= 32'd0;
            stat_stale_bits_q <= 32'd0;

            for (int unsigned set = 0; set < NUM_SETS; set++) begin
                for (int unsigned way = 0; way < NUM_PCD_WAYS; way++) begin
                    entry_valid_q[set][way] <= 1'b0;
                    present_q[set][way] <= 8'b0;
                    last_visit_q[set][way] <= '0;
                    for (int unsigned sector = 0; sector < NUM_SECTORS; sector++) begin
                        locator_q[set][way][sector] <= '0;
                    end
                end
            end
        end else begin
            visit_counter_q <= visit_counter_d;
            stat_entry_evictions_q <= stat_entry_evictions_d;
            stat_stale_bits_q <= stat_stale_bits_d;

            for (int unsigned set = 0; set < NUM_SETS; set++) begin
                for (int unsigned way = 0; way < NUM_PCD_WAYS; way++) begin
                    entry_valid_q[set][way] <= entry_valid_d[set][way];
                    present_q[set][way] <= present_d[set][way];
                    last_visit_q[set][way] <= last_visit_d[set][way];
                    for (int unsigned sector = 0; sector < NUM_SECTORS; sector++) begin
                        locator_q[set][way][sector] <= locator_d[set][way][sector];
                    end
                end
            end
        end
    end

`ifndef SYNTHESIS
    initial begin
        if (NUM_SETS == 0) begin
            $fatal(1, "ptcl_lookup_table: NUM_SETS must be positive");
        end
        if (NUM_PCD_WAYS == 0) begin
            $fatal(1, "ptcl_lookup_table: NUM_PCD_WAYS must be positive");
        end
        if (TLB_WAYS == 0) begin
            $fatal(1, "ptcl_lookup_table: TLB_WAYS must be positive");
        end
    end
`endif

endmodule
