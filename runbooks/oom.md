# HBM out-of-memory

## Symptom
`mem_pct` approaches 100% on one or more ranks; the process is about to be
killed with a CUDA out-of-memory error.

## Likely causes
- A batch-size or sequence-length increase in the latest config.
- Activation checkpointing disabled or a larger model variant.
- A memory leak in the training loop (tensors retained across steps).
- Fragmentation from dynamic shapes.

## Resolution
1. Compare the current run's config against the last healthy run.
2. Reduce micro-batch size or enable activation/gradient checkpointing.
3. Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` to fight
   fragmentation.
4. If a leak is suspected, snapshot allocator stats across steps.

## Prevention
- Gate config changes on a memory-headroom check before full-scale launch.
