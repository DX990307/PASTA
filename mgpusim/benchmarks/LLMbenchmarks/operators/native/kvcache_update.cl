#include "common.h"

__kernel void llm_kv_cache_update(__global float *k_cache,
                                  __global float *v_cache,
                                  const __global float *k_new,
                                  const __global float *v_new,
                                  int rows,
                                  int hidden,
                                  int kv_rows) {
  int gid = get_global_id(0);
  int n = rows * hidden;
  if (gid >= n) {
    return;
  }

  int row = gid / hidden;
  int col = gid - row * hidden;
  int dst_row = kv_rows - rows + row;
  int dst = dst_row * hidden + col;

  k_cache[dst] = k_new[gid];
  v_cache[dst] = v_new[gid];
}
