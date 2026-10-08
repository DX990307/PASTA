package tlb_gmmu

import (
	"testing"

	"github.com/sarchlab/akita/v3/mem/vm"
	"github.com/sarchlab/akita/v3/sim"
)

func TestPTCLMSHRLifetimeProfileUsesAllocationToReadyInterval(t *testing.T) {
	tlb := MakeBuilder().
		WithLog2PageSize(12).
		WithPTCLMSHRLifetimeProfile(true).
		Build("L2TLB")

	firstArrival := sim.VTimeInSec(10e-9)
	entry := tlb.mshr.Add(vm.PID(1), 0x1000, firstArrival, 0)
	tlb.mshr.UpdateUpLevelBitMap(vm.PID(1), 0x2000, 20e-9, 0)
	tlb.observePTCLMSHRLifetime(50e-9, entry)

	stats := tlb.PTCLMSHRLifetimeStats()
	if stats.CompletedEntries != 1 {
		t.Fatalf("completed entries = %d, want 1", stats.CompletedEntries)
	}
	if stats.LifetimeCycles != 40 {
		t.Fatalf("lifetime cycles = %d, want 40", stats.LifetimeCycles)
	}
	if stats.ArrivalSpanCycles != 10 {
		t.Fatalf("arrival span cycles = %d, want 10", stats.ArrivalSpanCycles)
	}
	if stats.DemandHistogram[2] != 1 {
		t.Fatalf("two-demand entries = %d, want 1", stats.DemandHistogram[2])
	}
	if stats.LifetimeHistogram[1] != 1 {
		t.Fatalf("33-64 cycle entries = %d, want 1", stats.LifetimeHistogram[1])
	}
}

func TestPTCLMSHRLifetimeProfileIgnoresVPNMSHRBaseline(t *testing.T) {
	tlb := MakeBuilder().
		WithLog2PageSize(12).
		WithPerVPNMSHRBaseline(true).
		WithPTCLMSHRLifetimeProfile(true).
		Build("L2TLB")

	entry := tlb.mshr.Add(vm.PID(1), 0x1000, 10e-9, 0)
	tlb.observePTCLMSHRLifetime(50e-9, entry)

	if got := tlb.PTCLMSHRLifetimeStats().CompletedEntries; got != 0 {
		t.Fatalf("completed entries = %d, want 0 for VPN MSHR baseline", got)
	}
}
