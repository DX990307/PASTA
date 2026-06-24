package mmuTLB

// IncomingRequestCount reports how many translation requests entered the
// IOMMU-side TLB from the GMMU path.
func (tlb *TLB) IncomingRequestCount() int {
	return tlb.incomingReqCount
}

// DownstreamRequestCount reports how many translation requests the IOMMU-side
// TLB sent toward MMUCache/MMU.
func (tlb *TLB) DownstreamRequestCount() int {
	return tlb.downstreamReqCount
}

// DemandPTEOnlyEnabled reports whether the IOMMU-side demand path is forced
// to single-page requests.
func (tlb *TLB) DemandPTEOnlyEnabled() bool {
	return tlb.demandPTEOnly
}

// VPNMSHRBaselineEnabled reports whether the IOMMU-side TLB is using exact-VPN
// MSHR entries instead of PTCL-granularity entries.
func (tlb *TLB) VPNMSHRBaselineEnabled() bool {
	return tlb.vpnMSHRBaseline
}

// LookupLatencyCycles reports the fixed MMUTLB/IOTLB lookup delay applied to
// each buffered request before tag lookup/hit-miss handling proceeds.
func (tlb *TLB) LookupLatencyCycles() int {
	return tlb.lookupLatencyCycles
}

// SetAsLineStats reports PTCL set-as-line lookup/fill activity in the
// IOMMU-side TLB.
func (tlb *TLB) SetAsLineStats() (
	enabled bool,
	lookupJobs int,
	requestedBits int,
	hitBits int,
	missBits int,
	savedJobs int,
	fills int,
	conflictEvictions int,
	lineEntries int,
) {
	if tlb.pcd != nil {
		lineEntries = tlb.pcd.validEntryCount()
	}

	return tlb.setAsLineTLBEnabled,
		tlb.setLookupJobs,
		tlb.setLookupRequestedBits,
		tlb.setLookupHitBits,
		tlb.setLookupMissBits,
		tlb.setLookupSavedJobs,
		tlb.setFills,
		tlb.setConflictEvictions,
		lineEntries
}
