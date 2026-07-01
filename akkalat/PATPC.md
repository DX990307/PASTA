# LATPC Mechanisms - GPT-Readable SOTA Comparison Notes

> Use case: detailed, structured notes for comparing LATPC against SOTA GPU address translation, TLB prefetching, speculative translation, and TLB/MSHR contention reduction mechanisms.

---

## 0. Document Metadata

```yaml
paper_title: "LATPC: Accelerating GPU Address Translation Using Locality-Aware TLB Prefetching and MSHR Compression"
venue: "MICRO 2025"
conference_full_name: "58th IEEE/ACM International Symposium on Microarchitecture"
publication_year: 2025
doi: "https://doi.org/10.1145/3725843.3756069"
paper_length: "14 pages"
topic_area:
  - GPU virtual memory
  - GPU address translation
  - TLB prefetching
  - TLB MSHR compression
  - Page table walk batching
  - Page table locality
authors:
  - Yeonan Ha
  - Jiho Park
  - Hanna Cha
  - Jiwon Lee
  - Joonsung Kim
  - Won Woo Ro
  - Youngsok Kim
core_method_name: "LATPC"
expanded_name: "Locality-Aware TLB Prefetching and MSHR Compression"
main_result: "1.47x geometric mean speedup over baseline without TLB prefetching"
```

---

## 1. Executive Summary

LATPC is a hardware mechanism for accelerating GPU address translation. It is designed for modern GPUs that support virtual memory and therefore need to translate virtual addresses to physical addresses before accessing global memory.

The paper argues that GPU address translation overhead is dominated by two hardware-contention problems:

1. **Limited Page Table Walkers (PTWs)**  
   TLB misses trigger page table walks. When many translation requests miss, PTWs become occupied and later requests wait in the page walk queue.

2. **Limited L1 TLB MSHR entries**  
   Each outstanding L1 TLB miss needs an MSHR entry. When many threads in a warp access different virtual pages, the L1 TLB MSHRs can fill up, causing reservation failures and blocking subsequent translations.

LATPC exploits an observation that is specific to GPU SIMT execution:

> Even when the global TLB access stream appears scattered, the VPNs requested by threads within the same warp memory instruction often exhibit regularity and page-table locality.

The paper turns this observation into three mechanisms:

| Mechanism | Short Name | Main Function | Bottleneck Targeted |
|---|---|---|---|
| Regularity Detector | RD | Detects stride-like VPN groups within a warp memory instruction | Enables LATC and LATP |
| Locality-Aware TLB MSHR Compression | LATC | Compresses multiple outstanding L1 TLB misses into fewer MSHR entries | L1 TLB MSHR contention |
| Locality-Aware TLB Prefetching | LATP | Batches page table walks and prefetches multiple PTEs from the same L4 page table | PTW contention and page walk queueing |

The combined design, **LATPC = Regularity Detector + LATC + LATP**, achieves:

```text
LATPC speedup: 1.47x GMean over baseline
LATC-only speedup: 1.20x
LATP-only speedup: 1.28x
Avatar speedup: 1.40x
Avatar + LATPC speedup: 1.64x
```

---

## 2. Problem Context

### 2.1 GPU Virtual Memory Pipeline

Modern GPUs support virtual memory for programmability, memory protection, multitasking, and unified memory support. A typical GPU translation path is:

```text
Warp scheduler
  -> LD/ST unit
  -> TLB coalescer
  -> L1 TLB
  -> L1 TLB MSHR on miss
  -> L2 TLB
  -> L2 TLB MSHR on miss
  -> Page Walk Queue
  -> Page Table Walker
  -> Page Walk Cache / DRAM page table access
  -> Fill L2 TLB
  -> Fill L1 TLB
  -> Release MSHR
  -> Replay stalled translation or memory access
```

### 2.2 Important Hardware Structures

| Term | Meaning | Role in This Paper |
|---|---|---|
| VPN | Virtual Page Number | Input key for address translation |
| PFN | Physical Frame Number | Output of address translation |
| PTE | Page Table Entry | Stores mapping information used to translate VPN to PFN |
| TLB | Translation Lookaside Buffer | Cache for recent VPN-to-PFN translations |
| L1 TLB | Per-SM private TLB | First translation lookup level |
| L2 TLB | Shared GPU-level TLB | Second translation lookup level |
| MSHR | Miss-Status Holding Register | Tracks outstanding misses |
| L1 TLB MSHR | Per-SM structure tracking L1 TLB misses | Bottleneck targeted by LATC |
| PTW | Page Table Walker | Hardware walker that reads page table entries | Bottleneck targeted by LATP |
| PW Queue | Page Walk Queue | Queue for page walk requests |
| PW Buffer | Page Walk Buffer | Tracks in-flight page walks |
| PWC | Page Walk Cache | Caches upper-level page table entries |
| SM | Streaming Multiprocessor | GPU core-like unit |
| Warp | Group of 32 GPU threads | Unit exploited by LATPC |

### 2.3 Why Address Translation Is Hard on GPUs

GPUs execute many threads concurrently. A warp memory instruction can require translations for multiple virtual pages because different threads in the warp may access different memory regions.

This behavior is called **page divergence** in the paper:

```text
page divergence = number of unique pages accessed by a warp memory instruction
```

High page divergence creates many translation requests from a single warp memory instruction. If these requests miss in the TLBs, they consume MSHR entries and PTW bandwidth.

### 2.4 Quantitative Motivation

The paper reports the following key statistics:

| Metric | Reported Value | Interpretation |
|---|---:|---|
| Address translation latency due to page table walks | 39.37% average | PTWs are a major contributor |
| Address translation latency due to L1 TLB MSHR reservation failures | 53.09% average | MSHR contention is even larger |
| Warp memory instructions requiring multiple translations | 75.32% average | Page divergence is common |
| Warp memory instructions requiring 32 translations | 28.86% average | Worst-case page divergence is common enough to matter |
| Regular workloads, translations in same L4 PT | 93.38% average | Strong page-table locality |
| Irregular workloads, translations in same L4 PT | 67.02% average | Locality also exists in irregular workloads |
| Overall translations in same L4 PT | 80.20% average | Supports LATP's batching strategy |
| Average unique VPN strides per warp instruction | 1.96 | Supports Regularity Detector |

---

## 3. Why Prior Approaches Are Insufficient

### 3.1 Traditional TLB Prefetchers

Prior TLB prefetchers often predict future translations using the observed global TLB miss stream.

| Prefetcher | Prediction Basis | Prefetch Target | Problem on GPUs |
|---|---|---|---|
| Sequential | Next PTE after a miss | VPN + 1 | GPU access order is not necessarily sequential |
| Stride | Learned stride | VPN + stride | Global TLB access stream is interleaved across many threads |
| Distance | Learned miss distances | VPN + d1, VPN + d2 | Global miss distances can be noisy |
| Valkyrie | Inter-L1 TLB locality | Replicates returned PTEs into other L1 TLBs | Does not fetch additional PTEs from the page table |

The paper's critique:

- Existing prefetchers view accesses in **temporal/global TLB access order**.
- GPU accesses from thousands of threads are interleaved.
- The resulting global TLB stream can appear scattered.
- A scattered stream makes global stride or distance prediction inaccurate.
- Inaccurate prefetching can increase PTW and MSHR contention.

### 3.2 Large Pages

Large pages increase TLB reach and reduce TLB misses, but they do not eliminate the core contention bottlenecks.

The paper evaluates 2 MB pages and finds that:

- Large pages reduce some translation pressure.
- Large memory footprints can still cause page divergence.
- PTW and MSHR contention remain important.
- LATPC still provides speedup even with 2 MB pages.

Reported result:

```text
LATPC with 2 MB pages: 1.18x GMean speedup over 2 MB baseline
```

### 3.3 Avatar

Avatar is treated as the state-of-the-art GPU address translation mechanism in the paper. It performs speculative address translation.

LATPC differs from Avatar:

| Dimension | Avatar | LATPC |
|---|---|---|
| Main idea | Speculate physical addresses on L1 TLB misses | Reduce translation hardware contention |
| Handles PTW contention? | Partially hides it | Directly reduces PTW occupancy via LATP |
| Handles L1 TLB MSHR contention? | No, still occupies MSHRs | Yes, via LATC |
| Orthogonal to LATPC? | Yes | Can be combined with Avatar |

Reported result:

```text
Avatar: 1.40x GMean speedup
LATPC: 1.47x GMean speedup
Avatar + LATPC: 1.64x GMean speedup
```

---

## 4. Central Observation of LATPC

### 4.1 Global View: TLB Accesses Look Irregular

If one plots TLB accesses by timestamp or global access order, many GPU workloads appear scattered. This makes global temporal prediction ineffective.

Example interpretation:

```text
Global TLB access order -> scattered VPN sequence -> poor sequential/stride/distance prediction
```

### 4.2 Warp-Intrinsic View: VPNs Are More Regular

If one instead looks at VPNs requested by the threads of a single warp memory instruction, the pattern is much more regular.

The paper describes this as looking at the relationship between:

```text
thread index -> VPN
```

rather than:

```text
global TLB access timestamp -> VPN
```

### 4.3 Page-Table Locality

LATPC also observes that many PTEs requested by the same warp memory instruction reside in the same L4 page table.

This is important because:

- A 4 KB page table row can contain many PTEs.
- The paper uses a 512-page boundary constraint.
- PTEs within the same L4 page table can often be loaded with high DRAM row buffer locality.
- This enables page walk batching with low additional DRAM overhead.

### 4.4 Mechanism-Observation Mapping

| Observation | Mechanism Enabled |
|---|---|
| VPNs within a warp often form stride-like groups | Regularity Detector |
| Multiple misses from a warp can be represented by base + stride + index | LATC |
| PTEs for intra-warp translations often reside in the same L4 page table | LATP |
| PTW and L1 TLB MSHR contention dominate translation latency | Combined LATPC |

---

## 5. Mechanism 1 - Regularity Detector

### 5.1 Mechanism Card

```yaml
mechanism_name: "Regularity Detector"
short_name: "RD"
paper_section: "Section 5.2"
mechanism_type: "Hardware detection logic"
pipeline_location: "After TLB coalescer, before L1 TLB"
input: "Unique VPNs generated by the TLB coalescer for one warp memory instruction"
output: "Triples of the form <VPN, Stride, Index>"
main_goal: "Identify intra-warp VPN regularity"
downstream_consumers:
  - "LATC"
  - "LATP"
core_observation: "VPNs within one warp memory instruction often have few unique strides"
```

### 5.2 Purpose

The Regularity Detector is the front-end component of LATPC. It converts the set of unique VPNs requested by a warp memory instruction into structured metadata that later components can use.

The output metadata is:

```text
<VPN, Stride, Index>
```

This metadata allows LATPC to know whether a translation request belongs to a strided group.

### 5.3 Demand vs Prefetch Encoding

LATPC uses the following encoding:

```text
Demand translation:
  <VPN, 0, 0>

Prefetch translation:
  <VPN, non-zero Stride, non-zero Index>
```

The first VPN of a group is treated as the demand request. Later VPNs in the same strided group can be treated as associated prefetch requests.

### 5.4 Base VPN Reconstruction

For a strided group, LATPC can reconstruct the base VPN:

```text
BaseVPN = VPN - Stride * Index
```

This is the key relation used later by both LATC and LATP.

### 5.5 Example

Suppose the TLB coalescer outputs the following VPN sequence for one warp memory instruction:

```text
0x1000, 0x1004, 0x1008, 0x100c, 0x1011, 0x1028, 0x1025, 0x1022
```

The Regularity Detector can classify this sequence into groups:

```text
Group A:
  0x1000 -> demand, stride 0, index 0
  0x1004 -> prefetch, stride 4, index 1
  0x1008 -> prefetch, stride 4, index 2
  0x100c -> prefetch, stride 4, index 3

Group B:
  0x1011 -> demand, stride 0, index 0
  0x1028 -> prefetch, stride 23, index 1

Group C:
  0x1025 -> demand, stride 0, index 0
  0x1022 -> prefetch, stride -3, index 1
```

Equivalent output:

```text
<0x1000, 0, 0>
<0x1004, 4, 1>
<0x1008, 4, 2>
<0x100c, 4, 3>
<0x1011, 0, 0>
<0x1028, 23, 1>
<0x1025, 0, 0>
<0x1022, -3, 1>
```

### 5.6 Hardware State

The paper describes the Regularity Detector as a small state machine with registers and combinational logic.

Important state fields:

| Field | Width | Purpose |
|---|---:|---|
| Base VPN | 36 bits | Base VPN of current group |
| Prev. VPN | 36 bits | Previous VPN in sequence |
| Prev. Stride | 9 bits | Previously detected stride |
| Prev. Index | 5 bits | Index within current strided group |
| Prev. Valid | 1 bit | Whether previous VPN is valid |

### 5.7 Why 9-Bit Stride?

LATPC uses 9-bit stride information because:

- 9 bits cover 512 possible page offsets.
- 512 PTEs correspond to a 4 KB L4 page table row.
- This keeps coalesced translations within the same L4 page table boundary.
- This supports row-buffer-local L4 page table access in LATP.

### 5.8 Why 5-Bit Index?

The index is 5 bits because a warp has up to 32 threads:

```text
2^5 = 32
```

The index identifies a translation within a warp-level strided group.

### 5.9 How It Handles Negative Strides

The paper states that the Regularity Detector can detect negative strides by allowing subtractor underflow and using the resulting stride value. Ambiguity between positive and negative interpretations is resolved by computing base VPN using only the lower 9 bits, avoiding borrow propagation.

### 5.10 What Makes It Different from Existing Prefetchers?

Existing prefetchers:

```text
observe global TLB access stream -> predict future VPNs
```

Regularity Detector:

```text
observe one warp memory instruction -> detect actual intra-warp VPN structure
```

This means RD is less speculative than global stream prefetchers. It operates on translations already implied by the currently issued warp memory instruction.

### 5.11 SOTA Comparison Points

| Comparison Axis | Regularity Detector | Prior Global Prefetchers |
|---|---|---|
| Prediction scope | One warp memory instruction | Global TLB stream |
| Correlation exploited | Thread index to VPN | Time/order to VPN |
| Sensitivity to interleaving | Lower | Higher |
| Target workloads | Regular and irregular workloads with intra-warp structure | Mostly regular global streams |
| Output | Structured triple `<VPN, Stride, Index>` | Predicted future VPN/PTE |

### 5.12 Limitations

Regularity Detector is most useful when:

- a warp memory instruction touches multiple pages;
- the unique VPNs have stride-like structure;
- the TLB coalescer output preserves enough thread-index-related order.

It is less useful when:

- the warp has low page divergence;
- the VPNs are effectively random within a warp;
- there are few TLB misses to optimize.

---

## 6. Mechanism 2 - LATC: Locality-Aware TLB MSHR Compression

### 6.1 Mechanism Card

```yaml
mechanism_name: "Locality-Aware TLB MSHR Compression"
short_name: "LATC"
paper_section: "Section 5.3"
mechanism_type: "L1 TLB MSHR microarchitecture modification"
pipeline_location: "L1 TLB MSHR"
input: "<VPN, Stride, Index> metadata from Regularity Detector"
output: "Compressed tracking of multiple outstanding L1 TLB misses"
main_goal: "Reduce L1 TLB MSHR reservation failures"
standalone_speedup: "1.20x GMean"
```

### 6.2 Purpose

LATC compresses multiple outstanding L1 TLB misses into one MSHR entry when those misses belong to the same strided VPN group.

The motivation is that high page divergence can make one warp memory instruction require many translations. If many of them miss in the L1 TLB, they can consume many MSHR entries.

### 6.3 Baseline MSHR Behavior

In a conventional design:

```text
1 outstanding L1 TLB miss -> 1 L1 TLB MSHR entry
```

This creates a bottleneck when a warp memory instruction has many L1 TLB misses.

### 6.4 LATC Behavior

In LATC:

```text
multiple strided outstanding L1 TLB misses -> 1 compressed L1 TLB MSHR entry
```

The matching relation is:

```text
VPN = BaseVPN + Stride * Index
```

### 6.5 Traditional MSHR Entry vs LATC MSHR Entry

| Field | Traditional L1 TLB MSHR | LATC L1 TLB MSHR |
|---|---|---|
| Valid field | 1-bit valid | 32-bit ValidMask |
| Tag | VPN | BaseVPN |
| Stride | None | 9-bit Stride |
| Index handling | None | 5-bit Index used for matching |
| Max represented VPNs | 1 | Up to 32 |
| Subentries | Existing wakeup tracking | Reused; no major modification |

### 6.6 ValidMask Semantics

The 32-bit ValidMask tracks which indexed translations are in flight:

```text
ValidMask[i] = 1 means:
  BaseVPN + Stride * i is currently outstanding
```

This aligns naturally with a 32-thread warp.

### 6.7 Insert Operation - Conceptual Pseudocode

```text
Insert(VPN, Stride, Index, WarpID):
  1. Search existing MSHR entries.
  2. If an entry matches BaseVPN and Stride:
       a. Mark the requesting warp in subentries.
       b. If ValidMask[Index] is already set:
            return MSHR hit / hit-under-miss.
       c. Otherwise:
            set ValidMask[Index].
            return MSHR miss / miss-under-miss.
  3. If no matching entry exists:
       a. Find an empty MSHR entry.
       b. Set BaseVPN = VPN - Stride * Index.
       c. Set Stride.
       d. Set ValidMask[Index].
       e. Mark WarpID in subentries.
       f. return MSHR miss.
  4. If no empty entry exists:
       return MSHR reservation failure.
```

### 6.8 Erase Operation - Conceptual Pseudocode

```text
Erase(VPN, Stride, Index):
  1. Search for the compressed MSHR entry matching VPN, Stride, and Index.
  2. Clear ValidMask[Index].
  3. Replay or wake stalled warp(s) recorded in subentries.
  4. If ValidMask becomes all zero, deallocate the MSHR entry.
```

### 6.9 Example

Suppose a warp generates misses for:

```text
0x8, 0xa, 0xc, 0xe
```

with:

```text
BaseVPN = 0x8
Stride = 2
Indices = 0, 1, 2, 3
```

Traditional MSHR:

```text
0x8 -> MSHR entry 0
0xa -> MSHR entry 1
0xc -> MSHR entry 2
0xe -> MSHR entry 3
```

LATC:

```text
One MSHR entry:
  BaseVPN = 0x8
  Stride = 2
  ValidMask = 0b000...1111
```

### 6.10 Why LATC Matters

The paper reports:

```text
Baseline L1 TLB MSHR reservation failure rate: 68.84%
Avatar L1 TLB MSHR reservation failure rate:   66.33%
LATPC L1 TLB MSHR reservation failure rate:    43.02%
Avatar + LATPC failure rate:                   39.73%
```

Interpretation:

- Avatar hides/speculates translation but does not solve MSHR pressure.
- LATC directly reduces MSHR entry demand.
- LATC is therefore complementary to speculation-based methods.

### 6.11 Standalone Performance

The paper evaluates LATC separately:

```text
LATC-only GMean speedup: 1.20x
```

This means MSHR compression alone provides meaningful performance gain.

### 6.12 SOTA Comparison Points

| Comparison Axis | LATC | Avatar | Traditional TLB Prefetchers |
|---|---|---|---|
| Primary bottleneck | L1 TLB MSHR contention | Translation latency exposure | TLB misses |
| Reduces number of MSHR entries needed? | Yes | No | No |
| Requires intra-warp regularity? | Yes | No | Usually no |
| Orthogonal to speculative translation? | Yes | N/A | Mostly yes |
| Main structural change | MSHR valid mask and stride matching | Speculation metadata | Prefetcher tables/buffers |

### 6.13 Limitations

LATC is less beneficial when:

- TLB MSHR contention is low;
- warp memory instructions usually touch one page;
- VPNs cannot be represented compactly by base + stride + index;
- MSHR capacity is already large enough for the workload.

---

## 7. Mechanism 3 - LATP: Locality-Aware TLB Prefetching

### 7.1 Mechanism Card

```yaml
mechanism_name: "Locality-Aware TLB Prefetching"
short_name: "LATP"
paper_section: "Section 5.4"
mechanism_type: "Page Table Walker and Page Walk Buffer extension"
pipeline_location: "GMMU / PTW / Page Walk Buffer"
input: "<VPN, Stride, Index> metadata from Regularity Detector"
output: "Multiple PTEs loaded for translations in the same L4 page table"
main_goal: "Reduce PTW occupancy and page walk queueing delay"
standalone_speedup: "1.28x GMean"
```

### 7.2 Purpose

LATP batches page table walks for multiple translations associated with a warp memory instruction.

Instead of walking the page table independently for each TLB miss, LATP uses the detected intra-warp group to load multiple PTEs from the same L4 page table.

### 7.3 Key Locality

The paper reports that translations within a warp memory instruction frequently fall in the same L4 page table:

```text
Overall average:      80.20%
Regular workloads:   93.38%
Irregular workloads: 67.02%
```

This locality enables efficient batching.

### 7.4 Why Same L4 Page Table Matters

If multiple requested PTEs are in the same L4 page table:

- The upper-level page table walk path is shared.
- The L1-L3 page table walks can be common.
- The L4 PTEs are near each other.
- The L4 PTEs often reside in the same 4 KB DRAM row.
- DRAM row buffer locality can reduce access cost.

### 7.5 512-Page Boundary Constraint

LATP constrains grouped translations within a 512-page boundary.

Reason:

```text
512 entries * 8 bytes per PTE = 4096 bytes = 4 KB
```

Thus, grouped L4 PTEs can fit in one 4 KB row/page-table region.

This explains why LATPC uses 9-bit stride:

```text
2^9 = 512
```

### 7.6 Page Walk Buffer Extension

LATP extends Page Walk Buffer entries similarly to LATC:

```text
Base Address + Stride + ValidMask
```

Traditional PW Buffer:

```text
Tracks one outstanding page walk
```

LATP PW Buffer:

```text
Tracks multiple related L4 page walks in one entry
```

### 7.7 What Changes in Page Walk Logic?

The paper keeps L1-L3 page table walk logic mostly unchanged. LATP mainly modifies L4 handling:

1. Traverse L1-L3 page table levels for the demand translation.
2. Coalesce subsequent requests into the same PW Buffer entry when possible.
3. Once the shared path is known, issue L4 page table accesses for the demand and associated prefetch translations.
4. Fill returned PTEs into L2 TLB and L1 TLBs.
5. Release related MSHR/PW Buffer state.

### 7.8 Conceptual Flow

```text
L1 TLB miss
  -> L2 TLB miss
  -> request enters PW Queue
  -> PTW checks whether request belongs to an existing strided group
  -> if yes, coalesce into existing PW Buffer entry
  -> if no, allocate new PW Buffer entry
  -> walk shared L1-L3 page table levels
  -> issue L4 PTE loads for grouped translations
  -> use DRAM row buffer locality
  -> fill returned PTEs into TLBs
```

### 7.9 Why It Is a TLB Prefetcher

LATP prefetches translations because not every loaded PTE corresponds to the first demand translation. Some PTEs are associated with other translations in the same warp memory instruction that are inferred by the Regularity Detector.

However, LATP is different from typical predictive prefetching:

```text
Traditional prefetcher:
  "This miss happened, so maybe a future miss will be VPN + k."

LATP:
  "This warp instruction already implies these related VPNs,
   and their PTEs are likely in the same L4 page table."
```

### 7.10 Standalone Performance

The paper evaluates LATP separately:

```text
LATP-only GMean speedup: 1.28x
```

This shows that reducing PTW queueing and occupancy is a major contributor.

### 7.11 Page Walk Stall Reduction

The paper reports:

```text
LATPC normalized page table walk stall cycles: 71.20% of baseline
Avatar normalized page table walk stall cycles: 75.54% of baseline
```

Interpretation:

- Avatar reduces exposed translation cost through speculation.
- LATP reduces PTW pressure through batching and locality.

### 7.12 Prefetch Coverage and Accuracy

The paper reports:

```text
LATPC prefetch coverage: 54.78%
Existing TLB prefetchers: less than 15% average coverage
```

The paper also argues that LATP has high accuracy because it prefetches translations based on detected intra-warp structure, rather than global temporal prediction.

For workloads with low page divergence, LATPC may issue no prefetches. The paper reports such workloads conservatively as 0% prefetch accuracy rather than 100%.

### 7.13 SOTA Comparison Points

| Comparison Axis | LATP | Sequential / Stride / Distance | Valkyrie |
|---|---|---|---|
| Prefetch trigger | Warp-level detected group | TLB miss stream | Returned PTE from PTW |
| Prefetch source | Same L4 page table | Predicted future PTEs | Replication of existing PTE |
| Uses page-table locality? | Yes | Not primarily | Inter-L1 locality, not L4 locality |
| Reduces PTW occupancy? | Yes | Can increase PTW pressure if inaccurate | Limited |
| Accuracy basis | Current warp instruction | Historical/global pattern | Sharing likelihood |

### 7.14 Limitations

LATP is less beneficial when:

- translations in a warp do not reside in the same L4 page table;
- page divergence is low;
- PTWs are already abundant;
- L2 TLB hit rate is high enough that page walks are rare;
- the cost of extra L4 PTE loads outweighs locality benefits.

---

## 8. Combined Design - LATPC

### 8.1 Composition

LATPC combines the three mechanisms:

```text
LATPC = Regularity Detector + LATC + LATP
```

Detailed flow:

```text
1. A warp memory instruction reaches LD/ST units.
2. The TLB coalescer extracts unique VPNs.
3. The Regularity Detector produces <VPN, Stride, Index> triples.
4. L1 TLB is probed.
5. On L1 TLB miss:
   a. LATC allocates or reuses a compressed L1 TLB MSHR entry.
   b. The request is sent to L2 TLB.
6. On L2 TLB miss:
   a. The request enters the page walk path.
   b. LATP coalesces related requests in the Page Walk Buffer.
   c. PTW batches L4 PTE loads from the same L4 page table.
7. Returned PTEs fill L2 TLB and L1 TLBs.
8. LATC releases the corresponding compressed MSHR bits.
9. Stalled warp translations are replayed.
```

### 8.2 Why LATC and LATP Are Complementary

LATP reduces PTW pressure:

```text
fewer independent page table walks
```

LATC reduces MSHR pressure:

```text
fewer L1 TLB MSHR entries needed
```

Using only one leaves the other bottleneck partly unresolved.

This is reflected in the standalone results:

```text
LATP only: 1.28x
LATC only: 1.20x
LATPC:     1.47x
```

### 8.3 Performance Summary

| Method | Type | GMean Speedup |
|---|---|---:|
| Baseline | No TLB prefetching | 1.00x |
| Sequential | Global TLB prefetching | 0.82x |
| Stride | Global/stride TLB prefetching | 1.00x |
| Distance | Distance-based TLB prefetching | 1.01x |
| Valkyrie | GPU TLB entry replication | 1.03x |
| Avatar | Speculative GPU address translation | 1.40x |
| LATC | MSHR compression only | 1.20x |
| LATP | Locality-aware TLB prefetching only | 1.28x |
| LATPC | LATC + LATP | 1.47x |
| Avatar + LATPC | Speculation + contention reduction | 1.64x |

### 8.4 Translation Latency Summary

| Method | Normalized Address Translation Latency | Direction |
|---|---:|---|
| Baseline | 100.00% | Reference |
| Valkyrie | 93.10% | Lower is better |
| Stride | 99.68% | Lower is better |
| Distance | 96.27% | Lower is better |
| Avatar | 71.24% | Lower is better |
| Sequential | 132.58% | Worse than baseline |
| LATPC | 69.46% | Best among listed individual methods |

### 8.5 Contention Metrics

| Metric | Baseline | Avatar | LATPC | Avatar + LATPC |
|---|---:|---:|---:|---:|
| L1 TLB MSHR reservation failure rate | 68.84% | 66.33% | 43.02% | 39.73% |
| Normalized page table walk stall cycles | 100.00% | 75.54% | 71.20% | Not specified in same table |

---

## 9. Hardware Cost

### 9.1 Added Components

LATPC adds or modifies:

1. **Regularity Detector per SM**
2. **L1 TLB MSHR tagging logic**
3. **L1 TLB MSHR valid field**
4. **Page Walk Buffer entries in the GMMU**
5. **PTW L4 page walk handling**

### 9.2 Storage Overhead

For L1 TLB MSHRs:

```text
extra bits per compressed MSHR entry = 32-bit ValidMask + 9-bit Stride - original 1-bit Valid
paper reports simplified requirement = 40 bits per MSHR entry
```

For 16 L1 TLB MSHR entries per SM and 30 SMs:

```text
40 bits * 16 entries * 30 SMs = 19,200 bits = 2,400 bytes
```

For PW Buffer:

```text
extra bits per entry = 9-bit Stride + 32-bit ValidMask = 41 bits
16 entries -> 656 bits = 82 bytes
```

Total storage:

```text
2,400 bytes + 82 bytes = 2,482 bytes ~= 2.48 KB
```

### 9.3 Area and Power

| Metric | Reported Value |
|---|---:|
| Additional chip area | 0.2581 mm2 |
| Additional power | 35.78 mW |
| Additional storage | 2.48 KB |
| Percent of baseline TU106 die area | 0.05801% |
| Percent of baseline TU106 TDP | 0.02045% |

### 9.4 Latency Overhead

The paper models:

```text
Regularity Detector latency: 1 cycle
LATC MSHR tagging latency: 1 cycle
```

Reported impact:

```text
Additional latency reduces LATPC GMean speedup by 0.45%
```

---

## 10. Evaluation Setup

### 10.1 Simulator

The paper uses:

```text
Accel-Sim cycle-level GPU simulator
```

The simulator is extended to model:

- multi-level TLBs;
- page walk queue;
- page table walkers;
- page walk cache;
- address translation process.

### 10.2 Simulated GPU Configuration

| Component | Configuration |
|---|---|
| SMs | 30 SMs |
| Warp schedulers | 4 per SM |
| Warp slots | 8 per scheduler |
| Scheduling | Greedy-Then-Oldest (GTO) |
| Frequency | 1,365 MHz |
| L1 cache | 64 KB, 64-way, 20 cycles |
| L2 cache | 3 MB, 16-way, 160 cycles |
| DRAM | GDDR6, 6 GB, 12 channels |
| L1 TLB for 4 KB pages | 32 entries, 16 MSHR entries |
| L1 TLB for 2 MB pages | 16 entries, 8 MSHR entries |
| L2 TLB for 4 KB pages | 1,024 entries, 128 MSHR entries |
| L2 TLB for 2 MB pages | 128 entries, 128 MSHR entries |
| GMMU | 128-entry page walk queue, 16 PTWs |
| Page table | x86-64 4-level page table |

### 10.3 Workloads

The paper evaluates 24 workloads from:

- CUDA SDK
- Lonestar
- Pannotia
- Parboil
- Polybench
- Rodinia

Workload classes:

| Class | Meaning |
|---|---|
| Regular+Low | Regular memory access, low L2 TLB MPKI |
| Regular+High | Regular memory access, high L2 TLB MPKI |
| Irregular | Irregular memory access |

Selection criteria:

- memory footprint exceeding 4 MB, and/or
- high L2 TLB MPKI exceeding 10.

Memory footprint range:

```text
0.01 MB to 128.82 MB
average: 29.50 MB
```

### 10.4 Compared Baselines

| Method | Category |
|---|---|
| Baseline | No TLB prefetching |
| Valkyrie | GPU TLB prefetch / PTE replication |
| Sequential | Access-pattern TLB prefetching |
| Stride | Stride-based TLB prefetching |
| Distance | Distance-based TLB prefetching |
| Avatar | Speculative GPU address translation |
| LATP | LATPC component |
| LATC | LATPC component |
| LATPC | Full method |

---

## 11. Detailed SOTA Comparison Matrix

### 11.1 Mechanism-Level Comparison

| Method | Main Bottleneck Targeted | Prediction Scope | Handles PTW Contention | Handles L1 TLB MSHR Contention | Key Limitation |
|---|---|---|---:|---:|---|
| Sequential | TLB misses | Global TLB stream | No, may worsen it | No | Assumes next-page access |
| Stride | TLB misses | Global or PC-correlated stream | No | No | Global stream noisy on GPUs |
| Distance | TLB misses | Global miss distances | No | No | Sensitive to interleaving |
| Valkyrie | Inter-L1 TLB sharing | Across L1 TLBs / SMs | Limited | Limited | Replicates returned PTEs only |
| Avatar | Translation latency exposure | Speculative mapping | Partially hides | No | Still occupies MSHRs/PTWs in background |
| LATC | L1 TLB MSHR contention | Intra-warp VPN group | No | Yes | Needs compressible VPN group |
| LATP | PTW contention | Intra-warp VPN group + L4 locality | Yes | Indirectly | Needs page-table locality |
| LATPC | PTW + MSHR contention | Intra-warp VPN group | Yes | Yes | Most useful under page divergence |

### 11.2 Conceptual Difference

```text
Prior TLB prefetchers:
  predict future translations from historical/global access patterns.

LATPC:
  detects related translations already present in the current warp memory instruction,
  then compresses miss tracking and batches page table walks.
```

### 11.3 Why LATPC Is Not Just Another TLB Prefetcher

LATPC should be described as a **translation contention reduction mechanism**, not merely as a prefetcher.

Reasons:

1. It reduces **PTW occupancy** using LATP.
2. It reduces **L1 TLB MSHR occupancy** using LATC.
3. It uses **warp-level structure**, not only temporal prediction.
4. It is complementary to speculation-based methods such as Avatar.

---

## 12. Suggested Writing for a SOTA Comparison Section

### 12.1 Short Version

```text
LATPC exploits intra-warp VPN regularity and page-table locality to reduce GPU address translation overhead. Unlike prior TLB prefetchers that infer future translations from the global TLB miss stream, LATPC detects stride-like VPN groups within each warp memory instruction. It then compresses multiple L1 TLB misses into fewer MSHR entries and batches page table walks for PTEs located in the same L4 page table. This allows LATPC to reduce both L1 TLB MSHR contention and PTW contention, achieving a 1.47x geometric mean speedup over the baseline and outperforming prior TLB prefetchers and Avatar.
```

### 12.2 Longer Version

```text
LATPC addresses GPU address translation overhead by targeting two structural bottlenecks: limited page table walkers and limited L1 TLB MSHR entries. The key observation is that although GPU TLB accesses appear irregular when viewed in global access order, translations generated by threads within the same warp memory instruction often exhibit stride-like VPN regularity and page-table locality. LATPC first uses a Regularity Detector after the TLB coalescer to encode each translation request as <VPN, Stride, Index>. This metadata enables LATC, which compresses multiple outstanding L1 TLB misses into a single MSHR entry using BaseVPN + Stride * Index, and LATP, which batches L4 page table walks for related translations whose PTEs reside in the same page table. Compared with Sequential, Stride, and Distance TLB prefetchers, LATPC avoids relying on noisy global TLB access streams. Compared with Valkyrie, it prefetches additional PTEs rather than only replicating returned PTEs across L1 TLBs. Compared with Avatar, it directly reduces MSHR and PTW contention rather than only speculating translation results. These properties allow LATPC to achieve 1.47x GMean speedup, while remaining complementary to Avatar, with the combined Avatar+LATPC design reaching 1.64x.
```

### 12.3 One-Sentence Positioning

```text
LATPC is best understood as a warp-aware GPU address translation mechanism that converts intra-warp VPN regularity into both MSHR compression and page-walk batching.
```

### 12.4 Claim for Related Work

```text
Prior TLB prefetching mechanisms mainly focus on predicting future translations, whereas LATPC focuses on reducing the hardware contention created by translations that are already implied by the current warp memory instruction.
```

### 12.5 Claim for Motivation

```text
The motivation for LATPC is that page divergence creates many simultaneous translation requests, and the resulting bottleneck is not only TLB miss latency but also contention for TLB MSHR entries and page table walkers.
```

---

## 13. GPT-Friendly Extraction Schema

This section is designed for directly copying into another GPT prompt.

```yaml
paper: "LATPC"
problem:
  - "GPU virtual memory introduces address translation overhead."
  - "High page divergence causes many translations per warp memory instruction."
  - "TLB misses consume L1 TLB MSHRs and trigger page table walks."
  - "Limited MSHRs and PTWs create major latency bottlenecks."
observations:
  - "Global TLB access order appears scattered on GPUs."
  - "Within a warp memory instruction, VPNs often have stride-like regularity."
  - "Average unique VPN strides per warp memory instruction is 1.96."
  - "80.20% of translations within a warp memory instruction fall in the same L4 page table on average."
mechanisms:
  regularity_detector:
    location: "After TLB coalescer, before L1 TLB"
    input: "Unique VPNs from one warp memory instruction"
    output: "<VPN, Stride, Index>"
    function: "Detect intra-warp VPN regularity"
  latc:
    full_name: "Locality-Aware TLB MSHR Compression"
    location: "L1 TLB MSHR"
    function: "Represent multiple outstanding L1 TLB misses with one compressed MSHR entry"
    representation: "BaseVPN + Stride + 32-bit ValidMask"
    target_bottleneck: "L1 TLB MSHR reservation failures"
    standalone_speedup: "1.20x"
  latp:
    full_name: "Locality-Aware TLB Prefetching"
    location: "Page Table Walker / Page Walk Buffer"
    function: "Batch page walks and prefetch multiple PTEs from the same L4 page table"
    representation: "Base Address + Stride + 32-bit ValidMask"
    target_bottleneck: "PTW contention and page walk queueing"
    standalone_speedup: "1.28x"
combined_result:
  latpc_speedup: "1.47x GMean"
  avatar_speedup: "1.40x GMean"
  avatar_plus_latpc_speedup: "1.64x GMean"
hardware_cost:
  area: "0.2581 mm2"
  power: "35.78 mW"
  storage: "2.48 KB"
main_sota_difference:
  - "Uses intra-warp VPN regularity rather than global TLB access stream prediction."
  - "Reduces both PTW contention and L1 TLB MSHR contention."
  - "Complementary to speculative translation methods such as Avatar."
```

---

## 14. Mechanism-to-Figure/Section Map

| Paper Location | Content | Useful for SOTA Comparison |
|---|---|---|
| Figure 1 | Address translation latency breakdown | Shows PTW and MSHR bottlenecks |
| Figure 2 | Memory footprint and page divergence | Shows page divergence increases with footprint |
| Figure 3 | Number of translations per warp memory instruction | Motivates warp-level translation handling |
| Section 3.1 | Limits of TLB prefetching | Explains why Sequential/Stride/Distance/Valkyrie are weak |
| Figure 4 | Existing TLB prefetcher IPC | Quantitative comparison against prior prefetchers |
| Figure 5 | TLB access pattern examples | Shows global access disorder |
| Section 3.2 | Large page discussion | Shows large pages do not remove bottlenecks |
| Figure 6 | PTW/MSHR bottlenecks under 4 KB and 2 MB pages | Shows contention remains under large pages |
| Section 4.1 | Regularity analysis | Motivates Regularity Detector |
| Figure 7 | TLB access viewed by timestamp vs thread index | Shows why thread-index view is better |
| Figure 8 | Unique VPN strides per warp instruction | Quantifies regularity |
| Section 4.2 | Locality analysis | Motivates LATP |
| Figure 9 | Same L4 PT ratio | Quantifies page-table locality |
| Section 5.2 | Regularity Detector | Mechanism 1 |
| Section 5.3 | LATC | Mechanism 2 |
| Section 5.4 | LATP | Mechanism 3 |
| Section 5.5 | Hardware cost | Area/power/storage overhead |
| Section 6.2 | Performance analysis | Main speedup results |
| Section 6.3 | Hardware contention analysis | PTW stall and MSHR failure reduction |
| Section 6.4 | Prefetch coverage and accuracy | Shows prefetch effectiveness |
| Section 6.5 | Avatar comparison | Shows orthogonality |
| Section 6.6 | Large pages | Shows robustness under 2 MB pages |
| Section 6.7 | Sensitivity study | Shows benefit across TLB/PTW sizes |

---

## 15. Potential Critiques and Caveats

### 15.1 Workload Selection

The paper intentionally selects workloads that stress virtual memory:

- memory footprint exceeding L2 TLB reach, and/or
- high L2 TLB MPKI.

This is reasonable for evaluating address translation mechanisms but may make benefits look larger than on workloads with low page divergence or high TLB locality.

### 15.2 Dependence on Intra-Warp Structure

LATPC depends on regularity and locality within warp memory instructions. If a workload has:

- low page divergence;
- random per-thread page accesses;
- low TLB miss rate;
- little L4 page-table locality;

then LATPC has less opportunity.

### 15.3 Hardware Integration Complexity

Although the paper reports low area/power/storage overhead, real implementation would need to carefully integrate:

- extra metadata through the L1 TLB and L2 TLB path;
- modified MSHR lookup/matching;
- PW Buffer coalescing;
- PTW issue logic for grouped L4 walks;
- correctness interactions with replay, invalidation, and coherence.

### 15.4 Interaction with Page Size

LATPC still helps under 2 MB pages, but speedup drops from:

```text
4 KB pages: 1.47x
2 MB pages: 1.18x
```

This suggests part of the benefit comes from mitigating pressure that large pages can already reduce.

### 15.5 Simulator-Based Evaluation

The results are from Accel-Sim with extensions. As with any architecture paper, conclusions depend on:

- model fidelity;
- workload selection;
- chosen GPU configuration;
- TLB/PTW parameters;
- memory system timing;
- implementation assumptions for prior work.

---

## 16. How to Use LATPC in Your SOTA Table

### 16.1 Recommended Columns

Use these columns when building your SOTA comparison table:

```text
Method
Year
Target platform
Target bottleneck
Granularity
Prediction/detection basis
Main hardware change
Handles PTW contention?
Handles MSHR contention?
Works with irregular workloads?
Reported speedup
Storage/area overhead
Limitations
```

### 16.2 Example Row for LATPC

| Method | Year | Target | Granularity | Main Idea | PTW Contention | MSHR Contention | Speedup | Overhead |
|---|---:|---|---|---|---:|---:|---:|---|
| LATPC | 2025 | GPU address translation | Warp memory instruction | Detect intra-warp VPN regularity, compress TLB MSHRs, batch L4 page walks | Yes | Yes | 1.47x | 0.2581 mm2, 35.78 mW, 2.48 KB |

### 16.3 Best One-Line LATPC Description

```text
LATPC is a warp-aware GPU address translation mechanism that exploits intra-warp VPN regularity and page-table locality to compress L1 TLB MSHR usage and batch page table walks.
```

---

## 17. Key Numbers to Remember

| Number | Meaning |
|---:|---|
| 39.37% | Average address translation latency due to page table walks |
| 53.09% | Average address translation latency due to L1 TLB MSHR reservation failures |
| 75.32% | Warp memory instructions requiring multiple translations |
| 28.86% | Warp memory instructions requiring 32 translations |
| 1.96 | Average unique VPN strides per warp memory instruction |
| 80.20% | Average translations within a warp instruction in same L4 PT |
| 1.20x | LATC-only speedup |
| 1.28x | LATP-only speedup |
| 1.47x | Full LATPC speedup |
| 1.64x | Avatar + LATPC speedup |
| 54.78% | LATPC prefetch coverage |
| 43.02% | LATPC L1 TLB MSHR reservation failure rate |
| 69.46% | LATPC normalized address translation latency |
| 71.20% | LATPC normalized page table walk stall cycles |
| 2.48 KB | LATPC storage overhead |
| 0.2581 mm2 | LATPC area overhead |
| 35.78 mW | LATPC power overhead |

---

## 18. Final Takeaway

LATPC's contribution is not simply "better TLB prefetching." Its real contribution is the combination of:

```text
1. warp-instruction-level VPN regularity detection
2. compressed L1 TLB MSHR tracking
3. locality-aware L4 page table walk batching
```

This combination attacks the two dominant GPU address translation bottlenecks:

```text
limited PTWs + limited L1 TLB MSHRs
```

For SOTA comparison, LATPC should be positioned as:

```text
a warp-aware, locality-aware, contention-reducing GPU address translation mechanism
```

rather than only:

```text
a TLB prefetcher
```

