package dispatching

import (
	"github.com/sarchlab/mgpusim/v3/kernels"
	"github.com/sarchlab/mgpusim/v3/protocol"
	"github.com/sarchlab/mgpusim/v3/timing/cp/internal/resource"
)

type partition struct {
	gridBuilder  kernels.GridBuilder
	dispatchedWG int
	limit        int
	exhausted    bool
}

// partitionAlgorithm dispatches workgroups to CUs by assigning each CU a
// contiguous workgroup partition. Work stealing is optional for the legacy
// partition policy, but PTCL-friendly huge-page runs should keep it disabled.
type partitionAlgorithm struct {
	partitions []*partition
	cuPool     resource.CUResourcePool

	nextPartition     int
	currWGs           []*kernels.WorkGroup
	numWG             int
	numDispatchedWG   int
	numWGPerPartition int

	enableWorkStealing bool
	initialized        bool
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
	if a.noWGInPartition(partitionIndex) {
		if !a.enableWorkStealing {
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

func (a *partitionAlgorithm) allWGDispatched() bool {
	return a.numDispatchedWG >= a.numWG
}

func (a *partitionAlgorithm) noWGInPartition(partitionIndex int) bool {
	p := a.partitions[partitionIndex]
	if p.exhausted || p.dispatchedWG >= p.limit {
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

// FreeResources marks the dispatched location to be available.
func (a *partitionAlgorithm) FreeResources(location dispatchLocation) {
	a.cuPool.GetCU(location.cuID).FreeResourcesForWG(location.wg)
}
