package mmuTLB

import "github.com/sarchlab/akita/v3/mem/vm"

type pcdLocator struct {
	wayID int
}

type pcdEntry struct {
	valid         bool
	presentBitmap [8]bool
	locators      [8]pcdLocator
	lastVisit     uint64
}

type pcdSet struct {
	entries []pcdEntry
}

type pcdLookupResult struct {
	HitBitmap  [8]bool
	MissBitmap [8]bool
	Pages      [8]vm.Page
	LineHit    bool
	StaleBits  int
}

type ptclCoverageDirectory struct {
	sets           []pcdSet
	numSets        int
	numWays        int
	log2PageSize   uint64
	visitCounter   uint64
	entryEvictions int
	staleBits      int
}

func newPTCLCoverageDirectory(
	numSets int,
	numWays int,
	metadataWaysOverride int,
	log2PageSize uint64,
) *ptclCoverageDirectory {
	if numSets <= 0 {
		numSets = 1
	}
	if numWays <= 0 {
		numWays = 1
	}
	metadataWays := metadataWaysOverride
	if metadataWays <= 0 {
		metadataWays = (numWays + 7) / 8
	}
	if metadataWays <= 0 {
		metadataWays = 1
	}

	d := &ptclCoverageDirectory{
		sets:         make([]pcdSet, numSets),
		numSets:      numSets,
		numWays:      metadataWays,
		log2PageSize: log2PageSize,
	}

	for i := range d.sets {
		d.sets[i].entries = make([]pcdEntry, metadataWays)
	}

	return d
}

func (d *ptclCoverageDirectory) baseVAddr(vAddr uint64) uint64 {
	vpn := vAddr >> d.log2PageSize
	return ((vpn >> 3) << 3) << d.log2PageSize
}

func (d *ptclCoverageDirectory) bit(vAddr uint64) int {
	vpn := vAddr >> d.log2PageSize
	return int(vpn & 0x7)
}

func (d *ptclCoverageDirectory) setID(pid vm.PID, baseVAddr uint64) int {
	if d.numSets <= 1 {
		return 0
	}

	ptclID := baseVAddr >> (d.log2PageSize + 3)
	shift := uint(0)
	for (1 << shift) < d.numSets {
		shift++
	}

	pidHash := uint64(pid) ^ (uint64(pid) >> shift)
	hashed := ptclID ^ (ptclID >> shift) ^ pidHash
	if d.numSets&(d.numSets-1) == 0 {
		return int(hashed & uint64(d.numSets-1))
	}

	return int(hashed % uint64(d.numSets))
}

func (d *ptclCoverageDirectory) setForLine(
	pid vm.PID,
	baseVAddr uint64,
) *pcdSet {
	return &d.sets[d.setID(pid, baseVAddr)]
}

func (d *ptclCoverageDirectory) findEntryForLine(
	pid vm.PID,
	baseVAddr uint64,
	matches func(*pcdEntry) bool,
) (*pcdEntry, bool) {
	set := d.setForLine(pid, baseVAddr)
	for i := range set.entries {
		entry := &set.entries[i]
		if !entry.valid {
			continue
		}
		if matches(entry) {
			d.visit(entry)
			return entry, true
		}
	}

	return nil, false
}

func (d *ptclCoverageDirectory) findOrAllocateEntry(
	pid vm.PID,
	baseVAddr uint64,
) *pcdEntry {
	set := d.setForLine(pid, baseVAddr)
	for i := range set.entries {
		entry := &set.entries[i]
		if !entry.valid {
			d.initializeEntry(entry)
			return entry
		}
	}

	victim := &set.entries[0]
	for i := 1; i < len(set.entries); i++ {
		if set.entries[i].lastVisit < victim.lastVisit {
			victim = &set.entries[i]
		}
	}

	d.entryEvictions++
	d.initializeEntry(victim)
	return victim
}

func (d *ptclCoverageDirectory) initializeEntry(entry *pcdEntry) {
	*entry = pcdEntry{
		valid: true,
	}
	d.visit(entry)
}

func (d *ptclCoverageDirectory) visit(entry *pcdEntry) {
	d.visitCounter++
	entry.lastVisit = d.visitCounter
}

func (d *ptclCoverageDirectory) recordFillInEntry(
	entry *pcdEntry,
	page vm.Page,
	wayID int,
) {
	if entry == nil || !entry.valid || !page.Valid {
		return
	}

	bit := d.bit(page.VAddr)
	if bit < 0 || bit >= 8 {
		return
	}

	entry.presentBitmap[bit] = true
	entry.locators[bit] = pcdLocator{
		wayID: wayID,
	}
	d.visit(entry)
}

func (d *ptclCoverageDirectory) removePage(
	page vm.Page,
	wayID int,
) {
	if !page.Valid {
		return
	}

	baseVAddr := d.baseVAddr(page.VAddr)
	bit := d.bit(page.VAddr)
	if bit < 0 || bit >= 8 {
		return
	}

	set := d.setForLine(page.PID, baseVAddr)
	for i := range set.entries {
		entry := &set.entries[i]
		if !entry.valid {
			continue
		}
		locator := entry.locators[bit]
		if entry.presentBitmap[bit] && locator.wayID == wayID {
			d.clearBit(entry, bit)
		}
	}
}

func (d *ptclCoverageDirectory) clearBit(entry *pcdEntry, bit int) {
	entry.presentBitmap[bit] = false
	entry.locators[bit] = pcdLocator{}

	for i := 0; i < 8; i++ {
		if entry.presentBitmap[i] {
			return
		}
	}

	entry.valid = false
}

func (d *ptclCoverageDirectory) validEntryCount() int {
	count := 0
	for i := range d.sets {
		set := &d.sets[i]
		for j := range set.entries {
			if set.entries[j].valid {
				count++
			}
		}
	}
	return count
}
