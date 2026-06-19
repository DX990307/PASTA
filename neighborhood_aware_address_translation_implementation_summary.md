# Neighborhood-Aware Address Translation for Irregular GPU Applications

Implementation-oriented summary of paper [35]:

Seunghee Shin, Michael LeBeane, Yan Solihin, and Arkaprava Basu.
"Neighborhood-Aware Address Translation for Irregular GPU Applications."
MICRO 2018.

Source PDF used for this summary:
`/Users/daoxuan/Downloads/Neighborhood-Aware_Address_Translation_for_Irregular_GPU_Applications.pdf`

## Goal

The paper reduces GPU address-translation overhead by coalescing page-table
walks that need page-table entries located in the same cache line. The key idea
is that a page table walker fetches a full 64-byte cache line from the in-memory
page table, but a baseline walker usually consumes only one 8-byte page-table
entry. If other pending page-walk requests need entries in that same cache line,
they can be served using the already-fetched cache line.

The mechanism is implemented inside the IOMMU page-walk backend. It does not
modify GPU-side TLB MSHRs and does not reduce request traffic before requests
reach the IOMMU buffer.

## Core Observations

1. Page-table entries are cache-line grouped.
   - PTE size: 8 bytes.
   - Cache line size: 64 bytes.
   - Each cache line contains 8 page-table entries.

2. A page table walker fetches memory at cache-line granularity.
   - Even if a walk needs one PTE, the memory access brings back the cache line
     containing 8 entries.

3. Many concurrent GPU page walks need entries in the same cache line.
   - The paper calls these entries a "neighborhood".
   - A smarter walker can use one page-table cache-line fetch to serve multiple
     pending walks.

4. The same idea applies to upper-level page-table entries.
   - In a radix-tree page table, upper-level entries are also 8 bytes.
   - A cache line therefore contains 8 entries at any page-table level.
   - A walk can be partially coalesced at an upper level, then resume from a
     lower level later.

## Original System Model

The paper models a heterogeneous CPU + integrated GPU system with shared virtual
memory.

Baseline translation path:

1. GPU memory instruction generates addresses.
2. Existing GPU coalescer merges same-page accesses.
3. Request checks GPU L1 TLB.
4. On L1 TLB miss, request checks shared GPU L2 TLB.
5. On L2 TLB miss, request is sent to the IOMMU.
6. IOMMU TLBs are checked.
7. On IOMMU TLB miss, request enters the IOMMU buffer.
8. A page table walker picks pending requests from the IOMMU buffer and walks the
   in-memory page table.

The proposed mechanism acts at steps 7 and 8.

## Definition: Neighborhood

A neighborhood is the group of virtual addresses whose page-table entries for a
given page-table level fall in the same 64-byte page-table cache line.

For a 4KB page and a 512-ary radix tree:

- Leaf level neighborhood: 8 adjacent 4KB pages = 32KB VA region.
- Next upper level: 8 entries, each covering 2MB = 16MB VA region.
- Next upper level: 8 entries, each covering 1GB = 8GB VA region.
- Next upper level: 8 entries, each covering 512GB = 4TB VA region.

For a simulator with 5 page-table levels, generalize the same rule. If levels are
numbered from 1 at the leaf to N at the root:

- Entry coverage at level 1 is page size.
- Entry coverage at level L is `page_size * 512^(L - 1)`.
- Neighborhood coverage at level L is `8 * entry_coverage(level L)`.

Implementation tip: do not rely only on byte-range arithmetic if the simulator
already exposes VPN indices. It is often safer to compare page-table index groups.

For a 512-ary page table with 9 VPN bits per level:

```go
// levels are numbered from 1 at the leaf to NumLevels at the root.
// vpn does not include the page offset.
func sameNeighborhood(vpnA, vpnB uint64, level int) bool {
    shift := uint((level - 1) * 9)
    idxA := (vpnA >> shift) & 0x1ff
    idxB := (vpnB >> shift) & 0x1ff

    // Same parent page-table node at this level.
    highA := vpnA >> (shift + 9)
    highB := vpnB >> (shift + 9)
    if highA != highB {
        return false
    }

    // Same 8-entry cache-line group within the node.
    return (idxA >> 3) == (idxB >> 3)
}
```

This ignores lower-level index bits, which is intended for upper-level matching.

## Hardware State Added by the Paper

The paper adds state to the IOMMU buffer and adds one service table.

### IOMMU Buffer Entry Extensions

Each pending page-walk request in the IOMMU buffer gets:

- `PL`: the page-table level up to which this request has already coalesced with
  another walk.
- `PA`: the physical address of the next page-table node to access when this
  request resumes.
- `CL`: a bit indicating that this request has a current coalescing opportunity
  with an ongoing page walk and should not be launched independently yet.

For an implementation, a clearer representation is:

```go
type WalkBufferEntry struct {
    VPN uint64

    // Existing fields: source, PID/ASID, request metadata, callback, etc.

    HasResumePoint bool
    ResumeLevel    int    // next lower level that still needs to be walked
    ResumeNodePA   uint64 // page-table node address for ResumeLevel

    CoalescingLocked bool // equivalent to CL
}
```

### Page Walk Service Table

The paper adds a Page Walk Service Table (PWST), one entry per active page table
walker.

Each entry records:

- Whether the walker is active.
- The VPN being walked.
- The current page-table level being accessed.
- The request ID or buffer entry being served.

Suggested implementation:

```go
type PageWalkServiceEntry struct {
    Active       bool
    WalkerID     int
    RequestID    string // or pointer/index to active request
    VPN          uint64
    CurrentLevel int
    NodePA       uint64
}
```

The original paper reports about 1.5KB of added state for its modeled IOMMU.

## Main Algorithm

The mechanism has two parts:

1. Coalesce pending requests after each page-table memory access completes.
2. Avoid launching pending requests that can still coalesce with ongoing walks.

### Event: New IOMMU TLB Miss

Baseline behavior:

1. Allocate an IOMMU buffer entry.
2. Wait for a page walker.

Neighborhood-aware behavior:

1. Allocate an IOMMU buffer entry.
2. Initialize:
   - `HasResumePoint = false`
   - `ResumeLevel = 0`
   - `ResumeNodePA = 0`
   - `CoalescingLocked = false`
3. The request remains pending until a walker can start it or until it is served
   by another walk's fetched cache line.

### Event: Walker Becomes Free

Before selecting a request, recompute coalescing opportunities against all active
walkers.

Recommended robust policy:

1. Clear all pending entries' `CoalescingLocked` bits.
2. For each pending entry:
   - Compare it against each active PWST entry.
   - If it is in the same neighborhood at the active walker's current level, set
     `CoalescingLocked = true`.
3. Choose the oldest pending entry that is not coalescing-locked.
4. If the chosen entry has a resume point:
   - Start walking from `ResumeLevel` using `ResumeNodePA`.
5. Otherwise:
   - Start walking from the root level.
6. Remove or mark the chosen entry as no longer pending.
7. Populate the corresponding PWST entry.

This differs slightly from a literal sticky-CL implementation, but it is easier
to implement correctly in an event-driven simulator because CL cannot become
stale.

### Event: Page-Table Memory Access Completes

Input:

- Active walker W.
- Current walk VPN `vpnW`.
- Completed level `L`.
- Fetched 64-byte page-table cache line.

Steps:

1. Use the fetched cache line to advance W's own walk as usual.
2. Scan all pending IOMMU buffer entries.
3. For each pending entry P:
   - If `sameNeighborhood(vpnW, P.VPN, L)` is false, skip it.
   - If true, P can coalesce with this fetched cache line.

Leaf-level case:

1. Extract P's PTE from the fetched cache line.
2. Complete P's translation immediately.
3. Send the translation response through the same path used by baseline IOMMU
   translations.
4. Remove P from the IOMMU buffer.
5. Count this as a full/leaf coalescing event.

Upper-level case:

1. Extract the page-table entry for P at level L from the fetched cache line.
2. Decode the child page-table node physical address.
3. Update P:
   - `HasResumePoint = true`
   - `ResumeLevel = L - 1`
   - `ResumeNodePA = childNodePA`
4. Keep P pending.
5. Count this as an upper-level partial coalescing event.

After W's own access:

- If W completed the leaf level, finish W's translation and free the walker.
- Otherwise, update W's PWST entry to the next lower level and continue the walk.

## Pseudocode

```go
func onPageTableAccessDone(walker *Walker, level int, line CacheLine) {
    activeVPN := walker.VPN

    for _, entry := range iommuBuffer.PendingEntries() {
        if entry.RequestID == walker.RequestID {
            continue
        }
        if !sameNeighborhood(activeVPN, entry.VPN, level) {
            continue
        }

        if level == LeafLevel {
            pte := extractEntryForVPN(line, entry.VPN, level)
            pa := translateWithPTE(entry.VPN, pte)

            completeTranslation(entry, pa)
            iommuBuffer.Remove(entry)

            stats.LeafCoalescedWalks++
            stats.PageTableAccessesSaved++
        } else {
            pte := extractEntryForVPN(line, entry.VPN, level)
            childNodePA := decodeNextLevelNodePA(pte)

            entry.HasResumePoint = true
            entry.ResumeLevel = level - 1
            entry.ResumeNodePA = childNodePA

            stats.UpperLevelCoalesces++
            stats.PageTableAccessesSaved++
        }
    }

    advanceOrCompleteActiveWalk(walker, level, line)
}

func pickNextWalkRequest() *WalkBufferEntry {
    recomputeCoalescingLocks()

    for _, entry := range iommuBuffer.EntriesInFCFSOrder() {
        if entry.CoalescingLocked {
            continue
        }
        return entry
    }
    return nil
}

func recomputeCoalescingLocks() {
    for _, entry := range iommuBuffer.PendingEntries() {
        entry.CoalescingLocked = false
    }

    for _, entry := range iommuBuffer.PendingEntries() {
        for _, svc := range pageWalkServiceTable.ActiveEntries() {
            if sameNeighborhood(svc.VPN, entry.VPN, svc.CurrentLevel) {
                entry.CoalescingLocked = true
                break
            }
        }
    }
}
```

## What This Mechanism Does Not Do

This is important if implementing it as a comparison baseline for PASTA.

The paper's mechanism does not:

- Modify GPU L1/L2 TLB MSHR format.
- Merge translation misses before they reach the IOMMU.
- Reduce GPU-to-IOMMU network request count.
- Reduce IOTLB MSHR allocation before the request reaches the IOMMU buffer.
- Predict future translations.
- Prefetch translations.
- Push prefetched PTEs into remote GPU/GPM L2 TLBs.

It only coalesces page-table walk work after requests have reached the IOMMU
page-walk backend.

## Expected Benefits

The paper reports:

- Average speedup of about 1.7x on irregular GPU workloads.
- Up to about 2.3x in the best case.
- About 37 percent fewer page-table memory accesses on average.
- Both leaf-level and upper-level coalescing matter.
- Benefits remain under varying page-walker counts and IOMMU buffer sizes,
  although more walkers can reduce the headroom.

For a PASTA comparison, do not expect the same numbers. PASTA's wafer-scale GPU
has a different bottleneck structure: many GPMs inject translation requests into
a centralized IOMMU over a network. This means a [35]-style implementation may
reduce walker work but still leave front-end MSHR pressure and network traffic.

## Implementation Checklist for an Agent

1. Locate the IOMMU page-walk request buffer.
   - This is where pending IOMMU TLB misses wait before being assigned to page
     table walkers.

2. Locate the page walker state machine.
   - Identify the event where a page-table memory access completes.
   - Identify how the walker moves from one page-table level to the next.

3. Add a configuration flag.
   - Example: `EnableNeighborhoodAwareWalkCoalescing`.
   - Default should be false for baseline reproducibility.

4. Extend pending walk request metadata.
   - Add resume point fields.
   - Add a coalescing-lock bit or recomputed equivalent.

5. Add a PWST-like active walker table.
   - One entry per page table walker.
   - Keep it updated whenever a walker starts, advances, or completes.

6. Implement `sameNeighborhood(vpnA, vpnB, level)`.
   - Use VPN index bits rather than byte ranges if possible.
   - Make it generic for the simulator's number of page-table levels.

7. Scan pending requests after every page-table memory access.
   - On leaf-level match, complete pending requests immediately.
   - On upper-level match, record resume point.

8. Modify page-walk scheduling.
   - Do not launch a pending request if it currently has a coalescing opportunity
     with an active walker.
   - Use FCFS among non-locked requests.
   - Recompute locks to avoid stale CL bits.

9. Preserve baseline translation response behavior.
   - A coalesced request should update/fill TLBs exactly as if its own walk had
     completed.
   - Make sure callbacks, source request IDs, and timing events are handled.

10. Add stats.
   - Leaf-level coalesced requests.
   - Upper-level partial coalesces.
   - Page-table memory accesses saved.
   - Full page walks avoided.
   - IOMMU buffer occupancy.
   - Walker busy cycles.
   - Walker queue wait cycles.
   - Cycles blocked due to coalescing lock.
   - Translation latency distribution.

## Suggested Validation Tests

### Unit Test 1: Leaf-Level Coalescing

Setup:

- Two pending requests have VPNs in the same 8-PTE leaf cache-line group.
- One walker fetches the leaf cache line for the first request.

Expected:

- The second request completes without launching its own leaf access.
- Leaf coalescing counter increments.
- Only one page-table leaf memory access is counted.

### Unit Test 2: No Leaf Coalescing Across Cache-Line Boundary

Setup:

- Two pending requests have adjacent VPNs but fall in different 8-entry PTE
  groups.

Expected:

- No leaf-level coalescing.
- Both requests require separate leaf accesses unless upper-level coalescing
  applies.

### Unit Test 3: Upper-Level Partial Coalescing

Setup:

- Two pending requests share an upper-level neighborhood but not a leaf-level
  neighborhood.
- Walker A completes the shared upper-level memory access.

Expected:

- Request B receives a resume point.
- Request B later starts from a lower level rather than the root.
- Upper-level coalescing counter increments.

### Unit Test 4: Coalescing Lock

Setup:

- Walker A is active at a level where pending request B is in the same
  neighborhood.
- Another walker becomes free.

Expected:

- Request B is not selected for independent walking while the coalescing
  opportunity exists.
- Another non-coalescible request may be selected instead.

### Unit Test 5: Multiple Pending Matches

Setup:

- One fetched page-table cache line can serve several pending requests.

Expected:

- All matching pending requests are updated or completed.
- Non-matching requests remain pending.

## Comparison Against PASTA

If this is implemented to answer Reviewer C, report it as a [35]-style baseline:

- Baseline
- [35]-style neighborhood-aware page-walk coalescing
- PASTA MSHR coalescing only
- Full PASTA

Recommended metrics:

- Runtime speedup.
- IOTLB MSHR full cycles.
- L2 TLB MSHR full cycles.
- GPU/GPM-to-IOMMU translation request count.
- IOMMU buffer occupancy.
- Page-table memory accesses.
- Walker queueing cycles.
- Number of completed walks.
- Number of coalesced leaf and upper-level requests.

The key hypothesis for PASTA is:

> [35]-style coalescing reduces backend page-walker work, but it does not reduce
> front-end MSHR allocation or network request fan-in. PASTA targets those
> wafer-scale front-end bottlenecks by coalescing at the L2 TLB/IOTLB MSHR level
> before requests become independent IOMMU page-walk requests.

