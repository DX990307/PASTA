# Remote half: PLT 128 rows, 19 PASTA experiments

Local machine runs all 16-row and 64-row experiments (38 total). This package runs all 19 workloads at 128 rows. Do not run run_row_sweep.py or the local supervisors on the remote machine.

Configuration: exact reference runall2.py; PLT 128 rows (16 sets x 8 ways); fixed 64-cycle PLT latency (original 2 x 32); MT 4096 x 4096; IOTLB line and idle-IOMMU assist enabled; PASTA only; old baseline reused.

Requires Python 3 and Go >=1.22.4 in PATH. Go dependencies may need network access. Default runall2 scheduler: 16 workers and minimum 60 GiB available memory. No simulation parameters are changed.

```bash
git clone --single-branch --branch plt-rows128-lat64-runall2-20261010 https://github.com/DX990307/PASTA.git PASTA-rows128
cd PASTA-rows128
nohup bash run_remote_half.sh > remote.log 2>&1 &
tail -f remote.log
```

Results are in akkalat/results/<timestamp>-ptcl-sweep/. Return all *_metrics.csv and remote.log for merging.

Exact command:
```bash
cd akkalat
python3 runall2.py --configs ptcl_mode_flex_iommu_assist --gmmu-flex-pcd-ways 8
```
