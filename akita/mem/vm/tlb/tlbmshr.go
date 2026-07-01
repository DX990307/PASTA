package tlb

import (
	"log"

	"github.com/sarchlab/akita/v3/mem/vm"
)

type mshrEntry struct {
	pid         vm.PID
	vAddr       uint64
	Requests    []*vm.TranslationReq
	reqToBottom *vm.TranslationReq
	page        vm.Page

	latpc            bool
	latpcBaseVAddr   uint64
	latpcStridePages int64
	latpcValidMask   uint32
	latpcRequests    map[uint64][]*vm.TranslationReq
	latpcReqToBottom map[uint64]*vm.TranslationReq
}

// newMSHREntry returns a new MSHR entry object
func newMSHREntry() *mshrEntry {
	e := new(mshrEntry)
	return e
}

func (e *mshrEntry) containsVAddr(vAddr uint64) bool {
	if !e.latpc {
		return e.vAddr == vAddr
	}

	_, ok := e.latpcRequests[vAddr]
	return ok
}

func (e *mshrEntry) representsLATPCGroup(req *vm.TranslationReq) bool {
	if !e.latpc || req == nil || !req.LATPCValid {
		return false
	}
	if req.LATPCStridePages == 0 || req.LATPCIndex >= 32 {
		return false
	}
	return e.pid == req.PID &&
		e.latpcBaseVAddr == req.LATPCBaseVAddr &&
		e.latpcStridePages == req.LATPCStridePages
}

func (e *mshrEntry) addRequest(req *vm.TranslationReq) {
	if !e.latpc {
		e.Requests = append(e.Requests, req)
		return
	}

	if e.latpcRequests == nil {
		e.latpcRequests = make(map[uint64][]*vm.TranslationReq)
	}
	e.latpcRequests[req.VAddr] = append(e.latpcRequests[req.VAddr], req)
	e.latpcValidMask |= 1 << req.LATPCIndex
}

func (e *mshrEntry) recordBottomReq(req, reqToBottom *vm.TranslationReq) {
	if !e.latpc {
		e.reqToBottom = reqToBottom
		return
	}

	if e.latpcReqToBottom == nil {
		e.latpcReqToBottom = make(map[uint64]*vm.TranslationReq)
	}
	e.latpcReqToBottom[req.VAddr] = reqToBottom
}

func (e *mshrEntry) pendingCount() int {
	if !e.latpc {
		if len(e.Requests) == 0 {
			return 0
		}
		return 1
	}

	return len(e.latpcRequests)
}

// mshr is an interface that controls MSHR entries
type mshr interface {
	Query(pid vm.PID, addr uint64) *mshrEntry
	CanAddReq(req *vm.TranslationReq) bool
	AddReq(req *vm.TranslationReq) (*mshrEntry, bool)
	Ready(pid vm.PID, vAddr uint64, page vm.Page) (*mshrEntry, bool)
	Add(pid vm.PID, addr uint64) *mshrEntry
	Remove(pid vm.PID, addr uint64) *mshrEntry
	AllEntries() []*mshrEntry
	IsFull() bool
	Reset()
	GetEntry(pid vm.PID, vAddr uint64) *mshrEntry
	IsEntryPresent(pid vm.PID, vAddr uint64) bool
}

type mshrImpl struct {
	capacity     int
	entries      []*mshrEntry
	latpcEnabled bool
}

// newMSHR returns a new mshr object
func newMSHR(capacity int, latpcEnabled bool) mshr {
	m := new(mshrImpl)
	m.capacity = capacity
	m.latpcEnabled = latpcEnabled
	return m
}

func (m *mshrImpl) latpcEligible(req *vm.TranslationReq) bool {
	return m.latpcEnabled &&
		req != nil &&
		req.LATPCValid &&
		req.LATPCStridePages != 0 &&
		req.LATPCIndex < 32
}

func (m *mshrImpl) findLATPCGroup(req *vm.TranslationReq) *mshrEntry {
	if !m.latpcEligible(req) {
		return nil
	}
	for _, e := range m.entries {
		if e.representsLATPCGroup(req) {
			return e
		}
	}
	return nil
}

func (m *mshrImpl) CanAddReq(req *vm.TranslationReq) bool {
	if req == nil {
		return false
	}
	if m.Query(req.PID, req.VAddr) != nil {
		return true
	}
	if m.findLATPCGroup(req) != nil {
		return true
	}
	return len(m.entries) < m.capacity
}

func (m *mshrImpl) AddReq(req *vm.TranslationReq) (*mshrEntry, bool) {
	if req == nil {
		panic("cannot add nil request to mshr")
	}
	if e := m.Query(req.PID, req.VAddr); e != nil {
		return e, false
	}
	if e := m.findLATPCGroup(req); e != nil {
		e.addRequest(req)
		return e, true
	}

	if len(m.entries) >= m.capacity {
		log.Panic("MSHR is full")
	}

	entry := newMSHREntry()
	entry.pid = req.PID
	entry.vAddr = req.VAddr
	if m.latpcEligible(req) {
		entry.latpc = true
		entry.latpcBaseVAddr = req.LATPCBaseVAddr
		entry.latpcStridePages = req.LATPCStridePages
		entry.latpcRequests = make(map[uint64][]*vm.TranslationReq)
		entry.latpcReqToBottom = make(map[uint64]*vm.TranslationReq)
	}
	entry.addRequest(req)
	m.entries = append(m.entries, entry)
	return entry, false
}

func (m *mshrImpl) Add(pid vm.PID, vAddr uint64) *mshrEntry {
	for _, e := range m.entries {
		if e.pid == pid && e.containsVAddr(vAddr) {
			panic("entry already in mshr")
		}
	}

	if len(m.entries) >= m.capacity {
		log.Panic("MSHR is full")
	}

	entry := newMSHREntry()
	entry.pid = pid
	entry.vAddr = vAddr
	m.entries = append(m.entries, entry)
	return entry
}

func (m *mshrImpl) Query(pid vm.PID, vAddr uint64) *mshrEntry {
	for _, e := range m.entries {
		if e.pid == pid && e.containsVAddr(vAddr) {
			return e
		}
	}
	return nil
}

func (m *mshrImpl) Ready(
	pid vm.PID,
	vAddr uint64,
	page vm.Page,
) (*mshrEntry, bool) {
	for i, e := range m.entries {
		if e.pid != pid || !e.containsVAddr(vAddr) {
			continue
		}

		if !e.latpc {
			e.page = page
			m.entries = append(m.entries[:i], m.entries[i+1:]...)
			return e, true
		}

		requests := append([]*vm.TranslationReq(nil), e.latpcRequests[vAddr]...)
		ready := newMSHREntry()
		ready.pid = pid
		ready.vAddr = vAddr
		ready.Requests = requests
		ready.reqToBottom = e.latpcReqToBottom[vAddr]
		ready.page = page

		if len(requests) > 0 && requests[0].LATPCIndex < 32 {
			e.latpcValidMask &^= 1 << requests[0].LATPCIndex
		}
		delete(e.latpcRequests, vAddr)
		delete(e.latpcReqToBottom, vAddr)
		if e.pendingCount() == 0 {
			m.entries = append(m.entries[:i], m.entries[i+1:]...)
		}
		return ready, true
	}

	return nil, false
}

func (m *mshrImpl) Remove(pid vm.PID, vAddr uint64) *mshrEntry {
	for i, e := range m.entries {
		if e.pid != pid || !e.containsVAddr(vAddr) {
			continue
		}

		if !e.latpc {
			m.entries = append(m.entries[:i], m.entries[i+1:]...)
			return e
		}

		delete(e.latpcRequests, vAddr)
		delete(e.latpcReqToBottom, vAddr)
		if e.pendingCount() == 0 {
			m.entries = append(m.entries[:i], m.entries[i+1:]...)
		}
		return e
	}
	panic("trying to remove an non-exist entry")
}

func (m *mshrImpl) AllEntries() []*mshrEntry {
	return m.entries
}

func (m *mshrImpl) IsFull() bool {
	return len(m.entries) >= m.capacity
}

func (m *mshrImpl) Reset() {
	m.entries = nil
}

func (m *mshrImpl) GetEntry(pid vm.PID, vAddr uint64) *mshrEntry {
	return m.Query(pid, vAddr)
}

func (m *mshrImpl) IsEntryPresent(pid vm.PID, vAddr uint64) bool {
	return m.Query(pid, vAddr) != nil
}
