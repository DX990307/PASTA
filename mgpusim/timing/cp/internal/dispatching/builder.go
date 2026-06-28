package dispatching

import (
	"github.com/sarchlab/akita/v3/monitoring"
	"github.com/sarchlab/akita/v3/sim"
	"github.com/sarchlab/akita/v3/tracing"
	"github.com/sarchlab/mgpusim/v3/kernels"
	"github.com/sarchlab/mgpusim/v3/protocol"
	"github.com/sarchlab/mgpusim/v3/timing/cp/internal/resource"
)

const defaultStrictPartitionChunkSize = 0

// A Builder can build dispatchers
type Builder struct {
	cp              tracing.NamedHookable
	gpuID           uint64
	cuResourcePool  resource.CUResourcePool
	alg             string
	strictChunkSize int
	respondingPort  sim.Port
	dispatchingPort sim.Port
	monitor         *monitoring.Monitor
}

// MakeBuilder creates a builder with default dispatching configureations.
func MakeBuilder() Builder {
	b := Builder{
		alg:             "partition",
		strictChunkSize: defaultStrictPartitionChunkSize,
	}
	return b
}

// WithCP sets the Command Processor that the Dispatcher belongs to.
func (b Builder) WithCP(cp tracing.NamedHookable) Builder {
	b.cp = cp
	return b
}

func (b Builder) WithGPUID(gpuID uint64) Builder {
	b.gpuID = gpuID
	return b
}

// WithCUResourcePool sets the CU resource pool. It has to be given form
// outside, as all the dispatchers share the same CU resource pool.
func (b Builder) WithCUResourcePool(pool resource.CUResourcePool) Builder {
	b.cuResourcePool = pool
	return b
}

// WithRespondingPort sets the port that the dispatcher can send WFCompleteMsg
// to.
func (b Builder) WithRespondingPort(p sim.Port) Builder {
	b.respondingPort = p
	return b
}

// WithDispatchingPort sets the port that connects to the Compute Units.
func (b Builder) WithDispatchingPort(p sim.Port) Builder {
	b.dispatchingPort = p
	return b
}

// WithAlg sets the dispatching algorithm.
func (b Builder) WithAlg(alg string) Builder {
	switch alg {
	case "round-robin", "greedy", "partition", "partition-strict":
		b.alg = alg
	default:
		panic("unknown dispatching algorithm " + alg)
	}

	return b
}

// WithStrictPartitionChunkSize sets the tile size used by partition-strict.
// A value <= 0 restores the legacy one-contiguous-partition-per-CU behavior.
func (b Builder) WithStrictPartitionChunkSize(size int) Builder {
	b.strictChunkSize = size
	return b
}

// WithMonitor sets the monitor that manages progress bars.
func (b Builder) WithMonitor(monitor *monitoring.Monitor) Builder {
	b.monitor = monitor
	return b
}

// Build creates a dispatcher.
func (b Builder) Build(name string) Dispatcher {
	d := &DispatcherImpl{
		name:            name,
		gpuID:           b.gpuID,
		cp:              b.cp,
		respondingPort:  b.respondingPort,
		dispatchingPort: b.dispatchingPort,
		inflightWGs:     make(map[string]dispatchLocation),
		originalReqs:    make(map[string]*protocol.MapWGReq),
		latencyTable: []int{
			1,
			4, 4, 4, 4,
			5, 6, 7, 8,
			9, 10, 11, 12,
			13, 14, 15, 16,
		},
		constantKernelOverhead: 0,
		monitor:                b.monitor,
	}

	switch b.alg {
	case "round-robin":
		d.alg = &roundRobinAlgorithm{
			gridBuilder: kernels.NewGridBuilder(),
			cuPool:      b.cuResourcePool,
		}
	case "greedy":
		d.alg = &greedyAlgorithm{
			gridBuilder: kernels.NewGridBuilder(),
			cuPool:      b.cuResourcePool,
		}
	case "partition":
		d.alg = &partitionAlgorithm{
			cuPool: b.cuResourcePool,
		}
	case "partition-strict":
		d.alg = &partitionAlgorithm{
			cuPool:              b.cuResourcePool,
			disableWorkStealing: true,
			strictChunkSize:     b.strictChunkSize,
		}
	default:
		panic("unknown dispatching algorithm " + b.alg)
	}

	return d
}
