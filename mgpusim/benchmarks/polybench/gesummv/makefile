all: kernels.hsaco

kernels.hsaco: native/gesummv.cl
	clang -x cl -target amdgcn-amd-amdhsa -mcpu=fiji -mcode-object-version=2 --rocm-device-lib-path=$${ROCM_DEVICE_LIB_PATH:-/tmp/rocm-device-libs/usr/lib/x86_64-linux-gnu/amdgcn/bitcode} -o $@ $^
