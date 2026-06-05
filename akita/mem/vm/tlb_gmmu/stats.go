package tlb_gmmu

import "sort"

// PrefetchOutcomeBlockStat summarizes exact GPU-side prefetch outcomes for one page block.
type PrefetchOutcomeBlockStat struct {
	PageBlock       uint64
	Inserted        int
	Useful          int
	UsefulHit       int
	LateUseful      int
	Unused          int
	ResidentPending int
}

// PrefetchUnusedPTCLStat summarizes how often one PTCL became unused before demand consumption.
type PrefetchUnusedPTCLStat struct {
	PageBlock uint64
	PTCL      uint64
	Count     int
}

// PTCLModeEnabled reports whether the L2 TLB is currently coalescing at PTCL
// granularity.
func (tlb *GMMUTLB) PTCLModeEnabled() bool {
	return tlb.ptclMode && !tlb.vpnMSHRBaseline
}

// CoalescingCounter returns the adaptive PTCL/PTE score.
func (tlb *GMMUTLB) CoalescingCounter() int {
	return tlb.coalescingCounter
}

// PTCLThresholds returns the low and high hysteresis thresholds for the
// coalescing score.
func (tlb *GMMUTLB) PTCLThresholds() (low, high int) {
	return tlb.ptclLowThreshold, tlb.ptclHighThreshold
}

// ModeSwitchCounts returns the number of transitions into PTCL and PTE modes.
func (tlb *GMMUTLB) ModeSwitchCounts() (toPTCL, toPTE int) {
	return tlb.switchToPTCLCount, tlb.switchToPTECount
}

// VPNMSHRBaselineEnabled reports whether the GMMU L2 TLB is using exact-VPN
// MSHR entries instead of PTCL-granularity entries.
func (tlb *GMMUTLB) VPNMSHRBaselineEnabled() bool {
	return tlb.vpnMSHRBaseline
}

// DownstreamRequestCounts reports how many translation requests the GMMU L2 TLB
// issued downstream in total, to the local MMU, and to the IOMMU path.
func (tlb *GMMUTLB) DownstreamRequestCounts() (total, local, iommu int) {
	return tlb.downstreamReqCount, tlb.localReqCount, tlb.iommuReqCount
}

// PrefetchOutcomeStats reports exact GPU-side prefetch usefulness outcomes.
func (tlb *GMMUTLB) PrefetchOutcomeStats() (
	inserted int,
	useful int,
	usefulHit int,
	lateUseful int,
	unused int,
	residentPending int,
) {
	return tlb.prefetchExactInserted,
		tlb.prefetchExactUseful,
		tlb.prefetchExactUsefulHit,
		tlb.prefetchExactLateUseful,
		tlb.prefetchExactUnused,
		len(tlb.prefetchedResidentEntries)
}

// PrefetchBlockStats reports exact GPU-side prefetch usefulness outcomes grouped by page block.
func (tlb *GMMUTLB) PrefetchBlockStats() []PrefetchOutcomeBlockStat {
	if len(tlb.prefetchOutcomeByBlock) == 0 {
		return nil
	}

	pageBlocks := make([]uint64, 0, len(tlb.prefetchOutcomeByBlock))
	for pageBlock := range tlb.prefetchOutcomeByBlock {
		pageBlocks = append(pageBlocks, pageBlock)
	}

	sort.Slice(pageBlocks, func(i, j int) bool {
		return pageBlocks[i] < pageBlocks[j]
	})

	stats := make([]PrefetchOutcomeBlockStat, 0, len(pageBlocks))
	for _, pageBlock := range pageBlocks {
		counts := tlb.prefetchOutcomeByBlock[pageBlock]
		stats = append(stats, PrefetchOutcomeBlockStat{
			PageBlock:       pageBlock,
			Inserted:        counts.Inserted,
			Useful:          counts.Useful,
			UsefulHit:       counts.UsefulHit,
			LateUseful:      counts.LateUseful,
			Unused:          counts.Unused,
			ResidentPending: counts.ResidentPending,
		})
	}

	return stats
}

// PrefetchUnusedPTCLStats reports exact GPU-side unused PTCL counts grouped by page block and PTCL.
func (tlb *GMMUTLB) PrefetchUnusedPTCLStats() []PrefetchUnusedPTCLStat {
	if len(tlb.prefetchUnusedPTCLByBlock) == 0 {
		return nil
	}

	pageBlocks := make([]uint64, 0, len(tlb.prefetchUnusedPTCLByBlock))
	for pageBlock := range tlb.prefetchUnusedPTCLByBlock {
		pageBlocks = append(pageBlocks, pageBlock)
	}
	sort.Slice(pageBlocks, func(i, j int) bool {
		return pageBlocks[i] < pageBlocks[j]
	})

	stats := make([]PrefetchUnusedPTCLStat, 0)
	for _, pageBlock := range pageBlocks {
		ptclCounts := tlb.prefetchUnusedPTCLByBlock[pageBlock]
		ptcls := make([]uint64, 0, len(ptclCounts))
		for ptcl := range ptclCounts {
			ptcls = append(ptcls, ptcl)
		}
		sort.Slice(ptcls, func(i, j int) bool {
			return ptcls[i] < ptcls[j]
		})
		for _, ptcl := range ptcls {
			stats = append(stats, PrefetchUnusedPTCLStat{
				PageBlock: pageBlock,
				PTCL:      ptcl,
				Count:     ptclCounts[ptcl],
			})
		}
	}

	return stats
}
