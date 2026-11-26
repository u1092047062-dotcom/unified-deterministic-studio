"""Shared deterministic engineering primitives for Unified Deterministic Studio.

Unifies battle-tested patterns from:
- 05-platinen-elektronik (rotating telemetry, secret redaction, SHA-256 export provenance)
- Topaz / ART-Restoration-AI (atomic resumable job state, config-hash locking, quality-first resource guard)
"""

from platform_core.job_store import JobStore, atomic_write_json, compute_config_hash
from platform_core.provenance import (
    MANIFEST_SCHEMA_VERSION,
    build_provenance_manifest,
    sha256_bytes,
    sha256_file,
    verify_file_checksum,
)
from platform_core.resource_guard import (
    ResourcePlan,
    enforce_budget_gate,
    estimate_compute_cost,
    plan_resources,
)
from platform_core.security import scan_text_for_secrets, validate_safe_path
from platform_core.telemetry import PlatformTelemetryLogger, sanitize_payload

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "JobStore",
    "PlatformTelemetryLogger",
    "ResourcePlan",
    "atomic_write_json",
    "build_provenance_manifest",
    "compute_config_hash",
    "enforce_budget_gate",
    "estimate_compute_cost",
    "plan_resources",
    "sanitize_payload",
    "scan_text_for_secrets",
    "sha256_bytes",
    "sha256_file",
    "validate_safe_path",
    "verify_file_checksum",
]
