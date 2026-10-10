#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/akkalat"
command -v go >/dev/null
python3 runall2.py --configs ptcl_mode_flex_iommu_assist --gmmu-flex-pcd-ways 8 "$@"
