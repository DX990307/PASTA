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
