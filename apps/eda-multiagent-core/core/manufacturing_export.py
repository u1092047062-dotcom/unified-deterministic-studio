"""Fertigungsdaten-Export für JLCPCB (Stufe 8): BOM und CPL (Pick & Place).

JLCPCB-Formate:
- BOM (Stückliste): Comment, Designator, Footprint, LCSC Part #
- CPL (Bestückungsdaten): Designator, Val, Package, Mid X, Mid Y,
  Rotation, Layer

Konventionen:
- Nur Komponenten MIT lcsc_id landen in BOM/CPL: ohne Katalogreferenz
  kann JLCPCB das Teil nicht beschaffen und nicht bestücken. Teile ohne
  Referenz sind damit bewusst aus den Fertigungsdaten ausgeschlossen.
- Mid X/Mid Y sind boardzentriert in mm (JLCPCB-Präferenz): Ursprung in
  der Platinenmitte, Y zeigt nach oben wie im Schema.
- Rotation wird unverändert aus dem Schema übernommen (Grad).
- Layer: das Schema kennt bisher keine Bauteil-Layer; alle Komponenten
  werden als Top-Bestückung exportiert ("top").
- Zeilen sind deterministisch (natürliche Sortierung der Designatoren:
  R2 vor R10), Felder mit Komma/Anführungszeichen werden korrekt
  gequotet (csv-Modul).
"""
import csv
import io
import re
from typing import List, Tuple

from core.schema import BoardState, Component

BOM_HEADER = ["Comment", "Designator", "Footprint", "LCSC Part #"]
CPL_HEADER = ["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"]

def _designator_key(designator: str) -> Tuple:
    """Natürliche Sortierung: Präfix alphabetisch, Nummer numerisch
    (R2 < R10), Rest als deterministisches Fallback."""
    m = re.fullmatch(r"([A-Za-z]*)(\d+)", designator.strip())
    if m:
        return (m.group(1), int(m.group(2)), "")
    return (designator, -1, designator)

def _assembly_components(board: BoardState) -> List[Component]:
    """Bestückbare Komponenten (mit LCSC-Referenz), natürlich sortiert."""
    return sorted(
        (comp for comp in board.components.values() if comp.lcsc_id),
        key=lambda comp: _designator_key(comp.id),
    )

def _comment(comp: Component) -> str:
    return comp.value if comp.value else (comp.name or "")

def _fmt(value: float) -> str:
    """Kompakte Dezimaldarstellung ohne Endnullen."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"

def generate_jlcpcb_bom(board: BoardState) -> str:
    """Stückliste im JLCPCB-CSV-Format (eine Zeile je Designator)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(BOM_HEADER)
    for comp in _assembly_components(board):
        writer.writerow([_comment(comp), comp.id, comp.footprint, comp.lcsc_id])
    return buf.getvalue()

def generate_jlcpcb_cpl(board: BoardState) -> str:
    """Bestückdaten (Pick & Place) im JLCPCB-CSV-Format, boardzentriert."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CPL_HEADER)
    for comp in _assembly_components(board):
        mid_x = comp.x - board.width / 2.0
        mid_y = comp.y - board.height / 2.0
        writer.writerow([comp.id, _comment(comp), comp.footprint,
                         _fmt(mid_x), _fmt(mid_y), _fmt(comp.rotation), "top"])
    return buf.getvalue()
