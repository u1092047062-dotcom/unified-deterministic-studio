"""Quality-First Ressourcen- & Budget-Guard fuer EDA und Media Restoration.

Leitprinzip (aus ART-Restoration-AI + Hardware-Schutz fuer 8-GB-Hosts):
- Ressourcen werden ueber Kachelgroesse, Parallelitaet und Build-Jobs gesteuert.
- Niemals stilles Herabsetzen von Modellqualitaet, Praezision oder DRC-Regeln.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ResourcePlan:
    tile_size: int
    concurrency: int
    cargo_build_jobs: int
    quality_profile: str
    reason: str
    model_precision: str = "full"
    allow_quality_downgrade: bool = False


def plan_resources(
    *,
    ram_gb: float = 8.0,
    vram_gb: float = 4.0,
    quality_profile: str = "deterministic_strict",
) -> ResourcePlan:
    """Berechnet einen ressourcenschonenden Ausfuehrungsplan ohne Qualitaetsverlust."""
    if ram_gb <= 0:
        raise ValueError("RAM muss positiv sein")
    if vram_gb < 0:
        raise ValueError("VRAM darf nicht negativ sein")

    if ram_gb <= 8.5 or vram_gb < 8.0:
        return ResourcePlan(
            tile_size=512,
            concurrency=1,
            cargo_build_jobs=2,
            quality_profile=quality_profile,
            reason=(
                "Speicherschonender Modus (<= 8 GB RAM / < 8 GB VRAM): "
                "kleinere Kacheln, serielle Ausfuehrung, Cargo --jobs 2; "
                "keine Qualitaets- oder DRC-Reduktion."
            ),
        )
    if ram_gb < 16.0 or vram_gb < 16.0:
        return ResourcePlan(
            tile_size=768,
            concurrency=1,
            cargo_build_jobs=4,
            quality_profile=quality_profile,
            reason="Moderater Ressourcenmodus; volle Modell- und DRC-Qualitaet bleibt erhalten.",
        )
    return ResourcePlan(
        tile_size=1024,
        concurrency=2,
        cargo_build_jobs=4,
        quality_profile=quality_profile,
        reason="Hoher RAM/VRAM-Ausbau erlaubt kontrollierte Parallelitaet bei voller Qualitaet.",
    )


def estimate_compute_cost(runtime_seconds: float, hourly_rate: float) -> float:
    if runtime_seconds < 0 or hourly_rate < 0:
        raise ValueError("Laufzeit und Stundensatz duerfen nicht negativ sein")
    return round(runtime_seconds / 3600.0 * hourly_rate, 6)


def enforce_budget_gate(estimated_cost: float, budget: Optional[float]) -> Dict[str, object]:
    if estimated_cost < 0 or (budget is not None and budget < 0):
        raise ValueError("Kosten und Budget duerfen nicht negativ sein")
    if budget is None:
        return {
            "allowed": True,
            "reason": "Kein Budgetlimit konfiguriert; vor Cloud-Ausfuehrung Betreiberfreigabe erforderlich.",
        }
    if estimated_cost > budget:
        return {
            "allowed": False,
            "reason": "Budgetlimit ueberschritten; Abbruch ohne stilles Qualitaets-Downgrade.",
        }
    return {
        "allowed": True,
        "reason": "Innerhalb des konfigurierten Budgets.",
    }
