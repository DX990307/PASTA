# R12 Simple SOTA Models

This candidate is an isolated copy of `r23-baseline-v13`; no older candidate
or experiment binary is modified. The four modes are deliberately coarse
mechanism models because the original implementations are unavailable. They
must be labeled `-Simple` in plots and rebuttal text.

| Mode | Modeled action | Deliberately omitted |
|---|---|---|
| `MPW-Simple` | Up to 8 independent requests occupy one abstract walker capacity slot; each request retains its own logical page-table accesses. | Original grouping heuristic, per-level memory issue timing and batch hardware. |
| `SoftWalker-Simple` | Adds 32 ideal software walk slots per GPM. | PW-warp instructions, CU/cache/register interference and In-TLB MSHR details. |
| `TransFW-Simple` | Known non-local pages bypass the local GMMU walk and are sent to the shared IOMMU; a congested shared queue uses a 100-cycle abstract remote-prefix service. | PRT/FT cuckoo filters, false positives, explicit peer-GPU PW caches and racing duplicate responses. |
| `PWS-Simple` | Doubles effective walker capacity to represent ideal borrowing from idle tenants. | Real tenant identities, fairness policy, victim selection and interference. |

All modes require the historical per-VPN MSHR Baseline configuration and reject
PASTA Flex/PTCL, PLT, idle Assist, Neighbor, LATPC and legacy MMU coalescing.
They retain the exact historical FULL14 command and add only one
`-sota-simple=<mode>` flag. Existing `-report-all` traffic counters are used so
R3 Baseline/PASTA/Neighbor/LATPC results can be reused without rerunning them.

Additional metrics include walker starts/completions, logical page-table
accesses, service cycles, requests using capacity beyond the baseline walker
count, Trans-FW forwarding events and abstract cycles avoided.
