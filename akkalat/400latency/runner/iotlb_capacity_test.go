package runner

import (
	"github.com/sarchlab/akita/v3/sim"
	"reflect"
	"testing"
)

func TestIOTLBCapacityChangesOnlySetCount(t *testing.T) {
	old := *iotlbNumSets
	t.Cleanup(func() { *iotlbNumSets = old })
	for _, sets := range []int{32, 64, 128} {
		*iotlbNumSets = sets
		b := MakeR9NanoBuilder()
		b.engine = sim.NewSerialEngine()
		mmu, pt := b.createMMU(b.engine, nil)
		b.mmu = mmu
		b.createIOMMUCache()
		b.createIOMMUTLB(nil, pt)
		v := reflect.ValueOf(b.IOMMUTLB).Elem()
		if v.FieldByName("numSets").Int() != int64(sets) || v.FieldByName("numWays").Int() != 32 {
			t.Fatalf("wrong IOTLB geometry for %d sets", sets)
		}
		if v.FieldByName("mshr").Elem().Elem().FieldByName("capacity").Int() != 64 {
			t.Fatal("IOTLB MSHR capacity changed")
		}
	}
}
func TestIOTLBSetCountRejectsZero(t *testing.T) {
	old := *iotlbNumSets
	defer func() {
		*iotlbNumSets = old
		if recover() == nil {
			t.Fatal("nonpositive sets accepted")
		}
	}()
	*iotlbNumSets = 0
	iotlbSetCount()
}
