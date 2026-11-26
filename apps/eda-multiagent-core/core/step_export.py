"""3D-STEP-Export (Stufe 9): Platine + aufgesetzte Bauteilkörper als AP214.

Erzeugt ein ISO-10303-21 (STEP AP214, AUTOMOTIVE_DESIGN) File mit
MANIFOLD_SOLID_BREP-Festkörpern:

- Platinen-Slab: (0,0,-1.6) bis (W,H,0) — Oberfläche bei z=0 (wie Viewer).
- Bauteilkörper: um Z rotierbare Hexaeder auf der Board-Oberseite
  (z = 0..1.6 mm). Bauteile ohne body_w/body_h erhalten den
  Placer-Default 4x4 mm (core.placer.DEFAULT_BODY).

Warum ein eigener Writer? Es gibt keine gepflegten Geometry-Kernel-Bindings
(OCP/CadQuery) für Python 3.15. Die hier erzeugte Hexaeder-Topologie
(8 Vertex, 12 Edge, 6 Face je Körper) ist ein gültiges MANIFOLD_SOLID_BREP
und in CAD-Viewern/OCCT importierbar. Alle Körper liegen als Solids in
EINER ADVANCED_BREP_SHAPE_REPRESENTATION (eine Product-Struktur, kein
Assembly-Nesting).

Determinismus: fester Timestamp im Header — gleiche Eingabe ergibt ein
byte-identisches File.
"""
import math
from typing import List, Sequence, Tuple

from core.placer import DEFAULT_BODY
from core.schema import BoardState, Component

BOARD_THICKNESS = 1.6
BODY_HEIGHT = 1.6  # Körperhöhe aufgesetzter Bauteile (mm), wie im Viewer
STEP_TIMESTAMP = "2026-11-09T00:00:00"  # fest für byte-identische Exporte

# Hexaeder-Topologie: Ecken 0-3 unten, 4-7 oben (gleiche Lokalreihenfolge).
EDGE_PAIRS: Tuple[Tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 0),          # Unterring
    (4, 5), (5, 6), (6, 7), (7, 4),          # Oberring
    (0, 4), (1, 5), (2, 6), (3, 7),          # Vertikal
)
# Flächen als (Kantenindex, forward): gegen den Uhrzeiger von außen;
# geteilte Kanten tauchen in den beiden Flächen gegensinnig auf.
FACE_LOOPS: Tuple[Tuple[Tuple[int, bool], ...], ...] = (
    ((3, False), (2, False), (1, False), (0, False)),   # unten (-Z)
    ((4, True), (5, True), (6, True), (7, True)),       # oben (+Z)
    ((0, True), (9, True), (4, False), (8, False)),     # vorn (-Y)
    ((1, True), (10, True), (5, False), (9, False)),    # rechts (+X)
    ((2, True), (11, True), (6, False), (10, False)),   # hinten (+Y)
    ((3, True), (8, True), (7, False), (11, False)),    # links (-X)
)

def _fmt(value: float) -> str:
    """STEP-Real: immer mit Dezimalpunkt."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return (text if text not in ("", "-0") else "0") + "."

class _Builder:
    """Nummeriert STEP-Entities (#1, #2, ...) in Ausgabereihenfolge."""

    def __init__(self):
        self._lines: List[str] = []
        self._next_id = 0

    def add(self, body: str) -> int:
        self._next_id += 1
        self._lines.append(f"#{self._next_id} = {body};")
        return self._next_id

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"

def _hexahedron_corners(cx: float, cy: float, z0: float, z1: float,
                        w: float, h: float, rotation_deg: float) -> List[Tuple[float, float, float]]:
    """8 Ecken eines um Z rotierbaren Quaders: 0-3 unten, 4-7 oben."""
    angle = math.radians(rotation_deg)
    ca, sa = math.cos(angle), math.sin(angle)
    half_w, half_h = w / 2.0, h / 2.0
    local = ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h))
    corners: List[Tuple[float, float, float]] = []
    for dx, dy in local:
        corners.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca, z0))
    for dx, dy in local:
        corners.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca, z1))
    return corners

def _edge_endpoints(corners: Sequence[Tuple[float, float, float]],
                    edge_index: int, forward: bool) -> Tuple[Tuple[float, float, float],
                                                             Tuple[float, float, float]]:
    a, c = EDGE_PAIRS[edge_index]
    return (corners[a], corners[c]) if forward else (corners[c], corners[a])

def _newell_normal(loop_points: Sequence[Tuple[float, float, float]]) -> Tuple[float, float, float]:
    """Flächennormale über Newell-Methode (Loop gegen den Uhrzeiger von außen)."""
    nx = ny = nz = 0.0
    count = len(loop_points)
    for i in range(count):
        x1, y1, z1 = loop_points[i]
        x2, y2, z2 = loop_points[(i + 1) % count]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    return nx / length, ny / length, nz / length

def _add_box(builder: _Builder, corners: Sequence[Tuple[float, float, float]]) -> int:
    """Ein MANIFOLD_SOLID_BREP für einen Hexaeder (Ecken in Topologie-Ordnung)."""
    points = [builder.add(f"CARTESIAN_POINT('',({_fmt(x)},{_fmt(y)},{_fmt(z)}))")
              for x, y, z in corners]
    vertices = [builder.add(f"VERTEX_POINT('',#{p})") for p in points]

    edges: List[int] = []
    for edge_index, (a, c) in enumerate(EDGE_PAIRS):
        pa, pc = corners[a], corners[c]
        dx, dy, dz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        direction = builder.add(f"DIRECTION('',({_fmt(dx / length)},{_fmt(dy / length)},{_fmt(dz / length)}))")
        vector = builder.add(f"VECTOR('',#{direction},{_fmt(length)})")
        line = builder.add(f"LINE('',#{points[a]},#{vector})")
        edges.append(builder.add(f"EDGE_CURVE('',#{vertices[a]},#{vertices[c]},#{line},.T.)"))

    faces: List[int] = []
    for loop in FACE_LOOPS:
        oriented = [
            builder.add(f"ORIENTED_EDGE('',*,*,#{edges[edge_index]},.{'T' if forward else 'F'}.)")
            for edge_index, forward in loop
        ]
        loop_id = builder.add(f"EDGE_LOOP('',({','.join('#' + str(o) for o in oriented)}))")
        bound = builder.add(f"FACE_OUTER_BOUND('',#{loop_id},.T.)")

        loop_corners = [_edge_endpoints(corners, edge_index, forward)[0]
                        for edge_index, forward in loop]
        origin = loop_corners[0]
        normal = _newell_normal(loop_corners)
        ref_dir = _edge_endpoints(corners, loop[0][0], loop[0][1])[1]
        dx, dy, dz = (ref_dir[0] - origin[0], ref_dir[1] - origin[1], ref_dir[2] - origin[2])
        ref_length = math.sqrt(dx * dx + dy * dy + dz * dz)

        origin_id = builder.add(f"CARTESIAN_POINT('',({_fmt(origin[0])},{_fmt(origin[1])},{_fmt(origin[2])}))")
        normal_id = builder.add(f"DIRECTION('',({_fmt(normal[0])},{_fmt(normal[1])},{_fmt(normal[2])}))")
        ref_id = builder.add(f"DIRECTION('',({_fmt(dx / ref_length)},{_fmt(dy / ref_length)},{_fmt(dz / ref_length)}))")
        axis = builder.add(f"AXIS2_PLACEMENT_3D('',#{origin_id},#{normal_id},#{ref_id})")
        plane = builder.add(f"PLANE('',#{axis})")
        faces.append(builder.add(f"ADVANCED_FACE('',(#{bound}),#{plane},.T.)"))

    shell = builder.add(f"CLOSED_SHELL('',({','.join('#' + str(f) for f in faces)}))")
    return builder.add(f"MANIFOLD_SOLID_BREP('',#{shell})")

def _board_slab_corners(board: BoardState) -> List[Tuple[float, float, float]]:
    return _hexahedron_corners(board.width / 2.0, board.height / 2.0,
                               -BOARD_THICKNESS, 0.0,
                               board.width, board.height, 0.0)

def generate_step(board: BoardState, product_name: str = "eda_board_assembly") -> str:
    """Erzeugt das vollständige STEP-File (AP214) für Board + Bauteilkörper."""
    builder = _Builder()

    app_context = builder.add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    builder.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app_context})")
    product_context = builder.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app_context},'design')")
    mechanical = builder.add(f"MECHANICAL_CONTEXT('',#{app_context},'mechanical')")
    product = builder.add(f"PRODUCT('{_esc(product_name)}','{_esc(product_name)}','',(#{mechanical}))")
    formation = builder.add(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
    definition = builder.add(f"PRODUCT_DEFINITION('design','',#{formation},#{product_context})")
    definition_shape = builder.add(f"PRODUCT_DEFINITION_SHAPE('','',#{definition})")

    solids: List[int] = [_add_box(builder, _board_slab_corners(board))]
    for comp in board.components.values():
        w = comp.body_w if comp.body_w is not None else DEFAULT_BODY[0]
        h = comp.body_h if comp.body_h is not None else DEFAULT_BODY[1]
        solids.append(_add_box(builder, _hexahedron_corners(
            comp.x, comp.y, 0.0, BODY_HEIGHT, w, h, comp.rotation)))

    origin_id = builder.add("CARTESIAN_POINT('',(0.,0.,0.))")
    z_dir = builder.add("DIRECTION('',(0.,0.,1.))")
    x_dir = builder.add("DIRECTION('',(1.,0.,0.))")
    placement = builder.add(f"AXIS2_PLACEMENT_3D('',#{origin_id},#{z_dir},#{x_dir})")

    length_unit = builder.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    angle_unit = builder.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    solid_angle_unit = builder.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) PLANE_ANGLE_UNIT() )")
    uncertainty = builder.add(
        "UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),"
        f"#{length_unit},'distance_accuracy_value','confusion accuracy')")
    context = builder.add(
        "( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{uncertainty})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{length_unit},#{angle_unit},#{solid_angle_unit})) "
        "REPRESENTATION_CONTEXT('','3D Context with UNIT and UNCERTAINTY') )")

    brep = builder.add(
        f"ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{placement},"
        f"{','.join('#' + str(s) for s in solids)}),#{context})")
    builder.add(f"SHAPE_DEFINITION_REPRESENTATION(#{definition_shape},#{brep})")

    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('EDA board assembly: PCB and component bodies'),"
        "'2;1');\n"
        f"FILE_NAME('{_esc(product_name)}.step','{STEP_TIMESTAMP}',"
        "('eda-multiagent-core'),('eda-multiagent-core'),"
        "'core/step_export.py','eda-multiagent-core','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n")
    return header + builder.text() + "END-ISO-10303-21;\n"

def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("'", "''")
