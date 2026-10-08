package mmuTLB

import (
	"fmt"
	"log"
	"reflect"
	"sort"

	"github.com/sarchlab/akita/v3/mem/mem"
	"github.com/sarchlab/akita/v3/mem/vm"
	"github.com/sarchlab/akita/v3/mem/vm/mmuTLB/internal"
	"github.com/sarchlab/akita/v3/mem/vm/translationtrace"
	"github.com/sarchlab/akita/v3/sim"
	"github.com/sarchlab/akita/v3/tracing"
)

var log2PageSize uint64 = 12

// A TLB is a cache that maintains some page information.
type TLB struct {
	*sim.TickingComponent

	topPort     sim.Port
	bottomPort  sim.Port
	controlPort sim.Port

	LowModule sim.Port

	numSets        int
	numWays        int
	pageSize       uint64
	numReqPerCycle int
	TopPortBuffer  []sim.Msg

	Sets []internal.Set

	mshr                mshr
	respondingMSHREntry []*mshrEntry
	gmmuCacheTable      *mem.MultiPageFinder

	isPrediction bool
	BloomFilter  *BloomFilter
	log2PageSize uint64

	InnerLoop  map[uint64]uint64
	MiddleLoop map[uint64]uint64
	isPaused   bool
	pageTable  vm.PageTable

	reqBuffer              []*vm.TranslationReq
	incomingReqCount       int
	downstreamReqCount     int
	vpnMSHRBaseline        bool
	demandPTEOnly          bool
	setAsLineTLBEnabled    bool
	pcd                    *ptclCoverageDirectory
	lookupLatencyCycles    int
	setLookupJobs          int
	setLookupRequestedBits int
	setLookupHitBits       int
	setLookupMissBits      int
	setLookupSavedJobs     int
	setFills               int
	setConflictEvictions   int
	lookupReadyTimes       map[string]sim.VTimeInSec
	knownGPMIDs            []uint64
	knownGPMsReady         bool

	// translationRequests     map[uint64]map[vm.PID]*vm.TranslationReq
}

// Reset sets all the entries int he TLB to be invalid
func (tlb *TLB) reset() {
	tlb.Sets = make([]internal.Set, tlb.numSets)
	for i := 0; i < tlb.numSets; i++ {
		set := internal.NewSet(tlb.numWays)
		tlb.Sets[i] = set
	}

	clear(tlb.lookupReadyTimes)
	tlb.setLookupJobs = 0
	tlb.setLookupRequestedBits = 0
	tlb.setLookupHitBits = 0
	tlb.setLookupMissBits = 0
	tlb.setLookupSavedJobs = 0
	tlb.setFills = 0
	tlb.setConflictEvictions = 0

	if tlb.setAsLineTLBEnabled && !tlb.vpnMSHRBaseline && !tlb.demandPTEOnly {
		tlb.pcd = newPTCLCoverageDirectory(
			tlb.numSets,
			tlb.numWays,
			0,
			tlb.log2PageSize,
		)
	} else {
		tlb.pcd = nil
	}
}

// Tick defines how TLB update states at each cycle
func (tlb *TLB) Tick(now sim.VTimeInSec) bool {
	madeProgress := false

	if !tlb.isPaused {
		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.respondMSHREntry(now) || madeProgress
		}

		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.pushReqBuffer(now) || madeProgress
		}

		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.lookup(now) || madeProgress
		}

		for i := 0; i < tlb.numReqPerCycle; i++ {
			madeProgress = tlb.parseBottom(now) || madeProgress
		}
	}

	if tlb.mshr != nil {
		entries, capacity := tlb.mshr.Occupancy()
		translationtrace.ObserveIOMMUTLB(
			now,
			tlb.Name(),
			entries,
			capacity,
			entries >= capacity,
		)
	}

	return madeProgress
}
func (tlb *TLB) pushReqBuffer(now sim.VTimeInSec) bool {
	msg := tlb.topPort.Peek()
	if msg == nil {
		return false
	}

	switch msg := msg.(type) {
	case *vm.TranslationReq:
		req := msg
		req.BitMap = tlb.effectiveBitmap(req)
		req.BitMap = tlb.filterMappedBitmap(req.PID, req.VAddr, req.BitMap)

		tlb.reqBuffer = append(tlb.reqBuffer, req)
		tlb.setLookupReadyTime(now, req)
		tlb.incomingReqCount++
		translationtrace.RecordIOMMUIncoming(now)
		tlb.topPort.Retrieve(now)

		tracing.TraceReqReceive(req, tlb)
		tracing.AddTaskStep(tracing.MsgIDAtReceiver(req, tlb), tlb, "buffered")
		return true
	default:
		panic(fmt.Sprintf("cannot process top-port message %s", reflect.TypeOf(msg)))
	}
}

func (tlb *TLB) respondMSHREntry(now sim.VTimeInSec) bool {
	if len(tlb.respondingMSHREntry) == 0 {
		return false
	}

	mshrEntry := tlb.respondingMSHREntry[0]
	req := mshrEntry.Requests[0]
	bitmap := req.BitMap

	pages := mshrEntry.Pages

	for i := 0; i < 8; i++ {
		page := pages[i]

		if !bitmap[i] {
			continue
		}

		if req.Src == nil {
			panic(fmt.Sprintf(
				"mmutlb responding request has nil Src pid=%d baseVAddr=%#x device=%d task=%s",
				req.PID, tlb.getBaseVaddr(req.VAddr), req.DeviceID, req.TaskID,
			))
		}

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
			// fmt.Printf("Failed to send response to top for page %d, error: %s\n", page.VAddr, err)
			return false
		}

		bitmap[i] = false
		req.BitMap = bitmap
		break
	}

	for i := 0; i < 8; i++ {
		if bitmap[i] {
			return true
		}
	}

	mshrEntry.Requests = mshrEntry.Requests[1:]

	if len(mshrEntry.Requests) == 0 {
		tlb.respondingMSHREntry = tlb.respondingMSHREntry[1:]

	}

	tracing.TraceReqComplete(req, tlb)
	return true
}

func (tlb *TLB) lookup(now sim.VTimeInSec) bool {
	// msg := tlb.topPort.Peek()
	if len(tlb.reqBuffer) == 0 {
		return false
	}

	req := tlb.reqBuffer[0]
	// tlb.reqBuffer = tlb.reqBuffer[1:]

	if req == nil {
		return false
	}

	if !tlb.isLookupReady(now, req) {
		return false
	}

	if tlb.handleTranslationHits(now, req) {
		return true
	}

	mshrEntry := tlb.mshr.GetEntry(req.PID, req.VAddr)
	if mshrEntry != nil {
		return tlb.processTLBMSHRHit(now, mshrEntry, req)
	}

	return tlb.handleTranslationMiss(now, req)
}

func (tlb *TLB) handleTranslationHits(now sim.VTimeInSec, req *vm.TranslationReq) bool {
	if tlb.usePTCLSetLookup(req) {
		return tlb.handlePTCLSetTranslationHits(now, req)
	}

	pages := [8]vm.Page{}
	BaseVaddr := tlb.getBaseVaddr(req.VAddr)
	BaseVPN := BaseVaddr >> tlb.log2PageSize

	bitmap := tlb.normalizeBitmap(req)

	for i := 0; i < 8; i++ {

		if !bitmap[i] {
			continue
		}

		VPN := BaseVPN + uint64(i)
		newVaddr := VPN << tlb.log2PageSize

		setID := tlb.vAddrToSetID(newVaddr)
		set := tlb.Sets[setID]
		wayID, page, found := set.Lookup(req.PID, newVaddr)

		if found && page.Valid {
			pages[i] = page
			tlb.visit(setID, wayID)
		} else {
			return false
		}
	}

	for i := 0; i < 8; i++ {

		if !bitmap[i] {
			continue
		}

		ok := tlb.sendRspToTop(now, req, pages[i])

		if !ok {
			return false
		}

	}

	// tlb.topPort.Retrieve(now)
	tlb.reqBuffer = tlb.reqBuffer[1:]

	return true
}

func (tlb *TLB) handlePTCLSetTranslationHits(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
) bool {
	bitmap := tlb.normalizeBitmap(req)
	baseVAddr := tlb.getBaseVaddr(req.VAddr)
	result := tlb.lookupPTCLWithPCD(req.PID, baseVAddr, bitmap)
	requestedBits := tlb.bitmapCount(bitmap)
	hitBits := tlb.bitmapCount(result.HitBitmap)
	missBits := tlb.bitmapCount(result.MissBitmap)
	tlb.setLookupJobs++
	tlb.setLookupRequestedBits += requestedBits
	tlb.setLookupHitBits += hitBits
	tlb.setLookupMissBits += missBits
	if requestedBits > 1 {
		tlb.setLookupSavedJobs += requestedBits - 1
	}

	for i := 0; i < 8; i++ {
		if !result.HitBitmap[i] {
			continue
		}
		if !tlb.sendRspToTop(now, req, result.Pages[i]) {
			return false
		}
	}

	if missBits == 0 {
		tlb.reqBuffer = tlb.reqBuffer[1:]
		return true
	}

	if hitBits > 0 {
		req.BitMap = result.MissBitmap
	}
	return false
}

func (tlb *TLB) handleTranslationMiss(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
) bool {
	if tlb.mshr.IsFull() {
		translationtrace.BeginStage(req.ID, "iommutlb_mshr_wait", now)
		return false
	}
	translationtrace.EndStage(req.ID, "iommutlb_mshr_wait", now)

	if tlb.mshr.IsEntryFull(req.PID, req.VAddr) {
		translationtrace.BeginStage(req.ID, "iommutlb_mshr_entry_wait", now)
		return false
	}
	translationtrace.EndStage(req.ID, "iommutlb_mshr_entry_wait", now)

	fetched := tlb.fetchBottom(now, req)
	if fetched {
		// tlb.topPort.Retrieve(now)
		tlb.reqBuffer = tlb.reqBuffer[1:]

		tracing.TraceReqReceive(req, tlb)
		tracing.AddTaskStep(tracing.MsgIDAtReceiver(req, tlb), tlb, "miss")
		return true
	}

	return false
}

func (tlb *TLB) vAddrToSetID(vAddr uint64) (setID int) {
	return int(vAddr / tlb.pageSize % uint64(tlb.numSets))
}

func (tlb *TLB) ptclVAddrToSetID(pid vm.PID, baseVAddr uint64) (setID int) {
	if tlb.numSets <= 0 {
		return 0
	}

	ptclID := baseVAddr >> (tlb.log2PageSize + 3)
	shift := uint(0)
	for (1 << shift) < tlb.numSets {
		shift++
	}

	pidHash := uint64(pid) ^ (uint64(pid) >> shift)
	hashed := ptclID ^ (ptclID >> shift) ^ pidHash
	if tlb.numSets&(tlb.numSets-1) == 0 {
		return int(hashed & uint64(tlb.numSets-1))
	}

	return int(hashed % uint64(tlb.numSets))
}

func (tlb *TLB) usePTCLSetLookup(req *vm.TranslationReq) bool {
	return req != nil &&
		tlb.setAsLineTLBEnabled &&
		!tlb.vpnMSHRBaseline &&
		!tlb.demandPTEOnly
}

func (tlb *TLB) sendRspToTop(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	page vm.Page,
) bool {
	if req.Src == nil {
		panic(fmt.Sprintf(
			"mmutlb hit response has nil Src pid=%d vAddr=%#x device=%d task=%s",
			req.PID, req.VAddr, req.DeviceID, req.TaskID,
		))
	}

	rsp := vm.TranslationRspBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.topPort).
		WithDst(req.Src).
		WithRspTo(req.ID).
		WithPage(page).
		WithOriginPort(req.OriginPort).
		Build()

	err := tlb.topPort.Send(rsp)
	if err != nil {
		// fmt.Printf("Failed to send response to top for page %d, error: %s\n", page.VAddr, err)
		return false
	}

	return true
}

func (tlb *TLB) processTLBMSHRHit(
	now sim.VTimeInSec,
	mshrEntry *mshrEntry,
	req *vm.TranslationReq,
) bool {
	if tlb.mshr.IsEntryFull(req.PID, req.VAddr) {
		translationtrace.BeginStage(req.ID, "iommutlb_mshr_entry_wait", now)
		return false
	}
	translationtrace.EndStage(req.ID, "iommutlb_mshr_entry_wait", now)

	requestBitmap := tlb.normalizeBitmap(req)
	toIssue := tlb.subtractBitmaps(requestBitmap, mshrEntry.IssuedBitMap)
	if !tlb.isBitmapZero(toIssue) {
		reqToBottom, ok := tlb.issueBottomReqs(now, req, toIssue)
		if !ok {
			return false
		}
		mshrEntry.reqToBottom = reqToBottom
		mshrEntry.IssuedBitMap = mergeBitmaps(mshrEntry.IssuedBitMap, toIssue)
	}

	mshrEntry.UplevelBitMap = mergeBitmaps(mshrEntry.UplevelBitMap, requestBitmap)
	mshrEntry.Requests = append(mshrEntry.Requests, req)
	tlb.reqBuffer = tlb.reqBuffer[1:]

	tracing.TraceReqReceive(req, tlb)
	tracing.AddTaskStep(tracing.MsgIDAtReceiver(req, tlb), tlb, "mshr-hit")

	return true
}

func (tlb *TLB) fetchBottom(now sim.VTimeInSec, req *vm.TranslationReq) bool {
	requestBitmap := tlb.normalizeBitmap(req)
	reqToBottom, ok := tlb.issueBottomReqs(now, req, requestBitmap)
	if !ok {
		return false
	}

	mshrEntry := tlb.mshr.Add(req.PID, req.VAddr, requestBitmap)
	mshrEntry.Requests = append(mshrEntry.Requests, req)
	mshrEntry.reqToBottom = reqToBottom
	mshrEntry.IssuedBitMap = mergeBitmaps(mshrEntry.IssuedBitMap, requestBitmap)

	return true
}

func (tlb *TLB) parseBottom(now sim.VTimeInSec) bool {
	item := tlb.bottomPort.Peek()
	if item == nil {
		return false
	}

	switch rsp := item.(type) {
	case *vm.TranslationRsp:
		if tlb.handleRsp(now, rsp) {
			tlb.bottomPort.Retrieve(now)
			return true
		}
		return false
	default:
		log.Panicf("cannot process request %s", reflect.TypeOf(item))
	}

	return false
}

func (tlb *TLB) handleRsp(now sim.VTimeInSec, rsp *vm.TranslationRsp) bool {
	page := rsp.Page

	// fmt.Printf("Received from %s VAddr %d\n", rsp.Src.Name(), page.VAddr)

	mshrEntryPresent := tlb.mshr.IsEntryPresent(rsp.Page.PID, rsp.Page.VAddr)
	if !mshrEntryPresent {
		if !tlb.installPage(page) {
			panic("failed to evict")
		}
		return true
	}

	if !tlb.installPage(page) {
		panic("failed to evict")
	}

	tlb.mshr.UpdatePage(rsp.Page.PID, rsp.Page.VAddr, page)
	tlb.mshr.UpdateResponseBitMap(rsp.Page.PID, rsp.Page.VAddr)

	mshrEntry := tlb.mshr.GetEntry(page.PID, page.VAddr)
	if mshrEntry == nil {
		return true
	}

	if mshrEntry.IsReady() {
		tlb.respondingMSHREntry = append(tlb.respondingMSHREntry, mshrEntry)
		tlb.mshr.Remove(page.PID, page.VAddr)
	}

	return true
}

func (tlb *TLB) visit(setID, wayID int) {
	set := tlb.Sets[setID]
	set.Visit(wayID)
}

func (tlb *TLB) installPage(page vm.Page) bool {
	if tlb.setAsLineTLBEnabled && !tlb.vpnMSHRBaseline && !tlb.demandPTEOnly {
		setID := tlb.vAddrToSetID(page.VAddr)
		set := tlb.Sets[setID]

		if wayID, _, found := set.Lookup(page.PID, page.VAddr); found {
			set.Update(wayID, page)
			set.Visit(wayID)
			tlb.recordPCDFill(page, wayID)
			tlb.setFills++
			return true
		}

		wayID, ok := set.Evict()
		if !ok {
			return false
		}
		evictedPage, _ := set.Peek(wayID)
		if evictedPage.Valid {
			tlb.setConflictEvictions++
			if tlb.pcd != nil {
				tlb.pcd.removePage(evictedPage, wayID)
			}
		}

		set.Update(wayID, page)
		set.Visit(wayID)
		tlb.recordPCDFill(page, wayID)
		tlb.setFills++
		return true
	}

	setID := tlb.vAddrToSetID(page.VAddr)
	set := tlb.Sets[setID]
	wayID, ok := tlb.Sets[setID].Evict()
	if !ok {
		return false
	}
	set.Update(wayID, page)
	set.Visit(wayID)
	return true
}

func (tlb *TLB) installBitmapPages(
	pid vm.PID,
	baseVAddr uint64,
	pages [8]vm.Page,
	bitmap [8]bool,
) bool {
	if tlb.isBitmapZero(bitmap) {
		return false
	}

	if tlb.setAsLineTLBEnabled && !tlb.vpnMSHRBaseline && !tlb.demandPTEOnly {
		installed := false
		for i := 0; i < 8; i++ {
			if !bitmap[i] || !pages[i].Valid {
				continue
			}
			installed = tlb.installPage(pages[i]) || installed
		}
		return installed
	}

	installed := false
	for i := 0; i < 8; i++ {
		if !bitmap[i] || !pages[i].Valid {
			continue
		}
		installed = tlb.installPage(pages[i]) || installed
	}
	return installed
}

func (tlb *TLB) lookupPTCLWithPCD(
	pid vm.PID,
	baseVAddr uint64,
	lookupBitmap [8]bool,
) pcdLookupResult {
	result := pcdLookupResult{MissBitmap: lookupBitmap}
	if tlb.pcd == nil {
		return result
	}

	set := tlb.pcd.setForLine(pid, baseVAddr)
	for entryIndex := range set.entries {
		entry := &set.entries[entryIndex]
		if !entry.valid {
			continue
		}

		rowHit := false
		for i := 0; i < 8; i++ {
			if !lookupBitmap[i] || !entry.presentBitmap[i] ||
				result.HitBitmap[i] {
				continue
			}

			page, ok := tlb.validatePCDLocator(
				pid,
				baseVAddr,
				i,
				entry.locators[i],
			)
			if ok {
				result.HitBitmap[i] = true
				result.MissBitmap[i] = false
				result.Pages[i] = page
				rowHit = true
				continue
			}

			tlb.pcd.clearBit(entry, i)
			tlb.pcd.staleBits++
			result.StaleBits++
		}
		if rowHit {
			result.LineHit = true
			tlb.pcd.visit(entry)
		}
	}

	return result
}

func (tlb *TLB) validatePCDLocator(
	pid vm.PID,
	baseVAddr uint64,
	bit int,
	locator pcdLocator,
) (vm.Page, bool) {
	page, setID, ok := tlb.peekPCDLocator(pid, baseVAddr, bit, locator)
	if !ok {
		return vm.Page{}, false
	}

	tlb.visit(setID, locator.wayID)
	return page, true
}

func (tlb *TLB) peekPCDLocator(
	pid vm.PID,
	baseVAddr uint64,
	bit int,
	locator pcdLocator,
) (vm.Page, int, bool) {
	expectedVAddr := baseVAddr + (uint64(bit) << tlb.log2PageSize)
	setID := tlb.vAddrToSetID(expectedVAddr)
	if setID < 0 || setID >= len(tlb.Sets) {
		return vm.Page{}, 0, false
	}

	page, ok := tlb.Sets[setID].Peek(locator.wayID)
	if !ok || !page.Valid {
		return vm.Page{}, 0, false
	}

	if page.PID != pid || page.VAddr != expectedVAddr {
		return vm.Page{}, 0, false
	}

	return page, setID, true
}

func (tlb *TLB) recordPCDFill(page vm.Page, wayID int) {
	if tlb.pcd == nil || !page.Valid {
		return
	}

	baseVAddr := tlb.pcd.baseVAddr(page.VAddr)
	entry, found := tlb.pcd.findEntryForLine(
		page.PID,
		baseVAddr,
		func(entry *pcdEntry) bool {
			return tlb.pcdEntryMatchesLine(page.PID, baseVAddr, entry)
		},
	)
	if !found {
		entry = tlb.pcd.findOrAllocateEntry(page.PID, baseVAddr)
	}

	tlb.pcd.recordFillInEntry(entry, page, wayID)
}

func (tlb *TLB) pcdEntryMatchesLine(
	pid vm.PID,
	baseVAddr uint64,
	entry *pcdEntry,
) bool {
	if entry == nil || !entry.valid {
		return false
	}

	for bit := 0; bit < 8; bit++ {
		if !entry.presentBitmap[bit] {
			continue
		}
		if _, _, ok := tlb.peekPCDLocator(
			pid,
			baseVAddr,
			bit,
			entry.locators[bit],
		); ok {
			return true
		}
	}

	return false
}

func (tlb *TLB) getBaseVaddr(vAddr uint64) uint64 {
	VPN := vAddr >> tlb.log2PageSize
	BaseVPN := (VPN >> 3) << 3 // Clear the lower 3 bits to get the base VPN
	return BaseVPN << tlb.log2PageSize
}

func (tlb *TLB) getMSHREntryVAddr(vAddr uint64) uint64 {
	if tlb.vpnMSHRBaseline {
		return (vAddr >> tlb.log2PageSize) << tlb.log2PageSize
	}

	return tlb.getBaseVaddr(vAddr)
}

func (tlb *TLB) isInRespondingMSHREntry(pid vm.PID, vAddr uint64) bool {
	baseVAddr := tlb.getMSHREntryVAddr(vAddr)
	for _, e := range tlb.respondingMSHREntry {
		if e.pid == pid && e.baseVAddr == baseVAddr {
			return true
		}
	}
	return false
}

func (tlb *TLB) addToExistingRespondingMSHREntry(req *vm.TranslationReq) bool {
	baseVAddr := tlb.getMSHREntryVAddr(req.VAddr)
	for _, e := range tlb.respondingMSHREntry {
		if e.pid == req.PID && e.baseVAddr == baseVAddr {
			ok := tlb.mergedAndAppendToMSHREntries(e, req)
			if !ok {
				e.Requests = append(e.Requests, req)
			}
			return true
		}
	}
	return false
}

func mergeBitmaps(oldBitmap, newBitmap [8]bool) [8]bool {
	mergedBitmap := [8]bool{}
	for i := 0; i < 8; i++ {
		mergedBitmap[i] = oldBitmap[i] || newBitmap[i]
	}
	return mergedBitmap
}

func (tlb *TLB) mergedAndAppendToMSHREntries(mshr *mshrEntry, req *vm.TranslationReq) bool {
	for _, oldReq := range mshr.Requests {
		if oldReq.DeviceID == req.DeviceID {
			oldBitmap := oldReq.BitMap
			newBitmap := req.BitMap
			mergedBitmap := mergeBitmaps(oldBitmap, newBitmap)
			oldReq.BitMap = mergedBitmap
			return true
		}

	}
	return false
}

func (tlb *TLB) normalizeBitmap(req *vm.TranslationReq) [8]bool {
	if !tlb.isBitmapZero(req.BitMap) {
		return req.BitMap
	}

	return tlb.singlePageBitmap(req.VAddr)
}

func (tlb *TLB) effectiveBitmap(req *vm.TranslationReq) [8]bool {
	if tlb.demandPTEOnly {
		return tlb.singlePageBitmap(req.VAddr)
	}

	if tlb.setAsLineTLBEnabled && !tlb.vpnMSHRBaseline {
		return tlb.fullBitmap()
	}

	return tlb.normalizeBitmap(req)
}

func (tlb *TLB) singlePageBitmap(vAddr uint64) [8]bool {
	bitmap := [8]bool{}
	vpn := vAddr >> tlb.log2PageSize
	bitmap[vpn%8] = true
	return bitmap
}

func (tlb *TLB) fullBitmap() [8]bool {
	bitmap := [8]bool{}
	for i := 0; i < 8; i++ {
		bitmap[i] = true
	}
	return bitmap
}

func (tlb *TLB) subtractBitmaps(a, b [8]bool) [8]bool {
	result := [8]bool{}
	for i := 0; i < 8; i++ {
		result[i] = a[i] && !b[i]
	}
	return result
}

func (tlb *TLB) isBitmapZero(bitmap [8]bool) bool {
	for i := 0; i < 8; i++ {
		if bitmap[i] {
			return false
		}
	}
	return true
}

func (tlb *TLB) bitmapCount(bitmap [8]bool) int {
	count := 0
	for i := 0; i < 8; i++ {
		if bitmap[i] {
			count++
		}
	}

	return count
}

func (tlb *TLB) issueBottomReqs(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
	bitmap [8]bool,
) (*vm.TranslationReq, bool) {
	bitmap = tlb.filterMappedBitmap(req.PID, req.VAddr, bitmap)
	if tlb.isBitmapZero(bitmap) {
		return nil, false
	}

	baseVAddr := tlb.getBaseVaddr(req.VAddr)
	reqToBottom := vm.TranslationReqBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.bottomPort).
		WithDst(tlb.LowModule).
		WithPID(req.PID).
		WithVAddr(baseVAddr).
		WithDeviceID(req.DeviceID).
		WithTaskID(req.TaskID).
		WithOriginPort(req.OriginPort).
		WithBitMap(bitmap).
		Build()
	reqToBottom.StartGPUID = req.StartGPUID

	err := tlb.bottomPort.Send(reqToBottom)
	if err != nil {
		return nil, false
	}

	translationtrace.LinkRequest(reqToBottom.ID, req.ID)
	translationtrace.RecordIOMMUToMMU(now)
	tlb.downstreamReqCount++

	return reqToBottom, true
}

func (tlb *TLB) filterMappedBitmap(
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

		pageVAddr := baseVAddr + (uint64(i) << tlb.log2PageSize)
		if _, found := tlb.pageTable.Find(pid, pageVAddr); found {
			filtered[i] = true
		}
	}

	return filtered
}

func (tlb *TLB) setLookupReadyTime(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
) {
	if req == nil {
		return
	}

	if tlb.lookupLatencyCycles <= 0 {
		delete(tlb.lookupReadyTimes, req.ID)
		return
	}

	lookupBits := tlb.bitmapCount(req.BitMap)
	if lookupBits <= 0 {
		lookupBits = 1
	}
	if tlb.usePTCLSetLookup(req) {
		lookupBits = 1
	}

	tlb.lookupReadyTimes[req.ID] = tlb.Freq.NCyclesLater(
		tlb.lookupLatencyCycles*lookupBits,
		now,
	)
	translationtrace.AddStageCycles(
		req.ID,
		"iommutlb_lookup_service",
		uint64(tlb.lookupLatencyCycles*lookupBits),
	)
}

func (tlb *TLB) isLookupReady(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
) bool {
	if req == nil || tlb.lookupLatencyCycles <= 0 {
		return true
	}

	readyTime, found := tlb.lookupReadyTimes[req.ID]
	if !found {
		return true
	}

	if readyTime > now {
		tlb.TickNow(readyTime)
		return false
	}

	delete(tlb.lookupReadyTimes, req.ID)
	return true
}

func (tlb *TLB) lookupRequestPage(req *vm.TranslationReq) (vm.Page, bool) {
	if tlb.isBitmapZero(req.BitMap) {
		return tlb.pageTable.Find(req.PID, req.VAddr)
	}

	baseVAddr := tlb.getBaseVaddr(req.VAddr)
	for i := 0; i < 8; i++ {
		if !req.BitMap[i] {
			continue
		}

		pageVAddr := baseVAddr + (uint64(i) << tlb.log2PageSize)
		page, found := tlb.pageTable.Find(req.PID, pageVAddr)
		if found {
			return page, true
		}
	}

	return vm.Page{}, false
}

func (tlb *TLB) ptclID(vAddr uint64) uint64 {
	return tlb.getBaseVaddr(vAddr) >> (tlb.log2PageSize + 3)
}

func (tlb *TLB) ptclBaseVAddr(ptclID uint64) uint64 {
	return ptclID << (tlb.log2PageSize + 3)
}

func (tlb *TLB) knownGPMs() []uint64 {
	if tlb.knownGPMsReady {
		return tlb.knownGPMIDs
	}

	tlb.knownGPMsReady = true
	if tlb.gmmuCacheTable == nil {
		return nil
	}

	ids := make([]uint64, 0, len(tlb.gmmuCacheTable.LowModules))
	for gpmID := range tlb.gmmuCacheTable.LowModules {
		ids = append(ids, gpmID)
	}

	sort.Slice(ids, func(i, j int) bool {
		return ids[i] < ids[j]
	})

	tlb.knownGPMIDs = ids
	return tlb.knownGPMIDs
}

func (tlb *TLB) sharedFinePrefix(vAddr1, vAddr2 uint64) bool {
	vpn1 := vAddr1 >> tlb.log2PageSize
	vpn2 := vAddr2 >> tlb.log2PageSize
	return (vpn1 >> 6) == (vpn2 >> 6)
}

func (tlb *TLB) bitmapsEqual(a, b [8]bool) bool {
	for i := 0; i < 8; i++ {
		if a[i] != b[i] {
			return false
		}
	}

	return true
}
