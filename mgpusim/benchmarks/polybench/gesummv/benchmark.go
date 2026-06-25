// Package gesummv implements the GESUMMV benchmark from PolyBench/GPU.
package gesummv

import (
	"log"
	"math"

	// embed hsaco files
	_ "embed"

	"github.com/sarchlab/mgpusim/v3/driver"
	"github.com/sarchlab/mgpusim/v3/insts"
	"github.com/sarchlab/mgpusim/v3/kernels"
)

// KernelArgs lists GESUMMV kernel arguments.
type KernelArgs struct {
	A     driver.Ptr
	B     driver.Ptr
	X     driver.Ptr
	Y     driver.Ptr
	Tmp   driver.Ptr
	Alpha float32
	Beta  float32
	N     int32
}

// Benchmark defines a benchmark.
type Benchmark struct {
	driver  *driver.Driver
	context *driver.Context
	gpus    []int
	queues  []*driver.CommandQueue

	kernel *insts.HsaCo

	N       int
	alpha   float32
	beta    float32
	a       []float32
	b       []float32
	x       []float32
	y       []float32
	tmp     []float32
	output  []float32
	cpuY    []float32
	dA      driver.Ptr
	dB      driver.Ptr
	dX      driver.Ptr
	dY      driver.Ptr
	dTmp    driver.Ptr
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
	b.kernel = kernels.LoadProgramFromMemory(hsacoBytes, "gesummv_kernel")
	if b.kernel == nil {
		log.Panic("failed to load gesummv_kernel")
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
	b.alpha = 43532
	b.beta = 12313

	matrixLen := b.N * b.N
	b.a = make([]float32, matrixLen)
	b.b = make([]float32, matrixLen)
	b.x = make([]float32, b.N)
	b.y = make([]float32, b.N)
	b.tmp = make([]float32, b.N)
	b.output = make([]float32, b.N)

	for i := 0; i < b.N; i++ {
		b.x[i] = float32(i) / float32(b.N)
		for j := 0; j < b.N; j++ {
			b.a[i*b.N+j] = float32(i*j) / float32(b.N)
			b.b[i*b.N+j] = float32(i*j) / float32(b.N)
		}
	}

	matrixBytes := uint64(matrixLen * 4)
	vectorBytes := uint64(b.N * 4)
	if b.useUMem {
		b.dA = b.driver.AllocateUnifiedMemory(b.context, matrixBytes)
		b.dB = b.driver.AllocateUnifiedMemory(b.context, matrixBytes)
		b.dX = b.driver.AllocateUnifiedMemory(b.context, vectorBytes)
		b.dY = b.driver.AllocateUnifiedMemory(b.context, vectorBytes)
		b.dTmp = b.driver.AllocateUnifiedMemory(b.context, vectorBytes)
	} else {
		b.dA = b.driver.AllocateMemory(b.context, matrixBytes)
		b.dB = b.driver.AllocateMemory(b.context, matrixBytes)
		b.dX = b.driver.AllocateMemory(b.context, vectorBytes)
		b.dY = b.driver.AllocateMemory(b.context, vectorBytes)
		b.dTmp = b.driver.AllocateMemory(b.context, vectorBytes)
	}
}

func (b *Benchmark) exec() {
	b.driver.MemCopyH2D(b.context, b.dA, b.a)
	b.driver.MemCopyH2D(b.context, b.dB, b.b)
	b.driver.MemCopyH2D(b.context, b.dX, b.x)
	b.driver.MemCopyH2D(b.context, b.dY, b.y)
	b.driver.MemCopyH2D(b.context, b.dTmp, b.tmp)

	kernelArg := KernelArgs{
		A:     b.dA,
		B:     b.dB,
		X:     b.dX,
		Y:     b.dY,
		Tmp:   b.dTmp,
		Alpha: b.alpha,
		Beta:  b.beta,
		N:     int32(b.N),
	}
	b.driver.LaunchKernel(
		b.context,
		b.kernel,
		[3]uint32{roundUp(uint32(b.N), 256), 1, 1},
		[3]uint16{256, 1, 1},
		&kernelArg,
	)

	b.driver.MemCopyD2H(b.context, b.output, b.dY)
}

// Verify verifies results against a CPU implementation.
func (b *Benchmark) Verify() {
	b.cpuGesummv()
	for i := range b.output {
		if math.Abs(float64(b.cpuY[i]-b.output[i])) > 1e-2 {
			log.Panicf("mismatch at %d, expected %f, got %f",
				i, b.cpuY[i], b.output[i])
		}
	}
	log.Printf("Passed!\n")
}

func (b *Benchmark) cpuGesummv() {
	b.cpuY = make([]float32, b.N)
	for i := 0; i < b.N; i++ {
		tmp := float32(0)
		y := float32(0)
		for j := 0; j < b.N; j++ {
			tmp += b.a[i*b.N+j] * b.x[j]
			y += b.b[i*b.N+j] * b.x[j]
		}
		b.cpuY[i] = b.alpha*tmp + b.beta*y
	}
}

func roundUp(v, align uint32) uint32 {
	return ((v + align - 1) / align) * align
}
