package tlb_gmmu

import (
	"fmt"
	"sort"

	"github.com/sarchlab/akita/v3/mem/vm"
	"github.com/sarchlab/akita/v3/sim"
	"github.com/sarchlab/akita/v3/tracing"
)

type prefetchPatternState int

const (
	prefetchPatternStateCold prefetchPatternState = iota
	prefetchPatternStateClear
	prefetchPatternStateNoClear
)

type prefetchObservation struct {
	gpmID  uint64
	ptclID uint64
}

type prefetchCandidate struct {
	targetGPM uint64
	ptclID    uint64
}

type coldObservationState struct {
	buffered []prefetchObservation
	seen     map[prefetchObservation]struct{}
}

type boPrefetchLearner struct {
	boID        uint64
	perGPMPTCLs map[uint64]map[uint64]struct{}
	uniquePTCLs map[uint64]struct{}
	confirmed   bool
	state       prefetchPatternState
	baseGPM     uint64
	baseMinPTCL uint64
	intraStride int64
	interStride int64
}

type translationPrefetcher struct {
	enabled                bool
	promoteDemandToPTCL    bool
	admissionThreshold     int
	maxLearners            int
	lookahead              int
	maxCandidatesPerReq    int
	coldBOs                map[uint64]*coldObservationState
	learners               map[uint64]*boPrefetchLearner
	generatedCandidates    int
	enqueuedCandidates     int
	droppedCandidates      int
	rejectedByPrefix       int
	rejectedByDuplicate    int
	rejectedByInvalid      int
	noClearPatternSkips    int
	admittedLearnersCount  int
	promotedDemandRequests int
}

type prefetchTargetKey struct {
	pid       vm.PID
	targetGPM uint64
	baseVAddr uint64
}

type prefetchReqState struct {
	key            prefetchTargetKey
	req            *vm.TranslationReq
	remainingPages int
	pageBlock      uint64
	targetPTCL     uint64
	issueTime      sim.VTimeInSec
}

type prefetchOutcomeCounts struct {
	Enqueued      int
	Completed     int
	Useful        int
	Late          int
	LostBeforeUse int
}

type prefetchRejectReason int

const (
	prefetchRejectNone prefetchRejectReason = iota
	prefetchRejectInvalid
	prefetchRejectDuplicate
)

func newTranslationPrefetcher(
	enabled bool,
	promoteDemandToPTCL bool,
	admissionThreshold int,
	maxLearners int,
	lookahead int,
	maxCandidatesPerReq int,
) *translationPrefetcher {
	if admissionThreshold <= 0 {
		admissionThreshold = 6
	}
	if maxLearners <= 0 {
		maxLearners = 4
	}
	if lookahead <= 0 {
		lookahead = 2
	}
	if maxCandidatesPerReq <= 0 {
		maxCandidatesPerReq = 4
	}

	return &translationPrefetcher{
		enabled:             enabled,
		promoteDemandToPTCL: promoteDemandToPTCL,
		admissionThreshold:  admissionThreshold,
		maxLearners:         maxLearners,
		lookahead:           lookahead,
		maxCandidatesPerReq: maxCandidatesPerReq,
		coldBOs:             make(map[uint64]*coldObservationState),
		learners:            make(map[uint64]*boPrefetchLearner),
	}
}

func (p *translationPrefetcher) observe(boID, gpmID, ptclID uint64) *boPrefetchLearner {
	if !p.enabled {
		return nil
	}

	if learner, found := p.learners[boID]; found {
		learner.observe(gpmID, ptclID)
		return learner
	}

	state, found := p.coldBOs[boID]
	if !found {
		state = &coldObservationState{
			seen: make(map[prefetchObservation]struct{}),
		}
		p.coldBOs[boID] = state
	}

	obs := prefetchObservation{gpmID: gpmID, ptclID: ptclID}
	if _, found := state.seen[obs]; !found {
		state.seen[obs] = struct{}{}
		state.buffered = append(state.buffered, obs)
	}

	if len(state.seen) < p.admissionThreshold {
		return nil
	}

	if !p.canAdmit(boID, len(state.seen)) {
		return nil
	}

	learner := p.admit(boID)
	for _, buffered := range state.buffered {
		learner.observe(buffered.gpmID, buffered.ptclID)
	}
	delete(p.coldBOs, boID)
	return learner
}

func (p *translationPrefetcher) canAdmit(boID uint64, coldFootprint int) bool {
	if len(p.learners) < p.maxLearners {
		return true
	}

	evictID, evictFootprint := p.smallestLearner()
	if evictID == boID {
		return false
	}

	return coldFootprint > evictFootprint
}

func (p *translationPrefetcher) admit(boID uint64) *boPrefetchLearner {
	if len(p.learners) >= p.maxLearners {
		evictID, _ := p.smallestLearner()
		delete(p.learners, evictID)
	}

	learner := &boPrefetchLearner{
		boID:        boID,
		perGPMPTCLs: make(map[uint64]map[uint64]struct{}),
		uniquePTCLs: make(map[uint64]struct{}),
	}
	p.learners[boID] = learner
	p.admittedLearnersCount++
	return learner
}

func (p *translationPrefetcher) smallestLearner() (uint64, int) {
	var minID uint64
	minFootprint := -1

	for boID, learner := range p.learners {
		footprint := learner.footprint()
		if minFootprint == -1 || footprint < minFootprint {
			minID = boID
			minFootprint = footprint
		}
	}

	return minID, minFootprint
}

func (l *boPrefetchLearner) footprint() int {
	return len(l.uniquePTCLs)
}

func (l *boPrefetchLearner) observe(gpmID, ptclID uint64) {
	row, found := l.perGPMPTCLs[gpmID]
	if !found {
		row = make(map[uint64]struct{})
		l.perGPMPTCLs[gpmID] = row
	}

	row[ptclID] = struct{}{}
	l.uniquePTCLs[ptclID] = struct{}{}
	l.refreshPattern()
}

func (l *boPrefetchLearner) refreshPattern() {
	gpmIDs := make([]uint64, 0, len(l.perGPMPTCLs))
	for gpmID, ptcls := range l.perGPMPTCLs {
		if len(ptcls) >= 3 {
			gpmIDs = append(gpmIDs, gpmID)
		}
	}

	sort.Slice(gpmIDs, func(i, j int) bool {
		return gpmIDs[i] < gpmIDs[j]
	})

	l.confirmed = false
	l.state = prefetchPatternStateCold
	l.baseGPM = 0
	l.baseMinPTCL = 0
	l.intraStride = 0
	l.interStride = 0

	for i := 0; i+2 < len(gpmIDs); i++ {
		row0 := gpmIDs[i]
		row1 := gpmIDs[i+1]
		row2 := gpmIDs[i+2]
		if row1 != row0+1 || row2 != row1+1 {
			continue
		}

		table0 := firstNPTCLs(l.perGPMPTCLs[row0], 3)
		table1 := firstNPTCLs(l.perGPMPTCLs[row1], 3)
		table2 := firstNPTCLs(l.perGPMPTCLs[row2], 3)

		intra0, ok := consistentRowStride(table0)
		if !ok {
			continue
		}
		intra1, ok := consistentRowStride(table1)
		if !ok || intra1 != intra0 {
			continue
		}
		intra2, ok := consistentRowStride(table2)
		if !ok || intra2 != intra0 {
			continue
		}

		inter0 := int64(table1[0]) - int64(table0[0])
		inter1 := int64(table2[0]) - int64(table1[0])
		if inter0 != inter1 {
			continue
		}

		l.confirmed = true
		l.state = prefetchPatternStateClear
		l.baseGPM = row0
		l.baseMinPTCL = table0[0]
		l.intraStride = intra0
		l.interStride = inter0
		return
	}

	for _, row := range gpmIDs {
		table := firstNPTCLs(l.perGPMPTCLs[row], 3)
		intra, ok := consistentRowStride(table)
		if !ok || intra == 0 {
			continue
		}

		l.confirmed = true
		l.state = prefetchPatternStateClear
		l.baseGPM = row
		l.baseMinPTCL = table[0]
		l.intraStride = intra
		l.interStride = 0
		return
	}

	if l.hasSufficientNoClearEvidence(gpmIDs) {
		l.state = prefetchPatternStateNoClear
	}
}

func (l *boPrefetchLearner) hasClearPattern() bool {
	if l == nil {
		return false
	}

	return l.state == prefetchPatternStateClear
}

func (l *boPrefetchLearner) hasNoClearPattern() bool {
	if l == nil {
		return false
	}

	return l.state == prefetchPatternStateNoClear
}

func (l *boPrefetchLearner) hasSufficientNoClearEvidence(gpmIDs []uint64) bool {
	if len(gpmIDs) < 3 {
		return false
	}

	runLength := 1
	bestRunLength := 1
	for i := 1; i < len(gpmIDs); i++ {
		if gpmIDs[i] == gpmIDs[i-1]+1 {
			runLength++
		} else {
			runLength = 1
		}

		if runLength > bestRunLength {
			bestRunLength = runLength
		}
	}

	return bestRunLength >= 3
}

func (l *boPrefetchLearner) predict(
	currentGPM uint64,
	currentPTCL uint64,
	allGPMs []uint64,
	lookahead int,
) []prefetchCandidate {
	if !l.hasClearPattern() || lookahead <= 0 || l.intraStride == 0 {
		return nil
	}

	candidates := make([]prefetchCandidate, 0, lookahead+len(allGPMs))
	seen := make(map[prefetchCandidate]struct{})

	for k := 1; k <= lookahead; k++ {
		predicted := int64(currentPTCL) + int64(k)*l.intraStride
		if predicted < 0 {
			continue
		}

		candidate := prefetchCandidate{
			targetGPM: currentGPM,
			ptclID:    uint64(predicted),
		}
		if _, found := seen[candidate]; !found {
			seen[candidate] = struct{}{}
			candidates = append(candidates, candidate)
		}
	}

	currentOffset, ok := l.patternOffset(currentGPM, currentPTCL)
	if !ok {
		return candidates
	}

	for _, gpmID := range allGPMs {
		if gpmID == currentGPM {
			continue
		}

		startPTCL := l.predictStart(gpmID)
		predicted := startPTCL + int64(currentOffset)*l.intraStride
		if predicted < 0 {
			continue
		}

		candidate := prefetchCandidate{
			targetGPM: gpmID,
			ptclID:    uint64(predicted),
		}
		if _, found := seen[candidate]; found {
			continue
		}

		seen[candidate] = struct{}{}
		candidates = append(candidates, candidate)
	}

	return candidates
}

func (l *boPrefetchLearner) patternOffset(gpmID uint64, ptclID uint64) (int, bool) {
	start := l.predictStart(gpmID)
	delta := int64(ptclID) - start
	if delta < 0 {
		return 0, false
	}
	if l.intraStride == 0 || delta%l.intraStride != 0 {
		return 0, false
	}

	return int(delta / l.intraStride), true
}

func (l *boPrefetchLearner) predictStart(gpmID uint64) int64 {
	return int64(l.baseMinPTCL) + int64(gpmID-l.baseGPM)*l.interStride
}

func firstNPTCLs(ptcls map[uint64]struct{}, n int) []uint64 {
	values := make([]uint64, 0, len(ptcls))
	for ptclID := range ptcls {
		values = append(values, ptclID)
	}

	sort.Slice(values, func(i, j int) bool {
		return values[i] < values[j]
	})

	if len(values) > n {
		values = values[:n]
	}

	return values
}

func consistentRowStride(values []uint64) (int64, bool) {
	if len(values) < 3 {
		return 0, false
	}

	stride := int64(values[1]) - int64(values[0])
	for i := 1; i+1 < len(values); i++ {
		if int64(values[i+1])-int64(values[i]) != stride {
			return 0, false
		}
	}

	return stride, true
}

func (tlb *GMMUTLB) initPrefetchState() {
	if tlb.inflightPrefetches == nil {
		tlb.inflightPrefetches = make(map[prefetchTargetKey]struct{})
	}
	if tlb.prefetchReqStates == nil {
		tlb.prefetchReqStates = make(map[string]*prefetchReqState)
	}
	if tlb.prefetchOutcomeByBlock == nil {
		tlb.prefetchOutcomeByBlock = make(map[uint64]*prefetchOutcomeCounts)
	}
}

func (tlb *GMMUTLB) resetPrefetchState() {
	tlb.initPrefetchState()
	clear(tlb.inflightPrefetches)
	clear(tlb.prefetchReqStates)
	clear(tlb.prefetchOutcomeByBlock)
	tlb.prefetchCompletedCount = 0
}

func (tlb *GMMUTLB) maybeEnqueuePrefetches(
	now sim.VTimeInSec,
	req *vm.TranslationReq,
) {
	if tlb.prefetcher == nil || !tlb.prefetcher.enabled || req == nil || req.IsPrefetch {
		return
	}

	page, found := tlb.lookupRequestPage(req)
	if !found {
		return
	}

	previousLearner, hadLearner := tlb.prefetcher.learners[page.PageBlock]
	beforeClear := hadLearner && previousLearner != nil && previousLearner.hasClearPattern()
	beforeNoClear := hadLearner && previousLearner != nil && previousLearner.hasNoClearPattern()
	beforeBaseGPM := uint64(0)
	beforeBaseMinPTCL := uint64(0)
	beforeIntraStride := int64(0)
	beforeInterStride := int64(0)
	if hadLearner && previousLearner != nil {
		beforeBaseGPM = previousLearner.baseGPM
		beforeBaseMinPTCL = previousLearner.baseMinPTCL
		beforeIntraStride = previousLearner.intraStride
		beforeInterStride = previousLearner.interStride
	}

	learner := tlb.prefetcher.observe(page.PageBlock, tlb.DeviceID, tlb.ptclID(req.VAddr))
	if learner == nil {
		return
	}

	afterClear := learner.hasClearPattern()
	afterNoClear := learner.hasNoClearPattern()
	patternParametersChanged := beforeClear && afterClear &&
		(beforeBaseGPM != learner.baseGPM ||
			beforeBaseMinPTCL != learner.baseMinPTCL ||
			beforeIntraStride != learner.intraStride ||
			beforeInterStride != learner.interStride)
	switch {
	case !beforeClear && afterClear:
		tlb.printPrefetcherGateEvent(now, page.PageBlock, "enable", "pattern-confirmed", learner)
	case !beforeNoClear && afterNoClear:
		tlb.printPrefetcherGateEvent(now, page.PageBlock, "disable", "no-clear-pattern", learner)
	case patternParametersChanged:
		tlb.printPrefetcherGateEvent(now, page.PageBlock, "update", "pattern-parameters-changed", learner)
	}

	if !afterClear {
		if afterNoClear {
			tlb.prefetcher.noClearPatternSkips++
		}
		return
	}

	candidates := learner.predict(
		tlb.DeviceID,
		tlb.ptclID(req.VAddr),
		[]uint64{tlb.DeviceID},
		tlb.prefetcher.lookahead,
	)

	selected := 0
	for _, candidate := range candidates {
		if selected >= tlb.prefetcher.maxCandidatesPerReq {
			break
		}

		tlb.prefetcher.generatedCandidates++

		if !tlb.sharedFinePrefix(req.VAddr, tlb.ptclBaseVAddr(candidate.ptclID)) {
			tlb.prefetcher.droppedCandidates++
			tlb.prefetcher.rejectedByPrefix++
			continue
		}

		reason := tlb.prefetchRejectReason(req.PID, candidate.targetGPM, candidate.ptclID)
		if reason != prefetchRejectNone {
			tlb.prefetcher.droppedCandidates++
			switch reason {
			case prefetchRejectDuplicate:
				tlb.prefetcher.rejectedByDuplicate++
			default:
				tlb.prefetcher.rejectedByInvalid++
			}
			continue
		}

		if tlb.enqueuePrefetchRequest(now, req, page.PageBlock, candidate) {
			tlb.prefetcher.enqueuedCandidates++
			selected++
			continue
		}

		tlb.prefetcher.droppedCandidates++
	}
}

func (tlb *GMMUTLB) prefetchRejectReason(
	pid vm.PID,
	targetGPM uint64,
	ptclID uint64,
) prefetchRejectReason {
	if targetGPM != tlb.DeviceID {
		return prefetchRejectInvalid
	}

	baseVAddr := tlb.ptclBaseVAddr(ptclID)
	bitmap := tlb.filterMappedBitmap(pid, baseVAddr, tlb.fullBitmap())
	if tlb.isBitmapZero(bitmap) {
		return prefetchRejectInvalid
	}

	resident := tlb.residentBitmap(pid, baseVAddr, bitmap)
	if tlb.isBitmapZero(tlb.subtractBitmaps(bitmap, resident)) {
		return prefetchRejectDuplicate
	}

	if tlb.mshr.GetEntry(pid, baseVAddr) != nil {
		return prefetchRejectDuplicate
	}

	if tlb.hasRespondingRequestForTarget(pid, baseVAddr) {
		return prefetchRejectDuplicate
	}

	if tlb.hasPendingPTELookupForTarget(pid, baseVAddr) {
		return prefetchRejectDuplicate
	}

	if tlb.hasInflightPrefetchForTarget(pid, targetGPM, baseVAddr) {
		return prefetchRejectDuplicate
	}

	return prefetchRejectNone
}

func (tlb *GMMUTLB) enqueuePrefetchRequest(
	now sim.VTimeInSec,
	demandReq *vm.TranslationReq,
	pageBlock uint64,
	candidate prefetchCandidate,
) bool {
	if candidate.targetGPM != tlb.DeviceID {
		return false
	}

	baseVAddr := tlb.ptclBaseVAddr(candidate.ptclID)
	bitmap := tlb.filterMappedBitmap(demandReq.PID, baseVAddr, tlb.fullBitmap())
	bitmap = tlb.subtractBitmaps(bitmap, tlb.residentBitmap(demandReq.PID, baseVAddr, bitmap))
	if tlb.isBitmapZero(bitmap) {
		return false
	}

	prefetchReq := vm.TranslationReqBuilder{}.
		WithSendTime(now).
		WithSrc(tlb.topPort).
		WithDst(tlb.topPort).
		WithPID(demandReq.PID).
		WithVAddr(baseVAddr).
		WithDeviceID(tlb.DeviceID).
		WithTaskID(sim.GetIDGenerator().Generate()).
		WithOriginPort(tlb.topPort).
		WithBitMap(bitmap).
		WithPrefetch(true).
		Build()
	prefetchReq.StartGPUID = int(tlb.DeviceID)

	reqToBottom, ok := tlb.sendDownstream(now, prefetchReq, bitmap)
	if !ok || reqToBottom == nil {
		return false
	}

	tlb.registerInflightPrefetch(
		prefetchReq,
		reqToBottom,
		pageBlock,
		candidate.ptclID,
		now,
	)
	tracing.TraceReqReceive(prefetchReq, tlb)
	tracing.AddTaskStep(tracing.MsgIDAtReceiver(prefetchReq, tlb), tlb, "prefetch-miss")
	if reqToBottom != nil {
		tracing.TraceReqInitiate(reqToBottom, tlb, tracing.MsgIDAtReceiver(prefetchReq, tlb))
	}
	return true
}

func (tlb *GMMUTLB) registerInflightPrefetch(
	req *vm.TranslationReq,
	reqToBottom *vm.TranslationReq,
	pageBlock uint64,
	targetPTCL uint64,
	now sim.VTimeInSec,
) {
	if req == nil || reqToBottom == nil || !reqToBottom.IsPrefetch {
		return
	}

	remainingPages := tlb.bitmapCount(reqToBottom.BitMap)
	if remainingPages == 0 {
		return
	}

	key := prefetchTargetKey{
		pid:       reqToBottom.PID,
		targetGPM: reqToBottom.DeviceID,
		baseVAddr: tlb.getBaseVaddr(reqToBottom.VAddr),
	}

	tlb.initPrefetchState()
	tlb.inflightPrefetches[key] = struct{}{}
	tlb.prefetchReqStates[reqToBottom.ID] = &prefetchReqState{
		key:            key,
		req:            req,
		remainingPages: remainingPages,
		pageBlock:      pageBlock,
		targetPTCL:     targetPTCL,
		issueTime:      now,
	}
	tlb.prefetchOutcomeState(pageBlock).Enqueued++
}

func (tlb *GMMUTLB) handlePrefetchRsp(
	now sim.VTimeInSec,
	rsp *vm.TranslationRsp,
) bool {
	if rsp == nil || !rsp.IsPrefetch {
		return false
	}

	if !tlb.installPage(rsp.Page) {
		return false
	}

	tlb.completeInflightPrefetchRsp(now, rsp)
	return true
}

func (tlb *GMMUTLB) completeInflightPrefetchRsp(
	now sim.VTimeInSec,
	rsp *vm.TranslationRsp,
) {
	if rsp == nil || !rsp.IsPrefetch {
		return
	}

	state, found := tlb.prefetchReqStates[rsp.RespondTo]
	if !found {
		return
	}

	state.remainingPages--
	if state.remainingPages > 0 {
		return
	}

	delete(tlb.prefetchReqStates, rsp.RespondTo)
	delete(tlb.inflightPrefetches, state.key)
	tlb.prefetchCompletedCount++
	tlb.prefetchOutcomeState(state.pageBlock).Completed++

	if state.req != nil {
		tracing.TraceReqComplete(state.req, tlb)
	}

	_ = now
}

func (tlb *GMMUTLB) prefetchOutcomeState(pageBlock uint64) *prefetchOutcomeCounts {
	tlb.initPrefetchState()
	state, found := tlb.prefetchOutcomeByBlock[pageBlock]
	if !found {
		state = &prefetchOutcomeCounts{}
		tlb.prefetchOutcomeByBlock[pageBlock] = state
	}

	return state
}

func (tlb *GMMUTLB) lookupRequestPage(req *vm.TranslationReq) (vm.Page, bool) {
	if req == nil {
		return vm.Page{}, false
	}

	if tlb.isBitmapZero(req.BitMap) {
		return tlb.pageTable.Find(req.PID, req.VAddr)
	}

	baseVAddr := tlb.getBaseVaddr(req.VAddr)
	for i := 0; i < 8; i++ {
		if !req.BitMap[i] {
			continue
		}

		pageVAddr := baseVAddr + (uint64(i) << tlb.log2Pagesize)
		page, found := tlb.pageTable.Find(req.PID, pageVAddr)
		if found {
			return page, true
		}
	}

	return vm.Page{}, false
}

func (tlb *GMMUTLB) ptclBaseVAddr(ptclID uint64) uint64 {
	return ptclID << (tlb.log2Pagesize + 3)
}

func (tlb *GMMUTLB) residentBitmap(
	pid vm.PID,
	vAddr uint64,
	bitmap [8]bool,
) [8]bool {
	baseVAddr := tlb.getBaseVaddr(vAddr)
	resident := [8]bool{}

	for i := 0; i < 8; i++ {
		if !bitmap[i] {
			continue
		}

		pageVAddr := baseVAddr + (uint64(i) << tlb.log2Pagesize)
		setID := tlb.vAddrToSetID(pageVAddr)
		_, page, found := tlb.Sets[setID].Lookup(pid, pageVAddr)
		if found && page.Valid {
			resident[i] = true
		}
	}

	return resident
}

func (tlb *GMMUTLB) hasRespondingRequestForTarget(
	pid vm.PID,
	baseVAddr uint64,
) bool {
	key := tlb.pteLookupGroupKey(pid, baseVAddr)
	for _, entry := range tlb.respondingMSHREntry {
		if entry.pid == key.pid && entry.baseVAddr == key.baseVAddr {
			return true
		}
	}

	return false
}

func (tlb *GMMUTLB) hasPendingPTELookupForTarget(
	pid vm.PID,
	baseVAddr uint64,
) bool {
	key := tlb.pteLookupGroupKey(pid, baseVAddr)
	if _, found := tlb.pteLookupGroups[key]; found {
		return true
	}
	if _, found := tlb.ptclRepresentativeMiss[key]; found {
		return true
	}

	return false
}

func (tlb *GMMUTLB) hasInflightPrefetchForTarget(
	pid vm.PID,
	targetGPM uint64,
	baseVAddr uint64,
) bool {
	_, found := tlb.inflightPrefetches[prefetchTargetKey{
		pid:       pid,
		targetGPM: targetGPM,
		baseVAddr: baseVAddr,
	}]
	return found
}

func (tlb *GMMUTLB) sharedFinePrefix(vAddr1, vAddr2 uint64) bool {
	vpn1 := vAddr1 >> tlb.log2Pagesize
	vpn2 := vAddr2 >> tlb.log2Pagesize
	return (vpn1 >> 6) == (vpn2 >> 6)
}

func (tlb *GMMUTLB) printPrefetcherGateEvent(
	now sim.VTimeInSec,
	pageBlock uint64,
	action string,
	reason string,
	learner *boPrefetchLearner,
) {
	confirmed := false
	state := prefetchPatternStateCold
	baseGPM := uint64(0)
	baseMinPTCL := uint64(0)
	intraStride := int64(0)
	interStride := int64(0)
	footprint := 0

	if learner != nil {
		confirmed = learner.confirmed
		state = learner.state
		baseGPM = learner.baseGPM
		baseMinPTCL = learner.baseMinPTCL
		intraStride = learner.intraStride
		interStride = learner.interStride
		footprint = learner.footprint()
	}

	fmt.Printf("[GMMU-PF][gate] cycle=%d component=%s page_block=%d action=%s reason=%s confirmed=%t state=%d base_gpm=%d base_min_ptcl=%d intra_stride=%d inter_stride=%d footprint=%d\n",
		uint64(now*1e9), tlb.Name(), pageBlock, action, reason, confirmed, state,
		baseGPM, baseMinPTCL, intraStride, interStride, footprint)
}
