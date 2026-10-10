# PLT latency-only sweep

PLT fixed at 32 total rows per GMMU, 16 sets x 2 rows/set (`-gmmu-flex-pcd-ways=2`). PASTA points differ only in `-gmmu-plt-extra-latency=0/16/32/128`, which specifies total PTCL set lookup latency with no extra PTE base term. Zero is supported by the unchanged simulator code.

14 benchmarks x (1 shared Baseline + 4 PASTA latency points) = 70 jobs. MT remains 4096x4096. PTW stays 4/16, PW queue64, max-WG76800, mesh48/32, PTE lookup32 and slots8. idle-IOMMU assist and IOTLB set-as-line remain disabled for PASTA. Baseline uses the same flags as the current PLT row sweep, with no added PTW demand-only flag. Every Go source SHA256 matches the current PLT row package. Existing complete Baseline points are copied only after command and binary identity checks.

Queued locally after PTW and PLT row sweeps, workers17, new launches spaced20 seconds, MemAvailable>=30GiB. Does not start Mesh/IOTLB, which were disabled locally.

Commands:
python3 remote_campaign_runner.py status
python3 remote_campaign_runner.py summary
python3 remote_campaign_runner.py run --groups PTW --workers 17

Frozen settings: latency-settings.json; exact commands: configs-before-launch.json; result dirs: results/<job-id>.
