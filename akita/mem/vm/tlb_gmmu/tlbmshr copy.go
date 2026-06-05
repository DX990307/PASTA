package tlb_gmmu

import (
	"fmt"
	"log"

	"github.com/sarchlab/akita/v3/mem/vm"
	"github.com/sarchlab/akita/v3/sim"
)

type mshrEntry struct {
	pid               vm.PID
	baseVAddr         uint64
	UplevelBitMap     [8]bool
	UplevelBitMapTime [8]int
	IssuedBitMap      [8]bool
	ResponseBitMap    [8]bool
	Requests          []*vm.TranslationReq
	reqToBottom       *vm.TranslationReq
	Pages             [8]vm.Page // 替换为固定大小的 array
	RealAddrBitmap    [8]bool
	TranslatedBitmap  [8]bool
	// startPage     [8]int
}

// newMSHREntry returns a new MSHR entry object
func newMSHREntry() *mshrEntry {
	e := new(mshrEntry)
	return e
}

// IsReady 检查当前 mshrEntry 的页面 VPN 是否连续
func (e *mshrEntry) IsReady() bool {
	for i := 0; i < 8; i++ {
		if e.UplevelBitMap[i] && !e.ResponseBitMap[i] {
			return false // 如果 UplevelBitMap[i] 为 true，但 ResponseBitMap[i] 为 false，则页面不连续
		}
	}

	return true // 所有检查通过，页面连续且有效
}

// mshr is an interface that controls MSHR entries
type mshr interface {
	Add(pid vm.PID, addr uint64, now sim.VTimeInSec, predictRadius int) *mshrEntry
	Remove(pid vm.PID, addr uint64) *mshrEntry
	AllEntries() []*mshrEntry
	IsFull() bool
	IsEntryFull(pid vm.PID, vAddr uint64) bool
	Reset()
	GetEntry(pid vm.PID, vAddr uint64) *mshrEntry
	IsEntryPresent(pid vm.PID, vAddr uint64) bool
	PrintStats() (uint64, uint64, uint64)
	GetUpLevelBitMap(pid vm.PID, vAddr uint64) ([8]bool, bool)
	UpdateUpLevelBitMap(pid vm.PID, vAddr uint64, now sim.VTimeInSec, predictRadius int) bool
	UpdatePage(pid vm.PID, vAddr uint64, page vm.Page) bool
	GetPages(pid vm.PID, vAddr uint64) ([8]vm.Page, bool)
	IsReady(pid vm.PID, vAddr uint64) bool
	GetNonZeroElementsfromBitmap(pid vm.PID, vaddr uint64) int
	UpdateResponseBitMap(pid vm.PID, vAddr uint64) bool
	GetResponseBitMap(pid vm.PID, vAddr uint64) ([8]bool, bool)
	IsPredicted(pid vm.PID, vAddr uint64) bool
	// UpdateBitMap(pid vm.PID, vAddr uint64) bool
}

type mshrImpl struct {
	capacity     int
	entryDepth   int
	log2PageSize uint64
	perVPNMSHR   bool
	entries      []*mshrEntry
}

// newMSHR returns a new mshr object
func newMSHR(
	capacity int,
	entryDepth int,
	log2PageSize uint64,
	perVPNMSHR bool,
) mshr {
	m := new(mshrImpl)
	m.capacity = capacity
	m.entryDepth = entryDepth
	m.log2PageSize = log2PageSize
	m.perVPNMSHR = perVPNMSHR

	return m
}

func (m *mshrImpl) Add(pid vm.PID, vAddr uint64, now sim.VTimeInSec, predictRadius int) *mshrEntry {
	BaseVaddr := m.getEntryVAddr(vAddr)
	VPN := vAddr >> m.log2PageSize
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			m.UpdateUpLevelBitMap(pid, vAddr, now, predictRadius)
			return e
		}
	}

	if len(m.entries) >= m.capacity {
		log.Panic("MSHR is full")
	}

	entry := newMSHREntry()
	entry.pid = pid
	entry.baseVAddr = BaseVaddr // 添加缺失的 baseVAddr 设置
	bitMap := [8]bool{}
	TimeBitMap := [8]int{}
	RealAddrBitmap := [8]bool{}

	for i := 0; i < 8; i++ {
		TimeBitMap[i] = 0
	}

	for i := 0; i < 8; i++ {
		bitMap[i] = false
	}

	bitMap[VPN%8] = true
	RealAddrBitmap[VPN%8] = true
	TimeBitMap[VPN%8] = int(now * 1e9) // 将时间转换为纳秒存储为整数

	entry.UplevelBitMap = bitMap
	entry.UplevelBitMapTime = TimeBitMap
	entry.RealAddrBitmap = RealAddrBitmap

	m.entries = append(m.entries, entry)
	return entry
}

func (m *mshrImpl) Remove(pid vm.PID, vAddr uint64) *mshrEntry {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for i, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
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
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			return e
		}
	}
	return nil
}

func (m *mshrImpl) IsEntryPresent(pid vm.PID, vAddr uint64) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			return true
		}
	}
	return false
}

func (m *mshrImpl) IsEntryFull(pid vm.PID, vAddr uint64) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			return len(e.Requests) >= m.entryDepth
		}
	}
	return false
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

func (m *mshrImpl) getBaseVaddr(vAddr uint64) uint64 {
	VPN := vAddr >> m.log2PageSize
	BaseVAddr := (VPN >> 3) << 3 // Clear the lower 3 bits to get the base VPN
	return BaseVAddr << m.log2PageSize
}

func (m *mshrImpl) getEntryVAddr(vAddr uint64) uint64 {
	if m.perVPNMSHR {
		return (vAddr >> m.log2PageSize) << m.log2PageSize
	}

	return m.getBaseVaddr(vAddr)
}

func (m *mshrImpl) GetUpLevelBitMap(pid vm.PID, vAddr uint64) ([8]bool, bool) {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			return e.UplevelBitMap, true
		}
	}

	return [8]bool{}, false
}

func (m *mshrImpl) UpdateUpLevelBitMap(pid vm.PID, vAddr uint64, now sim.VTimeInSec, predictRadius int) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			bitmap := e.UplevelBitMap
			timeBitmap := e.UplevelBitMapTime
			realAddrBitmap := e.RealAddrBitmap
			VPN := vAddr >> m.log2PageSize

			bitmap[VPN%8] = true
			timeBitmap[VPN%8] = int(now * 1e9)
			realAddrBitmap[VPN%8] = true

			e.UplevelBitMapTime = timeBitmap
			e.UplevelBitMap = bitmap
			e.RealAddrBitmap = realAddrBitmap
			return true
		}
	}
	return false
}

func (m *mshrImpl) SavePage(Page vm.Page) bool {
	BaseVaddr := m.getEntryVAddr(Page.VAddr)
	for _, e := range m.entries {
		if e.pid == Page.PID && e.baseVAddr == BaseVaddr {
			// 计算在 8 页数组中的索引 (0-7)
			VPN := Page.VAddr >> m.log2PageSize
			BaseVPN := BaseVaddr >> m.log2PageSize
			offset := int(VPN - BaseVPN)
			if offset >= 0 && offset < 8 {
				e.Pages[offset] = Page
				e.ResponseBitMap[offset] = true
				return true
			}
			fmt.Printf("Invalid offset %d for vAddr\n", offset)
		}
	}
	return false
}

func (m *mshrImpl) getVaddrOffset(vAddr uint64) int {
	VPN := vAddr >> m.log2PageSize
	BaseVPN := (VPN >> 3) << 3 // Clear the lower 3 bits to get the base VPN
	return int(VPN - BaseVPN)  // 返回 0-7 的索引
}

func (m *mshrImpl) UpdatePage(pid vm.PID, vAddr uint64, page vm.Page) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			// 计算在 8 页数组中的索引 (0-7)
			offset := m.getVaddrOffset(vAddr)
			if offset >= 0 && offset < 8 {
				e.Pages[offset] = page
				e.ResponseBitMap[offset] = true
				return true
			}
			fmt.Printf("Invalid offset %d for vAddr %d in MSHR for PID %d\n", offset, vAddr, pid)
		}
	}
	return false
}

func (m *mshrImpl) GetPages(pid vm.PID, vAddr uint64) ([8]vm.Page, bool) {
	var pages [8]vm.Page
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			// 将指针数组转换为值数组
			for i := 0; i < 8; i++ {
				if e.Pages[i] != (vm.Page{}) {
					pages[i] = e.Pages[i]
				}
				// 如果 e.Page[i] 是零值，pages[i] 保持零值
			}
			return pages, true
		}
	}
	return pages, false
}

func (m *mshrImpl) IsReady(pid vm.PID, vAddr uint64) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			return e.IsReady()
		}
	}
	return false // 找不到对应的 MSHR 条目
}

func (m *mshrImpl) GetNonZeroElementsfromBitmap(pid vm.PID, vaddr uint64) int {
	BaseVAddr := m.getEntryVAddr(vaddr)
	for _, e := range m.entries {
		if e.baseVAddr == BaseVAddr && e.pid == pid {
			count := 0
			for _, bit := range e.RealAddrBitmap {
				if bit {
					count++
				}
			}
			return count
		}
	}
	return 0
}

func (m *mshrImpl) UpdateResponseBitMap(pid vm.PID, vAddr uint64) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			bitmap := e.ResponseBitMap
			VPN := vAddr >> m.log2PageSize

			bitmap[VPN%8] = true

			e.ResponseBitMap = bitmap
			return true
		}
	}
	return false
}

func (m *mshrImpl) GetResponseBitMap(pid vm.PID, vAddr uint64) ([8]bool, bool) {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			return e.ResponseBitMap, true
		}
	}

	return [8]bool{}, false
}

func (m *mshrImpl) IsPredicted(pid vm.PID, vAddr uint64) bool {
	BaseVaddr := m.getEntryVAddr(vAddr)
	for _, e := range m.entries {
		if e.pid == pid && e.baseVAddr == BaseVaddr {
			// 检查 UplevelBitMap 是否有任何位被设置为 true
			VPN := vAddr >> m.log2PageSize
			return e.UplevelBitMap[VPN%8]
		}
	}
	return false // 找不到对应的 MSHR 条目
}
