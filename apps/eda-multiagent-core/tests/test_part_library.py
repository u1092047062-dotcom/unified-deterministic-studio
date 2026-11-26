import math

import pytest

from core.part_library import (DEFAULT_LIBRARY, PartDefinition, PartLibrary,
                               PartNotFoundError, PinSpec, apply_part_to_component,
                               normalize_value)
from core.schema import BoardState, Component, Net, Pin, PinType
from core.pipeline_v2 import DesignPipeline, STATUS_OK


# ---------- Wert-Normalisierung ----------

@pytest.mark.parametrize("text,expected", [
    ("10k", 10000.0), ("10K", 10000.0), ("10000", 10000.0), ("R_10k", 10000.0),
    ("4R7", 4.7), ("1k5", 1500.0), ("22R", 22.0), ("10M", 1e7), ("4m7", 4.7e-3),
    ("100nF", 1e-7), ("0.1u", 1e-7), ("1uF", 1e-6), ("10pF", 1e-11),
    ("ESP32", 32.0), ("", None), (None, None),
])
def test_normalize_value(text, expected):
    assert normalize_value(text) == expected


# ---------- Exakte Treffer ----------

def test_exact_match_by_mpn():
    part = DEFAULT_LIBRARY.resolve_part("resistor", "RC0805FR-0710KL", "0805")
    assert part.lcsc_id == "C17414"
    assert part.mpn == "RC0805FR-0710KL"
    assert part.manufacturer == "YAGEO"
    assert part.electrical["resistance_ohm"] == 10000.0


def test_exact_match_by_lcsc_id():
    part = DEFAULT_LIBRARY.resolve_part("mcu", "C2040")
    assert part.mpn == "RP2040"
    assert part.footprint == "QFN-56"
    assert part.electrical["vdd"] == 3.3


def test_exact_match_by_normalized_value_and_footprint():
    part = DEFAULT_LIBRARY.resolve_part("resistor", "10k", "0805")
    assert part.lcsc_id == "C17414"
    part2 = DEFAULT_LIBRARY.resolve_part("resistor", "10K", "0805")
    assert part2.lcsc_id == "C17414"
    part3 = DEFAULT_LIBRARY.resolve_part("capacitor", "100nF", "0805")
    assert part3.lcsc_id == "C15850"
    part4 = DEFAULT_LIBRARY.resolve_part("capacitor", "0.1uF", "0603")
    assert part4.lcsc_id == "C14663"


def test_regulators_available():
    ams = DEFAULT_LIBRARY.resolve_part("regulator", "AMS1117-3.3")
    assert ams.lcsc_id == "C6186" and ams.footprint == "SOT-223"
    ap = DEFAULT_LIBRARY.resolve_part("regulator", "AP2112K-3.3")
    assert ap.lcsc_id == "C51118" and ap.electrical["i_out_max_a"] == 0.6
    # elektrische Rollen im Pin-Mapping
    functions = {spec.function for spec in ap.pin_mapping.values()}
    assert {"VIN", "GND", "VOUT"} <= functions


def test_mcus_and_connectors_available():
    esp = DEFAULT_LIBRARY.resolve_part("mcu", "ESP32")
    assert esp.mpn == "ESP32-WROOM-32E" and len(esp.pin_mapping) == 38
    stm = DEFAULT_LIBRARY.resolve_part("mcu", "STM32F103C8T6")
    assert stm.lcsc_id == "C8734" and len(stm.pin_mapping) == 48
    usb = DEFAULT_LIBRARY.resolve_part("connector", "USB-C")
    assert usb.mpn == "TYPE-C-31-M-12"
    functions = {spec.function for spec in usb.pin_mapping.values()}
    assert {"CC1", "CC2", "D+", "D-", "VBUS", "GND"} <= functions
    header = DEFAULT_LIBRARY.resolve_part("connector", "2.54mm")
    assert header.footprint == "PIN-HEADER-2.54-1x4" and len(header.pin_mapping) == 4


# ---------- Fallbacks ----------

def test_fallback_to_standard_value_for_unknown_value():
    part = DEFAULT_LIBRARY.resolve_part("resistor", "47k", "0805")
    # 47k ist nicht im Katalog -> Standard-0805-Widerstand
    assert part.footprint == "0805"
    assert part.category == "resistor"
    assert part.lcsc_id in {"C17557", "C17407", "C17513", "C17673", "C17414"}


def test_fallback_category_default_without_hints():
    part = DEFAULT_LIBRARY.resolve_part("capacitor")
    assert part.category == "capacitor"
    # deterministisch: erneuter Aufruf liefert dasselbe Teil
    assert DEFAULT_LIBRARY.resolve_part("capacitor").lcsc_id == part.lcsc_id


def test_footprint_hint_breaks_value_tie():
    # "10k" existiert in 0603 und 0805 -> Footprint entscheidet
    assert DEFAULT_LIBRARY.resolve_part("resistor", "10k", "0603").lcsc_id == "C25804"
    assert DEFAULT_LIBRARY.resolve_part("resistor", "10k", "0805").lcsc_id == "C17414"


def test_unknown_category_raises():
    with pytest.raises(PartNotFoundError):
        DEFAULT_LIBRARY.resolve_part("blinking_led")
    with pytest.raises(PartNotFoundError):
        DEFAULT_LIBRARY.resolve_part("")


def test_unknown_lcsc_id_raises():
    with pytest.raises(PartNotFoundError):
        DEFAULT_LIBRARY.get("C999999")


def test_category_aliases():
    assert DEFAULT_LIBRARY.resolve_part("widerstand", "1k", "0805").lcsc_id == "C17513"
    assert DEFAULT_LIBRARY.resolve_part("voltage_regulator", "AMS1117-3.3").lcsc_id == "C6186"
    assert DEFAULT_LIBRARY.resolve_part("soc", "C2040").mpn == "RP2040"


# ---------- Pin-Konsistenz über den ganzen Katalog ----------

def test_pin_mapping_consistency_all_parts():
    import sqlite3
    for lcsc_id in DEFAULT_LIBRARY._db.execute("SELECT lcsc_id FROM parts"):
        part = DEFAULT_LIBRARY.get(lcsc_id[0])
        assert part.pin_mapping, f"{lcsc_id[0]} ohne Pin-Mapping"
        for name, spec in part.pin_mapping.items():
            assert spec.name == name
            assert spec.pad_w > 0 and spec.pad_h > 0
            assert math.isfinite(spec.x) and math.isfinite(spec.y)
            # Pads dürfen über die Body-Kante hinausragen (SOT/QFN-Escape),
            # aber nicht absurderweitig weit:
            assert abs(spec.x) <= part.body_w / 2 + 2.0
            assert abs(spec.y) <= part.body_h / 2 + 2.0


def test_chip_parts_have_symmetric_two_pad_mapping():
    for category in ("resistor", "capacitor"):
        rows = DEFAULT_LIBRARY._db.execute(
            "SELECT lcsc_id FROM parts WHERE category = ?", (category,)).fetchall()
        for (lcsc_id,) in rows:
            part = DEFAULT_LIBRARY.get(lcsc_id)
            assert set(part.pin_mapping) == {"1", "2"}
            p1, p2 = part.pin_mapping["1"], part.pin_mapping["2"]
            assert p1.x == -p2.x and p1.y == p2.y == 0.0
            assert (p1.pad_w, p1.pad_h) == (p2.pad_w, p2.pad_h)


# ---------- Übertrag auf Komponenten ----------

def test_apply_part_materializes_missing_pin_coordinates():
    part = DEFAULT_LIBRARY.resolve_part("regulator", "AMS1117-3.3")
    comp = Component(id="U1", name="U1", category="regulator", footprint="SOT-223",
                     x=30.0, y=40.0,
                     pins={name: Pin(id=name, name=name, pin_type=PinType.PASSIVE)
                           for name in ("1", "2", "3")})
    apply_part_to_component(part, comp)
    assert comp.lcsc_id == "C6186" and comp.mpn == "AMS1117-3.3"
    assert comp.body_w == 6.5 and comp.body_h == 3.5
    # Pin 1: absolute Position = Komponentenmitte + relatives Pad
    assert comp.pins["1"].x == pytest.approx(30.0 - 2.3)
    assert comp.pins["1"].y == pytest.approx(40.0 - 2.9)
    # Existierende Koordinaten werden NICHT überschrieben
    comp.pins["2"].x = 99.0
    apply_part_to_component(part, comp)
    assert comp.pins["2"].x == 99.0


def test_apply_part_keeps_existing_body_dimensions():
    part = DEFAULT_LIBRARY.resolve_part("resistor", "10k", "0805")
    comp = Component(id="R1", name="R1", category="resistor", footprint="0805",
                     body_w=9.9, body_h=9.9)
    apply_part_to_component(part, comp)
    assert comp.body_w == 9.9 and comp.body_h == 9.9


# ---------- Pipeline-Integration ----------

def test_pipeline_assigns_lcsc_ids_before_routing():
    board = BoardState(width=40.0, height=20.0)
    board.components["R1"] = Component(id="R1", name="R_10k", category="resistor",
                                       footprint="0805",
                                       pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE,
                                                      x=4.0, y=10.0),
                                             "2": Pin(id="2", name="B", pin_type=PinType.PASSIVE,
                                                      x=36.0, y=10.0)})
    board.components["R2"] = Component(id="R2", name="R_unknown", category="resistor",
                                       footprint="0603",
                                       pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE,
                                                      x=4.0, y=14.0),
                                             "2": Pin(id="2", name="B", pin_type=PinType.PASSIVE,
                                                      x=36.0, y=14.0)})
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("R1", "1"), ("R2", "1")])

    result = DesignPipeline().run(board)

    assert result.status == STATUS_OK
    r1 = result.board.components["R1"]
    assert r1.lcsc_id == "C17414" and r1.mpn == "RC0805FR-0710KL"
    r2 = result.board.components["R2"]
    # 0603 + unbekannter Name -> Kategorie-/Footprint-Fallback
    assert r2.lcsc_id == "C25804"
    assert any("Teile-Auflösung" in line for line in result.report)


def test_pipeline_part_resolution_can_be_disabled():
    board = BoardState(width=20.0, height=20.0)
    board.components["R1"] = Component(id="R1", name="R", category="resistor", footprint="0805")
    result = DesignPipeline(enable_part_resolution=False).run(board)
    assert result.board.components["R1"].lcsc_id is None
    assert not any("Teile-Auflösung" in line for line in result.report)


def test_pipeline_survives_unresolvable_component():
    board = BoardState(width=20.0, height=20.0)
    board.components["X1"] = Component(id="X1", name="X1", category="flux_capacitor",
                                       footprint="DELOREAN")
    result = DesignPipeline().run(board)
    assert result.board.components["X1"].lcsc_id is None
    assert any("nicht auflösbar" in line for line in result.report)


# ---------- Unabhängige Bibliotheksinstanz ----------

def test_separate_library_instance_is_isolated():
    lib = PartLibrary()
    part = lib.resolve_part("mcu", "C2040")
    assert isinstance(part, PartDefinition)
    assert part.lcsc_id == "C2040"
