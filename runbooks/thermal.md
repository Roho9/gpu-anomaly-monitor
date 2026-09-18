# Thermal throttling

## Symptom
`temp_c` on a node's GPUs exceeds ~87C and `step_time_ms` rises uniformly
across that node as clocks are reduced to stay within thermal limits.

## Likely causes
- Datacenter cooling or airflow problem for that rack.
- Blocked intake, failed fan, or dust.
- Ambient temperature excursion.

## Resolution
1. Confirm the temperature spike is node-wide, not a single GPU.
2. Check rack cooling and airflow; escalate to datacenter ops.
3. Temporarily power-cap the GPUs on the node to hold clocks stable.
4. Migrate the job's ranks off the affected node if throughput matters.

## Prevention
- Alert on sustained temperatures above 85C before throttling begins.
