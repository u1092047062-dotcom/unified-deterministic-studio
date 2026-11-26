import json

from app.telemetry import record_art_event, reset_art_logger


def test_art_telemetry_writes_rotating_log_and_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ART_LOG_DIR", str(tmp_path))
    reset_art_logger()

    fake_token = "112233445" + ":" + ("Q" * 30)
    record = record_art_event(
        "restoration_job",
        status="COMPLETED",
        duration_ms=88.5,
        details={"project_id": "p1", "api_key": "secret", "raw": fake_token},
    )
    reset_art_logger()

    assert record is not None
    assert record["domain"] == "art-restoration-ai"
    assert record["details"]["api_key"] == "[REDACTED]"

    log_file = tmp_path / "art_pipeline.log"
    jsonl_file = tmp_path / "art_runs.jsonl"
    assert log_file.is_file()
    assert jsonl_file.is_file()
    assert fake_token not in log_file.read_text(encoding="utf-8")
    assert fake_token not in jsonl_file.read_text(encoding="utf-8")

    parsed = json.loads(jsonl_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert parsed["event"] == "restoration_job"
    assert parsed["status"] == "COMPLETED"
