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

	mshr                mshr
	respondingMSHREntry []*mshrEntry
	log2Pagesize        uint64
	vpnMSHRBaseline     bool
	ptclMode            bool
	coalescingCounter   int
	ptclHighThreshold   int
	ptclLowThreshold    int
	switchToPTCLCount   int
	switchToPTECount    int
	downstreamReqCount  int
	localReqCount       int
	iommuReqCount       int

	isPaused   bool
	DeviceID   uint64
	pageTable  vm.PageTable
	PageFinder mem.PageFinder

	TimeConsumption              map[uint64]TimeConsumption
	prefetchedResidentEntries    map[prefetchResidentKey]*prefetchedResidentState
	prefetchOutcomeByBlock       map[uint64]*prefetchOutcomeCounts
	prefetchUnusedPTCLByBlock    map[uint64]map[uint64]int
	prefetchExactInserted        int
	prefetchExactUseful          int
	prefetchExactUsefulHit       int
	prefetchExactLateUseful      int
	prefetchExactUnused          int
	pendingPrefetchFeedback      []prefetchFeedbackEvent
	prefetchFeedbackStateByBlock map[uint64]vm.PrefetchFeedbackState
}

type prefetchResidentKey struct {
	pid   vm.PID
	vAddr uint64
}

type prefetchedResidentState struct {
	pageBlock uint64
}

type prefetchOutcomeCounts struct {
	Inserted        int
	Useful          int
	UsefulHit       int
	LateUseful      int
	Unused          int
	ResidentPending int
}

type prefetchFeedbackEvent struct {
	pageBlock uint64
	targetGPM uint64
	state     vm.PrefetchFeedbackState
}

// Reset sets all the entries int he TLB to be invalid
func (tlb *GMMUTLB) reset() {
	tlb.Sets = make([]internal.Set, tlb.numSets)
	for i := 0; i < tlb.numSets; i++ {
		set := internal.NewSet(tlb.numWays)
		tlb.Sets[i] = set
	}

	clear(tlb.prefetchedResidentEntries)
	clear(tlb.prefetchOutcomeByBlock)
	clear(tlb.prefetchUnusedPTCLByBlock)
	tlb.prefetchExactInserted = 0
	tlb.prefetchExactUseful = 0
	tlb.prefetchExactUsefulHit = 0
	tlb.prefetchExactLateUseful = 0
	tlb.prefetchExactUnused = 0
	tlb.pendingPrefetchFeedback = nil
	clear(tlb.prefetchFeedbackStateByBlock)
}

// Tick defines how TLB update states at each cycle
func (tlb *GMMUTLB) Tick(now sim.VTimeInSec) bool {
	madeProgress := false

	madeProgress = tlb.performCtrlReq(now) || madeProgress

	if !tlb.isPaused {
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
			madeProgress = tlb.sendPrefetchFeedback(now) || madeProgress
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

				// break
			}
		}
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
	msg := tlb.OutsidePort.Peek()
	if msg == nil {
		return false
	}

	switch msg := msg.(type) {
	case *vm.TranslationRsp:
		return tlb.processRsp(now, msg, false)
	default:
		panic("unexpected message type")
	}
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

	if !req.IsPrefetch {
		tlb.recordDemandUseful(page)
	}

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

	fetched := tlb.fetchBottom(now, mshrReq)
	if fetched {
		tracing.TraceReqReceive(mshrReq, tlb)
		tracing.AddTaskStep(tracing.MsgIDAtReceiver(mshrReq, tlb), tlb, "miss")
		tracing.StartTask(mshrReq.TaskID,
			tracing.MsgIDAtReceiver(mshrReq, tlb),
			tlb, "EvictTest", "*vm.TranslationReq", mshrReq)
		return true
	}

	return false
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

	requestedBitmap := tlb.normalizeBitmap(mshrReq)
	toIssue := [8]bool{}
	if tlb.vpnMSHRBaseline || !tlb.ptclMode {
		toIssue = tlb.subtractBitmaps(requestedBitmap, mshrEntry.IssuedBitMap)
		toIssue = tlb.filterMappedBitmap(mshrReq.PID, mshrReq.VAddr, toIssue)
	}

	if !tlb.isBitmapZero(toIssue) {
		reqToBottom, ok := tlb.sendDownstream(now, mshrReq, toIssue)
		if !ok {
			return false
		}
		mshrEntry.reqToBottom = reqToBottom
		mshrEntry.IssuedBitMap = tlb.mergeBitmaps(mshrEntry.IssuedBitMap, toIssue)
	}

	tlb.mshr.UpdateUpLevelBitMap(mshrReq.PID, mshrReq.VAddr, now, 0)
	mshrEntry.Requests = append(mshrEntry.Requests, mshrReq)

	tlb.topPort.Retrieve(now)

	tracing.TraceReqReceive(mshrReq, tlb)
	tracing.AddTaskStep(tracing.MsgIDAtReceiver(mshrReq, tlb), tlb, "mshr-hit")
	tracing.StartTask(mshrReq.TaskID,
		tracing.MsgIDAtReceiver(mshrReq, tlb),
		tlb, "EvictTest", "*vm.TranslationReq", mshrReq)

	return true
}

func (tlb *GMMUTLB) fetchBottom(now sim.VTimeInSec, mshrReq *vm.TranslationReq) bool {
	requestedBitmap := tlb.normalizeBitmap(mshrReq)
	issuedBitmap := requestedBitmap
	if tlb.ptclMode && !tlb.vpnMSHRBaseline {
		issuedBitmap = tlb.fullBitmap()
	}
	issuedBitmap = tlb.filterMappedBitmap(mshrReq.PID, mshrReq.VAddr, issuedBitmap)

	reqToBottom, ok := tlb.sendDownstream(now, mshrReq, issuedBitmap)
	if !ok {
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

func (tlb *GMMUTLB) parseBottom(now sim.VTimeInSec) bool {
	if len(tlb.respondingMSHREntry) != 0 {
		return false
	}

	item := tlb.bottomPort.Peek()
	if item == nil {
		return false
	}

	switch item := item.(type) {
	case *vm.TranslationRsp:
		return tlb.processRsp(now, item, true)
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
	setID := tlb.vAddrToSetID(req.VAddr)
	set := tlb.Sets[setID]
	wayID, page, found := set.Lookup(req.PID, req.VAddr)
	if found && page.Valid {
		return tlb.handleTranslationHit(now, req, setID, wayID, page)
	}

	mshrEntry := tlb.mshr.GetEntry(req.PID, req.VAddr)

	if mshrEntry != nil {
		return tlb.processTLBMSHRHit(now, mshrEntry, req)
	}

	return tlb.handleTranslationMiss(now, req)
}

func (tlb *GMMUTLB) processRsp(now sim.VTimeInSec, rsp *vm.TranslationRsp, bottom bool) bool {
	page := rsp.Page

	// fmt.Printf("Received from %s VAddr %d\n", rsp.Src.Name(), page.VAddr)

	mshrEntryPresent := tlb.mshr.IsEntryPresent(rsp.Page.PID, rsp.Page.VAddr)

	if !mshrEntryPresent {
		setID := tlb.vAddrToSetID(page.VAddr)
		set := tlb.Sets[setID]
		wayID, ok, evictedPage := tlb.Sets[setID].Evict()

		if !ok {
			panic("failed to evict")
		}

		tlb.recordPrefetchEviction(evictedPage)
		set.Update(wayID, page)
		set.Visit(wayID)
		if rsp.IsPrefetch {
			tlb.recordPrefetchInsert(page)
		}

		if bottom {
			tlb.bottomPort.Retrieve(now)
			// fmt.Printf("Bottom\n")
		} else {
			tlb.OutsidePort.Retrieve(now)
			// fmt.Printf("OutsidePort\n")
		}

		return true
	}

	setID := tlb.vAddrToSetID(page.VAddr)
	set := tlb.Sets[setID]
	wayID, ok, evictedPage := tlb.Sets[setID].Evict()

	if !ok {
		panic("failed to evict")
	}

	tlb.recordPrefetchEviction(evictedPage)
	set.Update(wayID, page)
	set.Visit(wayID)
	if rsp.IsPrefetch {
		tlb.recordLatePrefetchUse(page)
	}

	tlb.mshr.UpdatePage(rsp.Page.PID, rsp.Page.VAddr, page)

	mshrEntry := tlb.mshr.GetEntry(page.PID, page.VAddr)
	if mshrEntry == nil {
		if bottom {
			tlb.bottomPort.Retrieve(now)
		} else {
			tlb.OutsidePort.Retrieve(now)
		}
		return true
	}

	tlb.mshr.UpdateResponseBitMap(page.PID, page.VAddr)

	if mshrEntry.IsReady() {
		tlb.respondingMSHREntry = append(tlb.respondingMSHREntry, mshrEntry)
		tlb.updateModeByMSHREntry(mshrEntry)
		tlb.mshr.Remove(page.PID, page.VAddr)
	}
	if bottom {
		tlb.bottomPort.Retrieve(now)

	} else {
		tlb.OutsidePort.Retrieve(now)
	}
	return true
}

func (tlb *GMMUTLB) prefetchOutcomeState(pageBlock uint64) *prefetchOutcomeCounts {
	state, found := tlb.prefetchOutcomeByBlock[pageBlock]
	if !found {
		state = &prefetchOutcomeCounts{}
		tlb.prefetchOutcomeByBlock[pageBlock] = state
	}

	return state
}

func (tlb *GMMUTLB) prefetchedEntryKey(page vm.Page) prefetchResidentKey {
	return prefetchResidentKey{pid: page.PID, vAddr: page.VAddr}
}

func (tlb *GMMUTLB) recordPrefetchInsert(page vm.Page) {
	if !page.Valid {
		return
	}

	key := tlb.prefetchedEntryKey(page)
	if _, found := tlb.prefetchedResidentEntries[key]; found {
		return
	}

	tlb.prefetchedResidentEntries[key] = &prefetchedResidentState{pageBlock: page.PageBlock}
	tlb.prefetchExactInserted++
	state := tlb.prefetchOutcomeState(page.PageBlock)
	state.Inserted++
	state.ResidentPending++
}

func (tlb *GMMUTLB) recordLatePrefetchUse(page vm.Page) {
	if !page.Valid {
		return
	}

	key := tlb.prefetchedEntryKey(page)
	if state, found := tlb.prefetchedResidentEntries[key]; found {
		counts := tlb.prefetchOutcomeState(state.pageBlock)
		if counts.ResidentPending > 0 {
			counts.ResidentPending--
		}
		delete(tlb.prefetchedResidentEntries, key)
	}

	tlb.prefetchExactInserted++
	tlb.prefetchExactUseful++
	tlb.prefetchExactLateUseful++
	counts := tlb.prefetchOutcomeState(page.PageBlock)
	counts.Inserted++
	counts.Useful++
	counts.LateUseful++
	// tlb.enqueuePrefetchFeedback(page.PageBlock, tlb.DeviceID, vm.PrefetchFeedbackOutcomeLateUseful)
	tlb.maybeNotifyPrefetchFeedbackState(page.PageBlock)
}

func (tlb *GMMUTLB) recordDemandUseful(page vm.Page) {
	if !page.Valid {
		return
	}

	key := tlb.prefetchedEntryKey(page)
	state, found := tlb.prefetchedResidentEntries[key]
	if !found {
		return
	}

	tlb.prefetchExactUseful++
	tlb.prefetchExactUsefulHit++
	counts := tlb.prefetchOutcomeState(state.pageBlock)
	counts.Useful++
	counts.UsefulHit++
	// tlb.enqueuePrefetchFeedback(state.pageBlock, tlb.DeviceID, vm.PrefetchFeedbackOutcomeUsefulHit)
	tlb.maybeNotifyPrefetchFeedbackState(state.pageBlock)
	if counts.ResidentPending > 0 {
		counts.ResidentPending--
	}
	delete(tlb.prefetchedResidentEntries, key)
}

func (tlb *GMMUTLB) recordPrefetchEviction(page vm.Page) {
	if !page.Valid {
		return
	}

	key := tlb.prefetchedEntryKey(page)
	state, found := tlb.prefetchedResidentEntries[key]
	if !found {
		return
	}

	tlb.prefetchExactUnused++
	counts := tlb.prefetchOutcomeState(state.pageBlock)
	counts.Unused++
	// tlb.enqueuePrefetchFeedback(state.pageBlock, tlb.DeviceID, vm.PrefetchFeedbackOutcomeUnused)
	tlb.maybeNotifyPrefetchFeedbackState(state.pageBlock)
	blockPTCLs, found := tlb.prefetchUnusedPTCLByBlock[state.pageBlock]
	if !found {
		blockPTCLs = make(map[uint64]int)
		tlb.prefetchUnusedPTCLByBlock[state.pageBlock] = blockPTCLs
	}
	blockPTCLs[tlb.ptclID(page.VAddr)]++
	if counts.ResidentPending > 0 {
		counts.ResidentPending--
	}
	delete(tlb.prefetchedResidentEntries, key)
}

func (tlb *GMMUTLB) currentPrefetchFeedbackState(pageBlock uint64) vm.PrefetchFeedbackState {
	counts := tlb.prefetchOutcomeState(pageBlock)
	resolved := counts.UsefulHit + counts.Unused
	if resolved >= 8 && counts.Unused > counts.UsefulHit {
		return vm.PrefetchFeedbackStateDisabled
	}

	return vm.PrefetchFeedbackStateEnabled
}

func (tlb *GMMUTLB) maybeNotifyPrefetchFeedbackState(pageBlock uint64) {
	state := tlb.currentPrefetchFeedbackState(pageBlock)
	previous, found := tlb.prefetchFeedbackStateByBlock[pageBlock]
	if found && previous == state {
		return
	}

	tlb.prefetchFeedbackStateByBlock[pageBlock] = state
	if !found && state == vm.PrefetchFeedbackStateEnabled {
		return
	}

	tlb.enqueuePrefetchFeedback(pageBlock, tlb.DeviceID, state)
}

func (tlb *GMMUTLB) enqueuePrefetchFeedback(
	pageBlock uint64,
	targetGPM uint64,
	state vm.PrefetchFeedbackState,
) {
	tlb.pendingPrefetchFeedback = append(tlb.pendingPrefetchFeedback, prefetchFeedbackEvent{
		pageBlock: pageBlock,
		targetGPM: targetGPM,
		state:     state,
	})
}

func (tlb *GMMUTLB) sendPrefetchFeedback(now sim.VTimeInSec) bool {
	if len(tlb.pendingPrefetchFeedback) == 0 {
		return false
	}

	if tlb.IOMMUPort == nil {
		log.Panicf("GMMUTLB %s does not have an IOMMU port", tlb.Name())
	}

	event := tlb.pendingPrefetchFeedback[0]
	msg := vm.PrefetchFeedbackMsgBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.OutsidePort).
		WithDst(tlb.IOMMUPort).
		WithPageBlock(event.pageBlock).
		WithTargetGPM(event.targetGPM).
		WithState(event.state).
		Build()

	if err := tlb.IOMMUPort.Send(msg); err != nil {
		return false
	}

	tlb.pendingPrefetchFeedback = tlb.pendingPrefetchFeedback[1:]
	return true
}

func (tlb *GMMUTLB) sendToIOMMU(
	req *vm.TranslationReq,
	now sim.VTimeInSec,
	Bitmap [8]bool,
) (*vm.TranslationReq, bool) {
	if tlb.IOMMUPort == nil {
		log.Panicf("GMMUTLB %s does not have an IOMMU port", tlb.Name())
	}

	newReq := vm.TranslationReqBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.OutsidePort).
		WithDst(tlb.IOMMUPort).
		WithPID(req.PID).
		WithVAddr(req.VAddr).
		WithDeviceID(tlb.DeviceID).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		WithBitMap(Bitmap).
		Build()

	err := tlb.IOMMUPort.Send(newReq)
	if err != nil {
		return nil, false
	}

	return newReq, true
}

func (tlb *GMMUTLB) sendDownstream(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	bitmap [8]bool,
) (*vm.TranslationReq, bool) {
	if tlb.isBitmapZero(bitmap) {
		return nil, true
	}

	page, found := tlb.pageTable.Find(req.PID, req.VAddr)
	if !found {
		panic("page not found")
	}

	targetVAddr := tlb.bitmapVAddr(req.VAddr, bitmap)
	newReq := vm.TranslationReqBuilder{}.
		WithSendTime(now).
		WithPID(req.PID).
		WithVAddr(targetVAddr).
		WithDeviceID(tlb.DeviceID).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		WithBitMap(bitmap)

	if page.DeviceID != tlb.DeviceID {
		if tlb.IOMMUPort == nil {
			log.Panicf("GMMUTLB %s does not have an IOMMU port", tlb.Name())
		}

		translatedReq := newReq.
			WithSrc(tlb.OutsidePort).
			WithDst(tlb.IOMMUPort).
			Build()

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

	err := tlb.bottomPort.Send(translatedReq)
	if err != nil {
		return nil, false
	}

	tlb.downstreamReqCount++
	tlb.localReqCount++

	return translatedReq, true
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
