# RunPod worker integration – gated, not yet dispatchable

The 1.0 API deliberately reports `dispatch_enabled=false`. No RunPod API request is made by this release.

Before enabling a worker, implement and verify all of the following:

1. A pinned, reproducible CUDA container with exact Python/CUDA/PyTorch/model versions.
2. Model and checkpoint license review plus SHA-256 allowlist.
3. Worker contract: job ID, source object key + checksum, model ID/version, quality profile, tile manifest, cancellation, heartbeat, structured logs, output checksums and QC results.
4. Private object storage with TLS, scoped short-lived credentials, lifecycle deletion and explicit region/data-retention settings.
5. Budget preflight and maximum spend guard. Budget exhaustion stops dispatch; it never selects a lower-quality model.
6. Retry/resume semantics and idempotent tile outputs.
7. Worker image scanning, non-root runtime, no embedded API keys, restricted network egress and deletion verification.
8. End-to-end tests against a disposable endpoint before enabling production dispatch.

Do not paste API tokens into source files or commit them. Configure secrets only through the deployment platform's secret facility. The notebook's 8 GB RAM / GTX 1050 4 GB profile is suitable for orchestration and small preparation tasks; large restoration inference should be delegated to an adequately provisioned GPU worker after validation.
