import math

import pytest

from core.stackup_manager import (DEFAULT_STACKUP, PREPREG_STYLES, Stackup,
                                  StackupLayer, StackupManager)


@pytest.fixture
def manager() -> StackupManager:
    return StackupManager()


# ---------- Stackup-Definition ----------

def test_default_stackup_is_four_layer(manager):
    names = manager.copper_layer_names()
    assert names == ["TOP", "INNER1", "INNER2", "BOTTOM"]
    assert manager.layer_role("INNER1") == "GND-Plane"
    assert manager.layer_role("INNER2") == "Power-Plane"
    assert manager.layer_role("TOP") == "signal"


def test_total_thickness_is_about_1_6mm(manager):
    total = manager.total_thickness()
    # 4x 35µm Kupfer + 2x 0,2104 Prepreg + 1,065 Kern
    assert total == pytest.approx(4 * 0.035 + 2 * 0.2104 + 1.065)
    assert 1.55 <= total <= 1.70


def test_prepreg_styles_reference():
    assert PREPREG_STYLES["7628"] == 0.2104
    assert PREPREG_STYLES["2313"] == 0.1016
    assert PREPREG_STYLES["1080"] < PREPREG_STYLES["2116"] < PREPREG_STYLES["7628"]


def test_dielectric_height_for_outer_layers(manager):
    # Microstrip-Höhe = Prepreg unter der jeweiligen Außenlage
    assert manager.dielectric_height("TOP") == pytest.approx(0.2104)
    assert manager.dielectric_height("BOTTOM") == pytest.approx(0.2104)


def test_dielectric_height_rejects_inner_layers(manager):
    with pytest.raises(ValueError):
        manager.dielectric_height("INNER1")
    with pytest.raises(ValueError):
        manager.dielectric_height("INNER2")


def test_custom_stackup_is_usable():
    stackup = Stackup(name="2L", layers=[
        StackupLayer(name="TOP", kind="copper", thickness=0.035, role="signal"),
        StackupLayer(name="Core FR4", kind="dielectric", thickness=1.6, er=4.5),
        StackupLayer(name="BOTTOM", kind="copper", thickness=0.035, role="signal"),
    ])
    mgr = StackupManager(stackup)
    assert mgr.dielectric_height("TOP") == 1.6
    assert mgr.total_thickness() == pytest.approx(1.67)


# ---------- Single-Ended-Impedanz (IPC-2141) ----------

def test_single_ended_reference_50_ohm_on_1_6mm_fr4():
    # Klassiker: ~2,9 mm Leiterbahn auf 1,6 mm FR4 (Er 4,5) ergibt ~50 Ohm
    from core.spatial_drc import HighSpeedDRC
    z = HighSpeedDRC.calculate_microstrip_impedance(2.9, dielectric_height=1.6, er=4.5)
    assert abs(z - 50.0) < 2.0


def test_single_ended_on_jlc_stackup_matches_hand_calculation(manager):
    # Handrechnung: 87/sqrt(4.2+1.41)=36.728; ln(5.98*0.2104/(0.8*0.35+0.035))=1.3848
    z = manager.single_ended_impedance(0.35, "TOP")
    expected = (87.0 / math.sqrt(4.2 + 1.41)) * math.log(5.98 * 0.2104 / (0.8 * 0.35 + 0.035))
    assert z == pytest.approx(expected, rel=1e-9)
    assert abs(z - 50.9) < 0.5  # nahe 50 Ohm (Klassiker der 4-Lagen-Fertigung)


# ---------- Differential-Impedanz ----------

def test_differential_approaches_two_times_single_ended(manager):
    z0 = manager.single_ended_impedance(0.35, "TOP")
    z_diff_far = manager.differential_impedance(0.35, spacing=100.0, layer_name="TOP")
    assert z_diff_far == pytest.approx(2 * z0, rel=1e-6)


def test_differential_coupling_lowers_impedance(manager):
    wide = manager.differential_impedance(0.35, spacing=2.0, layer_name="TOP")
    tight = manager.differential_impedance(0.35, spacing=0.15, layer_name="TOP")
    assert tight < wide  # engere Kopplung -> niederohmiger
    # IPC-2141-Kopplungsfaktor bei sehr engem Abstand: Faktor ~ 1 - 0.48
    factor_floor = 2 * manager.single_ended_impedance(0.35, "TOP") * (1 - 0.48)
    assert tight >= factor_floor - 1e-6


def test_solve_single_ended_width_hits_target(manager):
    width = manager.solve_single_ended_width(50.0, "TOP")
    assert manager.single_ended_impedance(width, "TOP") == pytest.approx(50.0, abs=0.2)
    assert width > 0


def test_solve_differential_pair_90_ohm_usb(manager):
    width, spacing = manager.solve_differential_pair(90.0, "TOP")
    z = manager.differential_impedance(width, spacing, "TOP")
    assert z == pytest.approx(90.0, abs=0.5)
    assert width > 0 and spacing > 0


def test_solve_differential_pair_100_ohm_ethernet(manager):
    width, spacing = manager.solve_differential_pair(100.0, "TOP")
    z = manager.differential_impedance(width, spacing, "TOP")
    assert z == pytest.approx(100.0, abs=0.5)


def test_100_ohm_pair_is_wider_or_wider_spaced_than_90_ohm(manager):
    w90, s90 = manager.solve_differential_pair(90.0, "TOP")
    w100, s100 = manager.solve_differential_pair(100.0, "TOP")
    # Höhere Impedanz -> schmaler und/oder weiter auseinander
    assert (w100, s100) != (w90, s90)
    assert manager.differential_impedance(w100, s100) > manager.differential_impedance(w90, s90)
