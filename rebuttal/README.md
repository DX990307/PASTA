# Remote Rebuttal Runs

R2/R3 新任务包见 [R2_R3_SIMPLE.md](R2_R3_SIMPLE.md)，启动脚本为
`bash run_r2_r3.sh --workers 15`。包含 FULL14 的 84 个逻辑点；
按 [最新范围和复用规则](RESULT_REUSE.md) 排除 14 个 M1 点，
运行 B_Neq 14 项及 R3 的 Baseline/PASTA/Neighbor/LATPC 各 14 项，共 70 项；
R3 明确重新采集数据，不再用旧 R1/R6 数据跳过本轮控制点。
R2 的 B20 为估算容量，R3 的 Neighbor/LATPC 为简化模型；本轮 R3 明确不纳入 HDPAT，
仅比较 Baseline/PASTA/Neighbor/LATPC 的机制差异。

Linux x86_64、Python 3 标准库即可。bin/ 是冻结二进制，plans/ 是正式配置；不含结果。
机器 B 的 R5 包含 66 个分配点，现复用其中 2 个已完成点，只新跑剩余 64 项，最多 15 并行；
机器 C 跑 R10 demand-only 的 42 项，最多 5 并行。以上为本次核验快照。
原子配置来源和二进制 SHA-256 保存在各 plan 中。全部开启 AkitaRTM，max-wg=76800。

```bash
git clone --branch rebuttal-multi-machine-plan git@github.com:DX990307/PASTA.git
cd PASTA/rebuttal
# B 机器检查；C 使用 run_machine5.sh
bash run_machine15.sh --check
```

先由本机从调度队列撤出 plan 内的逻辑任务，再启动目标机器：

```bash
# B 机器
nohup bash run_machine15.sh --ownership-transferred > machine15.log 2>&1 &
# C 机器
nohup bash run_machine5.sh --ownership-transferred > machine5.log 2>&1 &
```

当前发布只是可部署包，本机队列尚未撤出任务；ownership-transferred 表示你已经完成交接。
每个任务唯一分配一台机器。机器不足 30 GiB 加候选峰值预算时等待，启动间隔至少 60 秒。
并行数是上限。脚本不自动 kill 活跃实验；现有进程后续增长可能使可用内存下降。
异常退出保留失败记录；无 durable completion 的任务标为 unverified，不自动重跑。
重新执行同一命令可接管仍运行的任务，单机文件锁阻止重复调度器。

进度在 results/machine15/status.json 或 results/machine5/status.json。
每个任务保存 job、launch、state、completion、stdout 及模拟器生成的全部指标。
exit=0 表示正常退出，严格科学结果资格仍需本机验证。R10 是 demand-only 嵌套对照。

完成后将 results/ 整个目录连同 plans/ 汇回本机，汇总验证后更新 Excel。
这些运行不能直接当作硬件等面积比较。冻结二进制不保证在其他 CPU 架构执行。
