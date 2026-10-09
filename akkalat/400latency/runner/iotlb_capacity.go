package runner

import "flag"

var iotlbNumSets = flag.Int("iotlb-num-sets", 64, "Number of IOTLB sets; ways remain 32 and MSHRs remain 64.")

func iotlbSetCount() int {
	if *iotlbNumSets <= 0 {
		panic("IOTLB set count must be positive")
	}
	return *iotlbNumSets
}
