package runner

import (
	"encoding/json"
	"flag"
	"os"
	"strings"
	"testing"

	"github.com/sarchlab/akita/v3/mem/mem"
	"github.com/sarchlab/akita/v3/mem/vm/translationtrace"
	"github.com/sarchlab/akita/v3/sim"
)

// Parse each FULL14 command and build real translation components, not a GPU run.
func TestR4ActualTranslationBuilders(t *testing.T) {
	var plan struct {
		Jobs []struct {
			ID      string   `json:"id"`
			Command []string `json:"command"`
		} `json:"jobs"`
	}
	data, err := os.ReadFile(os.Getenv("R4_PLAN_JSON"))
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &plan); err != nil || len(plan.Jobs) != 14 {
		t.Fatal("expected FULL14 R4 commands", err)
	}
	for _, job := range plan.Jobs {
		t.Run(job.ID, func(t *testing.T) {
			saved := map[string]string{}
			flag.VisitAll(func(f *flag.Flag) {
				if !strings.HasPrefix(f.Name, "test.") {
					saved[f.Name] = f.Value.String()
					if err := flag.Set(f.Name, f.DefValue); err != nil {
						t.Fatal(err)
					}
				}
			})
			t.Cleanup(func() {
				for name, value := range saved {
					if err := flag.Set(name, value); err != nil {
						t.Error(err)
					}
				}
				translationtrace.Configure(false, "", 100000)
			})
			for _, arg := range job.Command[1:] {
				name, value, found := strings.Cut(strings.TrimPrefix(arg, "-"), "=")
				if name == "benchmark" {
					continue
				}
				if !found {
					value = "true"
				}
				if err := flag.Set(name, value); err != nil {
					t.Fatal(err)
				}
			}
			translationtrace.Configure(false, "", 100000)
			engine := sim.NewSerialEngine()
			table := mem.NewMultiPageFinder()
			p := R9NanoPlatformBuilder{engine: engine, log2PageSize: 12}
			mmu, pt := p.createMMU(engine, table)
			p.mmu = mmu
			p.createIOMMUCache()
			p.createIOMMUTLB(table, pt)
			g := R9NanoGPUBuilder{engine: engine, freq: sim.GHz, gpuID: 1,
				gpuName: "R4Contract.GPU", gpu: &GPU{Domain: sim.NewDomain("R4Contract.GPU")},
				pageTable: pt, log2PageSize: 12, mmu: mmu,
				IOMMUCache: p.IOMMUTLB, gmmuCacheTable: table}
			g.buildGMMU()
			g.buildGMMUCache()
			l2, io := g.gmmuCache, p.IOMMUTLB
			stats := l2.PLTStudyStats()
			low, high := l2.PTCLThresholds()
			assist, _, _, _ := l2.IdleIOMMUAssistStats()
			line, _, _, _, _, _, _, _, _ := io.SetAsLineStats()
			if stats["plt_lookup_disabled"] != 1 || stats["plt_total_rows"] != 0 ||
				l2.VPNMSHRBaselineEnabled() || io.VPNMSHRBaselineEnabled() ||
				!assist || !line || low != 4 || high != 16 ||
				io.DemandPTEOnlyEnabled() || *ptwDemandPTEOnly ||
				l2.MSHRAdmissionStats()["mshr_entry_capacity"] != 16 ||
				l2.PTELookupLatencyCycles() != 32 || l2.PTELookupSlotLimit() != 8 ||
				g.gmmu.PTWCapacity() != 4 || mmu.PTWCapacity() != 16 ||
				configuredLATPC() || configuredNeighborAbstract() != "off" ||
				*maxWGCount != 76800 || *numGPMs != 48 || engine.CurrentTime() != 0 {
				t.Fatal("R4 must remove only PLT while retaining PASTA mechanisms")
			}
		})
	}
}
