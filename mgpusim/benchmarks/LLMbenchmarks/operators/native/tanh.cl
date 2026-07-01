#include "common.h"

__kernel void llm_tanh(__global float *out,
                       const __global float *in,
                       int n) {
  int gid = get_global_id(0);
  if (gid >= n) {
    return;
  }

  out[gid] = llm_tanh_value(in[gid]);
}
