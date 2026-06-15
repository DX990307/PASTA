package tlb_gmmu

import "github.com/sarchlab/akita/v3/mem/vm"

type flexEntryMode uint8

const (
	flexEntryInvalid flexEntryMode = iota
	flexEntryPTEPack
	flexEntryPTCLLine
)

type flexTag struct {
	pid       vm.PID
	baseVAddr uint64
}

type flexEntry struct {
	mode      flexEntryMode
	valid     [8]bool
	tags      [8]flexTag
	bits      [8]int
	pages     [8]vm.Page
	lastVisit uint64
	slotVisit [8]uint64
}

type flexSet struct {
	entries []flexEntry
}

type flexBitmapLookupResult struct {
	hitBitmap      [8]bool
	missBitmap     [8]bool
	pages          [8]vm.Page
	ptePackHits    int
	ptclLineHits   int
	partialPTCLHit bool
	fullPTCLHit    bool
}

type flexFillResult struct {
	evictedPages             []vm.Page
	promotedToPTCLLine       bool
	invalidatedPTEPackSlots  int
	evictedValidSlotsForPTCL int
}

type flexTLBStore struct {
	sets               []flexSet
	numSets            int
	numWays            int
	pageSize           uint64
	log2PageSize       uint64
	promotionThreshold int
	clock              uint64
}

func newFlexTLBStore(
	numSets int,
	legacyNumWays int,
	pageSize uint64,
	log2PageSize uint64,
	promotionThreshold int,
) *flexTLBStore {
	if numSets < 1 {
		numSets = 1
	}

	flexWays := legacyNumWays / 8
	if flexWays < 1 {
		flexWays = 1
	}
	if promotionThreshold < 1 {
		promotionThreshold = 1
	}

	store := &flexTLBStore{
		numSets:            numSets,
		numWays:            flexWays,
		pageSize:           pageSize,
		log2PageSize:       log2PageSize,
		promotionThreshold: promotionThreshold,
	}
	store.sets = make([]flexSet, numSets)
	for i := range store.sets {
		store.sets[i].entries = make([]flexEntry, flexWays)
	}

	return store
}

func (s *flexTLBStore) baseVAddr(vAddr uint64) uint64 {
	vpn := vAddr >> s.log2PageSize
	return ((vpn >> 3) << 3) << s.log2PageSize
}

func (s *flexTLBStore) bit(vAddr uint64) int {
	return int((vAddr >> s.log2PageSize) & 0x7)
}

func (s *flexTLBStore) tag(pid vm.PID, baseVAddr uint64) flexTag {
	return flexTag{pid: pid, baseVAddr: baseVAddr}
}

func (s *flexTLBStore) setID(pid vm.PID, baseVAddr uint64) int {
	x := (baseVAddr >> s.log2PageSize) ^ (uint64(pid) * 0x9e3779b97f4a7c15)
	x ^= x >> 33
	x *= 0xff51afd7ed558ccd
	x ^= x >> 33
	return int(x % uint64(s.numSets))
}

func (s *flexTLBStore) lookupPTE(
	pid vm.PID,
	vAddr uint64,
) (page vm.Page, source flexEntryMode, found bool) {
	baseVAddr := s.baseVAddr(vAddr)
	bit := s.bit(vAddr)
	tag := s.tag(pid, baseVAddr)
	set := &s.sets[s.setID(pid, baseVAddr)]

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTCLLine || entry.tags[0] != tag {
			continue
		}
		if entry.valid[bit] && entry.pages[bit].Valid {
			s.visitEntry(entry)
			return entry.pages[bit], flexEntryPTCLLine, true
		}
	}

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTEPack {
			continue
		}
		for slot := 0; slot < 8; slot++ {
			if entry.valid[slot] &&
				entry.tags[slot] == tag &&
				entry.bits[slot] == bit &&
				entry.pages[slot].Valid {
				s.visitSlot(entry, slot)
				return entry.pages[slot], flexEntryPTEPack, true
			}
		}
	}

	return vm.Page{}, flexEntryInvalid, false
}

func (s *flexTLBStore) lookupBitmap(
	pid vm.PID,
	baseVAddr uint64,
	requestBitmap [8]bool,
) flexBitmapLookupResult {
	result := flexBitmapLookupResult{}
	tag := s.tag(pid, baseVAddr)
	set := &s.sets[s.setID(pid, baseVAddr)]

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTCLLine || entry.tags[0] != tag {
			continue
		}
		lineHit := false
		for bit := 0; bit < 8; bit++ {
			if !requestBitmap[bit] || !entry.valid[bit] || !entry.pages[bit].Valid {
				continue
			}
			result.hitBitmap[bit] = true
			result.pages[bit] = entry.pages[bit]
			result.ptclLineHits++
			lineHit = true
		}
		if lineHit {
			s.visitEntry(entry)
		}
	}

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTEPack {
			continue
		}
		for slot := 0; slot < 8; slot++ {
			if !entry.valid[slot] || entry.tags[slot] != tag {
				continue
			}
			bit := entry.bits[slot]
			if bit < 0 || bit >= 8 ||
				!requestBitmap[bit] ||
				result.hitBitmap[bit] ||
				!entry.pages[slot].Valid {
				continue
			}
			result.hitBitmap[bit] = true
			result.pages[bit] = entry.pages[slot]
			result.ptePackHits++
			s.visitSlot(entry, slot)
		}
	}

	requestedBits := 0
	hitBits := 0
	for bit := 0; bit < 8; bit++ {
		if !requestBitmap[bit] {
			continue
		}
		requestedBits++
		if result.hitBitmap[bit] {
			hitBits++
		} else {
			result.missBitmap[bit] = true
		}
	}
	result.partialPTCLHit = hitBits > 0 && hitBits < requestedBits
	result.fullPTCLHit = requestedBits > 0 && hitBits == requestedBits

	return result
}

func (s *flexTLBStore) fillPTE(page vm.Page) []vm.Page {
	if !page.Valid {
		return nil
	}

	baseVAddr := s.baseVAddr(page.VAddr)
	bit := s.bit(page.VAddr)
	tag := s.tag(page.PID, baseVAddr)
	set := &s.sets[s.setID(page.PID, baseVAddr)]

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode == flexEntryPTCLLine && entry.tags[0] == tag {
			entry.pages[bit] = page
			entry.valid[bit] = true
			s.visitEntry(entry)
			s.removeDuplicatePTEPackSlots(set, tag, s.singleBitBitmap(bit), nil)
			return nil
		}
	}

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTEPack {
			continue
		}
		for slot := 0; slot < 8; slot++ {
			if entry.valid[slot] &&
				entry.tags[slot] == tag &&
				entry.bits[slot] == bit {
				entry.pages[slot] = page
				s.visitSlot(entry, slot)
				return nil
			}
		}
	}

	if entry, slot, ok := set.freePTEPackSlot(); ok {
		s.fillPTEPackSlot(entry, slot, tag, bit, page)
		return nil
	}

	entry, slot, evictedPages := set.victimPTEPackSlot()
	if entry == nil {
		entry, evictedPages = set.victimWholeEntry(nil)
		slot = 0
		*entry = flexEntry{mode: flexEntryPTEPack}
	}
	s.fillPTEPackSlot(entry, slot, tag, bit, page)
	return evictedPages
}

func (s *flexTLBStore) fillBitmap(
	pid vm.PID,
	baseVAddr uint64,
	pages [8]vm.Page,
	bitmap [8]bool,
) flexFillResult {
	result := flexFillResult{}
	fillBitmap := bitmap
	for bit := 0; bit < 8; bit++ {
		if fillBitmap[bit] && !pages[bit].Valid {
			fillBitmap[bit] = false
		}
	}
	if s.bitmapCount(fillBitmap) == 0 {
		return result
	}

	tag := s.tag(pid, baseVAddr)
	set := &s.sets[s.setID(pid, baseVAddr)]

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTCLLine || entry.tags[0] != tag {
			continue
		}
		for bit := 0; bit < 8; bit++ {
			if !fillBitmap[bit] {
				continue
			}
			entry.pages[bit] = pages[bit]
			entry.valid[bit] = true
		}
		result.invalidatedPTEPackSlots +=
			s.removeDuplicatePTEPackSlots(set, tag, fillBitmap, entry)
		s.visitEntry(entry)
		return result
	}

	if s.bitmapCount(fillBitmap) >= s.promotionThreshold {
		entry, evictedPages, evictedSlots := set.victimPTCLLineEntry()
		result.evictedPages = append(result.evictedPages, evictedPages...)
		result.evictedValidSlotsForPTCL += evictedSlots
		*entry = flexEntry{mode: flexEntryPTCLLine}
		entry.tags[0] = tag
		for bit := 0; bit < 8; bit++ {
			if !fillBitmap[bit] {
				continue
			}
			entry.pages[bit] = pages[bit]
			entry.valid[bit] = true
		}
		result.promotedToPTCLLine = true
		result.invalidatedPTEPackSlots +=
			s.removeDuplicatePTEPackSlots(set, tag, fillBitmap, entry)
		s.visitEntry(entry)
		return result
	}

	for bit := 0; bit < 8; bit++ {
		if !fillBitmap[bit] {
			continue
		}
		page := pages[bit]
		result.evictedPages = append(result.evictedPages, s.fillPTE(page)...)
	}

	return result
}

func (s *flexTLBStore) invalidateVPN(pid vm.PID, vAddr uint64) {
	baseVAddr := s.baseVAddr(vAddr)
	bit := s.bit(vAddr)
	tag := s.tag(pid, baseVAddr)
	set := &s.sets[s.setID(pid, baseVAddr)]

	for i := range set.entries {
		entry := &set.entries[i]
		switch entry.mode {
		case flexEntryPTCLLine:
			if entry.tags[0] != tag {
				continue
			}
			entry.valid[bit] = false
			if entry.validCount() == 0 {
				*entry = flexEntry{}
			}
		case flexEntryPTEPack:
			for slot := 0; slot < 8; slot++ {
				if entry.valid[slot] &&
					entry.tags[slot] == tag &&
					entry.bits[slot] == bit {
					entry.valid[slot] = false
				}
			}
			if entry.validCount() == 0 {
				*entry = flexEntry{}
			}
		}
	}
}

func (s *flexTLBStore) invalidatePTCL(pid vm.PID, baseVAddr uint64) {
	tag := s.tag(pid, baseVAddr)
	set := &s.sets[s.setID(pid, baseVAddr)]

	for i := range set.entries {
		entry := &set.entries[i]
		switch entry.mode {
		case flexEntryPTCLLine:
			if entry.tags[0] == tag {
				*entry = flexEntry{}
			}
		case flexEntryPTEPack:
			for slot := 0; slot < 8; slot++ {
				if entry.valid[slot] && entry.tags[slot] == tag {
					entry.valid[slot] = false
				}
			}
			if entry.validCount() == 0 {
				*entry = flexEntry{}
			}
		}
	}
}

func (s *flexTLBStore) residentBitmap(
	pid vm.PID,
	baseVAddr uint64,
	bitmap [8]bool,
) [8]bool {
	return s.lookupBitmap(pid, baseVAddr, bitmap).hitBitmap
}

func (s *flexTLBStore) occupancy() (ptePackEntries, ptclLineEntries int) {
	for setID := range s.sets {
		set := &s.sets[setID]
		for entryID := range set.entries {
			switch set.entries[entryID].mode {
			case flexEntryPTEPack:
				ptePackEntries++
			case flexEntryPTCLLine:
				ptclLineEntries++
			}
		}
	}
	return ptePackEntries, ptclLineEntries
}

func (s *flexTLBStore) capacityStats() (sets, ways, pteSlots int) {
	return s.numSets, s.numWays, s.numSets * s.numWays * 8
}

func (s *flexTLBStore) fillPTEPackSlot(
	entry *flexEntry,
	slot int,
	tag flexTag,
	bit int,
	page vm.Page,
) {
	entry.mode = flexEntryPTEPack
	entry.valid[slot] = true
	entry.tags[slot] = tag
	entry.bits[slot] = bit
	entry.pages[slot] = page
	s.visitSlot(entry, slot)
}

func (s *flexTLBStore) removeDuplicatePTEPackSlots(
	set *flexSet,
	tag flexTag,
	bitmap [8]bool,
	except *flexEntry,
) int {
	invalidated := 0
	for i := range set.entries {
		entry := &set.entries[i]
		if entry == except || entry.mode != flexEntryPTEPack {
			continue
		}
		for slot := 0; slot < 8; slot++ {
			if !entry.valid[slot] || entry.tags[slot] != tag {
				continue
			}
			bit := entry.bits[slot]
			if bit < 0 || bit >= 8 || !bitmap[bit] {
				continue
			}
			entry.valid[slot] = false
			invalidated++
		}
		if entry.validCount() == 0 {
			*entry = flexEntry{}
		}
	}
	return invalidated
}

func (s *flexTLBStore) visitEntry(entry *flexEntry) {
	s.clock++
	entry.lastVisit = s.clock
}

func (s *flexTLBStore) visitSlot(entry *flexEntry, slot int) {
	s.clock++
	entry.lastVisit = s.clock
	entry.slotVisit[slot] = s.clock
}

func (s *flexTLBStore) singleBitBitmap(bit int) [8]bool {
	bitmap := [8]bool{}
	if bit >= 0 && bit < 8 {
		bitmap[bit] = true
	}
	return bitmap
}

func (s *flexTLBStore) bitmapCount(bitmap [8]bool) int {
	count := 0
	for i := 0; i < 8; i++ {
		if bitmap[i] {
			count++
		}
	}
	return count
}

func (set *flexSet) freePTEPackSlot() (*flexEntry, int, bool) {
	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTEPack {
			continue
		}
		for slot := 0; slot < 8; slot++ {
			if !entry.valid[slot] {
				return entry, slot, true
			}
		}
	}

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode == flexEntryInvalid || entry.validCount() == 0 {
			*entry = flexEntry{mode: flexEntryPTEPack}
			return entry, 0, true
		}
	}

	return nil, 0, false
}

func (set *flexSet) victimPTEPackSlot() (*flexEntry, int, []vm.Page) {
	var victim *flexEntry
	victimSlot := 0
	var oldest uint64
	found := false

	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTEPack {
			continue
		}
		for slot := 0; slot < 8; slot++ {
			if !entry.valid[slot] {
				return entry, slot, nil
			}
			age := entry.slotVisit[slot]
			if !found || age < oldest {
				found = true
				oldest = age
				victim = entry
				victimSlot = slot
			}
		}
	}

	if !found {
		return nil, 0, nil
	}

	evicted := []vm.Page{}
	if victim.valid[victimSlot] && victim.pages[victimSlot].Valid {
		evicted = append(evicted, victim.pages[victimSlot])
	}
	victim.valid[victimSlot] = false
	return victim, victimSlot, evicted
}

func (set *flexSet) victimPTCLLineEntry() (
	entry *flexEntry,
	evictedPages []vm.Page,
	evictedValidSlots int,
) {
	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode == flexEntryInvalid || entry.validCount() == 0 {
			return entry, nil, 0
		}
	}

	var candidate *flexEntry
	candidateValidCount := 9
	for i := range set.entries {
		entry := &set.entries[i]
		if entry.mode != flexEntryPTEPack {
			continue
		}
		validCount := entry.validCount()
		if validCount < candidateValidCount {
			candidate = entry
			candidateValidCount = validCount
		}
	}

	if candidate == nil {
		candidate, evictedPages = set.victimWholeEntry(nil)
	} else {
		evictedPages = validPages(candidate)
	}

	return candidate, evictedPages, len(evictedPages)
}

func (set *flexSet) victimWholeEntry(except *flexEntry) (*flexEntry, []vm.Page) {
	var victim *flexEntry
	var oldest uint64
	found := false

	for i := range set.entries {
		entry := &set.entries[i]
		if entry == except {
			continue
		}
		if entry.mode == flexEntryInvalid || entry.validCount() == 0 {
			return entry, nil
		}
		if !found || entry.lastVisit < oldest {
			found = true
			oldest = entry.lastVisit
			victim = entry
		}
	}

	if victim == nil {
		return &set.entries[0], validPages(&set.entries[0])
	}
	return victim, validPages(victim)
}

func (entry *flexEntry) validCount() int {
	count := 0
	for i := 0; i < 8; i++ {
		if entry.valid[i] {
			count++
		}
	}
	return count
}

func validPages(entry *flexEntry) []vm.Page {
	pages := []vm.Page{}
	if entry == nil {
		return pages
	}
	for i := 0; i < 8; i++ {
		if entry.valid[i] && entry.pages[i].Valid {
			pages = append(pages, entry.pages[i])
		}
	}
	return pages
}
