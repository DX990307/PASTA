package tlb

import (
	"testing"

	"github.com/sarchlab/akita/v3/mem/vm"
)

func latpcReq(vAddr uint64, index uint8) *vm.TranslationReq {
	return vm.TranslationReqBuilder{}.
		WithPID(1).
		WithVAddr(vAddr).
		WithLATPCMetadata(true, 0x1000, 1, index, 1<<index).
		Build()
}

func TestLATPCMSHRCompressionSharesEntry(t *testing.T) {
	m := newMSHR(1, true)

	req0 := latpcReq(0x1000, 0)
	req1 := latpcReq(0x2000, 1)

	if !m.CanAddReq(req0) {
		t.Fatal("first LATPC request should fit")
	}
	if _, compressed := m.AddReq(req0); compressed {
		t.Fatal("first LATPC request should allocate a new MSHR entry")
	}
	if !m.CanAddReq(req1) {
		t.Fatal("second request in the same LATPC group should fit despite full capacity")
	}
	if _, compressed := m.AddReq(req1); !compressed {
		t.Fatal("second LATPC request should attach to the existing compressed entry")
	}
	if got := len(m.AllEntries()); got != 1 {
		t.Fatalf("compressed LATPC group should use one MSHR entry, got %d", got)
	}

	page0 := vm.Page{PID: 1, VAddr: 0x1000, Valid: true}
	ready0, ok := m.Ready(1, 0x1000, page0)
	if !ok || ready0 == nil {
		t.Fatal("first response should find a ready sub-entry")
	}
	if len(ready0.Requests) != 1 || ready0.Requests[0] != req0 {
		t.Fatal("first response should only wake the first VPN's requests")
	}
	if !m.IsEntryPresent(1, 0x2000) {
		t.Fatal("second VPN should remain pending after first response")
	}

	page1 := vm.Page{PID: 1, VAddr: 0x2000, Valid: true}
	ready1, ok := m.Ready(1, 0x2000, page1)
	if !ok || ready1 == nil {
		t.Fatal("second response should find a ready sub-entry")
	}
	if len(ready1.Requests) != 1 || ready1.Requests[0] != req1 {
		t.Fatal("second response should only wake the second VPN's requests")
	}
	if got := len(m.AllEntries()); got != 0 {
		t.Fatalf("all LATPC sub-entries should be removed, got %d entries", got)
	}
}
