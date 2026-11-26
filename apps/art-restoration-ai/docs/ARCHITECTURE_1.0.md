# Restoration Engine 1.0 – architecture

## Pipeline

1. Immutable source upload and SHA-256 manifest.
2. Metadata analysis and bounded tile planning.
3. Adapter selection by explicit allowlist; no automatic downgrade.
4. Tile execution with persisted outputs and state manifest.
5. Weighted feather stitching.
6. Quality metrics, output checksum and provenance report.
7. Optional licensed Topaz desktop handoff/import.

## Components

- `app/main.py`: local FastAPI control plane and project/job endpoints.
- `app/storage.py`: image validation, metadata and checksums.
- `app/topaz.py`: safe manual desktop handoff and import; no UI or licensing automation.
- `restoration/tiling.py`: deterministic full-coverage tile geometry.
- `restoration/stitching.py`: overlap feather weights and composite reconstruction.
- `restoration/engine.py`: resumable tile execution and QC manifest.
- `restoration/adapters.py`: explicit model adapter boundary; validation adapter only is enabled.
- `resource_manager/`: quality-preserving resource decisions and budget gates.
- `app/cloud.py`: cloud readiness status; dispatch remains disabled.

## Safety invariants

- Original file is never overwritten.
- Source hash is rechecked before processing and Topaz export.
- Only known adapter IDs can execute.
- A job ID cannot be resumed with a changed source/configuration.
- Model precision and quality profile are not reduced to meet a budget.
- Topaz is used via manual export/import until a vendor-supported interface is verified.
- AI reconstruction is not labeled historical truth.
