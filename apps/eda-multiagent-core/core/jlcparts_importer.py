"""Open-Source jlcparts / JLCPCB-Parts-Database Importer (CSV & SQLite).

Kompatibel mit:
- yaqwsx/jlcparts (SQLite `components`-Tabelle)
- CDFER/jlcpcb-parts-database (CSV-Export mit LCSC Part, MFR.Part, Package, Library Type, Stock)

Laedt reale JLCPCB-Bauteile lokal und offline (ohne API-Key) in core.part_library.PartLibrary.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
import sqlite3
from typing import Dict, Optional, Tuple

from core.part_library import PartDefinition, PartLibrary, PinSpec

# Standard-SMD-Gehaeuseabmessungen (mm) als deterministische Naeherung
PACKAGE_DIMENSIONS_MM: Dict[str, Tuple[float, float]] = {
    "0402": (1.0, 0.5),
    "0603": (1.6, 0.8),
    "0805": (2.0, 1.25),
    "1206": (3.2, 1.6),
    "SOT-23": (2.9, 1.3),
    "SOT-223": (6.5, 3.5),
    "SOIC-8": (4.9, 3.9),
    "QFN-32": (5.0, 5.0),
    "LQFP-48": (7.0, 7.0),
    "USB-C": (9.0, 7.3),
}


def _infer_category(first_cat: str, second_cat: str, description: str) -> str:
    combined = f"{first_cat} {second_cat} {description}".lower()
    if "resistor" in combined or "widerstand" in combined:
        return "resistor"
    if "capacitor" in combined or "kondensator" in combined:
        return "capacitor"
    if "microcontroller" in combined or "mcu" in combined or "esp32" in combined or "stm32" in combined:
        return "mcu"
    if "regulator" in combined or "ldo" in combined or "buck" in combined:
        return "regulator"
    if "usb" in combined or "connector" in combined or "header" in combined:
        return "connector"
    if "led" in combined or "diode" in combined:
        return "led"
    if "inductor" in combined or "ferrite" in combined:
        return "inductor"
    return "ic"


def _infer_dimensions(package: str) -> Tuple[float, float]:
    pkg_upper = (package or "").strip().upper()
    for key, dims in PACKAGE_DIMENSIONS_MM.items():
        if key in pkg_upper:
            return dims
    return (3.0, 2.0)


def _default_pins_for_category(category: str, body_w: float) -> Dict[str, PinSpec]:
    if category in ("resistor", "capacitor", "led", "inductor"):
        half = round(body_w * 0.45, 2)
        return {
            "1": PinSpec(name="1", x=-half, y=0.0, pad_w=0.7, pad_h=0.9),
            "2": PinSpec(name="2", x=half, y=0.0, pad_w=0.7, pad_h=0.9),
        }
    return {}


def import_jlcparts_csv(
    csv_text: str,
    library: PartLibrary,
    *,
    basic_only: bool = False,
    min_stock: int = 1,
) -> Dict[str, int]:
    """Importiert einen JLCPCB/jlcparts-CSV-Auszug deterministisch in PartLibrary."""
    reader = csv.DictReader(io.StringIO(csv_text))
    imported = 0
    skipped = 0

    for row in reader:
        lcsc = (row.get("LCSC Part") or row.get("lcsc") or "").strip().upper()
        mpn = (row.get("MFR.Part") or row.get("mfr") or row.get("MPN") or "").strip()
        if not lcsc or not mpn:
            skipped += 1
            continue

        lib_type = (row.get("Library Type") or row.get("basic") or "").strip().lower()
        is_basic = lib_type in ("basic", "1", "true", "yes", "preferred")
        if basic_only and not is_basic:
            skipped += 1
            continue

        try:
            stock = int(str(row.get("Stock") or row.get("stock") or "0").replace(",", "").strip() or "0")
        except ValueError:
            stock = 0
        if stock < min_stock:
            skipped += 1
            continue

        package = (row.get("Package") or row.get("package") or "SMD").strip()
        manufacturer = (row.get("Manufacturer") or row.get("manufacturer") or "Unknown").strip()
        first_cat = (row.get("First Category") or row.get("category") or "").strip()
        second_cat = (row.get("Second Category") or row.get("subcategory") or "").strip()
        desc = (row.get("Description") or row.get("description") or mpn).strip()
        datasheet = (row.get("Datasheet") or row.get("datasheet") or "").strip() or None

        category = _infer_category(first_cat, second_cat, desc)
        bw, bh = _infer_dimensions(package)
        part = PartDefinition(
            lcsc_id=lcsc,
            mpn=mpn,
            manufacturer=manufacturer,
            category=category,
            footprint=package,
            body_w=bw,
            body_h=bh,
            datasheet_url=datasheet,
            description=desc,
            electrical={"stock": float(stock), "is_basic": 1.0 if is_basic else 0.0},
            pin_mapping=_default_pins_for_category(category, bw),
        )
        library.upsert_part(part, value=desc)
        imported += 1

    return {"imported": imported, "skipped": skipped}


def import_jlcparts_sqlite(
    db_path: Path | str,
    library: PartLibrary,
    *,
    basic_only: bool = True,
    min_stock: int = 10,
    limit: int = 5000,
) -> Dict[str, int]:
    """Importiert Bauteile direkt aus einer lokalen yaqwsx/jlcparts SQLite-Datenbank."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"jlcparts SQLite-Datenbank nicht gefunden: {path}")

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    imported = 0
    skipped = 0
    try:
        where_parts = ["stock >= ?"]
        params: list[object] = [int(min_stock)]
        if basic_only:
            where_parts.append("basic = 1")
        sql = (
            "SELECT lcsc, mfr, package, manufacturer, category, description, datasheet, stock, basic "
            f"FROM components WHERE {' AND '.join(where_parts)} LIMIT ?"
        )
        params.append(int(limit))
        for row in conn.execute(sql, tuple(params)):
            lcsc_raw = str(row["lcsc"]).strip()
            lcsc = lcsc_raw.upper() if lcsc_raw.upper().startswith("C") else f"C{lcsc_raw}"
            mpn = str(row["mfr"] or "").strip()
            if not mpn:
                skipped += 1
                continue
            pkg = str(row["package"] or "SMD").strip()
            desc = str(row["description"] or mpn).strip()
            cat = _infer_category(str(row["category"] or ""), "", desc)
            bw, bh = _infer_dimensions(pkg)
            part = PartDefinition(
                lcsc_id=lcsc,
                mpn=mpn,
                manufacturer=str(row["manufacturer"] or "Unknown"),
                category=cat,
                footprint=pkg,
                body_w=bw,
                body_h=bh,
                datasheet_url=str(row["datasheet"]) if row["datasheet"] else None,
                description=desc,
                electrical={
                    "stock": float(row["stock"] or 0),
                    "is_basic": 1.0 if row["basic"] else 0.0,
                },
                pin_mapping=_default_pins_for_category(cat, bw),
            )
            library.upsert_part(part, value=desc)
            imported += 1
    finally:
        conn.close()

    return {"imported": imported, "skipped": skipped}
