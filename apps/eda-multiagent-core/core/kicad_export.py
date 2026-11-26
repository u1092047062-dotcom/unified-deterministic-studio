"""KiCad-v8-Exporter (.kicad_pcb, S-Expression).

Erzeugt ein syntaktisch valides KiCad-PCB-File im v8-Format
(version 20240108, generator_version "8.0"). Exportiert werden:

- Platzenumriss als vier gr_line auf Edge.Cuts,
- Footprints mit Reference/Value-Properties, F.Fab-Bodyoutline und
  SMD-Pads (inkl. Netz- und Pintype-Zuordnung),
- Traces als segment-Einträge auf F.Cu/B.Cu,
- Vias als via-Einträge (F.Cu <-> B.Cu),
- Lagenblock mit dem Standard-2-Lagen-Satz.

Konventionen:
- KiCad-Y-Achse zeigt nach unten -> y' = Boardhöhe - y (gespiegelt),
  damit das Layout in KiCad genauso aussieht wie im Web-CAD-Viewer.
- Rotationen werden ignoriert: Padpositionen liegen als absolute
  Koordinaten im Schema vor und werden direkt abgebildet.
- UUIDs sind deterministisch (uuid5 über stabile Schlüssel), damit
  der Export für dasselbe Board byte-identisch ist.
- Alle Zahlen werden kompakt (ohne Endnullen) in mm ausgegeben;
  Strings werden S-Expression-gerecht escaped.
"""
import uuid
from typing import Dict, List, Optional, Tuple

from core.schema import BoardState, Component, Pin

KICAD_VERSION = "20240108"        # KiCad 8.0 Dateiformat
GENERATOR = "eda-multiagent-core"
GENERATOR_VERSION = "1.0"
FOOTPRINT_LIB = "eda-core"
BOARD_THICKNESS = 1.6
EDGE_LINE_WIDTH = 0.1
DEFAULT_PAD_SIZE = (1.0, 1.0)

LAYER_MAP = {
    "TOP": "F.Cu",
    "INNER1": "In1.Cu",
    "INNER2": "In2.Cu",
    "BOTTOM": "B.Cu",
}

# Standard-Lagensatz einer 4-Lagen-Platine (KiCad-Layer-IDs)
STANDARD_LAYERS: List[Tuple[int, str, str, Optional[str]]] = [
    (0, "F.Cu", "signal", None),
    (1, "In1.Cu", "signal", None),
    (2, "In2.Cu", "signal", None),
    (31, "B.Cu", "signal", None),
    (32, "B.Adhes", "user", "B.Adhesive"),
    (33, "F.Adhes", "user", "F.Adhesive"),
    (34, "B.Paste", "user", None),
    (35, "F.Paste", "user", None),
    (36, "B.SilkS", "user", "B.Silkscreen"),
    (37, "F.SilkS", "user", "F.Silkscreen"),
    (38, "B.Mask", "user", None),
    (39, "F.Mask", "user", None),
    (40, "Dwgs.User", "user", "User.Drawings"),
    (41, "Cmts.User", "user", "User.Comments"),
    (42, "Eco1.User", "user", "User.Eco1"),
    (43, "Eco2.User", "user", "User.Eco2"),
    (44, "Edge.Cuts", "user", None),
    (45, "Margin", "user", None),
    (46, "B.CrtYd", "user", "B.Courtyard"),
    (47, "F.CrtYd", "user", "F.Courtyard"),
    (48, "B.Fab", "user", None),
    (49, "F.Fab", "user", None),
]

# Schema-PinType -> KiCad pintype-Token
PINTYPE_MAP = {
    "POWER": "power_in",
    "GROUND": "power_in",
    "INPUT": "input",
    "OUTPUT": "output",
    "PASSIVE": "passive",
}

def _fmt(value: float) -> str:
    """Kompakte Dezimaldarstellung ohne Endnullen (KiCad erwartet mm)."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"

def _esc(text: str) -> str:
    return (str(text).replace("\\", "\\\\")
                     .replace('"', '\\"')
                     .replace("\n", "\\n")
                     .replace("\r", "\\r"))

def _stable_uuid(key: str) -> str:
    """Deterministische UUID für reproduzierbare Exporte."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{GENERATOR}/{key}"))

def _net_assignments(board: BoardState) -> Tuple[Dict[str, int], Dict[Tuple[str, str], Tuple[int, str]]]:
    """Netz-Nummerierung (1..n in Board-Reihenfolge) und Pad->Netz-Lookup."""
    net_numbers: Dict[str, int] = {net_id: i + 1 for i, net_id in enumerate(board.nets)}
    pin_nets: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for net_id, net in board.nets.items():
        for comp_id, pin_id in net.nodes:
            # Pads referenzieren den deklarierten Netz-Namen (wie KiCad selbst).
            pin_nets[(comp_id, pin_id)] = (net_numbers[net_id], net.name)
    return net_numbers, pin_nets

class _Writer:
    def __init__(self):
        self._parts: List[str] = []
        self._depth = 0

    def open(self, head: str):
        self._parts.append("  " * self._depth + "(" + head)
        self._depth += 1

    def line(self, text: str):
        self._parts.append("  " * self._depth + text)

    def close(self):
        self._depth -= 1
        self._parts.append("  " * self._depth + ")")

    def text(self) -> str:
        return "\n".join(self._parts) + "\n"

def _body_size(comp: Component) -> Tuple[float, float]:
    w = comp.body_w if comp.body_w is not None else 4.0
    h = comp.body_h if comp.body_h is not None else 4.0
    return float(w), float(h)

def _write_footprint(w: _Writer, board: BoardState, comp: Component,
                     pin_nets: Dict[Tuple[str, str], Tuple[int, str]]):
    h_flip = board.height
    body_w, body_h = _body_size(comp)
    w.open(f'footprint "{_esc(FOOTPRINT_LIB + ":" + comp.footprint)}"')
    w.line('(layer "F.Cu")')
    w.line(f'(uuid "{_stable_uuid("footprint/" + comp.id)}")')
    w.line(f'(at {_fmt(comp.x)} {_fmt(h_flip - comp.y)})')
    w.line("(attr smd)")

    value = comp.value if comp.value is not None else comp.name
    for prop_name, prop_value, layer in (("Reference", comp.id, "F.SilkS"),
                                         ("Value", value, "F.Fab")):
        w.open(f'property "{_esc(prop_name)}" "{_esc(prop_value)}"')
        w.line("(at 0 0)")
        w.line(f'(layer "{layer}")')
        w.line(f'(uuid "{_stable_uuid(f"property/{comp.id}/{prop_name}")}")')
        w.line("(effects (font (size 1 1) (thickness 0.15)))")
        w.close()

    # Body-Outline auf F.Fab (Pads liegen als absolute Koordinaten vor,
    # daher ist die relative Lage exakt; Rotationen bleiben unberücksichtigt).
    hx, hy = body_w / 2.0, body_h / 2.0
    corners = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
    for i in range(4):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % 4]
        w.line(f'(fp_line (start {_fmt(x1)} {_fmt(y1)}) (end {_fmt(x2)} {_fmt(y2)}) '
               f'(stroke (width {_fmt(EDGE_LINE_WIDTH)}) (type solid)) (layer "F.Fab") '
               f'(uuid "{_stable_uuid(f"fab/{comp.id}/{i}")}"))')

    for pin_id, pin in comp.pins.items():
        rel_x = pin.x - comp.x if pin.x is not None else 0.0
        rel_y = -(pin.y - comp.y) if pin.y is not None else 0.0
        w.open(f'pad "{_esc(pin_id)}" smd roundrect')
        w.line(f'(uuid "{_stable_uuid(f"pad/{comp.id}/{pin_id}")}")')
        w.line(f'(at {_fmt(rel_x)} {_fmt(rel_y)})')
        w.line(f'(size {_fmt(DEFAULT_PAD_SIZE[0])} {_fmt(DEFAULT_PAD_SIZE[1])})')
        w.line('(layers "F.Cu" "F.Paste" "F.Mask")')
        w.line("(roundrect_rratio 0.25)")
        net = pin_nets.get((comp.id, pin_id))
        if net is not None:
            w.line(f'(net {net[0]} "{_esc(net[1])}")')
        w.line(f'(pintype "{PINTYPE_MAP.get(pin.pin_type.value, "unspecified")}")')
        w.close()
    w.close()

def export_kicad_pcb(board: BoardState, title: str = "eda-multiagent-core export") -> str:
    """Exportiert ein BoardState als vollständiges .kicad_pcb (KiCad v8)."""
    net_numbers, pin_nets = _net_assignments(board)
    w = _Writer()

    w.open("kicad_pcb")
    w.line(f"(version {KICAD_VERSION})")
    w.line(f'(generator "{_esc(GENERATOR)}")')
    w.line(f'(generator_version "{GENERATOR_VERSION}")')
    w.line(f"(general (thickness {_fmt(BOARD_THICKNESS)}) (legacy_teardrops no))")
    w.line('(paper "A4")')
    w.open("title_block")
    w.line(f'(title "{_esc(title)}")')
    w.close()

    w.open("layers")
    for layer_id, name, kind, alias in STANDARD_LAYERS:
        suffix = f' "{_esc(alias)}"' if alias else ""
        w.line(f'({layer_id} "{name}" {kind}{suffix})')
    w.close()

    w.open("setup")
    w.line("(pad_to_mask_clearance 0)")
    w.line("(allow_soldermask_bridges_in_footprints no)")
    w.close()

    w.line('(net 0 "")')
    for net_id, net in board.nets.items():
        w.line(f'(net {net_numbers[net_id]} "{_esc(net.name)}")')

    for comp in board.components.values():
        _write_footprint(w, board, comp, pin_nets)

    for trace in board.traces:
        layer = LAYER_MAP.get(trace.layer, "F.Cu")
        net_num = net_numbers.get(trace.net_id)
        net_token = f" (net {net_num})" if net_num is not None else ""
        path = trace.path
        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]
            w.line(f'(segment (start {_fmt(x1)} {_fmt(board.height - y1)}) '
                   f'(end {_fmt(x2)} {_fmt(board.height - y2)}) '
                   f'(width {_fmt(trace.width)}) (layer "{layer}"){net_token} '
                   f'(uuid "{_stable_uuid(f"segment/{trace.net_id}/{i}/{x1}/{y1}/{x2}/{y2}")}"))')

    for via in board.vias:
        net_num = net_numbers.get(via.net_id)
        net_token = f" (net {net_num})" if net_num is not None else ""
        w.line(f'(via (at {_fmt(via.x)} {_fmt(board.height - via.y)}) '
               f'(size {_fmt(via.diameter)}) (drill {_fmt(via.diameter / 2.0)}) '
               f'(layers "F.Cu" "B.Cu"){net_token} '
               f'(uuid "{_stable_uuid(f"via/{via.net_id}/{via.x}/{via.y}")}"))')

    # Platzenumriss: 4 gr_line um das Board-Rechteck (KiCad-Koordinaten).
    width, height = board.width, board.height
    outline = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    for i in range(4):
        x1, y1 = outline[i]
        x2, y2 = outline[(i + 1) % 4]
        w.line(f'(gr_line (start {_fmt(x1)} {_fmt(y1)}) (end {_fmt(x2)} {_fmt(y2)}) '
               f'(stroke (width {_fmt(EDGE_LINE_WIDTH)}) (type solid)) (layer "Edge.Cuts") '
               f'(uuid "{_stable_uuid(f"edge/{i}")}"))')

    w.close()
    return w.text()
