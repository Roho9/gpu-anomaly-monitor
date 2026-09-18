# NVIDIA XID hardware faults

## Symptom
An `xid_error` code is reported for a GPU. XID errors are driver/hardware
level events that usually make the device unreliable for the rest of the run.

## Common codes
- **48** - double-bit ECC error (uncorrectable).
- **63** - row-remapping event pending; GPU reset required.
- **79** - GPU has fallen off the bus; hardware fault, node likely needs a
  power cycle.
- **13 / 31** - kernel/user memory faults, often a bad custom CUDA kernel.

## Resolution
1. For 13/31, inspect the most recent user kernel or model change.
2. For 48/63/79, cordon the node and reset the GPU (or reboot the host).
3. If the XID recurs after reset, RMA the card.

## Prevention
- Fail fast: kill and reschedule the job off any GPU reporting a hardware XID.
