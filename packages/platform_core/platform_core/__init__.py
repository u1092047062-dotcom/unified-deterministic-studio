"""Shared deterministic engineering primitives for Unified Deterministic Studio.

Unifies battle-tested patterns from:
- 05-platinen-elektronik (rotating telemetry, secret redaction, SHA-256 export provenance)
- Topaz / ART-Restoration-AI (atomic resumable job state, config-hash locking, quality-first resource guard)
"""

from platform_core.c2pa_credentials import (
    C2PA_SCHEMA_VERSION,
    build_c2pa_manifest,
    compute_png_pixel_digest,
    detect_c2pa_backend,
    embed_png_content_credentials,
    read_png_content_credentials,
    verify_png_content_credentials,
)
from platform_core.crypto_signing import (
    build_transparency_bundle,
    detect_hardware_trust_providers,
    ed25519_public_key,
    embed_signed_png_c2pa,
    generate_ed25519_keypair,
    sign_ed25519,
    sign_manifest_ed25519,
    verify_ed25519,
    verify_manifest_ed25519,
    verify_signed_png_c2pa,
    verify_transparency_bundle,
)
from platform_core.durable_workflow import (
    BudgetCircuitBreakerError,
    ConfigMismatchError,
    DurableWorkflowEngine,
)
from platform_core.job_store import JobStore, atomic_write_json, compute_config_hash
from platform_core.otel_tracing import (
    PlatformTracer,
    format_traceparent,
    new_span_id,
    new_trace_id,
    parse_traceparent,
)
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
    "BudgetCircuitBreakerError",
    "C2PA_SCHEMA_VERSION",
    "ConfigMismatchError",
    "DurableWorkflowEngine",
    "MANIFEST_SCHEMA_VERSION",
    "JobStore",
    "PlatformTelemetryLogger",
    "PlatformTracer",
    "ResourcePlan",
    "atomic_write_json",
    "build_c2pa_manifest",
    "build_provenance_manifest",
    "compute_config_hash",
    "compute_png_pixel_digest",
    "detect_c2pa_backend",
    "embed_png_content_credentials",
    "enforce_budget_gate",
    "estimate_compute_cost",
    "format_traceparent",
    "new_span_id",
    "new_trace_id",
    "parse_traceparent",
    "plan_resources",
    "read_png_content_credentials",
    "sanitize_payload",
    "scan_text_for_secrets",
    "sha256_bytes",
    "sha256_file",
    "validate_safe_path",
    "verify_file_checksum",
    "verify_png_content_credentials",
]
