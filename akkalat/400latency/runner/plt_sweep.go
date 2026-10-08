package runner

import "flag"

var gmmuPLTExtraLatency = flag.Int("gmmu-plt-extra-latency", 0, "Total GMMU PTCL set lookup latency in cycles, without a PTE base term.")
