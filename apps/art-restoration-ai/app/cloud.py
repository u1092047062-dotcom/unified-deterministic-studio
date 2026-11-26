from __future__ import annotations
from app.config import RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID


def cloud_status() -> dict:
    configured = bool(RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID)
    return {
        "provider": "runpod",
        "configured": configured,
        "dispatch_enabled": False,
        "reason": "Cloud-Dispatch bleibt gesperrt, bis ein geprüfter Worker, API-Zugang, Speicherpfad und Kostenfreigabe konfiguriert und getestet sind." if not configured else "Credentials vorhanden; Worker-/Endpoint-Vertrag muss vor Aktivierung validiert werden.",
        "secrets_present": bool(RUNPOD_API_KEY),
        "endpoint_present": bool(RUNPOD_ENDPOINT_ID),
    }
