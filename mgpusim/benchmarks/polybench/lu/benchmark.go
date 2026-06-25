// Package lu implements the LU benchmark from PolyBench/GPU.
package lu

import (
	"log"
	"math"

	// embed hsaco files
	_ "embed"

	"github.com/sarchlab/mgpusim/v3/driver"
	"github.com/sarchlab/mgpusim/v3/insts"
	"github.com/sarchlab/mgpusim/v3/kernels"
)

// Kernel1Args lists the first LU kernel arguments.
type Kernel1Args struct {
	A driver.Ptr
	K int32
	N int32
}

// Kernel2Args lists the second LU kernel arguments.
type Kernel2Args struct {
	A driver.Ptr
	K int32
	N int32
}

// Benchmark defines a benchmark.
type Benchmark struct {
	driver  *driver.Driver
	context *driver.Context
	gpus    []int
	queues  []*driver.CommandQueue

	kernel1 *insts.HsaCo
	kernel2 *insts.HsaCo

	N       int
	a       []float32
	output  []float32
	cpuA    []float32
	dA      driver.Ptr
	useUMem bool
}

// NewBenchmark creates a new benchmark.
func NewBenchmark(driver *driver.Driver) *Benchmark {
	b := new(Benchmark)
	b.driver = driver
	b.context = driver.Init()
	b.loadProgram()
	return b
}

// SelectGPU selects GPUs.
func (b *Benchmark) SelectGPU(gpus []int) {
	b.gpus = gpus
}

// SetUnifiedMemory uses unified memory.
func (b *Benchmark) SetUnifiedMemory() {
	b.useUMem = true
}

//go:embed kernels.hsaco
var hsacoBytes []byte

func (b *Benchmark) loadProgram() {
	b.kernel1 = kernels.LoadProgramFromMemory(hsacoBytes, "lu_kernel1")
	if b.kernel1 == nil {
		log.Panic("failed to load lu_kernel1")
	}

	b.kernel2 = kernels.LoadProgramFromMemory(hsacoBytes, "lu_kernel2")
	if b.kernel2 == nil {
		log.Panic("failed to load lu_kernel2")
	}
}

// Run runs the benchmark.
func (b *Benchmark) Run() {
	for _, gpu := range b.gpus {
		b.driver.SelectGPU(b.context, gpu)
		b.queues = append(b.queues, b.driver.CreateCommandQueue(b.context))
	}

	b.initMem()
	b.exec()
}

func (b *Benchmark) initMem() {
	if b.N == 0 {
		b.N = 2048
	}

	b.a = make([]float32, b.N*b.N)
	b.output = make([]float32, b.N*b.N)
	for i := 0; i < b.N; i++ {
		for j := 0; j < b.N; j++ {
			b.a[i*b.N+j] = (float32(i*j) + 1) / float32(b.N)
		}
	}

	bytes := uint64(len(b.a) * 4)
	if b.useUMem {
		b.dA = b.driver.AllocateUnifiedMemory(b.context, bytes)
	} else {
		b.dA = b.driver.AllocateMemory(b.context, bytes)
	}
}

func (b *Benchmark) exec() {
	b.driver.MemCopyH2D(b.context, b.dA, b.a)

	for k := 0; k < b.N-1; k++ {
		kernel1Arg := Kernel1Args{
			A: b.dA,
			K: int32(k),
			N: int32(b.N),
		}
		b.driver.LaunchKernel(
			b.context,
			b.kernel1,
			[3]uint32{roundUp(uint32(b.N-k-1), 256), 1, 1},
			[3]uint16{256, 1, 1},
			&kernel1Arg,
		)

		kernel2Arg := Kernel2Args{
			A: b.dA,
			K: int32(k),
			N: int32(b.N),
		}
		b.driver.LaunchKernel(
			b.context,
			b.kernel2,
			[3]uint32{
				roundUp(uint32(b.N-k-1), 32),
				roundUp(uint32(b.N-k-1), 8),
				1,
			},
			[3]uint16{32, 8, 1},
			&kernel2Arg,
		)
	}

	b.driver.MemCopyD2H(b.context, b.output, b.dA)
}

// Verify verifies results against a CPU implementation.
func (b *Benchmark) Verify() {
	b.cpuLU()
	for i := range b.output {
		if math.Abs(float64(b.cpuA[i]-b.output[i])) > 1e-3 {
			log.Panicf("mismatch at %d, expected %f, got %f",
				i, b.cpuA[i], b.output[i])
		}
	}
	log.Printf("Passed!\n")
}

func (b *Benchmark) cpuLU() {
	b.cpuA = make([]float32, len(b.a))
	copy(b.cpuA, b.a)

	for k := 0; k < b.N; k++ {
		for j := k + 1; j < b.N; j++ {
			b.cpuA[k*b.N+j] /= b.cpuA[k*b.N+k]
		}
		for i := k + 1; i < b.N; i++ {
			for j := k + 1; j < b.N; j++ {
				b.cpuA[i*b.N+j] -= b.cpuA[i*b.N+k] * b.cpuA[k*b.N+j]
			}
		}
	}
}

func roundUp(v, align uint32) uint32 {
	return ((v + align - 1) / align) * align
}
