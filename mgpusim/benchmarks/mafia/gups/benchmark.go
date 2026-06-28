// Package gups implements a random-update GUPS workload in the style of MAFIA.
package gups

import (
	"log"
	"math/bits"

	// embed hsaco files
	_ "embed"

	"github.com/sarchlab/mgpusim/v3/driver"
	"github.com/sarchlab/mgpusim/v3/insts"
	"github.com/sarchlab/mgpusim/v3/kernels"
)

// KernelArgs lists GUPS kernel arguments.
type KernelArgs struct {
	Table   driver.Ptr
	Index   driver.Ptr
	Updates int32
}

// Benchmark defines a benchmark.
type Benchmark struct {
	driver  *driver.Driver
	context *driver.Context
	gpus    []int
	queues  []*driver.CommandQueue

	kernel *insts.HsaCo

	TableEntries int
	Updates      int
	table        []uint64
	indices      []uint32
	output       []uint64
	dTable       driver.Ptr
	dIndices     driver.Ptr
	useUMem      bool
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
	b.kernel = kernels.LoadProgramFromMemory(hsacoBytes, "gups_kernel")
	if b.kernel == nil {
		log.Panic("failed to load gups_kernel")
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
	if b.TableEntries == 0 {
		b.TableEntries = 1 << 24
	}
	if b.Updates == 0 {
		b.Updates = b.TableEntries
	}
	if b.TableEntries&(b.TableEntries-1) != 0 {
		log.Panic("gups TableEntries must be a power of two")
	}

	b.table = make([]uint64, b.TableEntries)
	b.indices = make([]uint32, b.Updates)
	b.output = make([]uint64, b.TableEntries)

	for i := range b.table {
		b.table[i] = uint64(i)
	}

	indexBits := bits.Len(uint(b.TableEntries)) - 1
	for i := 0; i < b.Updates; i++ {
		base := uint32(i & (b.TableEntries - 1))
		b.indices[i] = bits.Reverse32(base) >> uint(32-indexBits)
	}

	tableBytes := uint64(len(b.table) * 8)
	indexBytes := uint64(len(b.indices) * 4)
	if b.useUMem {
		b.dTable = b.driver.AllocateUnifiedMemory(b.context, tableBytes)
		b.dIndices = b.driver.AllocateUnifiedMemory(b.context, indexBytes)
	} else {
		b.dTable = b.driver.AllocateMemory(b.context, tableBytes)
		b.dIndices = b.driver.AllocateMemory(b.context, indexBytes)
	}
}

func (b *Benchmark) exec() {
	b.driver.MemCopyH2D(b.context, b.dTable, b.table)
	b.driver.MemCopyH2D(b.context, b.dIndices, b.indices)

	kernelArg := KernelArgs{
		Table:   b.dTable,
		Index:   b.dIndices,
		Updates: int32(b.Updates),
	}
	b.driver.LaunchKernel(
		b.context,
		b.kernel,
		[3]uint32{roundUp(uint32(b.Updates), 256), 1, 1},
		[3]uint16{256, 1, 1},
		&kernelArg,
	)

	b.driver.MemCopyD2H(b.context, b.output, b.dTable)
}

// Verify verifies results against a CPU implementation.
func (b *Benchmark) Verify() {
	expected := make([]uint64, len(b.table))
	copy(expected, b.table)
	for i, idx := range b.indices {
		expected[idx] ^= mix(uint64(i))
	}
	for i := range expected {
		if expected[i] != b.output[i] {
			log.Panicf("mismatch at %d, expected %d, got %d",
				i, expected[i], b.output[i])
		}
	}
	log.Printf("Passed!\n")
}

func mix(v uint64) uint64 {
	return v*2862933555777941757 + 3037000493
}

func roundUp(v, align uint32) uint32 {
	return ((v + align - 1) / align) * align
}
