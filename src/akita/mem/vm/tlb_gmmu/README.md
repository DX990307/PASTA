# GMMU Flex TLB

This note describes the current Flex implementation in `akita/mem/vm/tlb_gmmu`.
It is based on the code in this branch, not on the older sectorized-entry
design.

## One-Sentence Summary

Flex keeps the normal GMMU L2 TLB as a PTE-granular set-associative cache. It
adds a small PCD locator table that lets one PTCL lookup job validate up to 8
ordinary PTE slots.

The PCD does not store PTEs, PID tags, PTCL tags, or fingerprints. It only stores
which way may contain each sector of a PTCL line. Every candidate still has to
pass the normal exact `PID + VAddr` check before it can be returned.

## Main Files

```text
builder.go        Build-time knobs for GMMU Flex and PTCL mode
pcd.go            PTCL Coverage Directory, the Flex locator table
tlb copy 2.go     Current GMMUTLB lookup/fill/miss path
stats.go          Flex/PTCL metrics exported to the benchmark reporter
internal/set.go   Ordinary set-associative PTE storage
```

## Baseline Storage

The GMMU L2 TLB still stores one translated page per slot:

```text
TLB
  set 0: way0 way1 ... way15
  set 1: way0 way1 ... way15
  ...
```

For the current `400latency` platform, the GMMU L2 TLB is configured as:

```text
numSets = 16
numWays = 16
capacity = 256 PTE slots
```

The exact set for a PTE is computed from the VPN:

```text
setID = (vAddr / pageSize) % numSets
```

The way is chosen by replacement, so it cannot be recomputed from the address.
This is the only information Flex needs to remember.

## PTCL Line

A PTCL line covers 8 consecutive PTEs:

```text
baseVAddr = align_down(vAddr, 8 * pageSize)

sector 0 -> baseVAddr + 0 * pageSize
sector 1 -> baseVAddr + 1 * pageSize
...
sector 7 -> baseVAddr + 7 * pageSize
```

These 8 PTEs are not stored as a single physical TLB line. They remain ordinary
PTE entries and may live in different TLB sets and ways:

```text
sector 0 -> TLB[set0][way5]
sector 1 -> TLB[set1][way2]
sector 2 -> TLB[set2][way9]
sector 3 -> not resident
...
```

The sector set IDs are computed from the 8 sector addresses. The sector way IDs
come from the PCD.

## PCD Structure

The PCD is allocated when `WithFlexTLB(true)` is used:

```go
type pcdLocator struct {
    wayID int
}

type pcdEntry struct {
    valid         bool
    presentBitmap [8]bool
    locators      [8]pcdLocator
    lastVisit     uint64
}
```

One PCD row means:

```text
For this remembered PTCL-shaped group,
sector i may be in way locators[i].wayID if presentBitmap[i] is true.
```

One PCD row does not contain:

```text
PID
baseVAddr / PTCL tag
VPN tag
PPN / page payload
setID
fingerprint
```

This is why the PCD is only a locator. It cannot prove a hit by itself.

## PCD Indexing

The PCD has the same number of sets as the GMMU L2 TLB:

```text
pcd.numSets = tlb.numSets
```

The PCD way count is controlled by `-gmmu-flex-pcd-ways`.

```text
if gmmu-flex-pcd-ways > 0:
    pcd.numWays = gmmu-flex-pcd-ways
else:
    pcd.numWays = ceil(tlb.numWays / 8)
```

For the current 16-way GMMU L2 TLB, the default PCD is:

```text
16 PCD sets * 2 PCD ways = 32 PCD rows
```

The PCD set hash uses `PID` and PTCL ID:

```text
ptclID = baseVAddr >> (log2PageSize + 3)
pcdSet = hash(pid, ptclID) % pcd.numSets
```

The PCD row itself is tagless. If two PTCL lines alias into the same PCD set,
the code distinguishes them by validating existing locator bits against the
ordinary TLB entries.

## Lookup Path

When a request reaches the GMMU L2 TLB, the MSHR key is normally PTCL-granular
unless `-gmmu-vpn-mshr-baseline` is enabled:

```text
groupKey = (PID, baseVAddr)
```

In PTCL mode, the lookup bitmap is widened to the PTCL line:

```text
lookupBitmap = 11111111, filtered by mapped pages
```

There are three relevant lookup paths:

```text
PTE mode:
  one PTE lookup job per requested bit

PTCL parallel mode without GMMU Flex:
  one PTE lookup job per PTCL bit
  these jobs share the normal pteLookupSlotLimit

PTCL mode with GMMU Flex:
  one PTCL-set lookup job for the bitmap
  the job occupies one normal PTE lookup slot
  after the lookup latency, it reads PCD locators and validates candidate slots
```

The active Flex condition is:

```go
tlb.flexTLBEnabled && tlb.ptclMode && !tlb.vpnMSHRBaseline
```

## Flex Lookup Algorithm

For a PTCL lookup job:

```text
Input:
  PID
  baseVAddr
  lookupBitmap
  demandBitmap

1. Read the PCD set for (PID, baseVAddr).

2. For each valid PCD row in that set:
     For each sector i in lookupBitmap:
       if presentBitmap[i] is false:
           skip

       wayID = locators[i].wayID
       expectedVAddr = baseVAddr + i * pageSize
       setID = set_index(expectedVAddr)

       candidate = TLB[setID][wayID]

       if candidate.Valid &&
          candidate.PID == PID &&
          candidate.VAddr == expectedVAddr:
              sector i hits
       else:
              locator is stale; clear presentBitmap[i]

3. MissBitmap = demandBitmap - HitBitmap.

4. Hit sectors update the MSHR response bitmap.

5. Miss sectors are represented by a downstream translation request.
```

Important distinction:

```text
PCD hit != translation hit
PCD hit only gives a candidate way
The ordinary TLB entry must still pass exact tag validation
```

So a stale or aliased PCD row can only cause extra misses and cleanup. It cannot
return the wrong translation.

## Fill and Eviction

Whenever a page is installed into the ordinary GMMU L2 TLB:

```text
installPage(page)
  -> choose/update ordinary TLB set and way
  -> recordPCDFill(page, setID, wayID)
```

`recordPCDFill`:

```text
1. Computes baseVAddr and sector bit.
2. Finds a PCD row in the matching PCD set whose existing locators validate to
   the same PTCL line.
3. If none exists, allocates an invalid row or an LRU PCD row.
4. Records:
      presentBitmap[bit] = true
      locators[bit].wayID = wayID
```

When a valid ordinary PTE slot is evicted:

```text
pcd.removePage(evictedPage, setID, wayID)
```

This clears the matching locator bit if the PCD row points at the evicted way.
If an invalidation path does not know the precise way, stale locator bits are
left in place and filtered later by exact validation.

## Why WayID Is Stored, But SetID Is Not

For sector `i`:

```text
expectedVAddr = baseVAddr + i * pageSize
setID = set_index(expectedVAddr)
```

So `setID` is a pure function of the address and does not need storage.

`wayID` depends on replacement history:

```text
wayID = chosen by EvictInvalid/Evict/LRU
```

That cannot be recomputed from `PID` or `VPN`, so Flex stores only `wayID`.

## Area Model

For current `400latency` parameters:

```text
TLB sets = 16
TLB ways = 16
PCD ways = ceil(16 / 8) = 2
PCD rows = 16 * 2 = 32
wayID bits = ceil(log2(16)) = 4
```

Ignoring replacement state, one PCD row stores:

```text
valid bit           1
presentBitmap       8
wayID[8]            8 * 4 = 32
--------------------------------
total               41 bits
```

So the PCD storage is:

```text
32 rows * 41 bits = 1312 bits
```

A general formula is:

```text
PCD bits =
  numSets * pcdWays * (1 + 8 + 8 * ceil(log2(numWays)))
```

The overhead as a fraction of the PTE array is:

```text
overhead = PCD bits / (numSets * numWays * PTE_entry_bits)
```

For example:

```text
if PTE_entry_bits = 128:
    overhead = 1312 / (16 * 16 * 128) = 4.00%

if PTE_entry_bits = 137:
    overhead = 1312 / (16 * 16 * 137) = 3.74%
```

Replacement metadata such as `lastVisit` is modeled in the simulator. Hardware
can use a small LRU/pseudo-LRU policy for the 2-way PCD set.

## MSHR Area

The current GMMU MSHR entry is PTCL-aware:

```go
type mshrEntry struct {
    pid            vm.PID
    baseVAddr      uint64
    UplevelBitMap  [8]bool
    IssuedBitMap   [8]bool
    ResponseBitMap [8]bool
    Requests       []*vm.TranslationReq
    reqToBottom    *vm.TranslationReq
    Pages          [8]vm.Page
    RealAddrBitmap [8]bool
    RealAddrTime   [8]sim.VTimeInSec
}
```

For hardware cost, it is better to separate the architectural PTCL MSHR metadata
from simulator bookkeeping:

```text
UplevelBitMap   8 bits   // sectors requested by upper-level TLBs
IssuedBitMap    8 bits   // sectors already issued to local/IOMMU path
ResponseBitMap  8 bits   // sectors whose translations have returned
--------------------------------
core PTCL MSHR bitmap metadata = 24 bits / entry
```

The simulator also has:

```text
RealAddrBitmap  8 bits
RealAddrTime[8] simulator timing values
Pages[8]        simulator storage for returned vm.Page objects
Requests[]      simulator request pointers
reqToBottom     simulator request pointer
```

`RealAddrTime`, `Requests[]`, and Go pointers should not be counted as direct
hardware storage for the Flex locator. `RealAddrBitmap` is optional hardware
metadata if the implementation wants to distinguish true demand sectors from
predicted/prefetch sectors.

For the current `400latency` GMMU:

```text
MSHR entries = 16
```

The bitmap-only PTCL MSHR overhead is:

```text
16 entries * 24 bits = 384 bits
```

If `RealAddrBitmap` is kept in hardware:

```text
16 entries * (24 + 8) bits = 512 bits
```

Combined with the PCD:

```text
PCD                         = 1312 bits
MSHR core bitmap metadata   = 384 bits
PCD + MSHR metadata         = 1696 bits

With optional RealAddrBitmap:
PCD + MSHR metadata         = 1824 bits
```

Relative to the 16-set, 16-way TLB PTE array:

```text
if PTE_entry_bits = 128:
    PCD + MSHR core metadata = 1696 / 32768 = 5.18%
    PCD + optional demand bitmap = 1824 / 32768 = 5.57%

if PTE_entry_bits = 137:
    PCD + MSHR core metadata = 1696 / 35072 = 4.84%
    PCD + optional demand bitmap = 1824 / 35072 = 5.20%
```

Relative to the total after adding the metadata:

```text
128-bit PTE assumption:
    1696 / (32768 + 1696) = 4.92%

137-bit PTE assumption:
    1696 / (35072 + 1696) = 4.61%
```

There is a much more conservative interpretation: mirror the simulator literally
and reserve 8 returned PTE payload slots in every MSHR entry. Compared with a
per-VPN MSHR that stores one returned PTE, that adds:

```text
extra payload bits = numMSHREntry * 7 * PTE_entry_bits
```

For 16 MSHR entries and 128-bit PTE payloads:

```text
extra payload + core bitmaps
  = 16 * (7 * 128 + 24)
  = 14720 bits
```

That alone is about `44.9%` of the 32768-bit PTE array, so it is not the low-area
hardware point. The low-area Flex design should keep only the sector bitmaps in
the MSHR and either stream returned translations to the TLB/response path or use
a small shared response buffer instead of dedicating 8 full PTE payload slots per
MSHR entry.

## Important Runtime Flags

```text
-gmmu-flex-tlb
    Enable the GMMU Flex locator path.

-gmmu-flex-pcd-ways=N
    Number of PCD rows per PCD set.
    N=0 means ceil(tlb.numWays / 8).

-gmmu-initial-ptcl-mode=true/false
    Whether the GMMU starts in PTCL mode.

-gmmu-ptcl-threshold-low=N
-gmmu-ptcl-threshold-high=N
    Adaptive hysteresis thresholds for entering/leaving PTCL mode.

-gmmu-pte-lookup-latency=N
    Latency charged to one internal lookup job.

-gmmu-pte-lookup-slots=N
    Number of internal lookup jobs that can be in flight.
```

`-gmmu-flex-promotion-threshold` is still passed through and reported by the
benchmark script, but this current tagless-locator path records PCD locators
incrementally on ordinary PTE fills. It does not store a separate PTCL-line
payload entry in the L2 TLB.

## Metrics

The benchmark reporter exports these Flex metrics:

```text
flex_tlb_enabled
flex_promotion_threshold
flex_pte_pack_entries
flex_ptcl_line_entries
flex_lookup_jobs
flex_lookup_requested_bits
flex_lookup_hit_bits
flex_lookup_miss_bits
flex_lookup_saved_jobs
flex_pte_pack_hits
flex_ptcl_line_hits
flex_partial_ptcl_hits
flex_full_ptcl_hits
flex_promotions
flex_demotions
flex_invalidated_pte_pack_slots
flex_evicted_valid_slots_for_ptcl_line
flex_num_sets
flex_num_ways
flex_pte_slot_capacity
```

The most useful counters for this implementation are:

```text
flex_lookup_jobs
    Number of one-slot Flex PTCL lookup jobs.

flex_lookup_requested_bits
    Number of PTCL sector bits represented by those jobs.

flex_lookup_saved_jobs
    Sum of (requested bits - 1) for multi-bit Flex lookup jobs.

flex_lookup_hit_bits / flex_lookup_miss_bits
    How many requested sectors were found through validated PCD locators.

flex_ptcl_line_entries
    Number of valid PCD locator rows, not physical PTCL-line payload entries.
```

## Mental Model

Think of Flex as a small exact-safe hint table:

```text
PTCL request
   |
   v
PCD locator row
   |
   |  gives candidate wayID for each sector
   v
ordinary TLB[computed setID][wayID]
   |
   |  exact PID + VAddr validation
   v
real hit or clean miss
```

It is not a fingerprint cache and it is not a separate PTCL-line TLB. It is a
low-area way locator that lets the PTCL-mode GMMU check up to 8 ordinary PTEs
with one modeled lookup-slot job.
