package tlb_gmmu

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
	IsFull() bool
	IsEntryFull(pid vm.PID, vAddr uint64) bool
	Reset()
	GetEntry(pid vm.PID, vAddr uint64) *mshrEntry
	IsEntryPresent(pid vm.PID, vAddr uint64) bool
	UpdatePage(pid vm.PID, vAddr uint64, page vm.Page) bool
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
	if entry, _ := m.findEntry(pid, vAddr); entry != nil {
		return entry
	}

	if len(m.entries) >= m.capacity {
		log.Panic("MSHR is full")
	}

	entry := &mshrEntry{
		pid:       pid,
		baseVAddr: m.getEntryVAddr(vAddr),
	}

	m.entries = append(m.entries, entry)
	return entry
}

func (m *mshrImpl) Remove(pid vm.PID, vAddr uint64) *mshrEntry {
	entry, index := m.findEntry(pid, vAddr)
	if entry == nil {
		panic("trying to remove an non-exist entry")
	}

	m.entries = append(m.entries[:index], m.entries[index+1:]...)
	return entry
}

func (m *mshrImpl) IsFull() bool {
	return len(m.entries) >= m.capacity
}

func (m *mshrImpl) Reset() {
	m.entries = nil
}

func (m *mshrImpl) GetEntry(pid vm.PID, vAddr uint64) *mshrEntry {
	entry, _ := m.findEntry(pid, vAddr)
	return entry
}

func (m *mshrImpl) IsEntryPresent(pid vm.PID, vAddr uint64) bool {
	entry, _ := m.findEntry(pid, vAddr)
	return entry != nil
}

func (m *mshrImpl) IsEntryFull(pid vm.PID, vAddr uint64) bool {
	entry, _ := m.findEntry(pid, vAddr)
	return entry != nil && len(entry.Requests) >= m.entryDepth
}

func (m *mshrImpl) getEntryVAddr(vAddr uint64) uint64 {
	return (vAddr >> m.log2PageSize) << m.log2PageSize
}

func (m *mshrImpl) findEntry(pid vm.PID, vAddr uint64) (*mshrEntry, int) {
	baseVAddr := m.getEntryVAddr(vAddr)
	for i, e := range m.entries {
		if e.pid == pid && e.baseVAddr == baseVAddr {
			return e, i
		}
	}

	return nil, -1
}

func (m *mshrImpl) UpdatePage(pid vm.PID, vAddr uint64, page vm.Page) bool {
	entry, _ := m.findEntry(pid, vAddr)
	if entry == nil {
		return false
	}

	entry.page = page
	entry.ready = true
	return true
}
