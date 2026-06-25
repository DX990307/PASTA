typedef unsigned long ulong;

ulong mix(ulong v)
{
	return v * 2862933555777941757UL + 3037000493UL;
}

__kernel void gups_kernel(__global ulong *table, __global uint *indices, int updates)
{
	int i = get_global_id(0);

	if (i < updates)
	{
		uint idx = indices[i];
		table[idx] = table[idx] ^ mix((ulong)i);
	}
}
