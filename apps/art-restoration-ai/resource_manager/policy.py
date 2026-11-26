from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceDecision:
    tile_size: int
    concurrency: int
    quality_profile: str
    reason: str
    model_precision: str = "full"
    allow_quality_downgrade: bool = False


def choose_resources(vram_gb: float, quality_profile: str = "museum_fidelity") -> ResourceDecision:
    """Adjust memory footprint only; never silently replace or weaken the chosen model."""
    if vram_gb <= 0:
        raise ValueError("VRAM muss positiv sein")
    if vram_gb < 8:
        return ResourceDecision(512, 1, quality_profile, "Kleinere Kacheln und serielle Ausführung; Modellqualität bleibt unverändert")
    if vram_gb < 16:
        return ResourceDecision(768, 1, quality_profile, "Moderate Kachelgröße; Modellqualität bleibt unverändert")
    return ResourceDecision(1024, 2, quality_profile, "Mehr VRAM erlaubt kontrollierte Parallelität; Modellqualität bleibt unverändert")
