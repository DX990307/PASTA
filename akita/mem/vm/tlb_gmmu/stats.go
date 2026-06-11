package tlb_gmmu

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

// PTELookupLatencyCycles reports the fixed GMMUCache PTE lookup delay charged
// for each lookup slot job before the local TLB entry lookup.
func (tlb *GMMUTLB) PTELookupLatencyCycles() int {
	return tlb.pteLookupLatencyCycles
}

// PTELookupDelayStats reports how many internal PTE lookup jobs paid the
// GMMUCache lookup budget and the total charged slot-cycles.
func (tlb *GMMUTLB) PTELookupDelayStats() (count int, cycles int) {
	return tlb.pteLookupDelayCount, tlb.pteLookupDelayCycles
}

// PTELookupQueueStats reports the maximum number of occupied lookup slots and
// the maximum waiting queue length observed.
func (tlb *GMMUTLB) PTELookupQueueStats() (maxInflight, maxWaiting int) {
	return tlb.pteLookupMaxInflight, tlb.pteLookupMaxWaiting
}

// PrefetchStats reports whether the GMMU-side prefetcher is enabled and how
// many candidates it generated, issued, or rejected.
func (tlb *GMMUTLB) PrefetchStats() (
	enabled bool,
	demandPTCLReturn bool,
	generated int,
	enqueued int,
	dropped int,
	rejectedByPrefix int,
	rejectedByDuplicate int,
	rejectedByInvalid int,
	noClearPatternSkips int,
	admitted int,
	promoted int,
) {
	if tlb.prefetcher == nil {
		return false, false, 0, 0, 0, 0, 0, 0, 0, 0, 0
	}

	return tlb.prefetcher.enabled,
		tlb.prefetcher.promoteDemandToPTCL,
		tlb.prefetcher.generatedCandidates,
		tlb.prefetcher.enqueuedCandidates,
		tlb.prefetcher.droppedCandidates,
		tlb.prefetcher.rejectedByPrefix,
		tlb.prefetcher.rejectedByDuplicate,
		tlb.prefetcher.rejectedByInvalid,
		tlb.prefetcher.noClearPatternSkips,
		tlb.prefetcher.admittedLearnersCount,
		tlb.prefetcher.promotedDemandRequests
}

// PrefetchOutcomeStats reports the observed completion count for GMMU-side
// prefetches. Useful/late/lost are kept for metric compatibility.
func (tlb *GMMUTLB) PrefetchOutcomeStats() (
	completed int,
	useful int,
	late int,
	lostBeforeUse int,
) {
	return tlb.prefetchCompletedCount, 0, 0, 0
}
