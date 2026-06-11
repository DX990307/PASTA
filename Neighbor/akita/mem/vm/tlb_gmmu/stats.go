package tlb_gmmu

// DownstreamRequestCounts reports how many translation requests the GMMU L2 TLB
// issued downstream in total, to the local MMU, and to the IOMMU path.
func (tlb *GMMUTLB) DownstreamRequestCounts() (total, local, iommu int) {
	return tlb.downstreamReqCount, tlb.localReqCount, tlb.iommuReqCount
}

// PTELookupLatencyCycles reports the fixed GMMUCache PTE lookup/fill delay
// charged for each returned PTE response.
func (tlb *GMMUTLB) PTELookupLatencyCycles() int {
	return tlb.pteLookupLatencyCycles
}

// PTELookupDelayStats reports how many PTE responses paid the GMMUCache lookup
// budget and the total charged cycles.
func (tlb *GMMUTLB) PTELookupDelayStats() (count int, cycles int) {
	return tlb.pteLookupDelayCount, tlb.pteLookupDelayCycles
}
