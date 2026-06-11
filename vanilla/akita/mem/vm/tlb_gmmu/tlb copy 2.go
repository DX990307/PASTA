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
	downstreamReqCount     int
	localReqCount          int
	iommuReqCount          int
	pteLookupLatencyCycles int
	pteLookupStates        map[string]pteLookupState
	pteLookupDelayCount    int
	pteLookupDelayCycles   int

	isPaused       bool
	DeviceID       uint64
	pageTable      vm.PageTable
	PageFinder     mem.PageFinder
	gmmuCacheTable *mem.MultiPageFinder

	TimeConsumption map[uint64]TimeConsumption
}

type pteLookupState struct {
	readyTime sim.VTimeInSec
}

// Reset sets all the entries int he TLB to be invalid
func (tlb *GMMUTLB) reset() {
	tlb.Sets = make([]internal.Set, tlb.numSets)
	for i := 0; i < tlb.numSets; i++ {
		set := internal.NewSet(tlb.numWays)
		tlb.Sets[i] = set
	}

	clear(tlb.pteLookupStates)
	tlb.pteLookupDelayCount = 0
	tlb.pteLookupDelayCycles = 0
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
	}

	return madeProgress
}

func (tlb *GMMUTLB) respondMSHREntry(now sim.VTimeInSec) bool {
	if len(tlb.respondingMSHREntry) == 0 {
		return false
	}

	mshrEntry := tlb.respondingMSHREntry[0]
	req := mshrEntry.Requests[0]

	rspToTop := vm.TranslationRspBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.topPort).
		WithDst(req.Src).
		WithRspTo(req.ID).
		WithPage(mshrEntry.page).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		Build()

	err := tlb.topPort.Send(rspToTop)
	if err != nil {
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
	reqToBottom, ok := tlb.sendDownstream(now, mshrReq)
	if !ok {
		return false
	}
	if reqToBottom == nil {
		return false
	}

	mshrEntry := tlb.mshr.Add(mshrReq.PID, mshrReq.VAddr)
	mshrEntry.Requests = append(mshrEntry.Requests, mshrReq)
	mshrEntry.reqToBottom = reqToBottom

	tlb.topPort.Retrieve(now)

	tracing.TraceReqInitiate(reqToBottom, tlb,
		tracing.MsgIDAtReceiver(mshrReq, tlb))

	return true
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
		if !tlb.isPTEReady(now, msg) {
			return false
		}
		return tlb.processRsp(now, msg, bottom)
	default:
		panic("unexpected message type")
	}
}

func (tlb *GMMUTLB) isPTEReady(
	now sim.VTimeInSec,
	rsp *vm.TranslationRsp,
) bool {
	if rsp == nil || tlb.pteLookupLatencyCycles <= 0 {
		return true
	}

	state, found := tlb.pteLookupStates[rsp.ID]
	if !found {
		state = pteLookupState{
			readyTime: tlb.Freq.NCyclesLater(tlb.pteLookupLatencyCycles, now),
		}
		tlb.pteLookupStates[rsp.ID] = state
		tlb.pteLookupDelayCount++
		tlb.pteLookupDelayCycles += tlb.pteLookupLatencyCycles
	}

	if state.readyTime > now {
		tlb.TickNow(state.readyTime)
		return false
	}

	delete(tlb.pteLookupStates, rsp.ID)
	return true
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

func (tlb *GMMUTLB) processRsp(
	now sim.VTimeInSec,
	rsp *vm.TranslationRsp,
	bottom bool,
) bool {
	page := rsp.Page

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

	if mshrEntry.IsReady() {
		tlb.respondingMSHREntry = append(tlb.respondingMSHREntry, mshrEntry)
		tlb.mshr.Remove(page.PID, page.VAddr)
	}

	tlb.retrieveRsp(now, bottom)
	return true
}

func (tlb *GMMUTLB) installPage(page vm.Page) {
	setID := tlb.vAddrToSetID(page.VAddr)
	set := tlb.Sets[setID]
	wayID, ok, _ := tlb.Sets[setID].Evict()

	if !ok {
		panic("failed to evict")
	}

	set.Update(wayID, page)
	set.Visit(wayID)
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
) (*vm.TranslationReq, bool) {
	page, found := tlb.pageTable.Find(req.PID, req.VAddr)
	if !found {
		panic("page not found")
	}

	newReq := vm.TranslationReqBuilder{}.
		WithSendTime(now).
		WithPID(req.PID).
		WithVAddr(req.VAddr).
		WithDeviceID(tlb.DeviceID).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort)

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
