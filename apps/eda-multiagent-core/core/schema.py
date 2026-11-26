from enum import Enum
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

class PinType(str, Enum):
    POWER = "POWER"
    GROUND = "GROUND"
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    PASSIVE = "PASSIVE"

class Layer(str, Enum):
    TOP = "TOP"
    INNER1 = "INNER1"   # 4-Lagen-Stackup: GND-Plane
    INNER2 = "INNER2"   # 4-Lagen-Stackup: Power-Plane
    BOTTOM = "BOTTOM"

class Pin(BaseModel):
    id: str
    name: str
    pin_type: PinType
    max_voltage: float = 3.3
    net_id: Optional[str] = None
    # Absolute Position auf dem Board in mm; None -> Router fällt auf die
    # Komponentenmitte zurück.
    x: Optional[float] = None
    y: Optional[float] = None

class Component(BaseModel):
    id: str
    name: str
    category: str
    footprint: str
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    pins: Dict[str, Pin] = Field(default_factory=dict)
    value: Optional[str] = None
    # Gehäuseabmessungen in mm (achsenparallel, ohne Rotation); None -> keine
    # Body-Kollisionsprüfung in der DRC.
    body_w: Optional[float] = None
    body_h: Optional[float] = None
    # Katalogreferenzen (Stufe 7): vom part_resolution_node aus der
    # Bauteilbibliothek zugewiesen.
    lcsc_id: Optional[str] = None
    mpn: Optional[str] = None
    datasheet_url: Optional[str] = None

class Net(BaseModel):
    id: str
    name: str
    nodes: List[Tuple[str, str]] = Field(default_factory=list)

class Trace(BaseModel):
    net_id: str
    layer: str = "TOP"  # Wert aus Layer; als str für JSON-Stabilität
    width: float = 0.25
    path: List[Tuple[float, float]] = Field(default_factory=list)

class Via(BaseModel):
    net_id: str
    x: float
    y: float
    from_layer: str = Layer.TOP
    to_layer: str = Layer.BOTTOM
    diameter: float = 0.6

class CopperPour(BaseModel):
    """Zusammenhängende Kupferfläche (z. B. GND-Pour).

    outer: äußerer Ring, holes: Innenebene (Anti-Pads um thermische
    Entkopplungspunkte bzw. Keepouts). Koordinaten in mm, Board-Koordinaten
    (Y nach oben, wie im Viewer).
    """
    net_id: str
    layer: str = Layer.BOTTOM
    outer: List[Tuple[float, float]] = Field(default_factory=list)
    holes: List[List[Tuple[float, float]]] = Field(default_factory=list)

class DesignRules(BaseModel):
    min_clearance: float = 0.2      # mm, Leiterbahn-zu-Leiterbahn
    min_trace_width: float = 0.2    # mm
    min_via_diameter: float = 0.6   # mm

class ValidationError(BaseModel):
    rule_id: str
    severity: str
    message: str
    affected_components: List[str] = Field(default_factory=list)
    affected_nets: List[str] = Field(default_factory=list)
    location: Optional[Tuple[float, float]] = None

class BoardState(BaseModel):
    width: float = 100.0
    height: float = 80.0
    components: Dict[str, Component] = Field(default_factory=dict)
    nets: Dict[str, Net] = Field(default_factory=dict)
    traces: List[Trace] = Field(default_factory=list)
    vias: List[Via] = Field(default_factory=list)
    copper_pours: List[CopperPour] = Field(default_factory=list)
    errors: List[ValidationError] = Field(default_factory=list)
