from gpumon.models import TelemetryEvent


def make_event(job="job-a", node="gb10-node-00", gpu=0, rank=0, **overrides):
    base = dict(
        job=job, node=node, gpu=gpu, rank=rank,
        sm_util=95.0, mem_used_gb=60.0, mem_total_gb=80.0,
        temp_c=70.0, power_w=650.0, ecc_errors=0,
        step=1, step_time_ms=180.0, tokens_per_s=3200.0,
        nccl_allreduce_ms=13.0, fabric_rx_gbps=190.0,
    )
    base.update(overrides)
    return TelemetryEvent(**base)
