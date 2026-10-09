# Mesh latency / bandwidth sensitivity study

分支：`mesh-sensitivity-20261008`。Base 为干净的 `hyperscan-current-20260619`，commit `4b5e718c052f2d459cd713b3ee5c73d724092c4c`。**所有模拟器 Go 源码保持原样，没有导入其他分支的修复或延迟修改。**

## 四组配置

| 配置名 | 设计 | 现有 mesh 延迟参数 cycles | 标称链路带宽 GB/s | 命令参数 |
| --- | --- | ---: | ---: | --- |
| `base_meshlat64` | Baseline | 64 | 768 | `-switch-latency=64 -bandwidth=48` |
| `pasta_meshlat64` | PASTA | 64 | 768 | `-switch-latency=64 -bandwidth=48` |
| `base_meshbw384` | Baseline | 32 | 384 | `-switch-latency=32 -bandwidth=24` |
| `pasta_meshbw384` | PASTA | 32 | 384 | `-switch-latency=32 -bandwidth=24` |

原代码 `-bandwidth` 的单位为16 GB/s。mesh配置采用1 GHz、16-byte flit，因此48对应768 GB/s、24对应384 GB/s。`-switch-latency` 是已有参数名，实际传给相邻switch连接两端的Latency；不新增或修改网络模型，不将表中参数解释成包含排队/序列化时间的端到端延迟。

## 固定设置与任务

采用历史FULL14 benchmark输入与Baseline/PASTA机制开关，**仅替换 `-switch-latency` 和 `-bandwidth`**，另将metrics路径定位到本实验包。PASTA使用Flex + idle-IOMMU assist；Baseline保留历史per-VPN MSHR和`-mmutlb-demand-pte-only`，不新增`-ptw-demand-pte-only`。

原参考源码的PTW默认值保持4/16，IOMMU pending walk queue保持64，48 GPM × 32 CU，原cache/TLB组织、lookup slots=8、普通PTE lookup=32 cycles、max-WG=76800及输入全部保持。

**本mesh实验保留参考分支原有的PTCL set lookup=64 cycles，不添加PLT额外延迟。** 当前运行PTW及排队PLT的“去掉2×32”修正在各自目录中保留，本实验不改动它们。三个campaign不能混用性能结果。

14 benchmarks × 4 configs = **56 jobs**。先MT、再MM，再其余benchmark。每个网络profile内Baseline与PASTA配对，最多28个有效性能配对。没有复用历史性能指标。

## 排队与运行

当前设置顺序：**PTW sweep → PLT行数实验 → 本mesh实验**。`after_previous_sweeps.py` 同时检查前两轮全部任务，只有都进入completed/failed/interrupted且记录中的worker/simulator全部退出，才启动本目录。queued/running/starting/stale均阻止启动；失败算已结束，不自动重跑。

```bash
bash build.sh
python3 remote_campaign_runner.py status
python3 remote_campaign_runner.py list
python3 remote_campaign_runner.py summary

python3 after_previous_sweeps.py \
  --after ../PASTA-ptw-sweep-pre-rebuttal-20261008 ../PASTA-plt-row-sweep-20261008 \
  --workers 17
```

等待程序位于tmux会话`mesh-after-ptw-plt`。检查状态：`after-previous-status.json`；等待日志：`results/after-previous.log`。启动后调度日志：`results/supervisor.log`。最大并发17，启动间隔至少20秒，可用内存至少30 GiB才接纳新任务。

取消等待启动可以关闭该tmux会话；开始运行后的任务由独立worker管理，停止调度器不等于停止模拟器。

结果目录为`results/<job-id>/state.json`、`stdout.log`、`metrics.csv`。只汇总completed配对，speedup=`Baseline Driver.total_time / PASTA Driver.total_time`，improvement=`(speedup-1)*100%`。原源码没有新增MT坐标或数值正确性校验，已知MT问题可能仍出现；达到max-WG停止也不等于完整kernel执行结束。

## 代码变动清单

没有改动已有模拟器源码。仅新增：

- `remote_campaign_runner.py`：四组mesh配置、独立worker和结果配对。
- `after_previous_sweeps.py`：等待PTW和PLT两轮结束再启动。
- `test_mesh_campaign.py`：只改变mesh参数、带宽换算与启动条件检查。
- `build.sh`：构建未经修改的参考Go源码，生成冻结配置。
- `historical_commands.json`：复制原benchmark/Baseline/PASTA命令，不含历史性能结果。
- 本README和`source-provenance.json`：配置与源码一致性记录。

`.gitignore`只追加生成文件忽略规则。`manifest.json`、`configs-before-launch.json`、`bin/simulator`、等待状态和results是本机生成文件，不进入提交。`source-provenance.json`记录参考分支全部Go源码的逐文件SHA-256，并确认全部相同。
