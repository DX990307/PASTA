package mmuTLB

import (
	"log"

	"github.com/sarchlab/akita/v3/mem/vm"
)

type mshrEntry struct {
	pid         vm.PID
	baseVAddr   uint64
	Requests    []*vm.TranslationReq
	reqToBottom *vm.TranslationReq
	page        vm.Page
	ready       bool
}

func (e *mshrEntry) IsReady() bool {
	return e.ready
}

type mshr interface {
	Add(pid vm.PID, addr uint64) *mshrEntry
	Remove(pid vm.PID, addr uint64) *mshrEntry
	AllEntries() []*mshrEntry
	IsFull() bool
	IsEntryFull(pid vm.PID, vAddr uint64) bool
	Reset()
	GetEntry(pid vm.PID, vAddr uint64) *mshrEntry
	IsEntryPresent(pid vm.PID, vAddr uint64) bool
	PrintStats() (uint64, uint64, uint64)
	UpdatePage(pid vm.PID, vAddr uint64, page vm.Page) bool
	MergeRequests(req *vm.TranslationReq) (*vm.TranslationReq, bool)
}

type mshrImpl struct {
	capacity     int
	entryDepth   int
	log2PageSize uint64
	entries      []*mshrEntry
}

func newMSHR(
	capacity int,
	entryDepth int,
	log2PageSize uint64,
) mshr {
	m := new(mshrImpl)
	m.capacity = capacity
	m.entryDepth = entryDepth
	m.log2PageSize = log2PageSize
	return m
}

func (m *mshrImpl) Add(pid vm.PID, vAddr uint64) *mshrEntry {
	baseVAddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == baseVAddr {
			return e
		}
	}

	if len(m.entries) >= m.capacity {
		log.Panic("MSHR is full")
	}

	entry := &mshrEntry{
		pid:       pid,
		baseVAddr: baseVAddr,
	}

	m.entries = append(m.entries, entry)
	return entry
}

func (m *mshrImpl) Remove(pid vm.PID, vAddr uint64) *mshrEntry {
	baseVAddr := m.getEntryVAddr(vAddr)
	for i, e := range m.entries {
		if e.pid == pid && e.baseVAddr == baseVAddr {
			m.entries = append(m.entries[:i], m.entries[i+1:]...)
			return e
		}
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
	baseVAddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == baseVAddr {
			return e
		}
	}
	return nil
}

func (m *mshrImpl) IsEntryPresent(pid vm.PID, vAddr uint64) bool {
	return m.GetEntry(pid, vAddr) != nil
}

func (m *mshrImpl) IsEntryFull(pid vm.PID, vAddr uint64) bool {
	entry := m.GetEntry(pid, vAddr)
	return entry != nil && len(entry.Requests) >= m.entryDepth
}

func (m *mshrImpl) PrintStats() (uint64, uint64, uint64) {
	var numEntries uint64
	var numReqs uint64
	var maxNumReqsPerEntry uint64
	for _, e := range m.entries {
		numEntries++
		numReqs += uint64(len(e.Requests))
		if uint64(len(e.Requests)) > maxNumReqsPerEntry {
			maxNumReqsPerEntry = uint64(len(e.Requests))
		}
	}
	return numEntries, numReqs, maxNumReqsPerEntry
}

func (m *mshrImpl) getEntryVAddr(vAddr uint64) uint64 {
	return (vAddr >> m.log2PageSize) << m.log2PageSize
}

func (m *mshrImpl) UpdatePage(pid vm.PID, vAddr uint64, page vm.Page) bool {
	entry := m.GetEntry(pid, vAddr)
	if entry == nil {
		return false
	}

	entry.page = page
	entry.ready = true
	return true
}

func (m *mshrImpl) MergeRequests(req *vm.TranslationReq) (*vm.TranslationReq, bool) {
	entry := m.GetEntry(req.PID, req.VAddr)
	if entry == nil {
		return nil, false
	}

	for _, r := range entry.Requests {
		if req.Src == r.Src {
			return r, true
		}
	}
	return nil, false
}
