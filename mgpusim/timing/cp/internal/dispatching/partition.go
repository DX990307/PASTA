package dispatching

import (
	"github.com/sarchlab/mgpusim/v3/kernels"
	"github.com/sarchlab/mgpusim/v3/protocol"
	"github.com/sarchlab/mgpusim/v3/timing/cp/internal/resource"
)

type partition struct {
	gridBuilder     kernels.GridBuilder
	dispatchedWG    int
	limit           int
	exhausted       bool
	nextWGIndex     int
	currentChunkEnd int
	chunkSize       int
	tileStride      int
}

// partitionAlgorithm dispatches workgroups to CUs by assigning each CU a
// contiguous workgroup partition. Work stealing is optional for the legacy
// partition policy. In strict mode, a positive strictChunkSize breaks each
// strict partition into interleaved contiguous tiles, preserving short-range
// locality while avoiding very long per-CU tails.
type partitionAlgorithm struct {
	partitions []*partition
	cuPool     resource.CUResourcePool

	nextPartition     int
	currWGs           []*kernels.WorkGroup
	numWG             int
	numDispatchedWG   int
	numWGPerPartition int

	disableWorkStealing bool
	strictChunkSize     int
	initialized         bool
}

// RegisterCU allows the partitionAlgorithm to dispatch work-group to the CU.
func (a *partitionAlgorithm) RegisterCU(cu resource.DispatchableCU) {
	a.cuPool.RegisterCU(cu)
}

// StartNewKernel lets the algorithms to start dispatching a new kernel.
func (a *partitionAlgorithm) StartNewKernel(info kernels.KernelLaunchInfo) {
	a.numDispatchedWG = 0
	a.nextPartition = 0

	gb := kernels.NewGridBuilder()
	gb.SetKernel(info)
	a.numWG = gb.NumWG()
	numCU := a.cuPool.NumCU()
	if numCU == 0 {
		panic("partition dispatching requires at least one CU")
	}
	if a.numWG == 0 {
		a.numWGPerPartition = 0
	} else {
		a.numWGPerPartition = (a.numWG-1)/numCU + 1
	}

	if a.usesChunkedStrictPartitions() {
		a.startChunkedStrictKernel(info, numCU)
		return
	}

	a.partitions = nil
	for i := 0; i < numCU; i++ {
		startWG := i * a.numWGPerPartition
		p := &partition{
			gridBuilder: kernels.NewGridBuilder(),
			limit:       partitionLimit(a.numWG, startWG, a.numWGPerPartition),
		}

		p.gridBuilder.SetKernel(info)
		if p.limit > 0 {
			p.gridBuilder.Skip(startWG)
		}

		a.partitions = append(a.partitions, p)
	}

	a.currWGs = make([]*kernels.WorkGroup, numCU)
}

func (a *partitionAlgorithm) usesChunkedStrictPartitions() bool {
	return a.disableWorkStealing && a.strictChunkSize > 0
}

func (a *partitionAlgorithm) startChunkedStrictKernel(
	info kernels.KernelLaunchInfo,
	numCU int,
) {
	chunkSize := a.strictChunkSize
	if a.numWG > 0 && chunkSize > a.numWG {
		chunkSize = a.numWG
	}
	tileStride := chunkSize * numCU

	a.partitions = nil
	for i := 0; i < numCU; i++ {
		startWG := i * chunkSize
		p := &partition{
			gridBuilder:     kernels.NewGridBuilder(),
			nextWGIndex:     startWG,
			currentChunkEnd: minInt(startWG+chunkSize, a.numWG),
			chunkSize:       chunkSize,
			tileStride:      tileStride,
		}

		p.gridBuilder.SetKernel(info)
		if startWG >= a.numWG || chunkSize <= 0 {
			p.exhausted = true
		} else {
			p.gridBuilder.Skip(startWG)
		}

		a.partitions = append(a.partitions, p)
	}

	a.currWGs = make([]*kernels.WorkGroup, numCU)
}

// NumWG returns the number of work-groups in the currently-dispatching
// work-group.
func (a *partitionAlgorithm) NumWG() int {
	return a.numWG
}

// HasNext check if there are more work-groups to dispatch.
func (a *partitionAlgorithm) HasNext() bool {
	return a.numDispatchedWG < a.numWG
}

// Next finds the location to dispatch the next work-group.
func (a *partitionAlgorithm) Next() (location dispatchLocation) {
	if a.allWGDispatched() {
		return dispatchLocation{}
	}

	for index := range a.partitions {
		i := (index + a.nextPartition) % len(a.partitions)

		wgToDispatch, wgFromPartition := a.nextWG(i)
		if wgToDispatch == nil {
			continue
		}

		cu := a.cuPool.GetCU(i)
		locations, ok := cu.ReserveResourceForWG(wgToDispatch)
		if ok {
			dispatch := dispatchLocation{
				valid: true,
				cu:    cu.DispatchingPort(),
				cuID:  i,
				wg:    wgToDispatch,
			}

			dispatch.locations =
				make([]protocol.WfDispatchLocation, len(locations))
			for i, localtion := range locations {
				dispatch.locations[i] = protocol.WfDispatchLocation(localtion)
			}

			a.currWGs[wgFromPartition] = nil
			a.partitions[wgFromPartition].dispatchedWG++
			a.numDispatchedWG++

			a.nextPartition = i + 1

			return dispatch
		}
	}

	return dispatchLocation{}
}

func (a *partitionAlgorithm) nextWG(partitionIndex int) (
	*kernels.WorkGroup, int,
) {
	if a.usesChunkedStrictPartitions() {
		return a.nextChunkedStrictWG(partitionIndex)
	}

	if a.noWGInPartition(partitionIndex) {
		if a.disableWorkStealing {
			return nil, 0
		}

		for i := range a.partitions {
			if a.currWGs[i] != nil {
				return a.currWGs[i], i
			}
		}

		return nil, 0
	}

	if a.currWGs[partitionIndex] != nil {
		return a.currWGs[partitionIndex], partitionIndex
	}

	a.currWGs[partitionIndex] =
		a.partitions[partitionIndex].gridBuilder.NextWG()
	if a.currWGs[partitionIndex] == nil {
		a.partitions[partitionIndex].exhausted = true
	}

	return a.currWGs[partitionIndex], partitionIndex
}

func (a *partitionAlgorithm) nextChunkedStrictWG(partitionIndex int) (
	*kernels.WorkGroup, int,
) {
	if a.currWGs[partitionIndex] != nil {
		return a.currWGs[partitionIndex], partitionIndex
	}

	p := a.partitions[partitionIndex]
	if p.exhausted {
		return nil, 0
	}

	if !a.advanceChunkedPartitionIfNeeded(p) {
		return nil, 0
	}

	a.currWGs[partitionIndex] = p.gridBuilder.NextWG()
	if a.currWGs[partitionIndex] == nil {
		p.exhausted = true
		return nil, 0
	}

	p.nextWGIndex++
	return a.currWGs[partitionIndex], partitionIndex
}

func (a *partitionAlgorithm) advanceChunkedPartitionIfNeeded(
	p *partition,
) bool {
	for p.nextWGIndex >= p.currentChunkEnd {
		if p.chunkSize <= 0 || p.tileStride < p.chunkSize {
			p.exhausted = true
			return false
		}

		gap := p.tileStride - p.chunkSize
		p.nextWGIndex += gap
		if p.nextWGIndex >= a.numWG {
			p.exhausted = true
			return false
		}

		p.gridBuilder.Skip(gap)
		p.currentChunkEnd = minInt(p.nextWGIndex+p.chunkSize, a.numWG)
	}

	return true
}

func (a *partitionAlgorithm) allWGDispatched() bool {
	return a.numDispatchedWG >= a.numWG
}

func (a *partitionAlgorithm) noWGInPartition(partitionIndex int) bool {
	p := a.partitions[partitionIndex]
	if a.usesChunkedStrictPartitions() {
		return p.exhausted
	}

	limit := p.limit
	if limit == 0 && partitionIndex*a.numWGPerPartition < a.numWG {
		limit = a.numWGPerPartition
	}
	if p.exhausted || p.dispatchedWG >= limit {
		return true
	}

	return false
}

func partitionLimit(numWG, startWG, maxPartitionSize int) int {
	if startWG >= numWG || maxPartitionSize <= 0 {
		return 0
	}

	remainingWG := numWG - startWG
	if remainingWG < maxPartitionSize {
		return remainingWG
	}

	return maxPartitionSize
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// FreeResources marks the dispatched location to be available.
func (a *partitionAlgorithm) FreeResources(location dispatchLocation) {
	a.cuPool.GetCU(location.cuID).FreeResourcesForWG(location.wg)
}
