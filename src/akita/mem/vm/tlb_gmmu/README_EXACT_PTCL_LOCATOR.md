# GMMU Flex: Tagless PTCL Locator

这个 README 解释当前 GMMU L2 TLB 里 Flex + PTCL mode 的数据结构和 lookup 流程。

一句话版本：

```text
普通 L2 TLB 仍然按 PTE 粒度存储。
Flex 额外维护一个很小的 PCD locator 表。
PCD 不存 PTE，不存 PID，不存 PTCL tag，不用 fingerprint。
PCD 只记录：PTCL line 里每个 sector 当前在普通 TLB 的哪个 way。
setID 由地址算出来，wayID 由 PCD 给出来。
最后必须检查普通 TLB PTE 的完整 tag，匹配才算 hit。
```

## 为什么需要 PCD

普通 L2 TLB 是 set-associative 结构：

```text
TLB set 0:  way0 way1 ... way15
TLB set 1:  way0 way1 ... way15
...
TLB set 15: way0 way1 ... way15
```

一个普通 PTE 被放在某个位置：

```text
(setID, wayID)
```

对某个地址来说：

```text
setID = set_index(PID, VPN)   // 可以算出来
wayID = replacement 决定       // 算不出来
```

PTCL line 覆盖 8 个连续 PTE：

```text
baseVPN = VPN & ~0x7

PTE0 = baseVPN + 0
PTE1 = baseVPN + 1
...
PTE7 = baseVPN + 7
```

这 8 个 PTE 保持普通 PTE-level 存储，所以它们可以分散在不同 set/way：

```text
PTE0 -> set 3,  way 5
PTE1 -> set 4,  way 2
PTE2 -> set 5,  way 9
PTE3 -> set 6,  way 1
...
```

其中 `set` 可以由地址计算，`way` 不能。因此 PCD 只记录必要的 `wayID`。

## 当前数据结构

代码位置：

```text
pcd.go
```

核心结构：

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

一个 PCD row 只存：

```text
valid
presentBitmap[8]
wayID[8]
LRU / lastVisit
```

一个 PCD row 不存：

```text
PID
baseVAddr
PTCL tag
setID
PPN
full PTE
fingerprint
```

所以它是 **tagless locator row**。

## 一个具体例子

假设某个 PTCL line 的 8 个 PTE 分别在：

```text
PTE0 -> set0, way5
PTE1 -> set1, way2
PTE2 -> set2, way9
PTE3 -> not present
PTE4 -> set4, way1
PTE5 -> set5, way6
PTE6 -> not present
PTE7 -> set7, way3
```

那么 PCD row 记录：

```text
presentBitmap = 11101101    // 只是示意：哪些 sector 有 locator

wayID[0] = 5
wayID[1] = 2
wayID[2] = 9
wayID[3] = x
wayID[4] = 1
wayID[5] = 6
wayID[6] = x
wayID[7] = 3
```

PCD 不记录 `set0/set1/...`，因为这些能从 `VPN0/VPN1/...` 算出来。

PCD 也不记录 `PID/baseVPN`。这意味着 PCD row 自己不能证明命中。它只是告诉硬件：

```text
你可以去这些 way 试试看。
```

最终是否命中，由普通 TLB PTE 的 full tag 决定。

## Lookup 怎么工作

代码路径：

```text
processReadyPTCLSetLookupJob()
  -> lookupPTCLWithPCD()
  -> validatePCDLocator()
  -> peekPCDLocator()
```

lookup 流程：

```text
输入: PID, VPN, lookupBitmap

1. 算 PTCL base:
   baseVPN = VPN & ~0x7

2. 算 PCD set:
   pcdSet = pcd_index(PID, baseVPN)

3. 扫 pcdSet 里的 locator rows。
   当前参数下是 2-way PCD，所以每个 PCD set 有 2 个 rows。

4. 对每个 requested sector i:
   如果 presentBitmap[i] = 0:
       跳过

   如果 presentBitmap[i] = 1:
       VPN_i   = baseVPN + i
       setID_i = set_index(PID, VPN_i)       // 现算
       wayID_i = pcdRow.locators[i].wayID    // PCD 给
       PTE_i   = TLB[setID_i][wayID_i]

5. 检查 PTE_i:
       PTE_i.Valid == true
       PTE_i.PID   == request PID
       PTE_i.VAddr == request VAddr_i

6. 只有检查通过，sector i 才算 hit。
```

如果 validation 失败：

```text
这个 locator 被认为是 stale。
该 bit 会被清掉。
这个 sector 作为 miss。
不会 fallback 到 full 16-way PTE lookup。
```

也就是说：

```text
PCD hit != translation hit
PCD hit 只是找到了一个 candidate slot
translation hit 必须通过普通 PTE tag validation
```

## 电路图

```text
                   PID, VPN, lookupBitmap
                             |
                             v
                    +------------------+
                    | baseVPN = VPN&~7 |
                    +--------+---------+
                             |
                             v
              +-------------------------------+
              | PCD set index                  |
              | pcd_index(PID, baseVPN)        |
              +---------------+---------------+
                              |
                              v
      +--------------------------------------------------+
      | Tagless PCD rows                                 |
      | row = valid + presentBitmap[8] + wayID[8]        |
      +-----------------------+--------------------------+
                              |
                              v
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
      sector 0            sector i            sector 7
          |                   |                   |
          v                   v                   v
  setID0 = f(PID,VPN0) setIDi = f(PID,VPNi) setID7 = f(PID,VPN7)
  wayID0 = PCD[0]      wayIDi = PCD[i]      wayID7 = PCD[7]
          |                   |                   |
          v                   v                   v
  TLB[setID0][wayID0] TLB[setIDi][wayIDi] TLB[setID7][wayID7]
          |                   |                   |
          v                   v                   v
    validate tag        validate tag        validate tag
          |                   |                   |
          +-------------------+-------------------+
                              |
                              v
                 hitBitmap / missBitmap / pages
```

## 为什么不是简单 parallel PTE lookup

`ptcl_parallel` 的意思是：

```text
PTE0 做一次普通 16-way lookup
PTE1 做一次普通 16-way lookup
...
PTE7 做一次普通 16-way lookup
```

也就是最多 8 个独立 lookup jobs。

当前 Flex 的意思是：

```text
先读一个 PCD locator set
PCD row 给出每个 sector 的 wayID
每个 sector 直接读 TLB[setID][wayID]
然后只做 selected PTE 的 tag validation
```

所以 common hit path 不需要每个 sector 都做 full 16-way tag search。

区别可以写成：

```text
ptcl_parallel:
  多个 PTE lookup jobs
  每个 sector 自己找 way

Flex tagless locator:
  一个 logical lookup job
  PCD 给 candidate way
  每个 sector 只验证被选中的 PTE slot
```

## 为什么不用 fingerprint 也安全

PCD 不存 tag，也不用 fingerprint。

这看起来危险，但实际上不会返回错误 translation，因为 PCD 不直接返回 PTE。PCD 只返回 candidate way：

```text
candidate = TLB[computed setID][PCD wayID]
```

最终必须检查：

```text
candidate.PID   == request.PID
candidate.VAddr == request.VAddr
```

如果不匹配，就是 miss。

所以 tagless PCD 最多造成：

```text
1. 选到了 stale slot
2. validation fail
3. 清掉 locator bit
4. 走 miss path
```

不会造成：

```text
返回错误 PTE
```

## Fill 怎么维护

当一个 PTE 被装入普通 L2 TLB 时，Flex 会记录它在哪个 way：

```text
recordPCDFill(page, setID, wayID)
```

流程：

```text
1. 根据 page.VAddr 算 baseVPN / baseVAddr
2. 根据 PID + baseVAddr 找 PCD set
3. 在这个 PCD set 里找一个已经属于同一 PTCL line 的 row
   判断方法不是看 PCD tag，因为 PCD 没 tag
   而是检查 row 里已有 locator 是否能 validate 到同一 PTCL line 的 PTE
4. 如果找到，就更新这个 row
5. 如果没找到，就分配/替换一个 row
6. 写入:
      presentBitmap[bit] = true
      locators[bit].wayID = wayID
```

## Evict / stale 怎么处理

普通 TLB replacement 可能把某个 PTE 换掉。

如果 replacement 路径知道被 evict 的 PTE 和 wayID，就会清掉对应 locator bit。

如果没有及时清掉，也没关系。之后 lookup 时：

```text
PCD 指到旧 way
读出的 PTE tag 不匹配
validation fail
清掉 stale bit
sector miss
```

所以 stale locator 不影响正确性，只影响命中率。

## Area

当前 GMMU L2 TLB 参数：

```text
L2 TLB = 16 sets * 16 ways = 256 PTE slots
PCD    = 16 sets * 2 ways  = 32 locator rows
```

因为普通 TLB 是 16-way：

```text
wayID bits = log2(16) = 4 bits
```

每个 PCD row：

```text
valid              1 bit
presentBitmap      8 bits
wayID[8]       8 * 4 bits
-------------------------
total             41 bits
```

PCD 总大小：

```text
32 rows * 41 bits = 1312 bits
```

普通 L2 TLB 粗略估计：

```text
PTE slot = valid 1 + PID 32 + VPN 52 + PPN 52 = 137 bits
L2 TLB   = 256 * 137 = 35072 bits
```

overhead：

```text
PCD / L2TLB         = 1312 / 35072 = 3.74%
PCD / (L2TLB + PCD) = 1312 / 36384 = 3.61%
```

对比 full exact PCD：

```text
full row = valid + PID + PTCL tag + presentBitmap + wayID[8]
         = 1 + 32 + 49 + 8 + 32
         = 122 bits

32 rows * 122 bits = 3904 bits
3904 / 35072 = 11.1%
```

所以 tagless locator 把 PCD storage 从约 `11.1%` 降到约 `3.74%`，同时不需要 1-way PCD，也不需要 fingerprint。

## 当前代码对应关系

```text
pcd.go
  pcdEntry
  pcdLocator
  findEntryForLine()
  recordFillInEntry()
  removePage()

tlb copy 2.go
  processReadyPTCLSetLookupJob()
  lookupPTCLWithPCD()
  validatePCDLocator()
  peekPCDLocator()
  recordPCDFill()
```

当前默认：

```text
gmmu-flex-pcd-ways = 0
```

`0` 表示自动：

```text
metadata ways = ceil(TLB ways / 8)
```

当前 16-way L2 TLB 下：

```text
metadata ways = ceil(16 / 8) = 2
```

## 最短总结

```text
PCD 不存 tag，不存 PTE，不用 fingerprint。
PCD 只存每个 sector 的 wayID。
setID 从地址算。
wayID 从 PCD 读。
读出 TLB[setID][wayID] 后，用普通 PTE tag 做最终确认。
确认成功才 hit，失败就是 miss。
```

这就是当前的低面积 Flex locator 设计。

## LaTeX 算法版本

论文/报告里可以直接引用这个 LaTeX 版本：

```text
akita/mem/vm/tlb_gmmu/tagless_ptcl_locator_algorithm.tex
```

里面包含一个短算法：

```text
Algorithm 1: GMMU TLB Lookup with Tagless PTCL Locator
```
