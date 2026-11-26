import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from platform_core import (
    JobStore,
    PlatformTelemetryLogger,
    build_provenance_manifest,
    compute_config_hash,
    enforce_budget_gate,
    estimate_compute_cost,
    plan_resources,
    sanitize_payload,
    scan_text_for_secrets,
    sha256_bytes,
    sha256_file,
    validate_safe_path,
    verify_file_checksum,
)


def test_provenance_manifest_and_file_checksum(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("deterministic-payload", encoding="utf-8")

    file_hash = sha256_file(sample)
    assert file_hash == sha256_bytes("deterministic-payload")
    assert verify_file_checksum(sample, file_hash) is True
    assert verify_file_checksum(sample, "0" * 64) is False

    m1 = build_provenance_manifest(
        {"a.txt": "alpha", "b.bin": b"\x01\x02"},
        {"width": 100},
        domain="eda",
        generated_at_utc="2026-11-26T12:00:00+00:00",
    )
    m2 = build_provenance_manifest(
        {"b.bin": b"\x01\x02", "a.txt": "alpha"},
        {"width": 100},
        domain="eda",
        generated_at_utc="2026-11-26T13:00:00+00:00",
    )
    assert m1["manifest_sha256"] == m2["manifest_sha256"]
    assert m1["artifact_count"] == 2
    assert m1["total_size_bytes"] == 7


def test_job_store_atomic_create_update_and_resume_guard(tmp_path):
    store = JobStore(tmp_path / "jobs")
    cfg = {"board_sha256": "abc123", "max_attempts": 3}

    created = store.create_job("job-001", domain="eda", config=cfg)
    assert created["status"] == "queued"
    assert created["config_sha256"] == compute_config_hash(cfg)

    # Duplicate job_id rejected
    with pytest.raises(FileExistsError):
        store.create_job("job-001", domain="eda", config=cfg)

    # Resume with same config succeeds
    resumed = store.validate_resume("job-001", cfg)
    assert resumed["job_id"] == "job-001"

    # Resume with modified config fails closed
    with pytest.raises(ValueError, match="Konfigurations-Hash"):
        store.validate_resume("job-001", {"board_sha256": "CHANGED", "max_attempts": 3})

    # Protected fields cannot be overwritten
    with pytest.raises(ValueError, match="Geschuetztes"):
        store.update_job("job-001", {"config_sha256": "fake"})

    updated = store.update_job("job-001", {"status": "completed", "drc_errors": 0})
    assert updated["status"] == "completed"
    assert updated["drc_errors"] == 0

    # Completed jobs reject resume
    with pytest.raises(ValueError, match="Abgeschlossener Job"):
        store.validate_resume("job-001", cfg)

    assert len(store.list_jobs()) == 1


def test_resource_guard_never_downgrades_quality():
    low = plan_resources(ram_gb=7.82, vram_gb=4.0)
    assert low.tile_size == 512
    assert low.concurrency == 1
    assert low.cargo_build_jobs == 2
    assert low.allow_quality_downgrade is False
    assert low.model_precision == "full"

    high = plan_resources(ram_gb=32.0, vram_gb=24.0)
    assert high.tile_size == 1024
    assert high.concurrency == 2
    assert high.allow_quality_downgrade is False

    cost = estimate_compute_cost(1800, 2.0)
    assert cost == 1.0
    assert enforce_budget_gate(cost, 1.5)["allowed"] is True
    assert enforce_budget_gate(cost, 0.5)["allowed"] is False


def test_telemetry_rotation_and_secret_redaction(tmp_path):
    logger = PlatformTelemetryLogger(
        tmp_path,
        domain="art",
        log_file_name="art_pipeline.log",
        jsonl_file_name="art_runs.jsonl",
        max_bytes=256,
        backup_count=2,
    )
    fake_tg = "987654321" + ":" + ("Z" * 30)
    try:
        for i in range(15):
            logger.log_event(
                "job_step",
                status="OK",
                duration_ms=5.0 + i,
                details={"api_key": "secret-val", "msg": f"token={fake_tg}"},
            )
    finally:
        logger.close()

    assert (tmp_path / "art_pipeline.log").is_file()
    assert (tmp_path / "art_pipeline.log.1").is_file()
    assert not (tmp_path / "art_pipeline.log.3").exists()

    jsonl_lines = (tmp_path / "art_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl_lines) == 15
    first = json.loads(jsonl_lines[0])
    assert first["details"]["api_key"] == "[REDACTED]"
    assert fake_tg not in (tmp_path / "art_runs.jsonl").read_text(encoding="utf-8")
    assert sanitize_payload({"password": "pw"})["password"] == "[REDACTED]"


def test_security_safe_path_and_secret_scanner(tmp_path):
    log_file = tmp_path / "ok.log"
    log_file.write_text("clean line", encoding="utf-8")

    resolved = validate_safe_path(
        log_file, base_dir=tmp_path, allowed_extensions={".log"}, must_exist=True
    )
    assert resolved == log_file.resolve()

    with pytest.raises(ValueError):
        validate_safe_path("../escape.log")
    with pytest.raises(ValueError):
        validate_safe_path(tmp_path / ".env")
    with pytest.raises(ValueError):
        validate_safe_path(tmp_path / "notes.json", allowed_extensions={".log"})

    synth_key = "sk-" + ("x" * 24)
    assert "openai_api_key" in scan_text_for_secrets(f"key={synth_key}")
    assert scan_text_for_secrets("normal deterministic engineering text") == []
