# PLT 行数与延迟实验

分支：`plt-row-sweep-20261008`。基于干净的 `hyperscan-current-20260619`，base commit `4b5e718c052f2d459cd713b3ee5c73d724092c4c`。未导入 rebuttal 的 barrier、CU、coalescer 或 IOTLB 修复。

## 配置

PTW 固定为原代码默认的每 GMMU 4 个 walker、共享 IOMMU 16 个 walker，IOMMU pending walk queue 64。保留 48 GPM × 32 CU、L2 TLB 16 sets × 16 ways、PTE lookup 32 cycles、lookup slots 8、max-WG 76800 及历史 FULL14 输入。

PLT 行数指每个 GMMU 的整个 locator 总行数，不是每个 set 的行数。保持 16 个 set，通过已有 `-gmmu-flex-pcd-ways` 改变每 set 的行数。

| PLT 总行数 | 每 set 行数 | 额外延迟 cycles | PTCL set lookup 总 cycles |
| --- | ---: | ---: | ---: |
| 16 | 1 | 32 | 96 |
| 64 | 4 | 128 | 192 |
| 128 | 8 | 256 | 320 |

总延迟为 `2 × 32 + extra`。新增参数 `-gmmu-plt-extra-latency` 只作用于已有 PTCL set lookup 路径。该实验同时改变 PLT 容量和额外延迟，不能把结果单独归因于容量。

## 任务

采用当前 PTW campaign 的历史 FULL14 命令作为 benchmark/Baseline/PASTA 功能配置来源；固定 PTW 4/16，取消 PTW 三档扫描。Baseline 保留历史 `-mmutlb-demand-pte-only`，不新增 runall2 的 `-ptw-demand-pte-only`。PASTA 保留 Flex 与 idle-IOMMU assist。

14 个 benchmark，每个 1 个共享 Baseline + 3 个 PASTA 行数点，共 **56 个任务（14 Baseline + 42 PASTA）**。同一 benchmark 的三个 PASTA 点与同一个 Baseline 配对，不重复运行不使用 PLT 的 Baseline。顺序为 MT、MM，然后其余 benchmark。每个点运行一次，不复用历史性能结果。

## 构建与查看

```bash
bash build.sh
python3 remote_campaign_runner.py status
python3 remote_campaign_runner.py list
python3 remote_campaign_runner.py summary
```

`manifest.json` 冻结全部 Go 源码及二进制 SHA-256。`configs-before-launch.json` 保存56个任务的实际命令。结果在 `results/<job-id>/`，包括状态、stdout 和 metrics。不同实验包的日志不会混用。

## 在当前 sweep 结束后启动

`after_current_sweep.py` 每30秒检查当前 `PASTA-ptw-sweep-pre-rebuttal-20261008` 的整个84任务清单。只有所有任务均为 completed/failed/interrupted，且其记录的 simulator/worker 都已退出，才启动本目录实验。queued/running/starting/stale 都会阻止启动；前一轮失败算作已结束，不自动重跑失败点。

```bash
python3 after_current_sweep.py \
  --current ../PASTA-ptw-sweep-pre-rebuttal-20261008 \
  --workers 17
```

当前已通过 tmux 会话 `plt-after-ptw` 设置等待启动。状态见 `after-current-status.json`，等待日志见 `results/after-current.log`，启动后调度日志见 `results/supervisor.log`。前一轮只结束正在运行的17个任务不够，还必须处理完其余队列。新实验最大并发17，启动间隔至少20秒，要求可用内存至少30 GiB。

## 结果限制

性能取 Driver.total_time：`speedup=Baseline_time/PASTA_time`，`improvement=(speedup-1)*100%`。只汇总已完成配对。

此参考源码没有严格 MT 工作组坐标校验，也没有导入 MT correctness fixes。进程返回0并产出正的 Driver time不证明完整输入覆盖或数值正确。MT原始问题仍可能出现。max-WG=76800的截断采样也不等于完整 kernel 执行；不同版本之间不可直接混用指标。
