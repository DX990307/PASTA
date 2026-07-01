`timescale 1ns/1ps

module ptcl_lookup_table_tb;
    localparam int unsigned NUM_SETS       = 4;
    localparam int unsigned NUM_PCD_WAYS   = 2;
    localparam int unsigned TLB_WAYS       = 16;
    localparam int unsigned PID_WIDTH      = 8;
    localparam int unsigned VADDR_WIDTH    = 32;
    localparam int unsigned LOG2_PAGE_SIZE = 12;
    localparam int unsigned SET_WIDTH      = (NUM_SETS <= 1) ? 1 : $clog2(NUM_SETS);
    localparam int unsigned PCD_WAY_WIDTH  = (NUM_PCD_WAYS <= 1) ? 1 : $clog2(NUM_PCD_WAYS);
    localparam int unsigned TLB_WAY_WIDTH  = (TLB_WAYS <= 1) ? 1 : $clog2(TLB_WAYS);

    logic clk;
    logic rst_n;

    logic lookup_valid;
    logic lookup_ready;
    logic [PID_WIDTH-1:0] lookup_pid;
    logic [VADDR_WIDTH-1:0] lookup_base_vaddr;
    logic [7:0] lookup_bitmap;
    logic lookup_resp_valid;
    logic [SET_WIDTH-1:0] lookup_resp_set;
    logic [NUM_PCD_WAYS-1:0] lookup_resp_row_valid;
    logic [NUM_PCD_WAYS*8-1:0] lookup_resp_present_bitmap;
    logic [NUM_PCD_WAYS*8*TLB_WAY_WIDTH-1:0] lookup_resp_way;

    logic fill_valid;
    logic [PID_WIDTH-1:0] fill_pid;
    logic [VADDR_WIDTH-1:0] fill_vaddr;
    logic [TLB_WAY_WIDTH-1:0] fill_tlb_way;
    logic fill_pcd_way_valid;
    logic [PCD_WAY_WIDTH-1:0] fill_pcd_way;

    logic clear_valid;
    logic [PID_WIDTH-1:0] clear_pid;
    logic [VADDR_WIDTH-1:0] clear_base_vaddr;
    logic [PCD_WAY_WIDTH-1:0] clear_pcd_way;
    logic [7:0] clear_bitmap;

    logic touch_valid;
    logic [PID_WIDTH-1:0] touch_pid;
    logic [VADDR_WIDTH-1:0] touch_base_vaddr;
    logic [PCD_WAY_WIDTH-1:0] touch_pcd_way;

    logic remove_valid;
    logic [PID_WIDTH-1:0] remove_pid;
    logic [VADDR_WIDTH-1:0] remove_vaddr;
    logic [TLB_WAY_WIDTH-1:0] remove_tlb_way;

    logic [31:0] stat_entry_evictions;
    logic [31:0] stat_stale_bits;

    ptcl_lookup_table #(
        .NUM_SETS(NUM_SETS),
        .NUM_PCD_WAYS(NUM_PCD_WAYS),
        .TLB_WAYS(TLB_WAYS),
        .PID_WIDTH(PID_WIDTH),
        .VADDR_WIDTH(VADDR_WIDTH),
        .LOG2_PAGE_SIZE(LOG2_PAGE_SIZE)
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

    function automatic logic present_bit(input int unsigned pcd_way, input int unsigned sector);
        begin
            present_bit = lookup_resp_present_bitmap[(pcd_way * 8) + sector];
        end
    endfunction

    function automatic logic [TLB_WAY_WIDTH-1:0] located_way(
        input int unsigned pcd_way,
        input int unsigned sector
    );
        begin
            located_way = lookup_resp_way[((pcd_way * 8 + sector) * TLB_WAY_WIDTH) +: TLB_WAY_WIDTH];
        end
    endfunction

    task automatic clear_inputs;
        begin
            lookup_valid = 1'b0;
            lookup_pid = '0;
            lookup_base_vaddr = '0;
            lookup_bitmap = '0;

            fill_valid = 1'b0;
            fill_pid = '0;
            fill_vaddr = '0;
            fill_tlb_way = '0;
            fill_pcd_way_valid = 1'b0;
            fill_pcd_way = '0;

            clear_valid = 1'b0;
            clear_pid = '0;
            clear_base_vaddr = '0;
            clear_pcd_way = '0;
            clear_bitmap = '0;

            touch_valid = 1'b0;
            touch_pid = '0;
            touch_base_vaddr = '0;
            touch_pcd_way = '0;

            remove_valid = 1'b0;
            remove_pid = '0;
            remove_vaddr = '0;
            remove_tlb_way = '0;
        end
    endtask

    task automatic tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task automatic check(input bit condition, input string message);
        begin
            if (!condition) begin
                $fatal(1, "CHECK failed: %s", message);
            end
        end
    endtask

    localparam logic [PID_WIDTH-1:0] PID = 8'd3;
    localparam logic [VADDR_WIDTH-1:0] BASE = 32'h0002_0000;
    localparam logic [VADDR_WIDTH-1:0] SECTOR0 = BASE + 32'h0000_0000;
    localparam logic [VADDR_WIDTH-1:0] SECTOR3 = BASE + 32'h0000_3000;

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
        check(lookup_ready, "lookup_ready should be tied high");
        check(lookup_resp_valid, "lookup response should be combinational");
        check(lookup_resp_row_valid == 2'b00, "empty table should not return candidates");
        check(lookup_resp_present_bitmap == '0, "empty table should have no sector bits");
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
        check(lookup_resp_row_valid[0], "first fill should allocate PCD way 0");
        check(present_bit(0, 0), "sector 0 should be present");
        check(located_way(0, 0) == 4'd5, "sector 0 should locate TLB way 5");
        clear_inputs();

        fill_valid = 1'b1;
        fill_pid = PID;
        fill_vaddr = SECTOR3;
        fill_tlb_way = 4'd9;
        fill_pcd_way_valid = 1'b1;
        fill_pcd_way = '0;
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
        clear_pcd_way = '0;
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
        check(stat_stale_bits == 32'd1, "stale clear counter should increment once");
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
        check(lookup_resp_row_valid == 2'b00, "remove should invalidate the empty PCD row");
        check(lookup_resp_present_bitmap == '0, "remove should clear the last sector bit");
        check(stat_entry_evictions == 32'd0, "basic test should not evict PCD rows");

        $display("ptcl_lookup_table_tb PASS");
        $finish;
    end
endmodule
