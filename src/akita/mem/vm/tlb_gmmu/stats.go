package tlb_gmmu

import "github.com/sarchlab/akita/v3/mem/vm/tlb_gmmu/internal"

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

func (tlb *GMMUTLB) TLBGeometry() (sets, ways, entries int) {
	return tlb.numSets, tlb.numWays, tlb.numSets * tlb.numWays
}

func (tlb *GMMUTLB) PTCLLineSize() int {
	return tlb.effectivePTCLLineSize()
}

// ModeSwitchCounts returns the number of transitions into PTCL and PTE modes.
func (tlb *GMMUTLB) ModeSwitchCounts() (toPTCL, toPTE int) {
	return tlb.switchToPTCLCount, tlb.switchToPTECount
}

// ModeCompletionCounts returns how many completed MSHR entries were accounted
// while the adaptive L2 TLB was in PTE or PTCL mode.
func (tlb *GMMUTLB) ModeCompletionCounts() (pteMode, ptclMode int) {
	return tlb.pteModeCompletions, tlb.ptclModeCompletions
}

// PTCLSetModeFlushes returns how often the set-as-line TLB state was flushed
// because the MSHR coalescing mode switched.
func (tlb *GMMUTLB) PTCLSetModeFlushes() int {
	return tlb.ptclSetModeFlushes
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

func (tlb *GMMUTLB) PTELookupSlotLimit() int {
	return tlb.pteLookupSlotLimit
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

func (tlb *GMMUTLB) IdleIOMMUAssistStats() (
	enabled bool,
	issued int,
	blockedBusy int,
	localFallback int,
) {
	return tlb.idleIOMMUAssistEnabled,
		tlb.idleIOMMUAssistIssued,
		tlb.idleIOMMUAssistBlockedBusy,
		tlb.idleIOMMUAssistLocalFallback
}

func (tlb *GMMUTLB) resetIdleIOMMUAssistStats() {
	tlb.idleIOMMUAssistIssued = 0
	tlb.idleIOMMUAssistBlockedBusy = 0
	tlb.idleIOMMUAssistLocalFallback = 0
}

// PTCLResidencyStats reports demand-weighted L2 residency at request arrival.
// The histogram index is the number of resident sibling PTEs in the PTCL.
func (tlb *GMMUTLB) PTCLResidencyStats() (
	enabled bool,
	probes uint64,
	residentPTEs uint64,
	histogram [9]uint64,
) {
	probes = tlb.ptclResidencyProbes
	residentPTEs = tlb.ptclResidencyResidentPTEs
	histogram = tlb.ptclResidencyHistogram
	for _, window := range tlb.ptclResidencyWindows {
		resident := tlb.bitmapCount(window.residentAtOpen)
		probes++
		residentPTEs += uint64(resident)
		histogram[resident]++
	}
	return tlb.ptclResidencyProfileEnabled, probes, residentPTEs, histogram
}

// PTCLWindowStats reports the distinct demand offsets and demand offsets that
// were already resident when each fixed-length shadow window opened.
func (tlb *GMMUTLB) PTCLWindowStats() (
	windowCycles int,
	windows uint64,
	demandPTEs uint64,
	demandHistogram [9]uint64,
	demandResidentPTEs uint64,
	demandResidentHistogram [9]uint64,
) {
	windows = tlb.ptclResidencyProbes
	demandPTEs = tlb.ptclWindowDemandPTEs
	demandHistogram = tlb.ptclWindowDemandHistogram
	demandResidentPTEs = tlb.ptclWindowDemandResidentPTEs
	demandResidentHistogram = tlb.ptclWindowDemandResidentHist
	for _, window := range tlb.ptclResidencyWindows {
		demand := tlb.bitmapCount(window.demandBitmap)
		demandResident := tlb.bitmapCount(tlb.intersectBitmaps(
			window.demandBitmap,
			window.residentAtOpen,
		))
		windows++
		demandPTEs += uint64(demand)
		demandHistogram[demand]++
		demandResidentPTEs += uint64(demandResident)
		demandResidentHistogram[demandResident]++
	}
	return tlb.ptclResidencyWindowCycles,
		windows,
		demandPTEs,
		demandHistogram,
		demandResidentPTEs,
		demandResidentHistogram
}

func (tlb *GMMUTLB) resetPTCLResidencyStats() {
	tlb.ptclResidencyProbes = 0
	tlb.ptclResidencyResidentPTEs = 0
	tlb.ptclResidencyHistogram = [9]uint64{}
	tlb.ptclWindowDemandPTEs = 0
	tlb.ptclWindowDemandHistogram = [9]uint64{}
	tlb.ptclWindowDemandResidentPTEs = 0
	tlb.ptclWindowDemandResidentHist = [9]uint64{}
	tlb.ptclResidencyWindows = make(map[pteLookupGroupKey]*ptclResidencyWindow)
}

// PTCLMSHRLifetimeProfileStats summarizes completed PTCL-granularity MSHRs.
// Lifetime is measured from entry allocation until the entry becomes ready
// and is removed. Arrival span covers the first through last distinct demand.
type PTCLMSHRLifetimeProfileStats struct {
	Enabled                bool
	CompletedEntries       uint64
	LifetimeCycles         uint64
	MinLifetimeCycles      uint64
	MaxLifetimeCycles      uint64
	ArrivalSpanCycles      uint64
	MaxArrivalSpanCycles   uint64
	DemandPTEs             uint64
	DemandHistogram        [9]uint64
	LifetimeHistogram      [9]uint64
	LifetimeCyclesByDemand [9]uint64
}

func (tlb *GMMUTLB) PTCLMSHRLifetimeStats() PTCLMSHRLifetimeProfileStats {
	return PTCLMSHRLifetimeProfileStats{
		Enabled:                tlb.ptclMSHRLifetimeProfileEnabled,
		CompletedEntries:       tlb.ptclMSHRLifetimeEntries,
		LifetimeCycles:         tlb.ptclMSHRLifetimeCycles,
		MinLifetimeCycles:      tlb.ptclMSHRLifetimeMinCycles,
		MaxLifetimeCycles:      tlb.ptclMSHRLifetimeMaxCycles,
		ArrivalSpanCycles:      tlb.ptclMSHRArrivalSpanCycles,
		MaxArrivalSpanCycles:   tlb.ptclMSHRArrivalSpanMaxCycles,
		DemandPTEs:             tlb.ptclMSHRDemandPTEs,
		DemandHistogram:        tlb.ptclMSHRDemandHistogram,
		LifetimeHistogram:      tlb.ptclMSHRLifetimeHistogram,
		LifetimeCyclesByDemand: tlb.ptclMSHRLifetimeByDemand,
	}
}

func (tlb *GMMUTLB) resetPTCLMSHRLifetimeStats() {
	tlb.ptclMSHRLifetimeEntries = 0
	tlb.ptclMSHRLifetimeCycles = 0
	tlb.ptclMSHRLifetimeMinCycles = 0
	tlb.ptclMSHRLifetimeMaxCycles = 0
	tlb.ptclMSHRArrivalSpanCycles = 0
	tlb.ptclMSHRArrivalSpanMaxCycles = 0
	tlb.ptclMSHRDemandPTEs = 0
	tlb.ptclMSHRDemandHistogram = [9]uint64{}
	tlb.ptclMSHRLifetimeHistogram = [9]uint64{}
	tlb.ptclMSHRLifetimeByDemand = [9]uint64{}
}

func (tlb *GMMUTLB) resetFlexStats() {
	tlb.flexLookupJobs = 0
	tlb.flexLookupRequestedBits = 0
	tlb.flexLookupHitBits = 0
	tlb.flexLookupMissBits = 0
	tlb.flexLookupSavedJobs = 0
	tlb.flexPTEPackHits = 0
	tlb.flexPTCLLineHits = 0
	tlb.flexPartialPTCLHits = 0
	tlb.flexFullPTCLHits = 0
	tlb.flexPromotions = 0
	tlb.flexDemotions = 0
	tlb.flexInvalidatedPTEPackSlots = 0
	tlb.flexEvictedValidSlotsForPTCL = 0
	tlb.ptclSetModeFlushes = 0
	tlb.pcdFallbackLookupBits = 0
	tlb.pcdStaleBits = 0
}

func (tlb *GMMUTLB) FlexTLBStats() (
	enabled bool,
	promotionThreshold int,
	ptePackEntries int,
	ptclLineEntries int,
	lookupJobs int,
	requestedBits int,
	hitBits int,
	missBits int,
	savedJobs int,
	ptePackHits int,
	ptclLineHits int,
	partialPTCLHits int,
	fullPTCLHits int,
	promotions int,
	demotions int,
	invalidatedPTEPackSlots int,
	evictedValidSlotsForPTCL int,
	flexSets int,
	flexWays int,
	flexPTESlots int,
) {
	if tlb.flexTLBEnabled {
		flexPTESlots = tlb.numSets * tlb.numWays
		for _, set := range tlb.Sets {
			ptclSet, ok := set.(internal.PTCLSet)
			if !ok {
				continue
			}
			ptePackEntries += ptclSet.ValidPageCount()
		}
		if tlb.pcd != nil {
			flexSets = tlb.pcd.numSets
			flexWays = tlb.pcd.numWays
			ptclLineEntries = tlb.pcd.validEntryCount()
		}
	}

	return tlb.flexTLBEnabled,
		tlb.flexPromotionThreshold,
		ptePackEntries,
		ptclLineEntries,
		tlb.flexLookupJobs,
		tlb.flexLookupRequestedBits,
		tlb.flexLookupHitBits,
		tlb.flexLookupMissBits,
		tlb.flexLookupSavedJobs,
		tlb.flexPTEPackHits,
		tlb.flexPTCLLineHits,
		tlb.flexPartialPTCLHits,
		tlb.flexFullPTCLHits,
		tlb.flexPromotions,
		tlb.flexDemotions,
		tlb.flexInvalidatedPTEPackSlots,
		tlb.flexEvictedValidSlotsForPTCL,
		flexSets,
		flexWays,
		flexPTESlots
}
