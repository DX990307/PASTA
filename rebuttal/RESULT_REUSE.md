# Reuse First, Across All Experiments

The user requires all experiment groups to reuse compatible existing results
before launching a new execution. A different binary name/build is not a
reason to rerun; hardware, input, allocation/initialization, modeled behavior
and sampling must match. Real model changes must be identified explicitly.
One physical run can supply multiple logical experiment references.

## R2/R3 Correction

The frozen plan retains 84 logical configurations, not 84 mandatory new runs.
`plans/r2-r3-reuse.json` assigns all 28 Baseline16/PASTA16 points to existing
R1/R6 sources, and all 14 M1 points to the existing R10 family. Strictly
verified completions are `reuse_external`; unfinished sources are
`external_pending`, never reported as completed. The remote runner skips
both categories. Only B20, Neighbor and LATPC are new remote executions:
**42 runs instead of 84**. A pending control is executed once by its existing
owner, not separately in this package. R10 M1 ownership stays on its assigned
R10 machine/local source queue; no ownership transfer is performed here.

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
reuse faulty old full-PASTA response paths.

If a remote scheduler has already started, `git pull` alone does not update
its running Python code. Stop only that scheduler, leaving detached workers
running, and restart the same command to adopt them. Do not use a broad
simulator/process kill. The updated script affects future launches only.

## Other Groups

| Group | Reuse Rule |
| --- | --- |
| R1 | Keep all qualified capacity points; fill only missing points. |
| R2/R3 | Share ordinary Baseline/PASTA controls and R10 M1; add only new B20/Neighbor/LATPC. |
| R4 | Reuse default PASTA and its read-only PLT shadow counters; add only missing legal no-PLT points. |
| R5 | Reuse completed points of the same fixed-service walker model; do not substitute native/cache-dependent walks. |
| R6 | Reuse ordinary PASTA and compatible existing no-Assist results. |
| R7 | Reuse exact controlled ready-time/request cohorts; closed-loop application runs are not the same experiment. |
| R8 | Reuse each existing scale/input point; 48 GPM is not enough if weak-scaling inputs or launch geometry differ. |
| R9 | Reuse results under the same network queues/arbitration; legacy and fixed-ingress models are not interchangeable. |
| R10 | Share compatible R2 M1, R4 no-PLT, R6 no-Assist and common full-PASTA controls. |
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
