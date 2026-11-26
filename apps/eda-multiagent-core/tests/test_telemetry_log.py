import json

import pytest

from core.pipeline_v2 import DesignPipeline, STATUS_DRC_FAILED, STATUS_OK
from core.schema import BoardState
from core.telemetry_log import TelemetryLogger, reset_default_logger, sanitize_payload


@pytest.fixture(autouse=True)
def _cleanup_default_logger():
    yield
    reset_default_logger()


def test_telemetry_creates_log_and_jsonl_with_expected_schema(tmp_path):
    logger = TelemetryLogger(log_dir=tmp_path)
    try:
        record = logger.log_pipeline_run(
            status=STATUS_OK,
            attempts=1,
            duration_ms=42.567,
            components=2,
            nets=3,
            traces=4,
            vias=1,
            drc_errors=0,
        )
    finally:
        logger.close()

    assert logger.log_file.is_file()
    assert logger.jsonl_file.is_file()

    lines = logger.jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == record
    assert parsed["event"] == "pipeline_run"
    assert parsed["status"] == STATUS_OK
    assert parsed["attempts"] == 1
    assert parsed["duration_ms"] == 42.57
    assert parsed["components"] == 2
    assert parsed["nets"] == 3
    assert parsed["traces"] == 4
    assert parsed["vias"] == 1
    assert parsed["drc_errors"] == 0
    assert "T" in parsed["timestamp"]

    log_text = logger.log_file.read_text(encoding="utf-8")
    assert "pipeline_run status=OK" in log_text


def test_telemetry_rotates_pipeline_log_files(tmp_path):
    logger = TelemetryLogger(log_dir=tmp_path, max_bytes=256, backup_count=3)
    try:
        for i in range(25):
            logger.log_pipeline_run(
                status=STATUS_OK,
                attempts=i + 1,
                duration_ms=10.0 + i,
                components=5,
                nets=8,
                traces=12,
                vias=2,
                drc_errors=0,
            )
    finally:
        logger.close()

    assert (tmp_path / "eda_pipeline.log").is_file()
    assert (tmp_path / "eda_pipeline.log.1").is_file()
    # Maximal 3 Backups
    assert not (tmp_path / "eda_pipeline.log.4").exists()


def test_telemetry_logs_failure_and_redacts_secrets(tmp_path):
    logger = TelemetryLogger(log_dir=tmp_path)
    # Synthetische Testmuster dynamisch bauen, um keine statischen Secret-Scanner auszuloesen
    fake_tg_token = "123456789" + ":" + ("A" * 24) + "123456"
    fake_openai = "sk-" + ("a" * 24) + "123456"
    fake_bearer = "mySecretBearer" + "Token12345"
    try:
        record = logger.log_pipeline_run(
            status=STATUS_DRC_FAILED,
            attempts=3,
            duration_ms=128.4,
            components=4,
            nets=6,
            traces=5,
            vias=1,
            drc_errors=2,
            extra={
                "teloxide_token": fake_tg_token,
                "note": f"Authorization Bearer {fake_bearer} and {fake_openai}",
                "inline_bot": fake_tg_token,
            },
        )
    finally:
        logger.close()

    assert record["status"] == STATUS_DRC_FAILED
    assert record["extra"]["teloxide_token"] == "[REDACTED]"

    log_content = logger.log_file.read_text(encoding="utf-8")
    jsonl_content = logger.jsonl_file.read_text(encoding="utf-8")
    for raw_secret in (fake_tg_token, fake_openai, fake_bearer):
        assert raw_secret not in log_content
        assert raw_secret not in jsonl_content

    assert "[WARNING]" in log_content


def test_sanitize_payload_handles_nested_structures():
    cleaned = sanitize_payload(
        {
            "api_key": "top-secret",
            "nested": [{"password": "pw", "safe": "ok"}],
        }
    )
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"][0]["password"] == "[REDACTED]"
    assert cleaned["nested"][0]["safe"] == "ok"


def test_design_pipeline_run_writes_telemetry_to_configured_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EDA_LOG_DIR", str(tmp_path))
    reset_default_logger()

    result = DesignPipeline().run(BoardState(width=30.0, height=20.0))
    reset_default_logger()

    assert result.status == STATUS_OK
    jsonl_path = tmp_path / "eda_runs.jsonl"
    assert jsonl_path.is_file()
    entries = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) >= 1
    last = entries[-1]
    assert last["event"] == "pipeline_run"
    assert last["status"] == STATUS_OK
    assert last["drc_errors"] == 0
    assert last["duration_ms"] >= 0.0
