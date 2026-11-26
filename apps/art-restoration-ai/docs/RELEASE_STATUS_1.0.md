# Release status – Restoration Engine 1.0.0-dev

## Implemented and locally tested
- Project/source API, image validation and source hash verification.
- Image analysis and tile-plan endpoint.
- Full-coverage overlapping tile planner.
- Identity validation adapter, persisted tile cache and resumable engine state.
- Weighted overlap stitching.
- Pixel-identity/MSE/PSNR report.
- Topaz manual handoff/import with manifest and checksums.
- Quality-first resource and budget gate.
- Local Windows install/start scripts and API docs.

## Deliberately not claimed as complete
- Real AI enhancement weights or model-specific GPU execution.
- Verified Topaz CLI/API automation.
- RunPod API dispatch or live cloud worker.
- Production object storage, authentication, roles, audit hardening and retention controls.
- Museum-grade acceptance: requires reference artwork test sets and human expert review.

## Verification
- Automated test suite: 15 passed in the build environment.
- Tests run without GPU, Topaz, API keys or cloud resources.
- The identity adapter verifies exact pixel-preserving tiling/stitching; it is not a restoration model.
