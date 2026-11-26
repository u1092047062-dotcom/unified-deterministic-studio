"""Multi-Layer-Stackup & Impedanzrechnung (Stufe 11).

4-Lagen-Standard-Stackup (Top / GND-Plane / Power-Plane / Bottom) nach
JLCPCB-Vorlage: Prepreg 0,2104 mm (7628-Äquivalent, gepresst), Kern
1,065 mm, 4x 35 µm Kupfer — Summe ~1,63 mm. Dielectric-Er 4,2 (FR4).

Impedanzformeln (dokumentiert, deterministisch):
- Single-Ended Microstrip nach IPC-2141:
      Z0 = (87 / sqrt(Er + 1.41)) * ln(5.98*h / (0.8*w + t))
- Gekoppeltes Differential-Microstrip-Paar (IPC-2141-Kopplungsnäherung):
      Zdiff = 2 * Z0 * (1 - 0.48 * exp(-0.96 * s / h))
  mit s = Kantenabstand der Paarleitungen, h = Dielektrikum unter der Lage.

Solver: deterministische Bisektion — solve_single_ended_width(target)
liefert die Leiterbahnbreite für 50 Ohm, solve_differential_pair(target)
liefert (Breite, Abstand) für 90 Ohm (USB 2.0) bzw. 100 Ohm (Ethernet).
"""
import math
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from core.spatial_drc import HighSpeedDRC

# Gängige Prepreg-Glasstile (gepresste Dicke in mm, JLCPCB-Angaben)
PREPREG_STYLES = {
    "1080": 0.0735,
    "2116": 0.1194,
    "2313": 0.1016,
    "7628": 0.2104,  # JLCPCB-4L-Standard (L1-L2 bzw. L3-L4)
}

DEFAULT_ER = 4.2
DEFAULT_COPPER = 0.035

class StackupLayer(BaseModel):
    name: str
    kind: str                    # "copper" | "dielectric"
    thickness: float
    er: Optional[float] = None   # nur für Dielektrika
    role: Optional[str] = None   # z. B. "signal", "GND-Plane", "Power-Plane"

class Stackup(BaseModel):
    name: str
    layers: List[StackupLayer] = Field(default_factory=list)

DEFAULT_STACKUP = Stackup(
    name="JLCPCB 4-Lagen (7628-Prepreg, 1.6 mm)",
    layers=[
        StackupLayer(name="TOP", kind="copper", thickness=DEFAULT_COPPER, role="signal"),
        StackupLayer(name="Prepreg 7628", kind="dielectric", thickness=PREPREG_STYLES["7628"], er=DEFAULT_ER),
        StackupLayer(name="INNER1", kind="copper", thickness=DEFAULT_COPPER, role="GND-Plane"),
        StackupLayer(name="Core FR4", kind="dielectric", thickness=1.065, er=DEFAULT_ER),
        StackupLayer(name="INNER2", kind="copper", thickness=DEFAULT_COPPER, role="Power-Plane"),
        StackupLayer(name="Prepreg 7628", kind="dielectric", thickness=PREPREG_STYLES["7628"], er=DEFAULT_ER),
        StackupLayer(name="BOTTOM", kind="copper", thickness=DEFAULT_COPPER, role="signal"),
    ],
)

class StackupManager:
    def __init__(self, stackup: Optional[Stackup] = None, er: Optional[float] = None):
        self.stackup = stackup if stackup is not None else DEFAULT_STACKUP
        self.er = er if er is not None else self._first_dielectric_er()

    # ---------- Stackup-Abfragen ----------

    def total_thickness(self) -> float:
        return sum(layer.thickness for layer in self.stackup.layers)

    def copper_layer_names(self) -> List[str]:
        return [layer.name for layer in self.stackup.layers if layer.kind == "copper"]

    def layer_role(self, layer_name: str) -> Optional[str]:
        for layer in self.stackup.layers:
            if layer.kind == "copper" and layer.name == layer_name:
                return layer.role
        return None

    def _first_dielectric_er(self) -> float:
        for layer in self.stackup.layers:
            if layer.kind == "dielectric" and layer.er is not None:
                return layer.er
        return DEFAULT_ER

    def dielectric_height(self, layer_name: str) -> float:
        """Dielektrikum unter der Kupferlage (Microstrip-Höhe h).

        Die Layer-Liste ist von oben (L1) nach unten (L4) sortiert: für die
        oberste Kupferlage zählt das nächste Dielektrikum DARUNTER, für die
        unterste das DARÜBER. Innenschichten wären Streifenleiter und werden
        abgelehnt.
        """
        layers = self.stackup.layers
        copper = [(i, layer) for i, layer in enumerate(layers) if layer.kind == "copper"]
        position = None
        for idx, (i, layer) in enumerate(copper):
            if layer.name == layer_name:
                position = idx
                break
        if position is None:
            raise ValueError(f"Kupferlage {layer_name!r} nicht im Stackup")
        if 0 < position < len(copper) - 1:
            raise ValueError(
                f"{layer_name!r} ist eine Innenschicht (Streifenleiter) - "
                "Microstrip nur auf Außenlagen")
        i = copper[position][0]
        scan = layers[i + 1:] if position == 0 else reversed(layers[:i])
        for candidate in scan:
            if candidate.kind == "dielectric":
                return candidate.thickness
        raise ValueError(f"Kein Dielektrikum unter {layer_name!r}")

    # ---------- Impedanz ----------

    def single_ended_impedance(self, width: float, layer_name: str = "TOP",
                               copper_thickness: float = DEFAULT_COPPER) -> float:
        h = self.dielectric_height(layer_name)
        return HighSpeedDRC.calculate_microstrip_impedance(
            width, dielectric_height=h, er=self.er, copper_thickness=copper_thickness)

    def differential_impedance(self, width: float, spacing: float,
                               layer_name: str = "TOP",
                               copper_thickness: float = DEFAULT_COPPER) -> float:
        """IPC-2141-Kopplungsnäherung; s -> unendlich liefert 2 * Z0."""
        h = self.dielectric_height(layer_name)
        z0 = self.single_ended_impedance(width, layer_name, copper_thickness)
        coupling = 1.0 - 0.48 * math.exp(-0.96 * spacing / h)
        return 2.0 * z0 * coupling

    # ---------- Solver (Bisektion, deterministisch) ----------

    def solve_single_ended_width(self, target: float, layer_name: str = "TOP",
                                 lo: float = 0.05, hi: float = 8.0) -> float:
        if not (0.0 < self.single_ended_impedance(lo, layer_name)):
            raise ValueError("Zielimpedanz oberhalb des lösbaren Bereichs")
        for _ in range(100):
            mid = (lo + hi) / 2.0
            z = self.single_ended_impedance(mid, layer_name)
            if z > target:      # breiter -> niederohmiger
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    def solve_differential_pair(self, target: float, layer_name: str = "TOP") -> Tuple[float, float]:
        """Löst (Breite, Abstand) für Zdiff = target.

        Schema: Breite mit Z0 = 0.55 * target lösen — der nötige Kopplungs-
        faktor target / (2*Z0) = 1/1.1 ≈ 0.909 liegt dann immer im gültigen
        Bereich (0.52 < f < 1) und der Abstand ergibt sich per Bisektion
        (Zdiff steigt monoton mit s) zu ~1.74*h, unabhängig vom Zielwert."""
        width = self.solve_single_ended_width(target * 0.55, layer_name)

        lo, hi = 0.01, 50.0 * self.dielectric_height(layer_name)
        for _ in range(100):
            mid = (lo + hi) / 2.0
            z = self.differential_impedance(width, mid, layer_name)
            if z < target:      # weiter auseinander -> impedanzhöher
                lo = mid
            else:
                hi = mid
        return width, (lo + hi) / 2.0
