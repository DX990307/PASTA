# Reuse First, Across All Experiments

The user requires all experiment groups to reuse compatible existing results
before launching a new execution. A different binary name/build is not a
reason to rerun; hardware, input, allocation/initialization, modeled behavior
and sampling must match. Real model changes must be identified explicitly.
One physical run can supply multiple logical experiment references.

## R2/R3 Correction

The frozen plan retains 84 logical configurations, not 84 mandatory new runs.
The latest explicit user instruction requires fresh R3 measurements for
Baseline/PASTA as well as Neighbor/LATPC, regardless of old common-data
availability. Old R1/R6 results no longer suppress the 28 R3 control launches.
`plans/r2-r3-reuse.json` now contains only 14 M1 scope exclusions. The remote
package therefore has **70 runs: R2 B20 x14 plus R3 four methods x14**.
M1's independent R10 jobs/results and ownership are unchanged. Old controls
remain available for R2/R4 and other compatible comparisons, not as substitutes
for this explicitly requested fresh R3 collection. Existing attempts already
started/completed within this remote R3 package are preserved and adopted.

The other remote packages also have reuse sidecars. The R5 machine15 package
now references its two qualified completed PageRank (4,16) controls rather
than running them again: 64 remaining executions instead of 66. The machine5
R10 package currently has no newly completed points among its 42 assigned
executions; its 14 historical references already sit outside that run queue.
These are audit-time counts, not a promise that no more results have completed.

No raw results or driver-time values are uploaded. The sidecar contains
reference identities and integrity/configuration evidence only. Remote
summary CSV leaves external result values blank and records the source;
central aggregation must resolve and revalidate those source files. Missing
new counters are not invented as measured zero. Already started remote
attempts are preserved, not killed or overwritten by this policy.

The original plan, binary/source hashes, input geometry, AkitaRTM setting and
worker/memory constraints are unchanged. Cross-build eligibility is audited:
v11/v12 differ only in corrected PageRank host launch; PR uses corrected v12.
The new v13 methods are inactive for ordinary controls and the default timing
and actual configurations were CPU-tested. M1's v10/v12 compatibility uses
the existing paired demand-only composed-flow validation, not permission to
reuse faulty old full-PASTA response paths. The M1 compatibility discussion
is historical; M1 is no longer required for the narrowed R2/R3 scope.

The old coverage audit in `provenance/r234-scope.json` is historical only.
Its 27 completed controls/one pending old source do not gate the new R3 runs.
This is an explicit data-collection exception to the general reuse-first rule;
it does not authorize blanket reruns of other experiment groups.

If a remote scheduler has already started, `git pull` alone does not update
its running Python code. Stop only that scheduler, leaving detached workers
running, and restart the same command to adopt them. Do not use a broad
simulator/process kill. The updated script affects future launches only.

## Other Groups

| Group | Reuse Rule |
| --- | --- |
| R1 | Keep all qualified capacity points; fill only missing points. |
| R2 | Reuse Baseline16/PASTA16; run only B_Neq (currently estimated B20). No M1 dependency. |
| R3 | Explicit exception: fresh Baseline/PASTA/Neighbor/LATPC across FULL14 for common mechanism data. |
| R4 | Reuse default PASTA and its read-only PLT shadow counters; add only missing legal no-PLT points. |
| R5 | Reuse completed points of the same fixed-service walker model; do not substitute native/cache-dependent walks. |
| R6 | Reuse ordinary PASTA and compatible existing no-Assist results. |
| R7 | Reuse exact controlled ready-time/request cohorts; closed-loop application runs are not the same experiment. |
| R8 | Reuse each existing scale/input point; 48 GPM is not enough if weak-scaling inputs or launch geometry differ. |
| R9 | Reuse results under the same network queues/arbitration; legacy and fixed-ingress models are not interchangeable. |
| R10 | Reuse its own M1 results and compatible R4 no-PLT/R6 no-Assist/full-PASTA controls; it is independent of narrowed R2. |
| R11 | Reuse existing operators only with matching shapes, repetitions, input/initial state and sampling. |
| R12 | Share R3 controls when platform/input/model match; implement and execute only the new MPW points. |
| R13 | Reuse existing validation/calibration records; test only uncovered behavior. |

Existing frozen R1/R5/R6/R8/R9/R10 manifests already have shared-reference
routing; retain it rather than creating another run for each figure.
Missing a metric does not invalidate an existing performance result: derive
it from original counters/traces first. If unavailable, keep the performance
result and identify the missing metric; supplementary measurement requires
its own scope/configuration qualification, not a blanket rerun.

Counts distinguish logical points, physical executions, qualified completions,
running dependencies and new jobs. A queued/running/failed source is not a
result. Equal WG counts are not automatically identical WG populations;
preserve that distinction and all negative results in the comparisons.
