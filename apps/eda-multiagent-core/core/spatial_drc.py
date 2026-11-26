import math
from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Set, Tuple

from core.schema import BoardState, DesignRules, Trace, ValidationError

Point = Tuple[float, float]
Segment = Tuple[Point, Point]
Rect = Tuple[float, float, float, float]  # min_x, min_y, max_x, max_y

class SpatialHashIndex:
    """Spatial Hash für Segmente und Rechtecke.

    Insert läuft als Zell-Walk entlang des Segments -> O(Länge/cell_size)
    statt O(Bounding-Box-Fläche). Queries liefern Kandidaten aus der
    um radius erweiterten Bounding-Box; die exakte Geometrie (Seg-Seg-Abstand)
    wird erst auf den Kandidaten gerechnet.
    """

    def __init__(self, cell_size: float = 1.0):
        self.cell_size = cell_size
        self.grid: Dict[Tuple[int, int], List[Tuple[str, Point, Point]]] = defaultdict(list)
        self.rect_grid: Dict[Tuple[int, int], List[Tuple[str, float, float, float, float]]] = defaultdict(list)

    def _hash_point(self, x: float, y: float) -> Tuple[int, int]:
        return int(math.floor(x / self.cell_size)), int(math.floor(y / self.cell_size))

    def _cells_along_segment(self, p1: Point, p2: Point) -> Iterator[Tuple[int, int]]:
        """Besucht jede Zelle, die das Segment schneidet, genau einmal
        (Gerade schneidet konvexe Zelle in einem zusammenhängenden Intervall)."""
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        steps = int(length / (self.cell_size * 0.5)) + 1
        last: Optional[Tuple[int, int]] = None
        for i in range(steps + 1):
            t = i / steps
            cell = self._hash_point(p1[0] + t * dx, p1[1] + t * dy)
            if cell != last:
                yield cell
                last = cell

    def insert_segment(self, net_id: str, p1: Point, p2: Point):
        for cx, cy in self._cells_along_segment(p1, p2):
            self.grid[(cx, cy)].append((net_id, p1, p2))

    def insert_rect(self, ref_id: str, center: Point, w: float, h: float):
        half_w, half_h = w / 2.0, h / 2.0
        min_x, max_x = center[0] - half_w, center[0] + half_w
        min_y, max_y = center[1] - half_h, center[1] + half_h
        c0 = self._hash_point(min_x, min_y)
        c1 = self._hash_point(max_x, max_y)
        for cx in range(c0[0], c1[0] + 1):
            for cy in range(c0[1], c1[1] + 1):
                self.rect_grid[(cx, cy)].append((ref_id, min_x, min_y, max_x, max_y))

    def query_clearance(self, net_id: str, p: Point, min_clearance: float) -> List[str]:
        """Fremd-Netze, deren Segmente näher als min_clearance am Punkt p liegen."""
        conflicts: Set[str] = set()
        for seg in self._candidates(p, p, min_clearance):
            other_net, a, b = seg
            if other_net == net_id:
                continue
            if self._dist_point_to_seg(p, a, b) < min_clearance:
                conflicts.add(other_net)
        return sorted(conflicts)

    def query_segment_clearance(self, net_id: str, p1: Point, p2: Point, min_clearance: float) -> List[str]:
        """Fremd-Netze, deren Segmente näher als min_clearance am Segment p1-p2 liegen."""
        conflicts: Set[str] = set()
        for seg in self._candidates(p1, p2, min_clearance):
            other_net, a, b = seg
            if other_net == net_id:
                continue
            if self._dist_seg_seg(p1, p2, a, b) < min_clearance:
                conflicts.add(other_net)
        return sorted(conflicts)

    def query_rect_violations(self, p1: Point, p2: Point) -> List[str]:
        """Rechtecke (Bauteil-Bodies), die vom Segment p1-p2 geschnitten werden."""
        hits: Set[str] = set()
        for ref_id, min_x, min_y, max_x, max_y in self._rect_candidates(p1, p2):
            if self._dist_seg_rect(p1, p2, (min_x, min_y, max_x, max_y)) == 0.0:
                hits.add(ref_id)
        return sorted(hits)

    def _candidates(self, p1: Point, p2: Point, radius: float) -> Iterator[Tuple[str, Point, Point]]:
        r = int(math.ceil(radius / self.cell_size))
        c0 = self._hash_point(min(p1[0], p2[0]), min(p1[1], p2[1]))
        c1 = self._hash_point(max(p1[0], p2[0]), max(p1[1], p2[1]))
        seen: Set[Tuple[str, Point, Point]] = set()
        for cx in range(c0[0] - r, c1[0] + r + 1):
            for cy in range(c0[1] - r, c1[1] + r + 1):
                for seg in self.grid.get((cx, cy), ()):
                    if seg not in seen:
                        seen.add(seg)
                        yield seg

    def _rect_candidates(self, p1: Point, p2: Point) -> Iterator[Tuple[str, float, float, float, float]]:
        c0 = self._hash_point(min(p1[0], p2[0]), min(p1[1], p2[1]))
        c1 = self._hash_point(max(p1[0], p2[0]), max(p1[1], p2[1]))
        seen: Set[Tuple[str, float, float, float, float]] = set()
        for cx in range(c0[0], c1[0] + 1):
            for cy in range(c0[1], c1[1] + 1):
                for rect in self.rect_grid.get((cx, cy), ()):
                    if rect not in seen:
                        seen.add(rect)
                        yield rect

    @staticmethod
    def _dist_point_to_seg(p: Point, a: Point, b: Point) -> float:
        px, py = p
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    @staticmethod
    def _orient(a: Point, b: Point, c: Point) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    @staticmethod
    def _on_segment(a: Point, b: Point, c: Point) -> bool:
        return (min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
                and min(a[1], b[1]) <= c[1] <= max(a[1], b[1]))

    @classmethod
    def _dist_seg_seg(cls, p1: Point, p2: Point, p3: Point, p4: Point) -> float:
        """Abstand zweier Strecken; 0.0 bei Schnitt oder kollinearer Überlappung."""
        o1 = cls._orient(p3, p4, p1)
        o2 = cls._orient(p3, p4, p2)
        o3 = cls._orient(p1, p2, p3)
        o4 = cls._orient(p1, p2, p4)
        if ((o1 > 0) != (o2 > 0)) and ((o3 > 0) != (o4 > 0)):
            return 0.0
        if o1 == 0 and cls._on_segment(p3, p4, p1):
            return 0.0
        if o2 == 0 and cls._on_segment(p3, p4, p2):
            return 0.0
        if o3 == 0 and cls._on_segment(p1, p2, p3):
            return 0.0
        if o4 == 0 and cls._on_segment(p1, p2, p4):
            return 0.0
        return min(
            cls._dist_point_to_seg(p1, p3, p4),
            cls._dist_point_to_seg(p2, p3, p4),
            cls._dist_point_to_seg(p3, p1, p2),
            cls._dist_point_to_seg(p4, p1, p2),
        )

    @classmethod
    def _dist_seg_rect(cls, p1: Point, p2: Point, rect: Tuple[float, float, float, float]) -> float:
        min_x, min_y, max_x, max_y = rect
        inside = lambda p: min_x <= p[0] <= max_x and min_y <= p[1] <= max_y
        if inside(p1) or inside(p2):
            return 0.0
        edges = (
            ((min_x, min_y), (max_x, min_y)),
            ((max_x, min_y), (max_x, max_y)),
            ((max_x, max_y), (min_x, max_y)),
            ((min_x, max_y), (min_x, min_y)),
        )
        return min(cls._dist_seg_seg(p1, p2, a, b) for a, b in edges)

class HighSpeedDRC:
    RULE_CLEARANCE = "DRC_CLEARANCE"
    RULE_COMPONENT_COLLISION = "DRC_COMPONENT_COLLISION"
    RULE_BODY_OVERLAP = "DRC_BODY_OVERLAP"

    @staticmethod
    def calculate_microstrip_impedance(trace_width: float, dielectric_height: float = 1.6,
                                       er: float = 4.5, copper_thickness: float = 0.035) -> float:
        """IPC-2141 Mikrostreifen-Näherung. Gültig für w/h < ~2.5; darüber
        unterschätzt die Formel und wir klemmen physikalisch bei 0 Ohm
        (w >> h -> Z0 -> 0)."""
        w, h, t = trace_width, dielectric_height, copper_thickness
        if w <= 0 or h <= 0:
            raise ValueError("trace_width und dielectric_height müssen positiv sein")
        ratio = (5.98 * h) / (0.8 * w + t)
        if ratio <= 1.0:
            return 0.0
        return (87.0 / math.sqrt(er + 1.41)) * math.log(ratio)

    @staticmethod
    def _rect_overlap_depth(r1: Rect, r2: Rect) -> float:
        """Durchdringungstiefe zweier achsenparalleler Rechtecke; 0.0 bei
        Berührung oder Abstand."""
        ox = min(r1[2], r2[2]) - max(r1[0], r2[0])
        oy = min(r1[3], r2[3]) - max(r1[1], r2[1])
        if ox > 0.0 and oy > 0.0:
            return min(ox, oy)
        return 0.0

    @staticmethod
    def _check_body_overlaps(index: SpatialHashIndex) -> List[ValidationError]:
        """Body-zu-Body-Überlappungen (Courtyard-Verstoß) aus dem Rect-Grid
        des Index; Kandidatenpaare teilen mindestens eine Zelle, O(N) erwartet."""
        errors: List[ValidationError] = []
        seen: Set[Tuple[str, str]] = set()
        for entries in index.rect_grid.values():
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    id_a, rect_a = entries[i][0], entries[i][1:]
                    id_b, rect_b = entries[j][0], entries[j][1:]
                    key = (id_a, id_b) if id_a < id_b else (id_b, id_a)
                    if key in seen:
                        continue
                    seen.add(key)
                    depth = HighSpeedDRC._rect_overlap_depth(rect_a, rect_b)
                    if depth > 0.0:
                        errors.append(ValidationError(
                            rule_id=HighSpeedDRC.RULE_BODY_OVERLAP, severity="ERROR",
                            message=f"Bauteil-Bodies {key[0]} und {key[1]} überlappen "
                                    f"(bis zu {depth:.2f} mm)",
                            affected_components=[key[0], key[1]],
                            location=((rect_a[0] + rect_a[2]) / 2.0, (rect_a[1] + rect_a[3]) / 2.0),
                        ))
        return errors

    @staticmethod
    def check_board(board: BoardState, rules: Optional[DesignRules] = None) -> List[ValidationError]:
        """Board-weite DRC: Clearance zwischen Fremd-Netzen und Kollision
        von Traces mit Bauteil-Bodies. Komplett über SpatialHashIndex, O(N)."""
        rules = rules or DesignRules()
        index = SpatialHashIndex(cell_size=max(rules.min_clearance * 4, 1.0))

        for comp in board.components.values():
            if comp.body_w is not None and comp.body_h is not None:
                index.insert_rect(comp.id, (comp.x, comp.y), comp.body_w, comp.body_h)

        errors: List[ValidationError] = HighSpeedDRC._check_body_overlaps(index)

        # Komponenten, deren Pins zu einem Netz gehören, dürfen von dessen
        # Traces berührt werden (Pins liegen auf der Body-Kante).
        net_owner_comps: Dict[str, Set[str]] = {
            net_id: {comp_id for comp_id, _ in net.nodes}
            for net_id, net in board.nets.items()
        }

        for trace in board.traces:
            if trace.width < rules.min_trace_width:
                errors.append(ValidationError(
                    rule_id="DRC_TRACE_WIDTH", severity="ERROR",
                    message=f"Trace-Breite {trace.width}mm < Minimum {rules.min_trace_width}mm",
                    affected_nets=[trace.net_id],
                ))
            own_comps = net_owner_comps.get(trace.net_id, set())
            for p1, p2 in zip(trace.path, trace.path[1:]):
                for other_net in index.query_segment_clearance(trace.net_id, p1, p2, rules.min_clearance):
                    errors.append(ValidationError(
                        rule_id=HighSpeedDRC.RULE_CLEARANCE, severity="ERROR",
                        message=f"Clearance-Verstoß zwischen {trace.net_id} und {other_net} "
                                f"(< {rules.min_clearance}mm)",
                        affected_nets=[trace.net_id, other_net],
                        location=p1,
                    ))
                for comp_id in index.query_rect_violations(p1, p2):
                    if comp_id in own_comps:
                        continue
                    errors.append(ValidationError(
                        rule_id=HighSpeedDRC.RULE_COMPONENT_COLLISION, severity="ERROR",
                        message=f"Trace von {trace.net_id} schneidet Bauteil-Body {comp_id}",
                        affected_nets=[trace.net_id],
                        affected_components=[comp_id],
                        location=p1,
                    ))
                index.insert_segment(trace.net_id, p1, p2)
        return errors
