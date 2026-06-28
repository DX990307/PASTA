// Package jacobi2d implements the Jacobi 2D benchmark from PolyBench/GPU.
package jacobi2d

import (
	"log"
	"math"

	// embed hsaco files
	_ "embed"

	"github.com/sarchlab/mgpusim/v3/driver"
	"github.com/sarchlab/mgpusim/v3/insts"
	"github.com/sarchlab/mgpusim/v3/kernels"
)

// KernelArgs lists Jacobi 2D kernel arguments.
type KernelArgs struct {
	A driver.Ptr
	B driver.Ptr
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
	TSteps  int
	a       []float32
	b       []float32
	outputA []float32
	outputB []float32
	cpuA    []float32
	cpuB    []float32
	dA      driver.Ptr
	dB      driver.Ptr
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
	b.kernel1 = kernels.LoadProgramFromMemory(hsacoBytes, "runJacobi2D_kernel1")
	if b.kernel1 == nil {
		log.Panic("failed to load runJacobi2D_kernel1")
	}

	b.kernel2 = kernels.LoadProgramFromMemory(hsacoBytes, "runJacobi2D_kernel2")
	if b.kernel2 == nil {
		log.Panic("failed to load runJacobi2D_kernel2")
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
		b.N = 4096
	}
	if b.TSteps == 0 {
		b.TSteps = 3
	}

	b.a = make([]float32, b.N*b.N)
	b.b = make([]float32, b.N*b.N)
	b.outputA = make([]float32, b.N*b.N)
	b.outputB = make([]float32, b.N*b.N)
	for i := 0; i < b.N; i++ {
		for j := 0; j < b.N; j++ {
			b.a[i*b.N+j] = (float32(i*(j+2)) + 10) / float32(b.N)
			b.b[i*b.N+j] = (float32((i-4)*(j-1)) + 11) / float32(b.N)
		}
	}

	bytes := uint64(len(b.a) * 4)
	if b.useUMem {
		b.dA = b.driver.AllocateUnifiedMemory(b.context, bytes)
		b.dB = b.driver.AllocateUnifiedMemory(b.context, bytes)
	} else {
		b.dA = b.driver.AllocateMemory(b.context, bytes)
		b.dB = b.driver.AllocateMemory(b.context, bytes)
	}
}

func (b *Benchmark) exec() {
	b.driver.MemCopyH2D(b.context, b.dA, b.a)
	b.driver.MemCopyH2D(b.context, b.dB, b.b)

	args := KernelArgs{
		A: b.dA,
		B: b.dB,
		N: int32(b.N),
	}
	globalSize := [3]uint32{
		roundUp(uint32(b.N), 32),
		roundUp(uint32(b.N), 8),
		1,
	}
	localSize := [3]uint16{32, 8, 1}

	for t := 0; t < b.TSteps; t++ {
		b.driver.LaunchKernel(b.context, b.kernel1,
			globalSize, localSize, &args)
		b.driver.LaunchKernel(b.context, b.kernel2,
			globalSize, localSize, &args)
	}

	b.driver.MemCopyD2H(b.context, b.outputA, b.dA)
	b.driver.MemCopyD2H(b.context, b.outputB, b.dB)
}

// Verify verifies results against a CPU implementation.
func (b *Benchmark) Verify() {
	b.cpuJacobi2D()
	for i := range b.outputA {
		if math.Abs(float64(b.cpuA[i]-b.outputA[i])) > 1e-5 {
			log.Panicf("mismatch in A at %d, expected %f, got %f",
				i, b.cpuA[i], b.outputA[i])
		}
		if math.Abs(float64(b.cpuB[i]-b.outputB[i])) > 1e-5 {
			log.Panicf("mismatch in B at %d, expected %f, got %f",
				i, b.cpuB[i], b.outputB[i])
		}
	}
	log.Printf("Passed!\n")
}

func (b *Benchmark) cpuJacobi2D() {
	b.cpuA = make([]float32, len(b.a))
	b.cpuB = make([]float32, len(b.b))
	copy(b.cpuA, b.a)
	copy(b.cpuB, b.b)

	for t := 0; t < b.TSteps; t++ {
		for i := 1; i < b.N-1; i++ {
			for j := 1; j < b.N-1; j++ {
				b.cpuB[i*b.N+j] = 0.2 * (b.cpuA[i*b.N+j] +
					b.cpuA[i*b.N+j-1] +
					b.cpuA[i*b.N+j+1] +
					b.cpuA[(i+1)*b.N+j] +
					b.cpuA[(i-1)*b.N+j])
			}
		}
		for i := 1; i < b.N-1; i++ {
			for j := 1; j < b.N-1; j++ {
				b.cpuA[i*b.N+j] = b.cpuB[i*b.N+j]
			}
		}
	}
}

func roundUp(v, align uint32) uint32 {
	return ((v + align - 1) / align) * align
}
