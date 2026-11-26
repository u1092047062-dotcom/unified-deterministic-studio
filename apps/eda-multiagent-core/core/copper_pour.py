"""Copper Pour (Stufe 5): Kupferfläche auf BOTTOM mit Thermal-Reliefs.

Geometrie über exakte Polygon-Booleans (Shapely), nicht über Raster- oder
Freitext-Heuristiken (Systeminvariant 1):

- Ausgangsfläche: Board-Rechteck auf der Ziellage.
- Keepouts: fremde Leiterbahnen derselben Lage (Stadium um das Segment
  mit Radius width/2 + Clearance) und fremde Vias (Kreis mit
  diameter/2 + Clearance). Traces und Vias des Pour-Netzes werden
  NICHT ausgespart - sie liegen elektrisch auf der Fläche.
- Thermal Reliefs: Für GND-Pads (Termini des Pour-Netzes) und GND-Vias
  wird ein Anti-Pad-Kreis ausgespart, aus dem vier Speichen (horizontal +
  vertikal, Breite thermal_spoke_width) wieder herausgenommen werden -
  die Speichen bleiben also Teil der Kupferfläche und verbinden das Pad
  thermisch (nicht galvanisch vollflächig) mit dem Pour.

Ergebnis: Liste von CopperPour-Einträgen (je zusammenhängender Region,
mit äußeren Ring und Aussparungen) - direkt renderbar (THREE.Shape +
Löcher) und json-stabil.
"""
from typing import List, Optional, Tuple

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from core.schema import BoardState, CopperPour, Pin

DEFAULT_PAD_SIZE = (1.0, 1.0)  # mm; Pads ohne eigene Geometrie im Schema

class CopperPourGenerator:
    def __init__(self,
                 net_id: str = "GND",
                 layer: str = "BOTTOM",
                 clearance: float = 0.3,
                 thermal_clearance: float = 0.3,
                 thermal_spoke_width: float = 0.3,
                 vertex_simplify: float = 0.01,
                 quad_segs: int = 12):
        self.net_id = net_id
        self.layer = layer
        self.clearance = clearance
        self.thermal_clearance = thermal_clearance
        self.thermal_spoke_width = thermal_spoke_width
        self.vertex_simplify = vertex_simplify
        self.quad_segs = quad_segs

    # ---------- Öffentliche API ----------

    def generate(self, board: BoardState) -> List[CopperPour]:
        board_poly = box(0.0, 0.0, board.width, board.height)
        keepouts = self._foreign_keepouts(board)
        thermal_keepouts = self._thermal_keepouts(board)
        obstacles = keepouts + thermal_keepouts

        if obstacles:
            pour = board_poly.difference(unary_union(obstacles))
        else:
            pour = board_poly

        pour = pour.simplify(self.vertex_simplify, preserve_topology=True)
        if pour.is_empty:
            return []
        regions = [pour] if pour.geom_type == "Polygon" else list(pour.geoms)

        pours: List[CopperPour] = []
        for region in regions:
            if region.is_empty or region.area < 1e-9:
                continue
            pours.append(CopperPour(
                net_id=self.net_id,
                layer=self.layer,
                outer=self._ring_coords(region.exterior),
                holes=[self._ring_coords(ring) for ring in region.interiors],
            ))
        return pours

    # ---------- Keepouts ----------

    def _foreign_keepouts(self, board: BoardState) -> List:
        """Fremde Leiterbahnen derselben Lage und fremde Vias."""
        keepouts = []
        for trace in board.traces:
            if trace.layer != self.layer or trace.net_id == self.net_id:
                continue
            radius = trace.width / 2.0 + self.clearance
            for p1, p2 in zip(trace.path, trace.path[1:]):
                segment = LineString([(p1[0], p1[1]), (p2[0], p2[1])])
                keepouts.append(segment.buffer(radius, quad_segs=self.quad_segs))
        for via in board.vias:
            if via.net_id == self.net_id:
                continue
            keepouts.append(Point(via.x, via.y).buffer(
                via.diameter / 2.0 + self.clearance, quad_segs=self.quad_segs))
        return keepouts

    def _thermal_keepouts(self, board: BoardState) -> List:
        """Anti-Pad minus Speicherkreuz für jeden GND-Pad/GND-Via."""
        keepouts = []
        for x, y, pad_radius in self._thermal_points(board):
            anti_pad = Point(x, y).buffer(
                pad_radius + self.thermal_clearance, quad_segs=self.quad_segs)
            reach = pad_radius + self.thermal_clearance + 0.1  # reicht in den Pour hinein
            half = self.thermal_spoke_width / 2.0
            spokes = box(x - reach, y - half, x + reach, y + half).union(
                box(x - half, y - reach, x + half, y + reach))
            keepouts.append(anti_pad.difference(spokes))
        return keepouts

    def _thermal_points(self, board: BoardState) -> List[Tuple[float, float, float]]:
        """GND-Pads (Termini des Pour-Netzes, Pad-Radius 0.5 mm) und GND-Vias."""
        points: List[Tuple[float, float, float]] = []
        net = board.nets.get(self.net_id)
        if net is not None:
            for comp_id, pin_id in net.nodes:
                comp = board.components.get(comp_id)
                if comp is None:
                    continue
                pin: Optional[Pin] = comp.pins.get(pin_id)
                if pin is not None and pin.x is not None and pin.y is not None:
                    points.append((pin.x, pin.y, DEFAULT_PAD_SIZE[0] / 2.0))
        for via in board.vias:
            if via.net_id == self.net_id:
                points.append((via.x, via.y, via.diameter / 2.0))
        return points

    # ---------- Helfer ----------

    @staticmethod
    def _ring_coords(ring) -> List[Tuple[float, float]]:
        coords = list(ring.coords)
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]  # schließenden Punkt abtrennen
        return [(round(x, 4), round(y, 4)) for x, y in coords]
