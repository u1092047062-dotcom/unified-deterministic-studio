PROFILES = {
    "museum_fidelity": {
        "description": "Originaltreue und nachvollziehbare Restaurierung; keine generative Rekonstruktion ohne explizite Freigabe.",
        "generative_reconstruction": False,
        "face_restore": False,
        "quality_gate": "strict",
        "default_model_precision": "full",
    },
    "original_faithful": {
        "description": "Minimale Eingriffe; Originalstruktur und Farbtreue priorisieren.",
        "generative_reconstruction": False,
        "face_restore": False,
        "quality_gate": "strict",
        "default_model_precision": "full",
    },
    "controlled_reconstruction": {
        "description": "Optionale KI-Rekonstruktion nur nach expliziter Freigabe; synthetische Bereiche müssen markiert werden.",
        "generative_reconstruction": "opt_in_per_region",
        "face_restore": "manual_approval",
        "quality_gate": "strict_with_provenance",
        "default_model_precision": "full",
    },
    "premium_quality": {
        "description": "Mehrere Verfahren vergleichen; Qualität vor Laufzeit und Kosten.",
        "generative_reconstruction": "opt_in_per_region",
        "face_restore": "manual_approval",
        "quality_gate": "strict_with_comparison",
        "default_model_precision": "full",
    },
}
