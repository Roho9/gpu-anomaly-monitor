# NCCL / RoCE fabric stall

## Symptom
`nccl_allreduce_ms` spikes across many ranks at once and `fabric_rx_gbps`
drops. The slowdown is cluster-wide rather than isolated to one GPU, which
points at the interconnect, not the accelerators.

## Likely causes
- A flapping or degraded RoCE/InfiniBand link.
- ECN/PFC misconfiguration causing congestion collapse.
- A degraded top-of-rack or spine switch.
- A bad cable or transceiver.

## Resolution
1. Correlate the spike across ranks to confirm it is fabric-wide.
2. Inspect switch counters (discards, pause frames) and link error rates.
3. Move the job off the degraded link/switch; drain the port.
4. Validate ECN/PFC and NCCL environment (e.g. `NCCL_IB_HCA`, GID index).

## Prevention
- Continuously monitor fabric bandwidth and collective latency percentiles.
