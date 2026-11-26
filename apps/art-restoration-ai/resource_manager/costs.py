def estimate_gpu_cost(runtime_seconds: float, hourly_rate: float) -> float:
    if runtime_seconds < 0 or hourly_rate < 0:
        raise ValueError("Laufzeit und Stundensatz dürfen nicht negativ sein")
    return round(runtime_seconds / 3600.0 * hourly_rate, 6)


def enforce_budget(estimated_cost: float, budget: float | None) -> dict:
    if estimated_cost < 0 or (budget is not None and budget < 0):
        raise ValueError("Kosten und Budget dürfen nicht negativ sein")
    if budget is None:
        return {"allowed": True, "reason": "Kein Budgetlimit konfiguriert; vor Cloud-Ausführung Betreiberfreigabe erforderlich"}
    if estimated_cost > budget:
        return {"allowed": False, "reason": "Budgetlimit überschritten; keine automatische Qualitätsreduktion"}
    return {"allowed": True, "reason": "Innerhalb des konfigurierten Budgets"}
