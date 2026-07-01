package gmmu

func (gmmu *GMMU) LATPCFreeReturnStats() (enabled bool, lines int, ptes int) {
	return gmmu.latpcFreeReturn8, gmmu.latpcFreeReturnLines, gmmu.latpcFreeReturnPTEs
}
