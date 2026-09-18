# ECC errors and failing HBM

## Symptom
Rising `ecc_errors` on a specific GPU. Correctable errors degrade
performance; a double-bit (uncorrectable) error crashes the process and can
silently corrupt gradients.

## Likely causes
- Aging or defective HBM stacks.
- Overheating accelerating error rates.
- A row that needs remapping (XID 63).

## Resolution
1. Treat any accumulation of ECC errors as a hardware fault - do not ignore.
2. Cordon and drain the node immediately.
3. Run field diagnostics; trigger row-remapping and reboot.
4. If uncorrectable errors persist, RMA the GPU.

## Prevention
- Alert on the first correctable ECC error, not a high threshold.
- Track error trends per serial number to catch degrading parts early.
