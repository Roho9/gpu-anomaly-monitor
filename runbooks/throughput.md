# Training throughput collapse (input-bound)

## Symptom
`tokens_per_s` falls sharply while `sm_util` also drops - the GPUs are
starved for work rather than compute-bound. `step_time_ms` rises because
each step waits on data.

## Likely causes
- Dataloader bottleneck (too few workers, slow decode/augmentation).
- Storage or object-store throughput regression.
- A recent code/deploy change that regressed the input pipeline.
- Checkpoint I/O blocking the training step.

## Resolution
1. Correlate the drop with the most recent job restart or config/deploy
   change; roll it back if it lines up.
2. Increase dataloader worker count and enable prefetching.
3. Check storage/object-store latency and bandwidth for the job.
4. Move checkpointing to an async path.

## Prevention
- Track a compute-bound vs input-bound ratio and alert on regressions.
