package runner

import (
	"regexp"
	"sort"
	"strconv"

	"github.com/sarchlab/akita/v3/mem/vm/mmu"
	"github.com/sarchlab/mgpusim/v3/timing/cu"
)

func (r *Runner) reportStats() {
	r.reportExecutionTime()
	r.reportInstCount()
	r.reportCPIStack()
	r.reportCacheLatency()
	r.reportRDMALatency()
	r.reportGMMULatency()
	r.reportMMULatency()
	r.reportCacheHitRate()
	r.reportTLBHitRate()
	r.reportGMMUCacheHitRate()
	r.reportTLBLatency()
	r.reportGMMUCacheLatency()
	r.reportRDMATransactionCount()
	r.reportGMMUTransactionCount()
	r.reportMMUTransactionCount()
	r.reportMMUNeighborhoodCoalescing()
	r.reportDRAMTransactionCount()
	r.reportIOMMUTLBStats()
	// r.reportGMMUCounts()
	// r.reportGMMUCacheCounts()
	r.dumpMetrics()
}

func (r *Runner) reportInstCount() {
	// kernelTime := float64(r.kernelTimeCounter.BusyTime())
	for _, t := range r.instCountTracers {
		// kernelTime := float64(r.kernelTimeCounter.BusyTime())
		// float64(r.kernelTimeCounter.BusyTime())
		cuName := t.cu.Name()
		gpuID := regexp.MustCompile(`GPU\[(\d+)\]`)
		match := gpuID.FindStringSubmatch(cuName)
		num, err := strconv.Atoi(match[1])
		if err != nil {
			return
		}
		if num > 23 {
			num = num - 1
		}
		kernelTime := float64(r.perGPUKernelTimeCounter[num].BusyTime())

		cuFreq := float64(t.cu.(*cu.ComputeUnit).Freq)
		numCycle := kernelTime * cuFreq

		r.metricsCollector.Collect(
			t.cu.Name(), "cu_inst_count", float64(t.tracer.count))

		r.metricsCollector.Collect(
			t.cu.Name(), "cu_CPI", numCycle/float64(t.tracer.count))
	}
}

func (r *Runner) reportCPIStack() {
	for _, t := range r.cuCPITraces {
		cu := t.cu
		hook := t.tracer

		r.reportCPIStackEntries(hook, cu, false)
		// r.reportCPIStackEntries(hook, cu, true)
	}
}

func (r *Runner) reportCPIStackEntries(
	hook *cu.CPIStackTracer,
	cu TraceableComponent,
	simdStack bool,
) {
	cpiStack := hook.GetCPIStack()
	if simdStack {
		cpiStack = hook.GetSIMDCPIStack()
	}

	keys := make([]string, 0, len(cpiStack))
	for k := range cpiStack {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	stackTypeName := "CPIStack"
	if simdStack {
		stackTypeName = "SIMDCPIStack"
	}

	for _, name := range keys {
		value := cpiStack[name]
		r.metricsCollector.Collect(cu.Name(), stackTypeName+"."+name, value)
	}
}

func (r *Runner) reportExecutionTime() {
	if r.Timing {
		r.metricsCollector.Collect(
			r.platform.Driver.Name(),
			"kernel_time", float64(r.kernelTimeCounter.BusyTime()))
		r.metricsCollector.Collect(
			r.platform.Driver.Name(),
			"total_time", float64(r.platform.Engine.CurrentTime()))

		for i, c := range r.perGPUKernelTimeCounter {
			r.metricsCollector.Collect(
				r.platform.GPUs[i].CommandProcessor.Name(),
				"kernel_time", float64(c.BusyTime()))
		}
	}
}

func (r *Runner) reportCacheLatency() {
	for _, tracer := range r.cacheLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.cache.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportRDMALatency() {
	for _, tracer := range r.rdmaLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.rdma.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportTLBLatency() {
	for _, tracer := range r.tlbLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.tlb.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportCacheHitRate() {
	for _, tracer := range r.cacheHitRateTracers {
		readHit := tracer.tracer.GetStepCount("read-hit")
		readMiss := tracer.tracer.GetStepCount("read-miss")
		readMSHRHit := tracer.tracer.GetStepCount("read-mshr-miss")
		writeHit := tracer.tracer.GetStepCount("write-hit")
		writeMiss := tracer.tracer.GetStepCount("write-miss")
		writeMSHRHit := tracer.tracer.GetStepCount("write-mshr-miss")

		totalTransaction := readHit + readMiss + readMSHRHit +
			writeHit + writeMiss + writeMSHRHit

		if totalTransaction == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.cache.Name(), "read-hit", float64(readHit))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "read-miss", float64(readMiss))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "read-mshr-hit", float64(readMSHRHit))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "write-hit", float64(writeHit))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "write-miss", float64(writeMiss))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "write-mshr-hit", float64(writeMSHRHit))
	}
}

func (r *Runner) reportTLBHitRate() {
	for _, tracer := range r.tlbHitRateTracers {
		hit := tracer.tracer.GetStepCount("hit")
		miss := tracer.tracer.GetStepCount("miss")
		mshrHit := tracer.tracer.GetStepCount("mshr-hit")

		totalTransaction := hit + miss + mshrHit

		if totalTransaction == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.tlb.Name(), "hit", float64(hit))
		r.metricsCollector.Collect(
			tracer.tlb.Name(), "miss", float64(miss))
		r.metricsCollector.Collect(
			tracer.tlb.Name(), "mshr-hit", float64(mshrHit))
	}
}

func (r *Runner) reportRDMATransactionCount() {
	for _, t := range r.rdmaTransactionCounters {
		r.metricsCollector.Collect(
			t.rdmaEngine.Name(),
			"outgoing_trans_count",
			float64(t.outgoingTracer.TotalCount()),
		)
		r.metricsCollector.Collect(
			t.rdmaEngine.Name(),
			"incoming_trans_count",
			float64(t.incomingTracer.TotalCount()),
		)
	}
}

func (r *Runner) reportGMMUTransactionCount() {
	for _, t := range r.gmmuTransactionCounters {
		r.metricsCollector.Collect(
			t.gmmuEngine.Name(),
			"outgoing_trans_count",
			float64(t.outgoingTracer.TotalCount()),
		)
		r.metricsCollector.Collect(
			t.gmmuEngine.Name(),
			"incoming_trans_count",
			float64(t.incomingTracer.TotalCount()),
		)
	}
}

func (r *Runner) reportMMUTransactionCount() {
	for _, t := range r.mmuTransactionCounters {
		r.metricsCollector.Collect(
			t.mmuEngine.Name(),
			"outgoing_trans_count",
			float64(t.outgoingTracer.TotalCount()),
		)
		r.metricsCollector.Collect(
			t.mmuEngine.Name(),
			"incoming_trans_count",
			float64(t.incomingTracer.TotalCount()),
		)
	}
}

func (r *Runner) reportMMUNeighborhoodCoalescing() {
	if r.platform == nil {
		return
	}

	var mmuEngine *mmu.MMU
	for _, gpu := range r.platform.GPUs {
		if gpu.MMUEngine == nil {
			continue
		}
		mmuEngine = gpu.MMUEngine
		break
	}
	if mmuEngine == nil {
		return
	}

	stats := mmuEngine.NeighborhoodCoalescingStats()
	enabled := 0.0
	if stats.Enabled {
		enabled = 1.0
	}

	r.metricsCollector.Collect("MMU", "neighborhood_coalescing_enabled", enabled)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_leaf_coalesced_walks",
		float64(stats.LeafCoalescedWalks),
	)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_upper_level_coalesces",
		float64(stats.UpperLevelCoalesces),
	)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_page_table_accesses_saved",
		float64(stats.PageTableAccessesSaved),
	)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_full_page_walks_avoided",
		float64(stats.FullPageWalksAvoided),
	)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_coalescing_lock_blocks",
		float64(stats.CoalescingLockBlocks),
	)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_walker_starts",
		float64(stats.WalkerStarts),
	)
	r.metricsCollector.Collect(
		"MMU",
		"neighborhood_max_pwqueue_occupancy",
		float64(stats.MaxPWQueueOccupancy),
	)
}

func (r *Runner) reportDRAMTransactionCount() {
	for _, t := range r.dramTracers {
		r.metricsCollector.Collect(
			t.dram.Name(),
			"read_trans_count",
			float64(t.tracer.readCount),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"write_trans_count",
			float64(t.tracer.writeCount),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"read_avg_latency",
			float64(t.tracer.readAvgLatency),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"write_avg_latency",
			float64(t.tracer.writeAvgLatency),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"read_size",
			float64(t.tracer.readSize),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"write_size",
			float64(t.tracer.writeSize),
		)
	}
}

func (r *Runner) reportGMMUCacheHitRate() {
	for _, tracer := range r.gmmuCacheHitRateTracers {
		totalDownstream, localDownstream, iommuDownstream :=
			tracer.gmmuCache.DownstreamRequestCounts()
		pteLookupDelayCount, pteLookupDelayCycles :=
			tracer.gmmuCache.PTELookupDelayStats()
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"downstream_req_count",
			float64(totalDownstream),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"local_req_count",
			float64(localDownstream),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"iommu_req_count",
			float64(iommuDownstream),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"lookup_latency_cycles_per_pte",
			float64(tracer.gmmuCache.PTELookupLatencyCycles()),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"pte_lookup_delay_count",
			float64(pteLookupDelayCount),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"pte_lookup_delay_cycles",
			float64(pteLookupDelayCycles),
		)

		hit := tracer.tracer.GetStepCount("hit")
		miss := tracer.tracer.GetStepCount("miss")
		mshrHit := tracer.tracer.GetStepCount("mshr-hit")

		totalTransaction := hit + miss + mshrHit

		if totalTransaction == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "hit", float64(hit))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "miss", float64(miss))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "mshr-hit", float64(mshrHit))
	}
}

func (r *Runner) reportGMMUCacheLatency() {
	for _, tracer := range r.gmmuCacheLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportGMMULatency() {
	for _, tracer := range r.gmmuLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.gmmu.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportMMULatency() {
	for _, tracer := range r.mmuLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			"MMU",
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportIOMMUTLBStats() {
	if r.platform == nil || r.platform.IOMMUTLB == nil {
		return
	}

	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"incoming_req_count",
		float64(r.platform.IOMMUTLB.IncomingRequestCount()),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"req_to_mmu_count",
		float64(r.platform.IOMMUTLB.DownstreamRequestCount()),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"lookup_latency_cycles_per_bit",
		float64(r.platform.IOMMUTLB.LookupLatencyCycles()),
	)
}

// func (r *Runner) reportGMMUCounts() {
// 	for _, t := range r.gmmuCountTracers {
// 		r.metricsCollector.Collect(
// 			t.gmmu.Name(),
// 			"total_ats_count",
// 			float64(t.tracer.GetTotalATSCount()),
// 		)
// 		r.metricsCollector.Collect(
// 			t.gmmu.Name(),
// 			"local_ats_count",
// 			float64(t.tracer.GetLocalATSCount()),
// 		)
// 		r.metricsCollector.Collect(
// 			t.gmmu.Name(),
// 			"remote_ats_count",
// 			float64(t.tracer.GetRemoteATSCount()),
// 		)
// 	}
// }

// func (r *Runner) reportGMMUCacheCounts() {
// 	for _, t := range r.GMMUTLBTracers {
// 		r.metricsCollector.Collect(
// 			t.tlb.Name(),
// 			"AverageLocalAccessCounts",
// 			float64(t.tracer.ReportAverageLocalAccessCounts()),
// 		)
// 		r.metricsCollector.Collect(
// 			t.tlb.Name(),
// 			"AverageRemoteAccessCounts",
// 			float64(t.tracer.ReportAverageRemoteAccessCounts()),
// 		)
// 	}
// }
