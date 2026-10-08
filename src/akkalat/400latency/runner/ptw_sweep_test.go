package runner

import (
	"reflect"
	"testing"

	"github.com/sarchlab/akita/v3/sim"
)

func TestPTWSweepActualResources(t *testing.T) {
	oldG, oldI, oldQ, oldExtra := *gmmuPTWCount, *iommuPTWCount, *iommuPWQueueCapacity, *gmmuPLTExtraLatency
	t.Cleanup(func() {
		*gmmuPTWCount, *iommuPTWCount, *iommuPWQueueCapacity, *gmmuPLTExtraLatency = oldG, oldI, oldQ, oldExtra
	})
	for _, config := range [][4]int{{4, 16, 64, 0}, {8, 32, 64, 32}, {16, 64, 64, 8}, {64, 256, 1024, 4}} {
		*gmmuPTWCount, *iommuPTWCount, *iommuPWQueueCapacity, *gmmuPLTExtraLatency = config[0], config[1], config[2], config[3]
		engine := sim.NewSerialEngine()
		platform := MakeR9NanoBuilder()
		if platform.tileWidth*platform.tileHeight-1 != 48 || platform.numSAPerGPU*platform.numCUPerSA != 32 {
			t.Fatal("Original 48x32 topology changed")
		}
		mmu, pt := platform.createMMU(engine, nil)
		mmuState := reflect.ValueOf(mmu).Elem()
		if mmuState.FieldByName("maxRequestsInFlight").Int() != int64(config[1]) || mmuState.FieldByName("pwQueueCapacity").Int() != int64(config[2]) {
			t.Fatal("IOMMU count or queue did not reach actual component")
		}
		gpu := MakeR9NanoGPUBuilder()
		gpu.engine, gpu.mmu, gpu.pageTable, gpu.gpu = engine, mmu, pt, &GPU{}
		gpu.gpuName = "SweepGPU"
		gpu.buildGMMU()
		if reflect.ValueOf(gpu.gmmu).Elem().FieldByName("maxRequestsInFlight").Int() != int64(config[0]) {
			t.Fatal("GMMU walker count did not reach actual component")
		}
		platform.engine, platform.mmu = engine, mmu
		platform.createIOMMUCache()
		platform.createIOMMUTLB(nil, pt)
		gpu.IOMMUCache = platform.IOMMUTLB
		gpu.buildGMMUCache()
		state := reflect.ValueOf(gpu.gmmuCache).Elem()
		if state.FieldByName("pltExtraLatencyCycles").Int() != int64(config[3]) || state.FieldByName("pteLookupLatencyCycles").Int() != 32 {
			t.Fatal("PLT extra or PTE base latency mismatch")
		}
		if state.FieldByName("mshr").Elem().Elem().FieldByName("capacity").Int() != 16 {
			t.Fatal("L2 TLB MSHR changed")
		}
	}
}
