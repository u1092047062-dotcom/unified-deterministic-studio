# Roadmap – Restoration Engine 1.0

## Implemented in 1.0.0-dev
- [x] FastAPI project/source API with upload limits and immutable original protection
- [x] SHA-256 source integrity verification
- [x] Image metadata analysis and tile-plan endpoint
- [x] Deterministic full-coverage overlapping tile planning
- [x] Weighted feather blending and stitching
- [x] Persisted tile cache and resume state bound to source hash/configuration
- [x] Identity-validation adapter for end-to-end pipeline verification
- [x] QC report with dimensions, hashes, MSE/PSNR and exact-pixel-identity flag
- [x] 16-bit grayscale preservation; unsupported modes are rejected instead of silently converted
- [x] Topaz manual export/import roundtrip with hash-verified copy and provenance log
- [x] Quality-first resource policy and budget stop gate
- [x] Local Windows installer/start scripts and CI test suite
- [x] Cloud readiness status; dispatch safely disabled

## Remaining before a true AI-restoration release
- [ ] Select exact Real-ESRGAN/SwinIR/HAT/DiffBIR model versions and checkpoint files
- [ ] Review code, weights, dataset and dependency licenses; pin checkpoint SHA-256 hashes
- [ ] Implement and validate real PyTorch GPU adapters and CUDA worker images
- [ ] Add content-aware routing and face restoration only when appropriate/approved
- [ ] Add streaming BigTIFF/pyvips processing for very large images
- [ ] Calibrate artifact/edge/texture/color QC on expert-reviewed artwork test sets
- [ ] Add masked reconstruction provenance and side-by-side human review
- [ ] Implement RunPod dispatch, cancellation, retries and budget controls against a real test endpoint
- [ ] Configure encrypted object storage, lifecycle deletion and verified cleanup
- [ ] Add authentication, roles, audit logs, rate limits, backup/restore and deployment hardening
- [ ] Verify Topaz vendor-supported automation rights for exact versions/licensing; until then manual desktop handoff only

## Release gate
The 1.0.0-dev core is locally testable. It is not a production release and does not claim to perform AI enhancement with the identity adapter.
