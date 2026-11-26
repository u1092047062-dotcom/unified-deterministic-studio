import sqlite3

from core.jlcparts_importer import import_jlcparts_csv, import_jlcparts_sqlite
from core.part_library import PartLibrary


SAMPLE_JLC_CSV = """LCSC Part,First Category,Second Category,MFR.Part,Package,Manufacturer,Library Type,Description,Datasheet,Stock
C25804,Resistors,Chip Resistor,0603WAF1002T5E,0603,UNI-ROYAL,Basic,10kOhm ±1% 100mW 0603,https://example.com/r10k.pdf,250000
C14663,Capacitors,Multilayer Ceramic,CL10B104KB8NNNC,0603,Samsung,Basic,100nF 50V X7R 0603,https://example.com/c100n.pdf,180000
C99999,Resistors,Chip Resistor,EXOTIC-1R,0805,VendorX,Extended,1Ohm Extended Part,,500
C88888,Resistors,Chip Resistor,OUTOFSTOCK,0603,VendorY,Basic,0 stock part,,0
"""


def test_import_jlcparts_csv_filters_basic_and_stock():
    lib = PartLibrary()
    stats = import_jlcparts_csv(SAMPLE_JLC_CSV, lib, basic_only=True, min_stock=1)
    assert stats == {"imported": 2, "skipped": 2}

    r = lib.get("C25804")
    assert r.category == "resistor"
    assert r.footprint == "0603"
    assert r.body_w == 1.6 and r.body_h == 0.8
    assert len(r.pin_mapping) == 2

    hits = lib.search_parts("CL10B104", category="capacitor")
    assert len(hits) == 1
    assert hits[0].lcsc_id == "C14663"


def test_import_jlcparts_sqlite(tmp_path):
    db_file = tmp_path / "jlcparts_sample.sqlite3"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE components (lcsc INTEGER, mfr TEXT, package TEXT, manufacturer TEXT, "
        "category TEXT, description TEXT, datasheet TEXT, stock INTEGER, basic INTEGER)"
    )
    conn.execute(
        "INSERT INTO components VALUES (17414, '0805W8F1002T5E', '0805', 'UNI-ROYAL', "
        "'Resistors', '10k 0805 Basic', 'https://example.com/ds.pdf', 50000, 1)"
    )
    conn.execute(
        "INSERT INTO components VALUES (55555, 'EXT-ONLY', '0805', 'Other', "
        "'Resistors', 'Extended', '', 100, 0)"
    )
    conn.commit()
    conn.close()

    lib = PartLibrary()
    res = import_jlcparts_sqlite(db_file, lib, basic_only=True, min_stock=10)
    assert res == {"imported": 1, "skipped": 0}
    assert lib.get("C17414").mpn == "0805W8F1002T5E"
