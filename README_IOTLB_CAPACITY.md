# ⑪ IOTLB容量敏感性实验（56次）

分支`iotlb-capacity-20261008`，基于干净的`hyperscan-current-20260619`（`4b5e718c052f2d459cd713b3ee5c73d724092c4c`）。只参数化IOTLB set数，不导入其他分支的correctness或延迟修复。

| 配置名 | 设计 | IOTLB set | way | 项数 | MSHR |
| --- | --- | ---: | ---: | ---: | ---: |
| `base_iotlb_half` | Baseline | 32 | 32 | 1024 | 64 |
| `pasta_iotlb_half` | PASTA | 32 | 32 | 1024 | 64 |
| `base_iotlb_double` | Baseline | 128 | 32 | 4096 | 64 |
| `pasta_iotlb_double` | PASTA | 128 | 32 | 4096 | 64 |

新增参数`-iotlb-num-sets=32/128`，未指定时仍为原默认64（2048项）。way固定32、MSHR固定64、MSHR entry depth固定64、IOTLB请求宽度固定32、lookup latency保持原参数。

## 其他配置

与上一个mesh实验采用同一历史FULL14输入、Baseline与PASTA功能命令，仅加入IOTLB set参数。网络恢复常规配置：32 cycles、768 GB/s（`-switch-latency=32 -bandwidth=48`），不继承mesh敏感性点。

PTW保持参考源码默认4/16，IOMMU PW queue64，48 GPM × 32 CU，原cache/TLB组织、GMMU lookup32 cycles与8 slots、max-WG76800。PASTA保留Flex+idle-IOMMU assist；Baseline保留历史per-VPN MSHR、`-mmutlb-demand-pte-only`，不额外新增runall2的`-ptw-demand-pte-only`。

这里的“其他默认”是前述历史标准运行参数加参考源码未覆盖的默认值，不是删除公共参数后使用CLI的低带宽等原始缺省值。参考原有PTCL lookup64 cycles保留，不额外设置PLT延迟；现有PTW和PLT实验目录不受影响。

14 benchmarks × 2 capacities × Baseline/PASTA = **56任务**。每点一次运行，没有重复次数或置信区间。MT先、MM其次，其余随后。对应容量内部B/P配对，不混用其他campaign结果。

## 实際代码改动

生产源码只有：

1. `akkalat/400latency/runner/timingplatform.go` 的 `createIOMMUTLB()`：将一行 `WithNumSets(64)` 改为 `WithNumSets(iotlbSetCount())`。`WithNumWays(32)` 和 `WithNumMSHREntry(64)` 未修改。
2. 新增`akkalat/400latency/runner/iotlb_capacity.go`：set数flag，默认64，拒绝非正数。

新增测试`iotlb_capacity_test.go`通过真实`createIOMMUTLB()`构造32/64/128set组件，反射检查way仍32、MSHR仍64；另测试非法set拒绝。`source-provenance.json`记录相对参考分支的完整Go源码哈希与变动清单。

另新增`build.sh`、`remote_campaign_runner.py`、`after_previous_sweeps.py`、`test_iotlb_campaign.py`、历史命令数据、本README及provenance，并给`.gitignore`追加生成文件忽略规则。没有修改已有PTW/PLT/mesh代码。

## 排在mesh之后

顺序为 **PTW sweep → PLT行数 → mesh敏感性 → IOTLB容量**。等待程序同时核对前三轮全部任务清单：全部completed/failed/interrupted且记录的worker/simulator都退出才启动。queued/running/starting/stale会阻止启动。失败算已结束，不自动重跑。

```bash
bash build.sh
python3 remote_campaign_runner.py status
python3 remote_campaign_runner.py list
python3 remote_campaign_runner.py summary
python3 after_previous_sweeps.py \
  --after ../PASTA-ptw-sweep-pre-rebuttal-20261008 \
          ../PASTA-plt-row-sweep-20261008 ../PASTA-mesh-sensitivity-20261008 \
  --workers 17
```

等待会话为tmux `iotlb-after-mesh`。等待状态`after-previous-status.json`、日志`results/after-previous.log`；启动后调度日志`results/supervisor.log`。最大并发17，新任务间隔至少20秒，接纳前需可用内存≥30GiB。

原始数据位于`results/<job-id>/`，包括state、stdout、metrics。manifest冻结Go源码及二进制，configs保存所有实际命令。只汇总completed配对：speedup=Baseline Driver.total_time/PASTA Driver.total_time，improvement=(speedup-1)×100%。

沿用参考源码的结果限制：成功退出并有正Driver time不证明MT完整tile覆盖或数值正确；原MT问题可能存在。max-WG截断停止也不是完整kernel结束。不同实验包的指标不能混用。
