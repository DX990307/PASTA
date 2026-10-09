# 在独立机器运行（2026-10-09 当前配置）

当前分支 `iotlb-capacity-20261008` 包含完整源码和调度脚本，共56个任务，无需本机其他实验目录。

- MT：4096×4096（`akkalat/benchmarkselection/benchmark.go` 的 `matrixtranspose.Width = 4096`）。
- PASTA：`-gmmu-idle-iommu-assist=false`，`-mmutlb-flex-tlb=false`；本地GMMU Flex保留。
- Baseline：保留本组历史配置；这两组没有新增PTW sweep专用的 `-ptw-demand-pte-only=true`。
- 远端页面必需的共享IOMMU路径保留。
- 并发默认17，启动间隔20秒，可用内存至少30 GiB；可用 `--workers` 降低并发。
- 需要Go 1.22.4或兼容版本、GCC、Python3、tmux。全新克隆不会携带结果或二进制。

```bash
git clone --single-branch --branch iotlb-capacity-20261008 https://github.com/DX990307/PASTA.git PASTA-iotlb-capacity-20261008
cd PASTA-iotlb-capacity-20261008
bash build.sh
mkdir -p results
tmux new-session -d -s iotlb-sweep 'python3 remote_campaign_runner.py run --groups IOTLB --workers 17 > results/supervisor.log 2>&1'
python3 remote_campaign_runner.py status
python3 remote_campaign_runner.py summary
```

独立机器直接运行上面的 `run` 命令，不使用依赖本机其他目录的 `after_previous_sweeps.py`。
`bash build.sh` 全新构建的 `bin/simulator` 包含4096 MT；本机旧运行使用独立MT二进制，两者MT尺寸相同。
MT校验失败记录为失败，正常失败不会阻止其他任务；SIGKILL会暂停新任务启动，需要检查内存。

详细实验点及原始源码来源见 [README_IOTLB_CAPACITY.md](README_IOTLB_CAPACITY.md)；当前配置优先于其中历史排队说明。
