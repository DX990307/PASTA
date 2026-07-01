`timescale 1ns/1ps

module ptcl_lookup_table_tb;
    parameter NUM_SETS       = 4;
    parameter NUM_PCD_WAYS   = 2;
    parameter TLB_WAYS       = 16;
    parameter PID_WIDTH      = 8;
    parameter VADDR_WIDTH    = 32;
    parameter LOG2_PAGE_SIZE = 12;
    parameter AGE_WIDTH      = 32;
    parameter SET_WIDTH      = 2;
    parameter PCD_WAY_WIDTH  = 1;
    parameter TLB_WAY_WIDTH  = 4;
    parameter HASH_WIDTH     = 32;

    reg clk;
    reg rst_n;

    reg lookup_valid;
    wire lookup_ready;
    reg [PID_WIDTH-1:0] lookup_pid;
    reg [VADDR_WIDTH-1:0] lookup_base_vaddr;
    reg [7:0] lookup_bitmap;
    wire lookup_resp_valid;
    wire [SET_WIDTH-1:0] lookup_resp_set;
    wire [NUM_PCD_WAYS-1:0] lookup_resp_row_valid;
    wire [NUM_PCD_WAYS*8-1:0] lookup_resp_present_bitmap;
    wire [NUM_PCD_WAYS*8*TLB_WAY_WIDTH-1:0] lookup_resp_way;

    reg fill_valid;
    reg [PID_WIDTH-1:0] fill_pid;
    reg [VADDR_WIDTH-1:0] fill_vaddr;
    reg [TLB_WAY_WIDTH-1:0] fill_tlb_way;
    reg fill_pcd_way_valid;
    reg [PCD_WAY_WIDTH-1:0] fill_pcd_way;

    reg clear_valid;
    reg [PID_WIDTH-1:0] clear_pid;
    reg [VADDR_WIDTH-1:0] clear_base_vaddr;
    reg [PCD_WAY_WIDTH-1:0] clear_pcd_way;
    reg [7:0] clear_bitmap;

    reg touch_valid;
    reg [PID_WIDTH-1:0] touch_pid;
    reg [VADDR_WIDTH-1:0] touch_base_vaddr;
    reg [PCD_WAY_WIDTH-1:0] touch_pcd_way;

    reg remove_valid;
    reg [PID_WIDTH-1:0] remove_pid;
    reg [VADDR_WIDTH-1:0] remove_vaddr;
    reg [TLB_WAY_WIDTH-1:0] remove_tlb_way;

    wire [31:0] stat_entry_evictions;
    wire [31:0] stat_stale_bits;

    ptcl_lookup_table #(
        .NUM_SETS(NUM_SETS),
        .NUM_PCD_WAYS(NUM_PCD_WAYS),
        .TLB_WAYS(TLB_WAYS),
        .PID_WIDTH(PID_WIDTH),
        .VADDR_WIDTH(VADDR_WIDTH),
        .LOG2_PAGE_SIZE(LOG2_PAGE_SIZE),
        .AGE_WIDTH(AGE_WIDTH),
        .SET_WIDTH(SET_WIDTH),
        .PCD_WAY_WIDTH(PCD_WAY_WIDTH),
        .TLB_WAY_WIDTH(TLB_WAY_WIDTH),
        .HASH_WIDTH(HASH_WIDTH)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .lookup_valid(lookup_valid),
        .lookup_ready(lookup_ready),
        .lookup_pid(lookup_pid),
        .lookup_base_vaddr(lookup_base_vaddr),
        .lookup_bitmap(lookup_bitmap),
        .lookup_resp_valid(lookup_resp_valid),
        .lookup_resp_set(lookup_resp_set),
        .lookup_resp_row_valid(lookup_resp_row_valid),
        .lookup_resp_present_bitmap(lookup_resp_present_bitmap),
        .lookup_resp_way(lookup_resp_way),
        .fill_valid(fill_valid),
        .fill_pid(fill_pid),
        .fill_vaddr(fill_vaddr),
        .fill_tlb_way(fill_tlb_way),
        .fill_pcd_way_valid(fill_pcd_way_valid),
        .fill_pcd_way(fill_pcd_way),
        .clear_valid(clear_valid),
        .clear_pid(clear_pid),
        .clear_base_vaddr(clear_base_vaddr),
        .clear_pcd_way(clear_pcd_way),
        .clear_bitmap(clear_bitmap),
        .touch_valid(touch_valid),
        .touch_pid(touch_pid),
        .touch_base_vaddr(touch_base_vaddr),
        .touch_pcd_way(touch_pcd_way),
        .remove_valid(remove_valid),
        .remove_pid(remove_pid),
        .remove_vaddr(remove_vaddr),
        .remove_tlb_way(remove_tlb_way),
        .stat_entry_evictions(stat_entry_evictions),
        .stat_stale_bits(stat_stale_bits)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    function present_bit;
        input integer pcd_way;
        input integer sector;
        begin
            present_bit = lookup_resp_present_bitmap[(pcd_way * 8) + sector];
        end
    endfunction

    function [TLB_WAY_WIDTH-1:0] located_way;
        input integer pcd_way;
        input integer sector;
        begin
            located_way = lookup_resp_way[((pcd_way * 8 + sector) * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH];
        end
    endfunction

    task clear_inputs;
        begin
            lookup_valid = 1'b0;
            lookup_pid = {PID_WIDTH{1'b0}};
            lookup_base_vaddr = {VADDR_WIDTH{1'b0}};
            lookup_bitmap = 8'b0;

            fill_valid = 1'b0;
            fill_pid = {PID_WIDTH{1'b0}};
            fill_vaddr = {VADDR_WIDTH{1'b0}};
            fill_tlb_way = {TLB_WAY_WIDTH{1'b0}};
            fill_pcd_way_valid = 1'b0;
            fill_pcd_way = {PCD_WAY_WIDTH{1'b0}};

            clear_valid = 1'b0;
            clear_pid = {PID_WIDTH{1'b0}};
            clear_base_vaddr = {VADDR_WIDTH{1'b0}};
            clear_pcd_way = {PCD_WAY_WIDTH{1'b0}};
            clear_bitmap = 8'b0;

            touch_valid = 1'b0;
            touch_pid = {PID_WIDTH{1'b0}};
            touch_base_vaddr = {VADDR_WIDTH{1'b0}};
            touch_pcd_way = {PCD_WAY_WIDTH{1'b0}};

            remove_valid = 1'b0;
            remove_pid = {PID_WIDTH{1'b0}};
            remove_vaddr = {VADDR_WIDTH{1'b0}};
            remove_tlb_way = {TLB_WAY_WIDTH{1'b0}};
        end
    endtask

    task tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task check;
        input condition;
        input [1023:0] message;
        begin
            if (!condition) begin
                $display("CHECK failed: %0s", message);
                $finish;
            end
        end
    endtask

    localparam [PID_WIDTH-1:0] PID = 8'd3;
    localparam [VADDR_WIDTH-1:0] BASE = 32'h0002_0000;
    localparam [VADDR_WIDTH-1:0] SECTOR0 = BASE + 32'h0000_0000;
    localparam [VADDR_WIDTH-1:0] SECTOR3 = BASE + 32'h0000_3000;

    initial begin
        clear_inputs();
        rst_n = 1'b0;
        repeat (3) tick();
        rst_n = 1'b1;
        tick();

        lookup_valid = 1'b1;
        lookup_pid = PID;
        lookup_base_vaddr = BASE;
        lookup_bitmap = 8'hff;
        #1;
        check(lookup_ready, "lookup_ready should be high");
        check(lookup_resp_valid, "lookup response should be valid");
        check(lookup_resp_row_valid == 2'b00, "empty table should not return rows");
        check(lookup_resp_present_bitmap == 16'b0, "empty table should not return sectors");
        clear_inputs();

        fill_valid = 1'b1;
        fill_pid = PID;
        fill_vaddr = SECTOR0;
        fill_tlb_way = 4'd5;
        tick();
        clear_inputs();

        lookup_valid = 1'b1;
        lookup_pid = PID;
        lookup_base_vaddr = BASE;
        lookup_bitmap = 8'h01;
        #1;
        check(lookup_resp_row_valid[0], "first fill should allocate row 0");
        check(present_bit(0, 0), "sector 0 should be present");
        check(located_way(0, 0) == 4'd5, "sector 0 should locate TLB way 5");
        clear_inputs();

        fill_valid = 1'b1;
        fill_pid = PID;
        fill_vaddr = SECTOR3;
        fill_tlb_way = 4'd9;
        fill_pcd_way_valid = 1'b1;
        fill_pcd_way = 1'b0;
        tick();
        clear_inputs();

        lookup_valid = 1'b1;
        lookup_pid = PID;
        lookup_base_vaddr = BASE + 32'h0000_0123;
        lookup_bitmap = 8'h09;
        #1;
        check(present_bit(0, 0), "sector 0 should still be present");
        check(present_bit(0, 3), "sector 3 should be present");
        check(located_way(0, 3) == 4'd9, "sector 3 should locate TLB way 9");
        clear_inputs();

        clear_valid = 1'b1;
        clear_pid = PID;
        clear_base_vaddr = BASE;
        clear_pcd_way = 1'b0;
        clear_bitmap = 8'h01;
        tick();
        clear_inputs();

        lookup_valid = 1'b1;
        lookup_pid = PID;
        lookup_base_vaddr = BASE;
        lookup_bitmap = 8'h09;
        #1;
        check(!present_bit(0, 0), "stale clear should remove sector 0");
        check(present_bit(0, 3), "stale clear should preserve sector 3");
        check(stat_stale_bits == 32'd1, "stale clear counter should increment");
        clear_inputs();

        remove_valid = 1'b1;
        remove_pid = PID;
        remove_vaddr = SECTOR3;
        remove_tlb_way = 4'd9;
        tick();
        clear_inputs();

        lookup_valid = 1'b1;
        lookup_pid = PID;
        lookup_base_vaddr = BASE;
        lookup_bitmap = 8'hff;
        #1;
        check(lookup_resp_row_valid == 2'b00, "remove should invalidate row");
        check(lookup_resp_present_bitmap == 16'b0, "remove should clear sectors");
        check(stat_entry_evictions == 32'd0, "basic test should not evict rows");

        $display("ptcl_lookup_table_tb PASS");
        $finish;
    end
endmodule
