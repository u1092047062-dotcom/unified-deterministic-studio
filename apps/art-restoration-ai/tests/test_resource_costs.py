import pytest
from resource_manager.policy import choose_resources
from resource_manager.costs import estimate_gpu_cost, enforce_budget


def test_quality_profile_preserved_when_vram_low():
    d = choose_resources(4, "museum_fidelity")
    assert d.tile_size == 512
    assert d.quality_profile == "museum_fidelity"
    assert d.allow_quality_downgrade is False


def test_cost_estimate():
    assert estimate_gpu_cost(1800, 2.0) == 1.0


def test_negative_cost_rejected():
    with pytest.raises(ValueError):
        estimate_gpu_cost(-1, 2)


def test_budget_stops_instead_of_lowering_quality():
    decision = enforce_budget(4.0, 3.0)
    assert decision["allowed"] is False
    assert "keine automatische Qualitätsreduktion" in decision["reason"]
