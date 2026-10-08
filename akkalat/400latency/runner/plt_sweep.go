package runner

import "flag"

var gmmuPLTExtraLatency = flag.Int("gmmu-plt-extra-latency", 0, "Additional latency of the GMMU PTCL set lookup, in cycles.")
