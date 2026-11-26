"""Bauteilbibliothek & LCSC/JLCPCB-Kataloganbindung (Stufe 7).

Lokaler, strukturierter Katalog (In-Memory-SQLite, stdlib) mit gängigen
JLCPCB-Basic-Parts und deterministischer Auflösung:

- ``PartDefinition``: LCSC-ID, MPN, Hersteller, Gehäuse, Pin-Mapping mit
  physikalischen Pad-Maßen (relativ zur Gehäusemitte) und elektrischen
  Grenzwerten.
- ``resolve_part(category, value, footprint)``: Fuzzy-Matching über MPN-
  /LCSC-/Wert- und Footprint-Treffer mit definierten Fallback-Stufen
  (exakt -> Wert beliebiges Gehäuse -> Gehäuse beliebig -> Kategorie-
  Standard). Unbekannte Werte fallen auf den Kategorie-Standard zurück,
  unbekannte Kategorien raise ``PartNotFoundError``.

Hinweis zu den Seed-Daten: Die LCSC-IDs sind ein Snapshot üblicher
JLCPCB-Basic-Parts (Referenzdaten für Tests/Demo), nicht live verifiziert;
Pin-Maße sind IPC-nahe Näherungen.
"""
import json
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.schema import Component


class PinSpec(BaseModel):
    """Physikalisches Pad relativ zur Gehäusemitte (mm)."""
    name: str
    x: float
    y: float
    pad_w: float = 0.6
    pad_h: float = 0.6
    function: Optional[str] = None  # z. B. "VCC", "GND", "GPIO"

class PartDefinition(BaseModel):
    lcsc_id: str
    mpn: str
    manufacturer: str
    category: str
    footprint: str
    body_w: float
    body_h: float
    datasheet_url: Optional[str] = None
    description: str = ""
    # Elektrische Grenzwerte/Kennwerte, z. B. {"v_out": 3.3, "i_out_max_a": 1.0}
    electrical: Dict[str, float] = Field(default_factory=dict)
    pin_mapping: Dict[str, PinSpec] = Field(default_factory=dict)

class PartNotFoundError(LookupError):
    """Kategorie oder LCSC-ID ist im Katalog nicht vorhanden."""

# ---------------------------------------------------------------- Wert-Normalisierung

_SUFFIX_SCALE = {"r": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "m": 1e-3, "M": 1e6,
                 "p": 1e-12, "P": 1e-12, "n": 1e-9, "N": 1e-9,
                 "u": 1e-6, "U": 1e-6, "µ": 1e-6, "f": 1.0}

def normalize_value(value: str) -> Optional[float]:
    """Normalisiert Werteangaben: '10k'->10000, '4R7'->4.7, '1k5'->1500,
    '100nF'->1e-7, '0.1u'->1e-6, '10M'->1e7. None, wenn nichts parsebar."""
    if not value:
        return None
    text = value.strip()
    # Zehnerpotenzen-Zwischensuffix: 4R7, 1k5, 4n7
    m = re.search(r"(\d+(?:\.\d+)?)([RrKkMmNnPpUuµ])(\d+)", text)
    if m:
        base = float(m.group(1)) + float(m.group(3)) * 10 ** -len(m.group(3))
        scale = _SUFFIX_SCALE.get(m.group(2), 1.0)
        return float(f"{base * scale:.12g}")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([RrKkMmNnPpUuµFf]?)", text)
    if not m:
        return None
    number = float(m.group(1))
    suffix = m.group(2)
    if suffix in ("", None):
        return number
    scale = _SUFFIX_SCALE.get(suffix, None)
    if scale is None:  # F/f = Farad-Faktor 1 (z. B. "1F")
        scale = 1.0
    # 12 signifikante Stellen: 100 * 1e-9 -> exakt 1e-7 (Float-Rauschen raus)
    return float(f"{number * scale:.12g}")

# ---------------------------------------------------------------- Seed-Katalog

def _chip2(body_w: float, body_h: float, pad_x: float, pad_w: float, pad_h: float) -> Dict[str, PinSpec]:
    return {
        "1": PinSpec(name="1", x=-pad_x, y=0.0, pad_w=pad_w, pad_h=pad_h),
        "2": PinSpec(name="2", x=pad_x, y=0.0, pad_w=pad_w, pad_h=pad_h),
    }

def _qfn_grid(count_per_side: int, pitch: float, pad_offset: float,
              pad_w: float, pad_h: float, named: Dict[str, str]) -> Dict[str, PinSpec]:
    """Strukturelle QFN/LQFP-Belegung: gegen den Uhrzeigersinn ab oben links."""
    pins: Dict[str, PinSpec] = {}
    half = (count_per_side - 1) * pitch / 2.0
    n = 1
    for i in range(count_per_side):  # links, oben -> unten
        pins[str(n)] = PinSpec(name=str(n), x=-pad_offset, y=half - i * pitch,
                               pad_w=pad_w, pad_h=pad_h, function=named.get(str(n)))
        n += 1
    for i in range(count_per_side):  # unten, links -> rechts
        pins[str(n)] = PinSpec(name=str(n), x=-half + i * pitch, y=-pad_offset,
                               pad_w=pad_w, pad_h=pad_h, function=named.get(str(n)))
        n += 1
    for i in range(count_per_side):  # rechts, unten -> oben
        pins[str(n)] = PinSpec(name=str(n), x=pad_offset, y=-half + i * pitch,
                               pad_w=pad_w, pad_h=pad_h, function=named.get(str(n)))
        n += 1
    for i in range(count_per_side):  # oben, rechts -> links
        pins[str(n)] = PinSpec(name=str(n), x=half - i * pitch, y=pad_offset,
                               pad_w=pad_w, pad_h=pad_h, function=named.get(str(n)))
        n += 1
    return pins

def _esp32_wroom_32e() -> Dict[str, PinSpec]:
    """WROOM-32E: 15 Pads links (oben->unten), 15 rechts, 4 unten."""
    left_names = {1: "GND", 2: "3V3", 3: "EN", 15: "GND"}
    pins: Dict[str, PinSpec] = {}
    for i in range(15):
        y = 10.5 - i * 1.5
        pins[str(i + 1)] = PinSpec(name=str(i + 1), x=-8.2, y=y, pad_w=1.2, pad_h=1.0,
                                   function=left_names.get(i + 1))
    for i in range(15):
        y = -10.5 + i * 1.5
        pins[str(16 + i)] = PinSpec(name=str(16 + i), x=8.2, y=y, pad_w=1.2, pad_h=1.0)
    for i in range(4):
        x = 6.0 - i * 1.5
        pins[str(31 + i)] = PinSpec(name=str(31 + i), x=x, y=12.1, pad_w=1.0, pad_h=1.2,
                                    function={31: "GND", 38: "GND"}.get(31 + i))
    for i in range(4):
        x = -6.0 + i * 1.5
        pins[str(35 + i)] = PinSpec(name=str(35 + i), x=x, y=12.1, pad_w=1.0, pad_h=1.2)
    return pins

def _usb_c_16p() -> Dict[str, PinSpec]:
    names = {"A1": "GND", "A4": "VBUS", "A5": "CC1", "A6": "D+", "A7": "D-",
             "A8": "SBU1", "B5": "CC2", "B8": "SBU2", "B4": "VBUS", "B1": "GND"}
    pins: Dict[str, PinSpec] = {}
    for row, prefix in ((1.75, "A"), (-1.75, "B")):
        for i in range(8):
            name = f"{prefix}{i + 1}"
            pins[name] = PinSpec(name=name, x=-3.5 + i, y=row, pad_w=0.5, pad_h=1.0,
                                 function=names.get(name))
    return pins

def _seed_rows() -> List[Tuple]:
    """(lcsc_id, mpn, manufacturer, category, footprint, body_w, body_h,
        value, value_norm, datasheet_url, description, electrical, pins)"""
    r0805 = _chip2(2.0, 1.25, 0.95, 0.9, 1.2)
    r0603 = _chip2(1.6, 0.8, 0.75, 0.7, 0.9)
    rows: List[Tuple] = []

    for lcsc, mpn, ohm in (("C17557", "RC0805FR-0722RL", 22.0),
                           ("C17407", "RC0805FR-07100RL", 100.0),
                           ("C17513", "RC0805FR-071KL", 1000.0),
                           ("C17673", "RC0805FR-074K7L", 4700.0),
                           ("C17414", "RC0805FR-0710KL", 10000.0)):
        rows.append((lcsc, mpn, "YAGEO", "resistor", "0805", 2.0, 1.25,
                     _fmt_ohm(ohm), ohm, None, "Dünnschicht-Widerstand 1%",
                     {"resistance_ohm": ohm, "tolerance_pct": 1.0, "power_w": 0.125}, r0805))
    rows.append(("C25804", "RC0603FR-0710KL", "YAGEO", "resistor", "0603", 1.6, 0.8,
                 "10k", 10000.0, None, "Dünnschicht-Widerstand 1%",
                 {"resistance_ohm": 10000.0, "tolerance_pct": 1.0, "power_w": 0.1}, r0603))

    for lcsc, mpn, value, norm, cap in (("C15850", "CL21B104KBCNNNC", "100nF", 1e-7, 1e-7),
                                        ("C28323", "CL21B105KBFNNNE", "1uF", 1e-6, 1e-6),
                                        ("C19702", "CL21A106KOQNNNE", "10uF", 1e-5, 1e-5)):
        rows.append((lcsc, mpn, "Samsung", "capacitor", "0805", 2.0, 1.25,
                     value, norm, None, "MLCC X7R",
                     {"capacitance_f": cap, "voltage_v": 50.0}, r0805))
    rows.append(("C14663", "CL10B104KB8NNNC", "Samsung", "capacitor", "0603", 1.6, 0.8,
                 "100nF", 1e-7, None, "MLCC X7R",
                 {"capacitance_f": 1e-7, "voltage_v": 50.0}, r0603))

    rows.append(("C6186", "AMS1117-3.3", "AMS", "regulator", "SOT-223", 6.5, 3.5,
                 None, None, None, "LDO 3.3 V, 1 A",
                 {"v_out": 3.3, "v_in_max": 15.0, "i_out_max_a": 1.0}, {
                     "1": PinSpec(name="1", x=-2.3, y=-2.9, pad_w=1.5, pad_h=1.6, function="GND"),
                     "2": PinSpec(name="2", x=0.0, y=-2.9, pad_w=1.5, pad_h=1.6, function="VOUT"),
                     "3": PinSpec(name="3", x=2.3, y=-2.9, pad_w=1.5, pad_h=1.6, function="VIN"),
                     "4": PinSpec(name="4", x=0.0, y=2.9, pad_w=3.6, pad_h=2.0, function="TAB_GND"),
                 }))
    rows.append(("C51118", "AP2112K-3.3", "Diodes", "regulator", "SOT-25", 2.9, 1.6,
                 None, None, None, "LDO 3.3 V, 600 mA",
                 {"v_out": 3.3, "v_in_max": 5.5, "i_out_max_a": 0.6}, {
                     "1": PinSpec(name="1", x=-1.9, y=-1.35, pad_w=0.7, pad_h=1.1, function="VIN"),
                     "2": PinSpec(name="2", x=-0.95, y=-1.35, pad_w=0.7, pad_h=1.1, function="GND"),
                     "3": PinSpec(name="3", x=0.0, y=-1.35, pad_w=0.7, pad_h=1.1, function="EN"),
                     "4": PinSpec(name="4", x=0.95, y=-1.35, pad_w=0.7, pad_h=1.1, function="NC"),
                     "5": PinSpec(name="5", x=1.9, y=-1.35, pad_w=0.7, pad_h=1.1, function="VOUT"),
                 }))

    rp2040_named = {"4": "GND", "5": "GND", "6": "GND", "7": "GND", "47": "USB_DP",
                    "46": "USB_DM", "56": "SWCLK", "55": "SWD"}
    rows.append(("C2040", "RP2040", "Raspberry Pi", "mcu", "QFN-56", 7.0, 7.0,
                 None, None, None, "Dual-Core Cortex-M0+ SoC",
                 {"vdd": 3.3, "freq_mhz": 133.0, "sram_kb": 264.0},
                 _qfn_grid(14, 0.4, 3.1, 0.25, 0.7, rp2040_named)))
    stm_named = {"1": "VBAT", "44": "BOOT0", "23": "VSS", "24": "VDD"}
    rows.append(("C8734", "STM32F103C8T6", "ST", "mcu", "LQFP-48", 7.0, 7.0,
                 None, None, None, "Cortex-M3, 72 MHz",
                 {"vdd": 3.3, "freq_mhz": 72.0, "flash_kb": 64.0},
                 _qfn_grid(12, 0.5, 3.0, 0.3, 0.75, stm_named)))
    rows.append(("C701344", "ESP32-WROOM-32E", "Espressif", "mcu", "ESP32-WROOM-32E", 18.0, 25.5,
                 None, None, None, "WiFi/BT-Modul",
                 {"vdd": 3.3, "freq_mhz": 240.0, "flash_mb": 4.0}, _esp32_wroom_32e()))

    rows.append(("C165948", "TYPE-C-31-M-12", "Korean Hroparts", "connector", "USB-C-16P", 8.94, 7.35,
                 None, None, None, "USB-C Buchse 16-Pin, SMD",
                 {"v_max": 20.0, "i_max_a": 5.0}, _usb_c_16p()))
    rows.append(("C124378", "2.54mm-1x4P", "Ckmtw", "connector", "PIN-HEADER-2.54-1x4", 10.16, 2.54,
                 None, None, None, "Stiftleiste 2.54 mm, 1x4, gerade",
                 {"v_max": 250.0, "i_max_a": 3.0}, {
                     str(i + 1): PinSpec(name=str(i + 1), x=-3.81 + i * 2.54, y=0.0,
                                         pad_w=1.7, pad_h=1.7)
                     for i in range(4)
                 }))
    return rows

def _fmt_ohm(ohm: float) -> str:
    if ohm >= 1e6:
        return f"{ohm / 1e6:g}M"
    if ohm >= 1e3:
        return f"{ohm / 1e3:g}k"
    return f"{ohm:g}R"

# ---------------------------------------------------------------- Bibliothek

_CATEGORY_ALIASES = {
    "resistor": "resistor", "widerstand": "resistor", "r": "resistor",
    "capacitor": "capacitor", "kondensator": "capacitor", "c": "capacitor",
    "regulator": "regulator", "voltage_regulator": "regulator", "ldo": "regulator",
    "spannungsregler": "regulator",
    "mcu": "mcu", "soc": "mcu", "microcontroller": "mcu",
    "connector": "connector", "anschluss": "connector", "usb": "connector",
}

class PartLibrary:
    """In-Memory-SQLite-Katalog mit deterministischer Auflösung."""

    def __init__(self):
        # check_same_thread=False: Der Katalog ist nach dem Seed read-only
        # und wird aus FastAPI-Worker-Threads (Threadpool) gelesen.
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.execute(
            "CREATE TABLE parts (lcsc_id TEXT PRIMARY KEY, mpn TEXT NOT NULL, "
            "manufacturer TEXT NOT NULL, category TEXT NOT NULL, footprint TEXT NOT NULL, "
            "body_w REAL, body_h REAL, value TEXT, value_norm REAL, datasheet_url TEXT, "
            "description TEXT, electrical TEXT, pins TEXT)")
        self._db.execute(
            "CREATE INDEX idx_parts_category ON parts(category)")
        self._load_seed()

    def _load_seed(self):
        for row in _seed_rows():
            (lcsc, mpn, manufacturer, category, footprint, body_w, body_h,
             value, value_norm, datasheet, description, electrical, pins) = row
            self._db.execute(
                "INSERT INTO parts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lcsc, mpn, manufacturer, category, footprint, body_w, body_h,
                 value, value_norm, datasheet, description,
                 json.dumps(electrical), json.dumps({k: v.model_dump() for k, v in pins.items()})))
        self._db.commit()

    # ---------- Abfragen ----------

    def list_categories(self) -> List[str]:
        return sorted({r[0] for r in self._db.execute("SELECT DISTINCT category FROM parts")})

    def upsert_part(self, part: PartDefinition, value: Optional[str] = None) -> None:
        """Fuegt ein Bauteil (z. B. aus jlcparts-Import) ein oder aktualisiert es."""
        val_text = value or part.description or part.mpn
        val_norm = normalize_value(val_text)
        cat = _CATEGORY_ALIASES.get(part.category.strip().lower(), part.category.strip().lower())
        self._db.execute(
            "INSERT OR REPLACE INTO parts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                part.lcsc_id.strip().upper(),
                part.mpn,
                part.manufacturer,
                cat,
                part.footprint,
                part.body_w,
                part.body_h,
                val_text,
                val_norm,
                part.datasheet_url,
                part.description,
                json.dumps(part.electrical),
                json.dumps({k: v.model_dump() for k, v in part.pin_mapping.items()}),
            ),
        )
        self._db.commit()

    def search_parts(
        self,
        query: str = "",
        *,
        category: Optional[str] = None,
        limit: int = 25,
    ) -> List[PartDefinition]:
        """Durchsucht den lokalen SQLite-Katalog nach LCSC-ID, MPN, Footprint oder Beschreibung."""
        clauses: List[str] = []
        params: List[object] = []
        if category:
            cat = _CATEGORY_ALIASES.get(category.strip().lower(), category.strip().lower())
            clauses.append("category = ?")
            params.append(cat)
        if query.strip():
            like = f"%{query.strip().lower()}%"
            clauses.append(
                "(LOWER(lcsc_id) LIKE ? OR LOWER(mpn) LIKE ? OR LOWER(footprint) LIKE ? OR LOWER(description) LIKE ?)"
            )
            params.extend([like, like, like, like])
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM parts{where_sql} ORDER BY lcsc_id LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_part(r) for r in rows]

    def get(self, lcsc_id: str) -> PartDefinition:
        return self._row_to_part(self._fetch_by_lcsc(lcsc_id))

    def _fetch_by_lcsc(self, lcsc_id: str) -> Tuple:
        cur = self._db.execute("SELECT * FROM parts WHERE lcsc_id = ?", (lcsc_id.strip().upper(),))
        row = cur.fetchone()
        if row is None:
            raise PartNotFoundError(f"LCSC-ID {lcsc_id!r} nicht im Katalog")
        return row

    def resolve_part(self, category: str, value: Optional[str] = None,
                     footprint: Optional[str] = None) -> PartDefinition:
        """Löst eine Kategorie (+ optionale Wert-/Footprint-Hinweise) zu einem
        konkreten Katalogteil auf. Fallback-Stufen:

        1. beste Score-Summe aus MPN/LCSC-/Wert-/Footprint-Treffern,
        2. falls kein Treffer: Kategorie-Standard (niedrigste LCSC-ID),
        3. unbekannte Kategorie -> PartNotFoundError.
        """
        cat = _CATEGORY_ALIASES.get((category or "").strip().lower())
        if cat is None:
            raise PartNotFoundError(f"Unbekannte Kategorie {category!r}")

        rows = self._db.execute(
            "SELECT * FROM parts WHERE category = ? ORDER BY lcsc_id", (cat,)).fetchall()
        if not rows:
            raise PartNotFoundError(f"Kategorie {category!r} ist leer")

        value_norm = normalize_value(value) if value else None
        best_row, best_score = rows[0], 0
        for row in rows:
            score = self._score(row, value, footprint, value_norm)
            if score > best_score:
                best_row, best_score = row, score
        return self._row_to_part(best_row)

    @staticmethod
    def _score(row: Tuple, value: Optional[str], footprint: Optional[str],
               value_norm: Optional[float]) -> int:
        (lcsc_id, mpn, _man, _cat, fp, _bw, _bh, _val, row_norm, _ds, desc,
         _el, _pins) = row
        score = 0
        if value:
            v = value.strip().lower()
            if v == mpn.lower() or v == lcsc_id.lower():
                score += 100
            elif v in mpn.lower():
                score += 40
            elif v in (desc or "").lower():
                score += 20
            if value_norm is not None and row_norm is not None and value_norm == row_norm:
                score += 80
        if footprint:
            f = footprint.strip().lower()
            if f == fp.lower():
                score += 30
            elif f in fp.lower() or f in (desc or "").lower():
                score += 15
        return score

    @staticmethod
    def _row_to_part(row: Tuple) -> PartDefinition:
        (lcsc_id, mpn, manufacturer, category, footprint, body_w, body_h,
         _value, _norm, datasheet, description, electrical, pins) = row
        return PartDefinition(
            lcsc_id=lcsc_id, mpn=mpn, manufacturer=manufacturer, category=category,
            footprint=footprint, body_w=body_w, body_h=body_h, datasheet_url=datasheet,
            description=description, electrical=json.loads(electrical),
            pin_mapping={k: PinSpec.model_validate(v) for k, v in json.loads(pins).items()},
        )

DEFAULT_LIBRARY = PartLibrary()

def apply_part_to_component(part: PartDefinition, comp: Component) -> None:
    """Überträgt Katalogdaten auf eine Komponente: LCSC-ID/MPN/Datasheet,
    Gehäuseabmessungen (falls nicht gesetzt) und physikalische Pad-Positionen
    für logische Pins, die noch keine Koordinaten haben."""
    comp.lcsc_id = part.lcsc_id
    comp.mpn = part.mpn
    comp.datasheet_url = part.datasheet_url
    if comp.body_w is None:
        comp.body_w = part.body_w
    if comp.body_h is None:
        comp.body_h = part.body_h
    for pad_name, spec in part.pin_mapping.items():
        pin = comp.pins.get(pad_name)
        if pin is None:
            continue
        if pin.x is None:
            pin.x = comp.x + spec.x
        if pin.y is None:
            pin.y = comp.y + spec.y
