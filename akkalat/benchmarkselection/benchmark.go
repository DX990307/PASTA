package benchmarkselection

import (
	"github.com/sarchlab/mgpusim/v3/benchmarks"
	"github.com/sarchlab/mgpusim/v3/benchmarks/LLMbenchmarks/bert"
	"github.com/sarchlab/mgpusim/v3/benchmarks/LLMbenchmarks/gpt"
	"github.com/sarchlab/mgpusim/v3/benchmarks/LLMbenchmarks/inference"
	"github.com/sarchlab/mgpusim/v3/benchmarks/LLMbenchmarks/llmop"
	"github.com/sarchlab/mgpusim/v3/benchmarks/LLMbenchmarks/resnet"
	"github.com/sarchlab/mgpusim/v3/benchmarks/TensorParallelismSample/layer_benchmarks/conv2d"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/bitonicsort"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/fastwalshtransform"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/floydwarshall"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/matrixmultiplication"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/matrixtranspose"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/nbody"
	"github.com/sarchlab/mgpusim/v3/benchmarks/amdappsdk/simpleconvolution"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/btfwt"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/fwtfws"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/fwtmt"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/kmsc"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/mmfir"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/prfws"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/prsc"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/scsc"
	"github.com/sarchlab/mgpusim/v3/benchmarks/concurrentRunning/spmvmt"

	// "github.com/sarchlab/mgpusim/v3/benchmarks/dnn/layer_benchmarks/conv2d"
	"github.com/sarchlab/mgpusim/v3/benchmarks/dnn/layer_benchmarks/im2col"
	"github.com/sarchlab/mgpusim/v3/benchmarks/dnn/layer_benchmarks/relu"
	"github.com/sarchlab/mgpusim/v3/benchmarks/dnn/training_benchmarks/lenet"
	"github.com/sarchlab/mgpusim/v3/benchmarks/dnn/training_benchmarks/minerva"
	"github.com/sarchlab/mgpusim/v3/benchmarks/dnn/training_benchmarks/vgg16"
	"github.com/sarchlab/mgpusim/v3/benchmarks/heteromark/aes"
	"github.com/sarchlab/mgpusim/v3/benchmarks/heteromark/fir"
	"github.com/sarchlab/mgpusim/v3/benchmarks/heteromark/kmeans"
	"github.com/sarchlab/mgpusim/v3/benchmarks/heteromark/pagerank"
	"github.com/sarchlab/mgpusim/v3/benchmarks/llm/kvcache"
	"github.com/sarchlab/mgpusim/v3/benchmarks/polybench/atax"
	"github.com/sarchlab/mgpusim/v3/benchmarks/polybench/bicg"
	"github.com/sarchlab/mgpusim/v3/benchmarks/rodinia/nw"
	"github.com/sarchlab/mgpusim/v3/benchmarks/shoc/fft"
	"github.com/sarchlab/mgpusim/v3/benchmarks/shoc/spmv"
	"github.com/sarchlab/mgpusim/v3/benchmarks/shoc/stencil2d"
	"github.com/sarchlab/mgpusim/v3/driver"
)

func SelectBenchmark(name string, driver *driver.Driver) benchmarks.Benchmark {
	var benchmark benchmarks.Benchmark
	switch name {
	case "aes":
		aes := aes.NewBenchmark(driver)
		aes.Length = 1073741824
		benchmark = aes
	case "atax":
		atax := atax.NewBenchmark(driver)
		atax.NX = 16384
		atax.NY = 16384
		benchmark = atax
	case "bicg":
		bicg := bicg.NewBenchmark(driver)
		bicg.NX = 16384
		bicg.NY = 16384
		benchmark = bicg
	case "bitonicsort":
		bitonicsort := bitonicsort.NewBenchmark(driver)
		bitonicsort.Length = 268435456
		benchmark = bitonicsort
	case "bert":
		benchmark = bert.NewBenchmark(driver)
	case "conv2d":
		conv2d := conv2d.NewBenchmark(driver)
		conv2d.N = 1
		conv2d.C = 3
		conv2d.H = 1052
		conv2d.W = 1052
		conv2d.KernelChannel = 3
		conv2d.KernelHeight = 4
		conv2d.KernelWidth = 4
		conv2d.PadX = 0
		conv2d.PadY = 0
		conv2d.StrideX = 1
		conv2d.StrideY = 1
		benchmark = conv2d
	case "fastwalshtransform":
		fastwalshtransform := fastwalshtransform.NewBenchmark(driver)
		fastwalshtransform.Length = 268435456
		benchmark = fastwalshtransform
	case "fir":
		fir := fir.NewBenchmark(driver)
		fir.Length = 134217728
		benchmark = fir
	case "fft":
		fft := fft.NewBenchmark(driver)
		fft.Bytes = 1024
		fft.Passes = 4
		benchmark = fft
	case "floydwarshall":
		floydwarshall := floydwarshall.NewBenchmark(driver)
		floydwarshall.NumNodes = 11584
		floydwarshall.NumIterations = 1
		benchmark = floydwarshall
	case "gpt":
		benchmark = gpt.NewBenchmark(driver)
	case "im2col":
		im2col := im2col.NewBenchmark(driver)
		im2col.N = 2
		im2col.C = 3
		im2col.H = 2116
		im2col.W = 2116
		im2col.KernelHeight = 3
		im2col.KernelWidth = 3
		im2col.PadX = 0
		im2col.PadY = 0
		im2col.StrideX = 1
		im2col.StrideY = 1
		im2col.DilateX = 1
		im2col.DilateY = 1
		benchmark = im2col
	case "kmeans":
		kmeans := kmeans.NewBenchmark(driver)
		kmeans.NumPoints = 53687091
		kmeans.NumClusters = 2
		kmeans.NumFeatures = 2
		kmeans.MaxIter = 18
		benchmark = kmeans
	case "kvcache":
		kvcache := kvcache.NewBenchmark(driver)
		kvcache.NumLayers = 16
		kvcache.NumHeads = 32
		kvcache.NumKVHeads = 32
		kvcache.SeqLen = 2048
		kvcache.HeadDim = 128
		kvcache.DecodeStep = 1
		benchmark = kvcache
	case "kvcache-decode":
		kvcache := kvcache.NewDecodeBenchmark(driver)
		kvcache.NumLayers = 16
		kvcache.NumHeads = 32
		kvcache.NumKVHeads = 32
		kvcache.SeqLen = 2048
		kvcache.HeadDim = 128
		kvcache.SeqBlock = 64
		kvcache.DecodeStep = 1
		benchmark = kvcache
	case "kvcache-decode-30b":
		kvcache := kvcache.NewDecode30BBenchmark(driver)
		kvcache.NumLayers = 60
		kvcache.NumHeads = 52
		kvcache.NumKVHeads = 52
		kvcache.SeqLen = 336
		kvcache.HeadDim = 128
		kvcache.SeqBlock = 64
		kvcache.DecodeStep = 1
		benchmark = kvcache
	case "llminference":
		benchmark = inference.NewBenchmark(driver)
	case "llmop":
		benchmark = llmop.NewBenchmarkFromFlags(driver)
	case "matrixmultiplication":
		matrixmultiplication := matrixmultiplication.NewBenchmark(driver)
		matrixmultiplication.X = 128
		matrixmultiplication.Y = 1048576
		matrixmultiplication.Z = 128
		benchmark = matrixmultiplication
	case "matrixmultiplication-ptw":
		matrixmultiplication := matrixmultiplication.NewBenchmark(driver)
		matrixmultiplication.X = 32
		matrixmultiplication.Y = 1048576
		matrixmultiplication.Z = 224
		benchmark = matrixmultiplication
	case "matrixmultiplication-ptw-heavy":
		matrixmultiplication := matrixmultiplication.NewBenchmark(driver)
		matrixmultiplication.X = 32
		matrixmultiplication.Y = 933888
		matrixmultiplication.Z = 256
		benchmark = matrixmultiplication
	case "matrixtranspose":
		matrixtranspose := matrixtranspose.NewBenchmark(driver)
		matrixtranspose.Width = 11584
		benchmark = matrixtranspose
	case "nbody":
		nbody := nbody.NewBenchmark(driver)
		nbody.NumParticles = 16777216
		nbody.NumIterations = 1024
		benchmark = nbody
	case "nw":
		nw := nw.NewBenchmark(driver)
		nw.SetLength(9472)
		benchmark = nw
	case "pagerank":
		pagerank := pagerank.NewBenchmark(driver)
		pagerank.NumNodes = 88700000
		pagerank.NumConnections = 1048576
		pagerank.MaxIterations = 1
		benchmark = pagerank
	case "relu":
		relu := relu.NewBenchmark(driver)
		relu.Length = 134217728
		benchmark = relu
	case "resnet":
		benchmark = resnet.NewBenchmark(driver)
	case "simpleconvolution":
		simpleconvolution := simpleconvolution.NewBenchmark(driver)
		simpleconvolution.Height = 2048
		simpleconvolution.Width = 65536
		simpleconvolution.SetMaskSize(3)
		benchmark = simpleconvolution
	case "spmv":
		spmv := spmv.NewBenchmark(driver)
		spmv.Dim = 84700000
		spmv.Sparsity = 0.000000001
		benchmark = spmv
	case "stencil2d":
		stencil2d := stencil2d.NewBenchmark(driver)
		stencil2d.NumRows = 16384
		stencil2d.NumCols = 8192
		stencil2d.NumIteration = 3
		benchmark = stencil2d
	case "lenet":
		lenet := lenet.NewBenchmark(driver)
		lenet.Epoch = 1
		lenet.MaxBatchPerEpoch = 2
		lenet.BatchSize = 32
		lenet.EnableTesting = false
		lenet.EnableVerification = false
		benchmark = lenet
	case "minerva":
		minerva := minerva.NewBenchmark(driver)
		minerva.Epoch = 1
		minerva.MaxBatchPerEpoch = 2
		minerva.BatchSize = 32
		minerva.EnableTesting = false
		minerva.EnableVerification = false
		benchmark = minerva
	case "vgg16":
		vgg16 := vgg16.NewBenchmark(driver)
		vgg16.Epoch = 1
		vgg16.MaxBatchPerEpoch = 2
		vgg16.BatchSize = 8
		vgg16.EnableTesting = false
		vgg16.EnableVerification = false
		benchmark = vgg16
	case "prfws":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		prfws := prfws.NewBenchmark(driver)
		benchmark = prfws

	case "btfwt":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		btfwt := btfwt.NewBenchmark(driver)
		benchmark = btfwt

	case "fwtfws":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		fwtfws := fwtfws.NewBenchmark(driver)
		benchmark = fwtfws

	case "kmsc":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		kmsc := kmsc.NewBenchmark(driver)
		benchmark = kmsc
	case "scsc":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		scsc := scsc.NewBenchmark(driver)
		benchmark = scsc
	case "mmfir":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		mmfir := mmfir.NewBenchmark(driver)
		benchmark = mmfir
	case "prsc":
		// This is a concurrent running benchmark that runs pagerank and fast walsh
		prsc := prsc.NewBenchmark(driver)
		benchmark = prsc
	case "fwtmt":
		// This is a concurrent running benchmark that runs fast walsh and matrix transpose
		fwtmt := fwtmt.NewBenchmark(driver)
		benchmark = fwtmt
	case "spmvmt":
		// This is a concurrent running benchmark that runs sparse matrix vector multiplication and matrix transpose
		spmvmt := spmvmt.NewBenchmark(driver)
		benchmark = spmvmt

	default:
		panic("Unknown benchmark")
	}

	return benchmark
}
