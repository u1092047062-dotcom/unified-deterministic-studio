# Changelog

## 1.0.0-dev1 – 2026-11-21
- Expanded starter 0.2.1 into a locally runnable Restoration Engine validation/orchestration core.
- Added API capabilities, image analysis, tile planning, queued/background jobs, job status and resume endpoint.
- Added weighted feather stitching, persisted per-tile outputs and QC manifests.
- Added 16-bit grayscale preservation and explicit rejection of unsupported high-bit-depth/color modes rather than silent conversion.
- Added local processing pixel guard to protect resource-limited notebooks.
- Added Topaz install-path inventory, safe manual handoff/import, hashes and provenance.
- Added quality-first resource plans and cost/budget preflight endpoint.
- Added Windows install/start scripts, Docker Compose loopback binding, CI tests and architecture/release docs.
- Explicitly left real AI checkpoints, Topaz CLI/API automation and RunPod dispatch disabled pending required licenses, credentials, worker and acceptance tests.
