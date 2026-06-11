package tlb_gmmu

import (
	"log"
	"reflect"

	"github.com/sarchlab/akita/v3/mem/mem"
	"github.com/sarchlab/akita/v3/mem/vm"
	"github.com/sarchlab/akita/v3/mem/vm/tlb_gmmu/internal"
	"github.com/sarchlab/akita/v3/sim"
	"github.com/sarchlab/akita/v3/tracing"
)

type TimeConsumption struct {
	StartTime sim.VTimeInSec
	EndTime   sim.VTimeInSec
}

// A TLB is a cache that maintains some page information.
type GMMUTLB struct {
	*sim.TickingComponent

	topPort     sim.Port
	bottomPort  sim.Port
	OutsidePort sim.Port
	controlPort sim.Port
	IOMMUPort   sim.Port

	LowModule sim.Port

	numSets        int
	numWays        int
	pageSize       uint64
	numReqPerCycle int
	TopPortBuffer  []sim.Msg

	Sets []internal.Set

	mshr                   mshr
	respondingMSHREntry    []*mshrEntry
	log2Pagesize           uint64
	vpnMSHRBaseline        bool
	ptclMode               bool
	coalescingCounter      int
	ptclHighThreshold      int
	ptclLowThreshold       int
	switchToPTCLCount      int
	switchToPTECount       int
	downstreamReqCount     int
	localReqCount          int
	iommuReqCount          int
	pteLookupLatencyCycles int
	pteLookupWaitingQueue  []pteLookupJob
	pteLookupInflight      []pteLookupJob
	pteLookupGroups        map[pteLookupGroupKey]*pteLookupGroup
	pteLookupReadyToIssue  []pteLookupGroupKey
	ptclRepresentativeMiss map[pteLookupGroupKey][8]bool
	pteLookupDelayCount    int
	pteLookupDelayCycles   int
	pteLookupMaxInflight   int
	pteLookupMaxWaiting    int
	prefetcher             *translationPrefetcher
	inflightPrefetches     map[prefetchTargetKey]struct{}
	prefetchReqStates      map[string]*prefetchReqState
	prefetchOutcomeByBlock map[uint64]*prefetchOutcomeCounts
	prefetchCompletedCount int

	isPaused       bool
	DeviceID       uint64
	pageTable      vm.PageTable
	PageFinder     mem.PageFinder
	gmmuCacheTable *mem.MultiPageFinder

	TimeConsumption map[uint64]TimeConsumption
}

type pteLookupGroupKey struct {
	pid       vm.PID
	baseVAddr uint64
}

type pteLookupGroup struct {
	key               pteLookupGroupKey
	req               *vm.TranslationReq
	ptclLookup        bool
	lookupBitmap      [8]bool
	missBitmap        [8]bool
	representedBitmap [8]bool
	remainingJobs     int
}

type pteLookupJob struct {
	groupKey  pteLookupGroupKey
	req       *vm.TranslationReq
	vAddr     uint64
	bit       int
	readyTime sim.VTimeInSec
}

// Reset sets all the entries int he TLB to be invalid
func (tlb *GMMUTLB) reset() {
	tlb.Sets = make([]internal.Set, tlb.numSets)
	for i := 0; i < tlb.numSets; i++ {
		set := internal.NewSet(tlb.numWays)
		tlb.Sets[i] = set
	}

	tlb.clearPTELookups()
	tlb.pteLookupDelayCount = 0
	tlb.pteLookupDelayCycles = 0
	tlb.pteLookupMaxInflight = 0
	tlb.pteLookupMaxWaiting = 0
	tlb.resetPrefetchState()
}

// Tick defines how TLB update states at each cycle
func (tlb *GMMUTLB) Tick(now sim.VTimeInSec) bool {
	madeProgress := false

	madeProgress = tlb.performCtrlReq(now) || madeProgress

	if !tlb.isPaused {
		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.advancePTELookups(now) || madeProgress
		}
		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.parseBottom(now) || madeProgress
		}
		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.respondMSHREntry(now) || madeProgress
		}
		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.lookupFromTopPort(now) || madeProgress
			madeProgress = tlb.lookupFromOutsidePort(now) || madeProgress
		}
		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.advancePTELookups(now) || madeProgress
		}
	}

	return madeProgress
}

func (tlb *GMMUTLB) respondMSHREntry(now sim.VTimeInSec) bool {
	if len(tlb.respondingMSHREntry) == 0 {
		return false
	}

	mshrEntry := tlb.respondingMSHREntry[0]
	req := mshrEntry.Requests[0]

	pages := mshrEntry.Pages

	sentRsp := false
	for i := 0; i < 8; i++ {
		page := pages[i]
		if page.Valid {
			if page.PID == req.PID && page.VAddr == req.VAddr {
				rspToTop := vm.TranslationRspBuilder{}.
					WithSendTime(now).
					WithSrc(tlb.topPort).
					WithDst(req.Src).
					WithRspTo(req.ID).
					WithPage(page).
					WithTaskID(req.TaskID).
					WithOriginPort(req.OriginPort).
					Build()

				err := tlb.topPort.Send(rspToTop)
				if err != nil {
					return false
				}

				sentRsp = true
				break
			}
		}
	}

	if !sentRsp {
		return false
	}

	mshrEntry.Requests = mshrEntry.Requests[1:]
	if len(mshrEntry.Requests) == 0 {
		tlb.respondingMSHREntry = tlb.respondingMSHREntry[1:]
	}

	tracing.TraceReqComplete(req, tlb)
	return true
}

func (tlb *GMMUTLB) lookupFromTopPort(now sim.VTimeInSec) bool {
	msg := tlb.topPort.Peek()
	if msg == nil {
		return false
	}

	req := msg.(*vm.TranslationReq)

	return tlb.processTranslation(now, req)
}

func (tlb *GMMUTLB) lookupFromOutsidePort(now sim.VTimeInSec) bool {
	return tlb.processRspFromPort(now, tlb.OutsidePort, false)
}

func (tlb *GMMUTLB) handleTranslationHit(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	setID, wayID int,
	page vm.Page,
) bool {
	ok := tlb.sendRspToTop(now, req, page)
	if !ok {
		return false
	}
	tlb.topPort.Retrieve(now)

	tlb.visit(setID, wayID)

	tracing.TraceReqReceive(req, tlb)
	tracing.AddTaskStep(tracing.MsgIDAtReceiver(req, tlb), tlb, "hit")
	tracing.TraceReqComplete(req, tlb)
	tracing.StartTask(req.TaskID,
		tracing.MsgIDAtReceiver(req, tlb),
		tlb, "EvictTest", "*vm.TranslationReq", req)

	return true
}

func (tlb *GMMUTLB) handleTranslationMiss(
	now sim.VTimeInSec,
	mshrReq *vm.TranslationReq,
) bool {
	if tlb.mshr.IsFull() {
		return false
	}

	lookupBitmap := tlb.lookupBitmapForReq(mshrReq)
	if tlb.isBitmapZero(lookupBitmap) {
		return false
	}

	mshrEntry := tlb.mshr.Add(mshrReq.PID, mshrReq.VAddr, now, 0)
	mshrEntry.Requests = append(mshrEntry.Requests, mshrReq)
	tlb.enqueuePTELookupJobsWithBitmap(now, mshrReq, mshrEntry, lookupBitmap)
	tlb.maybeEnqueuePrefetches(now, mshrReq)

	tlb.topPort.Retrieve(now)

	tracing.TraceReqReceive(mshrReq, tlb)
	tracing.AddTaskStep(tracing.MsgIDAtReceiver(mshrReq, tlb), tlb, "miss")
	tracing.StartTask(mshrReq.TaskID,
		tracing.MsgIDAtReceiver(mshrReq, tlb),
		tlb, "EvictTest", "*vm.TranslationReq", mshrReq)

	return true
}

func (tlb *GMMUTLB) vAddrToSetID(vAddr uint64) (setID int) {
	return int(vAddr / tlb.pageSize % uint64(tlb.numSets))
}

func (tlb *GMMUTLB) sendRspToTop(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	page vm.Page,
) bool {
	rsp := vm.TranslationRspBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.topPort).
		WithDst(req.Src).
		WithRspTo(req.ID).
		WithPage(page).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		Build()

	err := tlb.topPort.Send(rsp)
	return err == nil
}

func (tlb *GMMUTLB) processTLBMSHRHit(
	now sim.VTimeInSec,
	mshrEntry *mshrEntry,
	mshrReq *vm.TranslationReq,
) bool {
	if tlb.mshr.IsEntryFull(mshrReq.PID, mshrReq.VAddr) {
		return false
	}

	tlb.mshr.UpdateUpLevelBitMap(mshrReq.PID, mshrReq.VAddr, now, 0)
	mshrEntry.Requests = append(mshrEntry.Requests, mshrReq)
	tlb.enqueuePTELookupJobs(now, mshrReq, mshrEntry)
	tlb.maybeEnqueuePrefetches(now, mshrReq)

	tlb.topPort.Retrieve(now)

	tracing.TraceReqReceive(mshrReq, tlb)
	tracing.AddTaskStep(tracing.MsgIDAtReceiver(mshrReq, tlb), tlb, "mshr-hit")
	tracing.StartTask(mshrReq.TaskID,
		tracing.MsgIDAtReceiver(mshrReq, tlb),
		tlb, "EvictTest", "*vm.TranslationReq", mshrReq)

	tlb.scheduleReadyMSHREntry(now, mshrEntry, false)
	return true
}

func (tlb *GMMUTLB) fetchBottom(now sim.VTimeInSec, mshrReq *vm.TranslationReq) bool {
	requestedBitmap := tlb.normalizeBitmap(mshrReq)
	issuedBitmap := requestedBitmap
	if tlb.ptclMode && !tlb.vpnMSHRBaseline {
		issuedBitmap = tlb.fullBitmap()
	}
	issuedBitmap = tlb.filterMappedBitmap(mshrReq.PID, mshrReq.VAddr, issuedBitmap)
	if tlb.isBitmapZero(issuedBitmap) {
		return false
	}

	reqToBottom, ok := tlb.sendDownstream(now, mshrReq, issuedBitmap)
	if !ok {
		return false
	}
	if reqToBottom == nil {
		return false
	}

	mshrEntry := tlb.mshr.Add(mshrReq.PID, mshrReq.VAddr, now, 0)
	mshrEntry.Requests = append(mshrEntry.Requests, mshrReq)
	mshrEntry.reqToBottom = reqToBottom
	mshrEntry.IssuedBitMap = tlb.mergeBitmaps(mshrEntry.IssuedBitMap, issuedBitmap)

	tlb.topPort.Retrieve(now)

	tracing.TraceReqInitiate(reqToBottom, tlb,
		tracing.MsgIDAtReceiver(mshrReq, tlb))

	return true
}

func (tlb *GMMUTLB) clearPTELookups() {
	tlb.pteLookupWaitingQueue = nil
	tlb.pteLookupInflight = nil
	tlb.pteLookupReadyToIssue = nil
	if tlb.pteLookupGroups == nil {
		tlb.pteLookupGroups = make(map[pteLookupGroupKey]*pteLookupGroup)
	} else {
		clear(tlb.pteLookupGroups)
	}
	if tlb.ptclRepresentativeMiss == nil {
		tlb.ptclRepresentativeMiss = make(map[pteLookupGroupKey][8]bool)
	} else {
		clear(tlb.ptclRepresentativeMiss)
	}
}

func (tlb *GMMUTLB) lookupBitmapForReq(req *vm.TranslationReq) [8]bool {
	requestedBitmap := tlb.normalizeBitmap(req)
	lookupBitmap := requestedBitmap
	if tlb.ptclMode && !tlb.vpnMSHRBaseline {
		lookupBitmap = tlb.fullBitmap()
	}

	return tlb.filterMappedBitmap(req.PID, req.VAddr, lookupBitmap)
}

func (tlb *GMMUTLB) enqueuePTELookupJobs(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	entry *mshrEntry,
) {
	tlb.enqueuePTELookupJobsWithBitmap(now, req, entry, tlb.lookupBitmapForReq(req))
}

func (tlb *GMMUTLB) enqueuePTELookupJobsWithBitmap(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	entry *mshrEntry,
	lookupBitmap [8]bool,
) {
	toLookup := tlb.subtractBitmaps(lookupBitmap, entry.IssuedBitMap)
	if tlb.isBitmapZero(toLookup) {
		return
	}

	ptclLookup := tlb.ptclMode && !tlb.vpnMSHRBaseline
	key := tlb.pteLookupGroupKey(req.PID, req.VAddr)
	group := tlb.pteLookupGroups[key]
	if group != nil {
		group.ptclLookup = group.ptclLookup || ptclLookup
	} else {
		group = &pteLookupGroup{
			key:        key,
			req:        req,
			ptclLookup: ptclLookup,
		}
		tlb.pteLookupGroups[key] = group
	}

	baseVAddr := tlb.getBaseVaddr(req.VAddr)
	for i := 0; i < 8; i++ {
		if !toLookup[i] {
			continue
		}

		tlb.pteLookupWaitingQueue = append(tlb.pteLookupWaitingQueue, pteLookupJob{
			groupKey: key,
			req:      req,
			vAddr:    baseVAddr + (uint64(i) << tlb.log2Pagesize),
			bit:      i,
		})
		group.remainingJobs++
	}

	entry.IssuedBitMap = tlb.mergeBitmaps(entry.IssuedBitMap, toLookup)
	group.lookupBitmap = tlb.mergeBitmaps(group.lookupBitmap, toLookup)

	lookupBits := tlb.bitmapCount(toLookup)
	tlb.pteLookupDelayCount += lookupBits
	tlb.pteLookupDelayCycles += lookupBits * tlb.pteLookupLatencyCycles
	if len(tlb.pteLookupWaitingQueue) > tlb.pteLookupMaxWaiting {
		tlb.pteLookupMaxWaiting = len(tlb.pteLookupWaitingQueue)
	}
}

func (tlb *GMMUTLB) pteLookupGroupKey(pid vm.PID, vAddr uint64) pteLookupGroupKey {
	baseVAddr := tlb.getBaseVaddr(vAddr)
	if tlb.vpnMSHRBaseline {
		baseVAddr = (vAddr >> tlb.log2Pagesize) << tlb.log2Pagesize
	}

	return pteLookupGroupKey{
		pid:       pid,
		baseVAddr: baseVAddr,
	}
}

func (tlb *GMMUTLB) advancePTELookups(now sim.VTimeInSec) bool {
	if tlb.issueReadyPTELookupGroup(now) {
		return true
	}

	for i, job := range tlb.pteLookupInflight {
		if job.readyTime > now {
			continue
		}

		tlb.pteLookupInflight = append(
			tlb.pteLookupInflight[:i],
			tlb.pteLookupInflight[i+1:]...,
		)
		return tlb.processReadyPTELookupJob(now, job)
	}

	if len(tlb.pteLookupWaitingQueue) > 0 &&
		len(tlb.pteLookupInflight) < tlb.numReqPerCycle {
		job := tlb.pteLookupWaitingQueue[0]
		tlb.pteLookupWaitingQueue = tlb.pteLookupWaitingQueue[1:]
		if tlb.pteLookupLatencyCycles > 0 {
			job.readyTime = tlb.Freq.NCyclesLater(tlb.pteLookupLatencyCycles, now)
		} else {
			job.readyTime = now
		}
		tlb.pteLookupInflight = append(tlb.pteLookupInflight, job)
		if len(tlb.pteLookupInflight) > tlb.pteLookupMaxInflight {
			tlb.pteLookupMaxInflight = len(tlb.pteLookupInflight)
		}
		return true
	}

	tlb.tickAtNextPTELookupReadyTime(now)
	return false
}

func (tlb *GMMUTLB) processReadyPTELookupJob(
	now sim.VTimeInSec,
	job pteLookupJob,
) bool {
	group := tlb.pteLookupGroups[job.groupKey]

	setID := tlb.vAddrToSetID(job.vAddr)
	set := tlb.Sets[setID]
	wayID, page, found := set.Lookup(job.req.PID, job.vAddr)
	if found && page.Valid {
		tlb.visit(setID, wayID)
		if mshrEntry := tlb.mshr.GetEntry(job.req.PID, job.vAddr); mshrEntry != nil {
			tlb.mshr.UpdatePage(page.PID, page.VAddr, page)
			tlb.mshr.UpdateResponseBitMap(page.PID, page.VAddr)
			tlb.scheduleReadyMSHREntry(now, mshrEntry, false)
		}
	} else if group != nil {
		group.missBitmap[job.bit] = true
	}

	if group != nil {
		group.remainingJobs--
		if group.remainingJobs == 0 {
			tlb.finishPTELookupGroup(group)
		}
	}

	return true
}

func (tlb *GMMUTLB) finishPTELookupGroup(group *pteLookupGroup) {
	if tlb.isBitmapZero(group.missBitmap) {
		delete(tlb.pteLookupGroups, group.key)
		return
	}

	tlb.pteLookupReadyToIssue = append(tlb.pteLookupReadyToIssue, group.key)
}

func (tlb *GMMUTLB) issueReadyPTELookupGroup(now sim.VTimeInSec) bool {
	if len(tlb.pteLookupReadyToIssue) == 0 {
		return false
	}

	key := tlb.pteLookupReadyToIssue[0]
	group := tlb.pteLookupGroups[key]
	if group == nil {
		tlb.pteLookupReadyToIssue = tlb.pteLookupReadyToIssue[1:]
		return true
	}

	downstreamBitmap := tlb.downstreamBitmapForLookupGroup(group)
	if tlb.isBitmapZero(downstreamBitmap) {
		tlb.updateIssuedBitmapAfterLookupGroup(group, group.representedBitmap)
		tlb.pteLookupReadyToIssue = tlb.pteLookupReadyToIssue[1:]
		delete(tlb.pteLookupGroups, key)
		return true
	}

	reqToBottom, ok := tlb.sendDownstream(now, group.req, downstreamBitmap)
	if !ok {
		return false
	}

	if mshrEntry := tlb.mshr.GetEntry(group.req.PID, group.req.VAddr); mshrEntry != nil {
		mshrEntry.reqToBottom = reqToBottom
	}
	representedBitmap := downstreamBitmap
	if group.ptclLookup {
		representedBitmap = tlb.mergeBitmaps(group.missBitmap, downstreamBitmap)
		tlb.registerPTCLRepresentativeMiss(group, representedBitmap)
	}
	group.representedBitmap = tlb.mergeBitmaps(group.representedBitmap, representedBitmap)
	if reqToBottom != nil {
		tracing.TraceReqInitiate(reqToBottom, tlb,
			tracing.MsgIDAtReceiver(group.req, tlb))
	}

	tlb.updateIssuedBitmapAfterLookupGroup(group, group.representedBitmap)
	tlb.pteLookupReadyToIssue = tlb.pteLookupReadyToIssue[1:]
	delete(tlb.pteLookupGroups, key)
	return true
}

func (tlb *GMMUTLB) downstreamBitmapForLookupGroup(
	group *pteLookupGroup,
) [8]bool {
	if group == nil {
		return [8]bool{}
	}

	if !group.ptclLookup {
		return group.missBitmap
	}

	return tlb.ptclRepresentativeBitmap(group)
}

func (tlb *GMMUTLB) ptclRepresentativeBitmap(
	group *pteLookupGroup,
) [8]bool {
	if group == nil || tlb.isBitmapZero(group.missBitmap) {
		return [8]bool{}
	}

	bitmap := [8]bool{}
	bitmap[0] = true
	bitmap = tlb.filterMappedBitmap(group.req.PID, group.key.baseVAddr, bitmap)
	if !tlb.isBitmapZero(bitmap) {
		return bitmap
	}

	return tlb.firstBitBitmap(group.missBitmap)
}

func (tlb *GMMUTLB) registerPTCLRepresentativeMiss(
	group *pteLookupGroup,
	representedBitmap [8]bool,
) {
	if group == nil || tlb.isBitmapZero(representedBitmap) {
		return
	}

	if tlb.ptclRepresentativeMiss == nil {
		tlb.ptclRepresentativeMiss = make(map[pteLookupGroupKey][8]bool)
	}

	existing := tlb.ptclRepresentativeMiss[group.key]
	tlb.ptclRepresentativeMiss[group.key] =
		tlb.mergeBitmaps(existing, representedBitmap)
}

func (tlb *GMMUTLB) updateIssuedBitmapAfterLookupGroup(
	group *pteLookupGroup,
	downstreamBitmap [8]bool,
) {
	if group == nil || !group.ptclLookup {
		return
	}

	entry := tlb.mshr.GetEntry(group.req.PID, group.req.VAddr)
	if entry == nil {
		return
	}

	outsideGroup := tlb.subtractBitmaps(entry.IssuedBitMap, group.lookupBitmap)
	servedBitmap := tlb.mergeBitmaps(entry.ResponseBitMap, downstreamBitmap)
	entry.IssuedBitMap = tlb.mergeBitmaps(outsideGroup, servedBitmap)
}

func (tlb *GMMUTLB) tickAtNextPTELookupReadyTime(now sim.VTimeInSec) {
	if len(tlb.pteLookupInflight) == 0 {
		return
	}

	next := tlb.pteLookupInflight[0].readyTime
	for _, job := range tlb.pteLookupInflight[1:] {
		if job.readyTime < next {
			next = job.readyTime
		}
	}
	if next > now {
		tlb.TickNow(next)
	}
}

func (tlb *GMMUTLB) parseBottom(now sim.VTimeInSec) bool {
	if len(tlb.respondingMSHREntry) != 0 {
		return false
	}

	return tlb.processRspFromPort(now, tlb.bottomPort, true)
}

func (tlb *GMMUTLB) processRspFromPort(
	now sim.VTimeInSec,
	port sim.Port,
	bottom bool,
) bool {
	msg := port.Peek()
	if msg == nil {
		return false
	}

	switch msg := msg.(type) {
	case *vm.TranslationRsp:
		return tlb.processRsp(now, msg, bottom)
	default:
		panic("unexpected message type")
	}
}

func (tlb *GMMUTLB) performCtrlReq(now sim.VTimeInSec) bool {
	item := tlb.controlPort.Peek()
	if item == nil {
		return false
	}

	item = tlb.controlPort.Retrieve(now)

	switch req := item.(type) {
	case *FlushReq:
		return tlb.handleTLBFlush(now, req)
	case *RestartReq:
		return tlb.handleTLBRestart(now, req)
	default:
		log.Panicf("cannot process request %s", reflect.TypeOf(req))
	}

	return true
}

func (tlb *GMMUTLB) visit(setID, wayID int) {
	set := tlb.Sets[setID]
	set.Visit(wayID)
}

func (tlb *GMMUTLB) handleTLBFlush(now sim.VTimeInSec, req *FlushReq) bool {
	rsp := FlushRspBuilder{}.
		WithSrc(tlb.controlPort).
		WithDst(req.Src).
		WithSendTime(now).
		Build()

	err := tlb.controlPort.Send(rsp)
	if err != nil {
		return false
	}

	for _, vAddr := range req.VAddr {
		setID := tlb.vAddrToSetID(vAddr)
		set := tlb.Sets[setID]
		wayID, page, found := set.Lookup(req.PID, vAddr)
		if !found {
			continue
		}

		page.Valid = false
		set.Update(wayID, page)
	}

	tlb.mshr.Reset()
	tlb.clearPTELookups()
	tlb.isPaused = true
	return true
}

func (tlb *GMMUTLB) handleTLBRestart(now sim.VTimeInSec, req *RestartReq) bool {
	rsp := RestartRspBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.controlPort).
		WithDst(req.Src).
		Build()

	err := tlb.controlPort.Send(rsp)
	if err != nil {
		return false
	}

	tlb.isPaused = false

	for tlb.topPort.Retrieve(now) != nil {
		tlb.topPort.Retrieve(now)
	}

	for tlb.bottomPort.Retrieve(now) != nil {
		tlb.bottomPort.Retrieve(now)
	}

	return true
}

func (tlb *GMMUTLB) processTranslation(now sim.VTimeInSec, req *vm.TranslationReq) bool {
	req.BitMap = tlb.normalizeBitmap(req)

	mshrEntry := tlb.mshr.GetEntry(req.PID, req.VAddr)

	if mshrEntry != nil {
		return tlb.processTLBMSHRHit(now, mshrEntry, req)
	}

	return tlb.handleTranslationMiss(now, req)
}

func (tlb *GMMUTLB) processRsp(
	now sim.VTimeInSec,
	rsp *vm.TranslationRsp,
	bottom bool,
) bool {
	page := rsp.Page

	if rsp.IsPrefetch {
		if !tlb.handlePrefetchRsp(now, rsp) {
			return false
		}
		tlb.retrieveRsp(now, bottom)
		return true
	}

	if tlb.completePTCLRepresentativeRsp(now, page) {
		tlb.retrieveRsp(now, bottom)
		return true
	}

	// fmt.Printf("Received from %s VAddr %d\n", rsp.Src.Name(), page.VAddr)

	mshrEntryPresent := tlb.mshr.IsEntryPresent(page.PID, page.VAddr)
	tlb.installPage(page)

	if !mshrEntryPresent {
		tlb.retrieveRsp(now, bottom)
		return true
	}

	tlb.mshr.UpdatePage(page.PID, page.VAddr, page)

	mshrEntry := tlb.mshr.GetEntry(page.PID, page.VAddr)
	if mshrEntry == nil {
		tlb.retrieveRsp(now, bottom)
		return true
	}

	tlb.mshr.UpdateResponseBitMap(page.PID, page.VAddr)

	tlb.scheduleReadyMSHREntry(now, mshrEntry, true)

	tlb.retrieveRsp(now, bottom)
	return true
}

func (tlb *GMMUTLB) completePTCLRepresentativeRsp(
	now sim.VTimeInSec,
	representativePage vm.Page,
) bool {
	if tlb.ptclRepresentativeMiss == nil {
		return false
	}

	key := tlb.pteLookupGroupKey(representativePage.PID, representativePage.VAddr)
	representedBitmap, found := tlb.ptclRepresentativeMiss[key]
	if !found {
		return false
	}

	delete(tlb.ptclRepresentativeMiss, key)
	representedBitmap = tlb.mergeBitmaps(
		representedBitmap,
		tlb.singlePageBitmap(representativePage.VAddr),
	)

	var mshrEntry *mshrEntry
	for i := 0; i < 8; i++ {
		if !representedBitmap[i] {
			continue
		}

		pageVAddr := key.baseVAddr + (uint64(i) << tlb.log2Pagesize)
		page := representativePage
		if representativePage.VAddr != pageVAddr {
			var pageFound bool
			page, pageFound = tlb.pageTable.Find(representativePage.PID, pageVAddr)
			if !pageFound {
				continue
			}
		}

		tlb.installPage(page)

		if entry := tlb.mshr.GetEntry(page.PID, page.VAddr); entry != nil {
			mshrEntry = entry
			tlb.mshr.UpdatePage(page.PID, page.VAddr, page)
			tlb.mshr.UpdateResponseBitMap(page.PID, page.VAddr)
		}
	}

	tlb.scheduleReadyMSHREntry(now, mshrEntry, true)
	return true
}

func (tlb *GMMUTLB) scheduleReadyMSHREntry(
	now sim.VTimeInSec,
	entry *mshrEntry,
	updateMode bool,
) bool {
	if entry == nil || !entry.IsReady() {
		return false
	}

	if tlb.mshr.GetEntry(entry.pid, entry.baseVAddr) == nil {
		return false
	}

	tlb.respondingMSHREntry = append(tlb.respondingMSHREntry, entry)
	if updateMode {
		tlb.updateModeByMSHREntry(entry)
	}
	tlb.mshr.Remove(entry.pid, entry.baseVAddr)
	return true
}

func (tlb *GMMUTLB) installPage(page vm.Page) bool {
	setID := tlb.vAddrToSetID(page.VAddr)
	set := tlb.Sets[setID]
	wayID, ok, _ := tlb.Sets[setID].Evict()

	if !ok {
		return false
	}

	set.Update(wayID, page)
	set.Visit(wayID)
	return true
}

func (tlb *GMMUTLB) retrieveRsp(now sim.VTimeInSec, bottom bool) {
	if bottom {
		tlb.bottomPort.Retrieve(now)
	} else {
		tlb.OutsidePort.Retrieve(now)
	}
}

func (tlb *GMMUTLB) sendDownstream(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	bitmap [8]bool,
) (*vm.TranslationReq, bool) {
	if tlb.isBitmapZero(bitmap) {
		return nil, false
	}

	targetVAddr := tlb.bitmapVAddr(req.VAddr, bitmap)
	page, found := tlb.findFirstMappedPageInBitmap(req.PID, req.VAddr, bitmap)
	if !found {
		panic("page not found")
	}

	newReq := vm.TranslationReqBuilder{}.
		WithSendTime(now).
		WithPID(req.PID).
		WithVAddr(targetVAddr).
		WithDeviceID(tlb.DeviceID).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		WithBitMap(bitmap).
		WithPrefetch(req.IsPrefetch)

	if page.DeviceID != tlb.DeviceID {
		if tlb.IOMMUPort == nil {
			log.Panicf("GMMUTLB %s does not have an IOMMU port", tlb.Name())
		}

		translatedReq := newReq.
			WithSrc(tlb.OutsidePort).
			WithDst(tlb.IOMMUPort).
			Build()
		translatedReq.StartGPUID = req.StartGPUID

		err := tlb.IOMMUPort.Send(translatedReq)
		if err != nil {
			return nil, false
		}

		tlb.downstreamReqCount++
		tlb.iommuReqCount++

		return translatedReq, true
	}

	translatedReq := newReq.
		WithSrc(tlb.bottomPort).
		WithDst(tlb.LowModule).
		Build()
	translatedReq.StartGPUID = req.StartGPUID

	err := tlb.bottomPort.Send(translatedReq)
	if err != nil {
		return nil, false
	}

	tlb.downstreamReqCount++
	tlb.localReqCount++

	return translatedReq, true
}

func (tlb *GMMUTLB) findFirstMappedPageInBitmap(
	pid vm.PID,
	vAddr uint64,
	bitmap [8]bool,
) (vm.Page, bool) {
	baseVAddr := tlb.getBaseVaddr(vAddr)
	for i := 0; i < 8; i++ {
		if !bitmap[i] {
			continue
		}

		pageVAddr := baseVAddr + (uint64(i) << tlb.log2Pagesize)
		page, found := tlb.pageTable.Find(pid, pageVAddr)
		if found {
			return page, true
		}
	}

	return vm.Page{}, false
}

func (tlb *GMMUTLB) createNewBitmap(vaddr uint64, radius int) [8]bool {
	bitmap := [8]bool{}
	vpn := vaddr >> tlb.log2Pagesize

	for i := 0; i <= radius; i++ {
		if vpn%8+uint64(i) < 8 {
			bitmap[vpn%8+uint64(i)] = true
		}
		if vpn%8 >= uint64(i) {
			bitmap[vpn%8-uint64(i)] = true
		}
	}

	return bitmap
}

func (tlb *GMMUTLB) mergeBitmaps(b1, b2 [8]bool) [8]bool {
	merged := [8]bool{}

	for i := 0; i < 8; i++ {
		merged[i] = b1[i] || b2[i]
	}

	return merged
}

func (tlb *GMMUTLB) normalizeBitmap(req *vm.TranslationReq) [8]bool {
	if !tlb.isBitmapZero(req.BitMap) {
		return req.BitMap
	}

	return tlb.singlePageBitmap(req.VAddr)
}

func (tlb *GMMUTLB) singlePageBitmap(vAddr uint64) [8]bool {
	bitmap := [8]bool{}
	vpn := vAddr >> tlb.log2Pagesize
	bitmap[vpn%8] = true
	return bitmap
}

func (tlb *GMMUTLB) fullBitmap() [8]bool {
	bitmap := [8]bool{}
	for i := 0; i < 8; i++ {
		bitmap[i] = true
	}
	return bitmap
}

func (tlb *GMMUTLB) filterMappedBitmap(
	pid vm.PID,
	vAddr uint64,
	bitmap [8]bool,
) [8]bool {
	baseVAddr := tlb.getBaseVaddr(vAddr)
	filtered := [8]bool{}

	for i := 0; i < 8; i++ {
		if !bitmap[i] {
			continue
		}

		pageVAddr := baseVAddr + (uint64(i) << tlb.log2Pagesize)
		if _, found := tlb.pageTable.Find(pid, pageVAddr); found {
			filtered[i] = true
		}
	}

	return filtered
}

func (tlb *GMMUTLB) subtractBitmaps(a, b [8]bool) [8]bool {
	result := [8]bool{}
	for i := 0; i < 8; i++ {
		result[i] = a[i] && !b[i]
	}
	return result
}

func (tlb *GMMUTLB) intersectBitmaps(a, b [8]bool) [8]bool {
	result := [8]bool{}
	for i := 0; i < 8; i++ {
		result[i] = a[i] && b[i]
	}
	return result
}

func (tlb *GMMUTLB) firstBitBitmap(bitmap [8]bool) [8]bool {
	result := [8]bool{}
	for i := 0; i < 8; i++ {
		if bitmap[i] {
			result[i] = true
			return result
		}
	}
	return result
}

func (tlb *GMMUTLB) isBitmapZero(bitmap [8]bool) bool {
	for i := 0; i < 8; i++ {
		if bitmap[i] {
			return false
		}
	}
	return true
}

func (tlb *GMMUTLB) ptclID(vAddr uint64) uint64 {
	return vAddr >> (tlb.log2Pagesize + 3)
}

func (tlb *GMMUTLB) bitmapVAddr(vAddr uint64, bitmap [8]bool) uint64 {
	baseVAddr := tlb.getBaseVaddr(vAddr)
	if tlb.bitmapCount(bitmap) != 1 {
		return baseVAddr
	}

	for i := 0; i < 8; i++ {
		if bitmap[i] {
			return baseVAddr + (uint64(i) << tlb.log2Pagesize)
		}
	}

	return vAddr
}

func (tlb *GMMUTLB) bitmapCount(bitmap [8]bool) int {
	count := 0
	for i := 0; i < 8; i++ {
		if bitmap[i] {
			count++
		}
	}
	return count
}

func (tlb *GMMUTLB) getBaseVaddr(vAddr uint64) uint64 {
	vpn := vAddr >> tlb.log2Pagesize
	baseVPN := (vpn >> 3) << 3
	return baseVPN << tlb.log2Pagesize
}

func (tlb *GMMUTLB) adaptiveDelta(entry *mshrEntry) int {
	ones := tlb.bitmapCount(entry.RealAddrBitmap)
	if ones <= 1 {
		return -1
	}

	return 1
}

func (tlb *GMMUTLB) recordAdaptiveDelta(delta int) {
	tlb.coalescingCounter += delta
}

func (tlb *GMMUTLB) updateModeByMSHREntry(entry *mshrEntry) {
	if tlb.vpnMSHRBaseline {
		return
	}

	tlb.recordAdaptiveDelta(tlb.adaptiveDelta(entry))

	if !tlb.ptclMode && tlb.coalescingCounter > tlb.ptclHighThreshold {
		tlb.ptclMode = true
		tlb.switchToPTCLCount++
		return
	}

	if tlb.ptclMode && tlb.coalescingCounter < tlb.ptclLowThreshold {
		tlb.ptclMode = false
		tlb.switchToPTECount++
	}
}
