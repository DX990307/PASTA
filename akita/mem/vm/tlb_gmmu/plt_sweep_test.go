package tlb_gmmu

import "testing"

func TestPLTRowsAndExtraLookupLatency(t *testing.T) {
	for _, c := range []struct{ rows, extra, total int }{{16, 32, 96}, {64, 128, 192}, {128, 256, 320}} {
		d := newPTCLCoverageDirectory(16, 16, c.rows/16, 12)
		rows := 0
		for _, set := range d.sets {
			rows += len(set.entries)
		}
		if rows != c.rows {
			t.Fatalf("rows=%d, want %d", rows, c.rows)
		}
		tlb := &GMMUTLB{pteLookupLatencyCycles: 32, pltExtraLatencyCycles: c.extra}
		if got := tlb.ptclSetLookupLatencyCycles(); got != c.total {
			t.Fatalf("latency=%d, want %d", got, c.total)
		}
	}
	original := &GMMUTLB{pteLookupLatencyCycles: 32}
	if got := original.ptclSetLookupLatencyCycles(); got != 64 {
		t.Fatalf("default latency changed to %d", got)
	}
}

func TestPLTExtraLatencyRejectsNegative(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("negative PLT latency accepted")
		}
	}()
	MakeBuilder().WithPLTExtraLatencyCycles(-1)
}
