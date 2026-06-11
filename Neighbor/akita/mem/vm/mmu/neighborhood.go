package mmu

import (
	"github.com/sarchlab/akita/v3/mem/vm"
	"github.com/sarchlab/akita/v3/sim"
	"github.com/sarchlab/akita/v3/tracing"
)

const (
	leafPageTableLevel  = 1
	ptePerCacheLine     = 8
	pageTableFanoutBits = 9
)

// NeighborhoodCoalescingStats exposes counters for the [35]-style IOMMU
// page-walk backend coalescing implementation.
type NeighborhoodCoalescingStats struct {
	Enabled                bool
	LeafCoalescedWalks     uint64
	UpperLevelCoalesces    uint64
	PageTableAccessesSaved uint64
	FullPageWalksAvoided   uint64
	CoalescingLockBlocks   uint64
	WalkerStarts           uint64
	MaxPWQueueOccupancy    uint64
}

func (mmu *MMU) NeighborhoodCoalescingStats() NeighborhoodCoalescingStats {
	return NeighborhoodCoalescingStats{
		Enabled:                mmu.neighborhoodCoalescingEnabled,
		LeafCoalescedWalks:     mmu.leafCoalescedWalks,
		UpperLevelCoalesces:    mmu.upperLevelCoalesces,
		PageTableAccessesSaved: mmu.pageTableAccessesSaved,
		FullPageWalksAvoided:   mmu.fullPageWalksAvoided,
		CoalescingLockBlocks:   mmu.coalescingLockBlocks,
		WalkerStarts:           mmu.walkerStarts,
		MaxPWQueueOccupancy:    mmu.maxPWQueueOccupancy,
	}
}

func (mmu *MMU) updateMaxPWQueueOccupancy() {
	if uint64(len(mmu.PWqueue)) > mmu.maxPWQueueOccupancy {
		mmu.maxPWQueueOccupancy = uint64(len(mmu.PWqueue))
	}
}

func (mmu *MMU) pickNextWalkRequestIndex() int {
	if len(mmu.PWqueue) == 0 {
		return -1
	}

	if !mmu.neighborhoodCoalescingEnabled {
		return 0
	}

	mmu.recomputeCoalescingLocks()
	for i := range mmu.PWqueue {
		if mmu.PWqueue[i].CoalescingLocked {
			mmu.coalescingLockBlocks++
			continue
		}

		return i
	}

	return -1
}

func (mmu *MMU) recomputeCoalescingLocks() {
	for i := range mmu.PWqueue {
		mmu.PWqueue[i].HasResumePoint = false
		mmu.PWqueue[i].ResumeLevel = 0
		mmu.PWqueue[i].ResumeNodePA = 0
		mmu.PWqueue[i].CoalescingLocked = false
		mmu.PWqueue[i].coalescingCandidate = false
	}

	activeWalks := mmu.activePageWalkServiceEntries()
	if len(activeWalks) == 0 {
		return
	}

	for i := range mmu.PWqueue {
		req := mmu.PWqueue[i].req
		if req == nil {
			continue
		}

		for _, walk := range activeWalks {
			if !walk.Active || req.PID != walk.PID {
				continue
			}

			if !sameNeighborhood(
				mmu.vpnOf(req.VAddr),
				walk.VPN,
				walk.CurrentLevel,
			) {
				continue
			}

			mmu.PWqueue[i].HasResumePoint = true
			mmu.PWqueue[i].ResumeLevel = walk.CurrentLevel
			mmu.PWqueue[i].ResumeNodePA = walk.NodePA
			mmu.PWqueue[i].CoalescingLocked = true
			mmu.PWqueue[i].coalescingCandidate = true
			break
		}
	}
}

func (mmu *MMU) activePageWalkServiceEntries() []pageWalkServiceEntry {
	entries := make([]pageWalkServiceEntry, 0, len(mmu.walkingTranslations))
	for i, walking := range mmu.walkingTranslations {
		if walking.req == nil || mmu.toRemove(i) {
			continue
		}

		vpn := mmu.vpnOf(walking.req.VAddr)
		entries = append(entries, pageWalkServiceEntry{
			Active:       true,
			WalkerID:     i,
			RequestID:    walking.req.ID,
			PID:          walking.req.PID,
			VPN:          vpn,
			CurrentLevel: leafPageTableLevel,
			NodePA:       leafNeighborhoodBase(vpn),
		})
	}

	return entries
}

func (mmu *MMU) coalescePendingRequests(
	now sim.VTimeInSec,
	walking transaction,
) bool {
	if !mmu.neighborhoodCoalescingEnabled || walking.req == nil {
		return false
	}

	madeProgress := false
	newQueue := make([]PWqueue, 0, len(mmu.PWqueue))
	for _, pending := range mmu.PWqueue {
		req := pending.req
		if !mmu.sameLeafNeighborhood(walking.req, req) {
			newQueue = append(newQueue, pending)
			continue
		}

		page, ok := mmu.pageReadyForCoalescedResponse(req)
		if !ok {
			pending.CoalescingLocked = false
			pending.coalescingCandidate = false
			newQueue = append(newQueue, pending)
			continue
		}

		if !mmu.sendCoalescedTranslationRsp(now, req, page) {
			pending.CoalescingLocked = false
			pending.coalescingCandidate = false
			newQueue = append(newQueue, pending)
			continue
		}

		mmu.leafCoalescedWalks++
		mmu.pageTableAccessesSaved++
		mmu.fullPageWalksAvoided++
		tracing.TraceReqComplete(req, mmu)
		madeProgress = true
	}

	mmu.PWqueue = newQueue
	return madeProgress
}

func (mmu *MMU) pageReadyForCoalescedResponse(
	req *vm.TranslationReq,
) (vm.Page, bool) {
	if req == nil {
		return vm.Page{}, false
	}

	page, found := mmu.pageTable.Find(req.PID, req.VAddr)
	if !found {
		return vm.Page{}, false
	}

	if page.IsMigrating {
		return vm.Page{}, false
	}

	if mmu.pageNeedMigrate(transaction{req: req, page: page}) {
		return vm.Page{}, false
	}

	return page, true
}

func (mmu *MMU) sendCoalescedTranslationRsp(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	page vm.Page,
) bool {
	if req == nil || !mmu.topSender.CanSend(1) {
		return false
	}

	rsp := vm.TranslationRspBuilder{}.
		WithSendTime(now).
		WithSrc(mmu.topPort).
		WithDst(req.Src).
		WithRspTo(req.ID).
		WithPage(page).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		Build()

	mmu.topSender.Send(rsp)
	return true
}

func (mmu *MMU) sameLeafNeighborhood(
	a *vm.TranslationReq,
	b *vm.TranslationReq,
) bool {
	if a == nil || b == nil || a.PID != b.PID {
		return false
	}

	return sameNeighborhood(
		mmu.vpnOf(a.VAddr),
		mmu.vpnOf(b.VAddr),
		leafPageTableLevel,
	)
}

func (mmu *MMU) vpnOf(vAddr uint64) uint64 {
	return vAddr >> mmu.log2PageSize
}

func sameNeighborhood(vpnA, vpnB uint64, level int) bool {
	if level < leafPageTableLevel {
		level = leafPageTableLevel
	}

	shift := uint((level - 1) * pageTableFanoutBits)
	indexA := (vpnA >> shift) & 0x1ff
	indexB := (vpnB >> shift) & 0x1ff
	parentA := vpnA >> (shift + pageTableFanoutBits)
	parentB := vpnB >> (shift + pageTableFanoutBits)

	return parentA == parentB &&
		indexA/ptePerCacheLine == indexB/ptePerCacheLine
}

func leafNeighborhoodBase(vpn uint64) uint64 {
	return (vpn / ptePerCacheLine) * ptePerCacheLine
}
