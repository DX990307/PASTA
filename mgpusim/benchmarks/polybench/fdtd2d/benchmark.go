// Package fdtd2d implements the FDTD 2D benchmark from PolyBench/GPU.
package fdtd2d

import (
	"log"
	"math"

	// embed hsaco files
	_ "embed"

	"github.com/sarchlab/mgpusim/v3/driver"
	"github.com/sarchlab/mgpusim/v3/insts"
	"github.com/sarchlab/mgpusim/v3/kernels"
)

// Kernel1Args lists the first FDTD kernel arguments.
type Kernel1Args struct {
	Fict driver.Ptr
	Ex   driver.Ptr
	Ey   driver.Ptr
	Hz   driver.Ptr
	T    int32
	NX   int32
	NY   int32
}

// Kernel23Args lists the second and third FDTD kernel arguments.
type Kernel23Args struct {
	Ex driver.Ptr
	Ey driver.Ptr
	Hz driver.Ptr
	NX int32
	NY int32
}

// Benchmark defines a benchmark.
type Benchmark struct {
	driver  *driver.Driver
	context *driver.Context
	gpus    []int
	queues  []*driver.CommandQueue

	kernel1 *insts.HsaCo
	kernel2 *insts.HsaCo
	kernel3 *insts.HsaCo

	NX      int
	NY      int
	TMax    int
	fict    []float32
	ex      []float32
	ey      []float32
	hz      []float32
	output  []float32
	cpuHz   []float32
	dFict   driver.Ptr
	dEx     driver.Ptr
	dEy     driver.Ptr
	dHz     driver.Ptr
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
	b.kernel1 = kernels.LoadProgramFromMemory(hsacoBytes, "fdtd_kernel1")
	if b.kernel1 == nil {
		log.Panic("failed to load fdtd_kernel1")
	}
	b.kernel2 = kernels.LoadProgramFromMemory(hsacoBytes, "fdtd_kernel2")
	if b.kernel2 == nil {
		log.Panic("failed to load fdtd_kernel2")
	}
	b.kernel3 = kernels.LoadProgramFromMemory(hsacoBytes, "fdtd_kernel3")
	if b.kernel3 == nil {
		log.Panic("failed to load fdtd_kernel3")
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
	if b.NX == 0 {
		b.NX = 4096
	}
	if b.NY == 0 {
		b.NY = 4096
	}
	if b.TMax == 0 {
		b.TMax = 3
	}

	matrixLen := b.NX * b.NY
	b.fict = make([]float32, b.TMax)
	b.ex = make([]float32, matrixLen)
	b.ey = make([]float32, matrixLen)
	b.hz = make([]float32, matrixLen)
	b.output = make([]float32, matrixLen)

	for i := 0; i < b.TMax; i++ {
		b.fict[i] = float32(i)
	}
	for i := 0; i < b.NX; i++ {
		for j := 0; j < b.NY; j++ {
			idx := i*b.NY + j
			b.ex[idx] = (float32(i*(j+1)) + 1) / float32(b.NX)
			b.ey[idx] = (float32((i-1)*(j+2)) + 2) / float32(b.NX)
			b.hz[idx] = (float32((i-9)*(j+4)) + 3) / float32(b.NX)
		}
	}

	matrixBytes := uint64(matrixLen * 4)
	fictBytes := uint64(len(b.fict) * 4)
	if b.useUMem {
		b.dFict = b.driver.AllocateUnifiedMemory(b.context, fictBytes)
		b.dEx = b.driver.AllocateUnifiedMemory(b.context, matrixBytes)
		b.dEy = b.driver.AllocateUnifiedMemory(b.context, matrixBytes)
		b.dHz = b.driver.AllocateUnifiedMemory(b.context, matrixBytes)
	} else {
		b.dFict = b.driver.AllocateMemory(b.context, fictBytes)
		b.dEx = b.driver.AllocateMemory(b.context, matrixBytes)
		b.dEy = b.driver.AllocateMemory(b.context, matrixBytes)
		b.dHz = b.driver.AllocateMemory(b.context, matrixBytes)
	}
}

func (b *Benchmark) exec() {
	b.driver.MemCopyH2D(b.context, b.dFict, b.fict)
	b.driver.MemCopyH2D(b.context, b.dEx, b.ex)
	b.driver.MemCopyH2D(b.context, b.dEy, b.ey)
	b.driver.MemCopyH2D(b.context, b.dHz, b.hz)

	globalSize := [3]uint32{
		roundUp(uint32(b.NY), 32),
		roundUp(uint32(b.NX), 8),
		1,
	}
	localSize := [3]uint16{32, 8, 1}

	for t := 0; t < b.TMax; t++ {
		arg1 := Kernel1Args{
			Fict: b.dFict,
			Ex:   b.dEx,
			Ey:   b.dEy,
			Hz:   b.dHz,
			T:    int32(t),
			NX:   int32(b.NX),
			NY:   int32(b.NY),
		}
		b.driver.LaunchKernel(b.context, b.kernel1,
			globalSize, localSize, &arg1)

		arg23 := Kernel23Args{
			Ex: b.dEx,
			Ey: b.dEy,
			Hz: b.dHz,
			NX: int32(b.NX),
			NY: int32(b.NY),
		}
		b.driver.LaunchKernel(b.context, b.kernel2,
			globalSize, localSize, &arg23)
		b.driver.LaunchKernel(b.context, b.kernel3,
			globalSize, localSize, &arg23)
	}

	b.driver.MemCopyD2H(b.context, b.output, b.dHz)
}

// Verify verifies results against a CPU implementation.
func (b *Benchmark) Verify() {
	b.cpuFDTD2D()
	for i := range b.output {
		if math.Abs(float64(b.cpuHz[i]-b.output[i])) > 1e-3 {
			log.Panicf("mismatch at %d, expected %f, got %f",
				i, b.cpuHz[i], b.output[i])
		}
	}
	log.Printf("Passed!\n")
}

func (b *Benchmark) cpuFDTD2D() {
	ex := make([]float32, len(b.ex))
	ey := make([]float32, len(b.ey))
	b.cpuHz = make([]float32, len(b.hz))
	copy(ex, b.ex)
	copy(ey, b.ey)
	copy(b.cpuHz, b.hz)

	for t := 0; t < b.TMax; t++ {
		for j := 0; j < b.NY; j++ {
			ey[j] = b.fict[t]
		}
		for i := 1; i < b.NX; i++ {
			for j := 0; j < b.NY; j++ {
				idx := i*b.NY + j
				ey[idx] -= 0.5 * (b.cpuHz[idx] - b.cpuHz[(i-1)*b.NY+j])
			}
		}
		for i := 0; i < b.NX; i++ {
			for j := 1; j < b.NY; j++ {
				idx := i*b.NY + j
				ex[idx] -= 0.5 * (b.cpuHz[idx] - b.cpuHz[idx-1])
			}
		}
		for i := 0; i < b.NX-1; i++ {
			for j := 0; j < b.NY-1; j++ {
				idx := i*b.NY + j
				b.cpuHz[idx] -= 0.7 * (ex[idx+1] - ex[idx] +
					ey[(i+1)*b.NY+j] - ey[idx])
			}
		}
	}
}

func roundUp(v, align uint32) uint32 {
	return ((v + align - 1) / align) * align
}
