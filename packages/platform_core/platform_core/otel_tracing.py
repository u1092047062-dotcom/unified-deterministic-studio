"""W3C Trace Context & OpenTelemetry (OTLP/JSON)-kompatibler Tracer (Phase 4).

Stdlib-only, fail-closed:
- Erzeugt gueltige W3C-`traceparent`-Header (`00-<32-hex-trace_id>-<16-hex-span_id>-01`).
- Redigiert alle Span-Attribute automatisch ueber `sanitize_payload()` (0 Secret-Leaks).
- Schreibt Spans lokal als JSONL und exportiert standardkonforme OTLP-JSON-Payloads
  (`resourceSpans`) fuer Grafana Tempo, Jaeger oder OpenTelemetry Collector.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Dict, Iterator, List, Optional

from platform_core.telemetry import sanitize_payload


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{span_id}-{flags}"


def parse_traceparent(header: str) -> Optional[Dict[str, str]]:
    parts = header.strip().split("-")
    if len(parts) != 4 or parts[0] != "00" or len(parts[1]) != 32 or len(parts[2]) != 16:
        return None
    try:
        int(parts[1], 16)
        int(parts[2], 16)
    except ValueError:
        return None
    return {"version": parts[0], "trace_id": parts[1], "parent_span_id": parts[2], "flags": parts[3]}


class PlatformTracer:
    """Leichtgewichtiger OTLP-kompatibler Distributed Tracer mit automatischer Secret-Redaktion."""

    def __init__(self, service_name: str, span_log_path: Optional[Path | str] = None) -> None:
        self.service_name = service_name
        self.span_log_path = Path(span_log_path) if span_log_path is not None else None
        self.spans: List[Dict[str, Any]] = []

    @contextmanager
    def start_span(
        self,
        name: str,
        *,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        tid = trace_id or new_trace_id()
        sid = new_span_id()
        start_ns = time.time_ns()
        span: Dict[str, Any] = {
            "traceId": tid,
            "spanId": sid,
            "parentSpanId": parent_span_id or "",
            "traceparent": format_traceparent(tid, sid),
            "name": name,
            "serviceName": self.service_name,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(start_ns),
            "durationMs": 0.0,
            "status": {"code": 1, "message": "OK"},
            "attributes": sanitize_payload(attributes or {}),
        }
        try:
            yield span
        except Exception as exc:
            span["status"] = {"code": 2, "message": sanitize_payload({"err": str(exc)})["err"]}
            raise
        finally:
            end_ns = time.time_ns()
            span["endTimeUnixNano"] = str(end_ns)
            span["durationMs"] = round((end_ns - start_ns) / 1_000_000.0, 3)
            span["attributes"] = sanitize_payload(span["attributes"])
            self.spans.append(span)
            if self.span_log_path is not None:
                self.span_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.span_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(span, ensure_ascii=False) + "\n")

    def export_otlp_json(self) -> Dict[str, Any]:
        """Exportiert alle erfassten Spans im standardisierten OTLP/JSON `resourceSpans`-Format."""
        otlp_spans = []
        for s in self.spans:
            attrs = [
                {"key": str(k), "value": {"stringValue": str(v)}}
                for k, v in sorted(s.get("attributes", {}).items())
            ]
            otlp_spans.append(
                {
                    "traceId": s["traceId"],
                    "spanId": s["spanId"],
                    "parentSpanId": s["parentSpanId"],
                    "name": s["name"],
                    "kind": 1,
                    "startTimeUnixNano": s["startTimeUnixNano"],
                    "endTimeUnixNano": s["endTimeUnixNano"],
                    "attributes": attrs,
                    "status": s["status"],
                }
            )
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service_name}},
                            {"key": "otlp.endpoint.configured", "value": {"stringValue": str(bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")))}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "platform_core.otel_tracing", "version": "1.0.0"},
                            "spans": otlp_spans,
                        }
                    ],
                }
            ]
        }

    @staticmethod
    def read_recent_spans(span_log_path: Path | str, limit: int = 30) -> List[Dict[str, Any]]:
        path = Path(span_log_path)
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: List[Dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 200)) :]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
