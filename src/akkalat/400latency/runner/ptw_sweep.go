package runner

import "flag"

var gmmuPTWCount = flag.Int("gmmu-ptw-count", 4, "Number of walkers per GMMU.")
var iommuPTWCount = flag.Int("iommu-ptw-count", 16, "Number of shared IOMMU walkers.")
var iommuPWQueueCapacity = flag.Int("iommu-pw-queue-capacity", 64, "Shared IOMMU pending walk queue capacity.")
var gmmuPLTExtraLatency = flag.Int("gmmu-plt-extra-latency", 0, "Additional PTCL set lookup latency in cycles.")
