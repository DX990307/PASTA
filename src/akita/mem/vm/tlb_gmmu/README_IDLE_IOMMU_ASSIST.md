# Simple Idle-IOMMU Assist for GMMU L2 TLB

这个文档描述一个先于 distance-aware translation prefetching 的简单版本：

```text
当 GMMU L2 TLB 已经完成内部 PTE lookup，并确认 local demand request 需要
downstream page walk 时，如果 shared IOMMU page walker 当前有空位，就把这个
local request 改送到 IOMMU 路径。
```

它不是 prefetch，也不是 duplicate racing。它只是在 request 已经 miss、已经准备
发 downstream 的时刻，选择一个当前空闲的 walker。

## Goal

这个 feature 想利用 shared IOMMU 的空闲 page-walk bandwidth：

```text
baseline:
  local page miss -> GMMU local PTW
  remote page miss -> IOMMU PTW

idle-IOMMU assist:
  local page miss + IOMMU busy -> GMMU local PTW
  local page miss + IOMMU free -> IOMMU PTW
  remote page miss             -> IOMMU PTW
```

核心目标是降低 local GMMU PTW 的排队压力，同时不改变 L2 TLB lookup、MSHR
coalescing、TLB fill、response merge 的语义。

## Non-Goals

这个版本不做以下事情：

```text
1. 不学习 stride / distance / access pattern。
2. 不提前生成新的 translation request。
3. 不同时发 local PTW 和 IOMMU PTW。
4. 不复制 request。
5. 不改变 remote page 的正常 IOMMU 路径。
6. 不改变 MSHR key、bitmap、coalescing 语义。
```

所以它应该被看作一种 downstream routing policy，而不是 prefetcher。

## Trigger Point

触发点只放在 GMMU L2 TLB ready-to-downstream 阶段：

```text
incoming request
  -> MSHR coalescing
  -> internal PTE lookup queue
  -> PTE lookup completes
  -> miss bits become ready-to-downstream
  -> choose local PTW or IOMMU PTW
```

也就是说，只有当 request 已经完成内部 L2 TLB lookup，并且确认需要 page walk
时，才考虑 idle-IOMMU assist。

不要在以下位置触发：

```text
topPort backlog
MSHR allocation
PTE lookup waiting queue
PTCL coalescing update
L1 TLB request arrival
```

这样可以避免把 feature 变成额外 prefetch，也可以避免改变 L2 TLB 内部压力。

## Routing Rule

伪代码：

```text
function routeDownstream(req):
    if page_is_remote(req):
        send_to_iommu(req)
        stats.iommu_req_count++
        return

    if !idle_iommu_assist_enabled:
        send_to_local_gmmu_ptw(req)
        stats.local_req_count++
        return

    if shared_iommu.HasFreePTW():
        send_to_iommu(req)
        stats.iommu_req_count++
        stats.idle_iommu_assist_issued++
        return

    stats.idle_iommu_assist_blocked_busy++
    stats.idle_iommu_assist_local_fallback++
    send_to_local_gmmu_ptw(req)
    stats.local_req_count++
```

关键点：

```text
remote page:
  原本就应该走 IOMMU，不计入 assist issued。

local page + IOMMU free:
  改路由到 IOMMU，计入 assist issued。

local page + IOMMU busy:
  保持原行为，走 local GMMU PTW。
```

## Data Flow

```text
                 GMMU L2 TLB
        +----------------------------+
        | MSHR + PTE lookup complete |
        +-------------+--------------+
                      |
                      v
             ready-to-downstream
                      |
                      v
        +----------------------------+
        | page ownership / locality  |
        +------+------+--------------+
               |      |
          remote      local
               |      |
               v      v
            IOMMU   assist enabled?
                      |
              +-------+-------+
              |               |
             no              yes
              |               |
              v               v
          local PTW    shared IOMMU free?
                              |
                      +-------+-------+
                      |               |
                     no              yes
                      |               |
                      v               v
                  local PTW         IOMMU
```

IOMMU 返回后不需要特殊 response 语义：

```text
IOMMU response
  -> existing processRsp()
  -> installPage()
  -> update MSHR bitmap
  -> return ready demand response to L1
```

只要 request 没有被复制，response 就不会产生 duplicate completion。

## Required State

GMMU L2 TLB 需要新增一个开关：

```text
idleIOMMUAssistEnabled bool
```

builder 暴露配置：

```go
WithIdleIOMMUAssist(enabled bool)
```

runner flag：

```text
-gmmu-idle-iommu-assist
```

GMMU L2 TLB 还需要能读取 shared MMU 的 PTW occupancy 状态。可以用一个很小的
interface，避免直接绑定具体实现：

```go
type sharedPTWStateProvider interface {
    HasFreePTW() bool
    PTWInflight() int
    PTWCapacity() int
}
```

只要 `mmu.MMU` 已经实现这些方法，runner 在构建 GMMU L2 TLB 时把 shared MMU
传进去即可。

## Stats

新增 stats：

```text
idle_iommu_assist_enabled
idle_iommu_assist_issued
idle_iommu_assist_blocked_busy
idle_iommu_assist_local_fallback
```

含义：

```text
idle_iommu_assist_enabled:
  当前 experiment 是否打开这个 feature。

idle_iommu_assist_issued:
  local demand request 因为 IOMMU free 被改送 IOMMU 的次数。

idle_iommu_assist_blocked_busy:
  local demand request 想使用 assist，但 shared IOMMU 没有空位的次数。

idle_iommu_assist_local_fallback:
  assist 没有成功，最后仍走 local GMMU PTW 的次数。
```

已有的 `iommu_req_count` 会包含 assist request。因此分析时要区分：

```text
normal remote IOMMU requests:
  iommu_req_count - idle_iommu_assist_issued

assist IOMMU requests:
  idle_iommu_assist_issued
```

## Expected Metric Movement

如果 feature 生效，通常会看到：

```text
local_req_count decreases
iommu_req_count increases
idle_iommu_assist_issued > 0
```

如果 IOMMU 很忙，可能会看到：

```text
idle_iommu_assist_blocked_busy high
idle_iommu_assist_local_fallback high
performance close to baseline
```

如果 IOMMU 被 assist 压坏，可能会看到：

```text
iommu_req_count increases
shared MMU occupancy increases
remote translation latency gets worse
overall speedup decreases
```

所以这个 feature 需要同时看 local PTW pressure 和 shared IOMMU pressure。

## Code Touch Points

推荐修改点：

```text
akita/mem/vm/tlb_gmmu/builder.go
  add WithIdleIOMMUAssist()
  add shared PTW state provider field

akita/mem/vm/tlb_gmmu/tlb copy 2.go
  add routing decision at ready-to-downstream issue point
  do not modify MSHR lookup or PTE lookup behavior

akita/mem/vm/tlb_gmmu/stats.go
  add idle_iommu_assist_* counters

akkalat/400latency/runner/flag.go
  add -gmmu-idle-iommu-assist

akkalat/400latency/runner/r9nanobuilder.go
  pass shared MMU into GMMU L2 builder

akkalat/400latency/runner/report.go
  export new stats

akkalat/runall2.py
  add an optional idle_iommu_assist config
```

`runall2.py` 里建议先作为单独实验项，而不是替换 baseline/flex/PTCL：

```python
idle_iommu_assist = baseline_gmmu_lookup_flags + [
    "-gmmu-idle-iommu-assist",
]
```

## Correctness Checklist

需要保证：

```text
1. Feature 关闭时，行为和 baseline 完全一致。
2. Remote page 仍然走 IOMMU，不计入 assist issued。
3. Local page + IOMMU free 只发一份 request 到 IOMMU。
4. Local page + IOMMU busy 只发一份 request 到 local GMMU PTW。
5. IOMMU response 走现有 processRsp()，不引入新的 completion path。
6. 每个 demand request 最多返回一次。
7. MSHR bitmap 和 Pages[] 更新仍由现有 response path 完成。
```

## Build And Smoke Test

Build check：

```bash
GOCACHE=/tmp/gocache go build ./mem/vm/tlb_gmmu ./mem/vm/mmuTLB
GOCACHE=/tmp/gocache go build -buildvcs=false ./akkalat/400latency
python3 -m py_compile akkalat/runall2.py
```

Smoke run：

```text
1. 跑一个 benchmark 的 baseline。
2. 跑同一个 benchmark 的 idle_iommu_assist。
3. 确认 feature 关闭时 idle_iommu_assist_* 全为 0。
4. 确认 feature 打开时 idle_iommu_assist_enabled = 1。
5. 如果 shared IOMMU 有空位，idle_iommu_assist_issued 应该增加。
6. 如果 shared IOMMU 忙，blocked_busy 和 local_fallback 应该增加。
```

Performance check：

```text
compare:
  runtime / speedup
  local_req_count
  iommu_req_count
  idle_iommu_assist_issued
  idle_iommu_assist_blocked_busy
  shared MMU queue occupancy / PTW inflight
```

这个版本的最好结果应该来自这种情况：

```text
local GMMU PTW 是瓶颈
shared IOMMU 经常有空位
remote translation 本身没有被 assist 明显拖慢
```

