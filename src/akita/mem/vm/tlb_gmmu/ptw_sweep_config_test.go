package tlb_gmmu

import (
	"testing"

	"github.com/sarchlab/akita/v3/sim"
)

func TestPTWSweepPLTExtraLatency(t *testing.T) {
	for _, extra := range []int{0, 4, 8, 32} {
		tlb := MakeBuilder().WithEngine(sim.NewSerialEngine()).
			WithPTELookupLatencyCycles(32).WithPLTExtraLatencyCycles(extra).Build("SweepTLB")
		if tlb.pteLookupLatencyCycles != 32 || tlb.ptclSetLookupLatencyCycles() != 64+extra {
			t.Fatalf("extra=%d: base=%d total=%d", extra, tlb.pteLookupLatencyCycles, tlb.ptclSetLookupLatencyCycles())
		}
		if tlb.pteLookupSlotLimit != tlb.numReqPerCycle {
			t.Fatal("Inherited lookup slot default changed")
		}
	}
}
