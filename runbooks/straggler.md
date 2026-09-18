# Straggler rank stalling collective operations

## Symptom
One rank's `step_time_ms` is significantly higher than the cluster median
while its `sm_util` drops. Because `all-reduce` is a synchronous barrier,
every other rank waits on the slow one, so global `tokens_per_s` falls even
though most GPUs are idle.

## Likely causes
- A single GPU thermal-throttling or power-capped.
- A degraded NVLink/PCIe link on one board.
- CPU contention or a noisy neighbor on that node.
- Non-uniform data sharding (one rank processing heavier samples).

## Resolution
1. Identify the slow rank from the incident signals (node/gpu is included).
2. Cordon the offending GPU/node so the scheduler stops placing work there.
3. Restart the job from the last checkpoint excluding the bad rank.
4. If it recurs on the same hardware, drain the node for diagnostics.

## Prevention
- Enable per-rank step-time telemetry and alert on cross-rank variance.
- Balance shards; pin dataloader workers.
