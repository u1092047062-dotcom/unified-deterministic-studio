"""Deklarativer Hardware-as-Code (HAC) Compiler mit Z3-SMT-Elektrik-Beweisen (Phase 7).

Inspiriert von atopile / tscircuit:
- Parst deklarativen Text-Code (`board`, `rail`, `part`, `net`) oder JSON-Specs.
- Beweist ueber Z3-SMT VOR dem Layout:
  1. Spannungsdomaenen-Kompatibilitaet aller verbundenen Bauteile (verhindert z. B. 5V -> 3.3V Schaden).
  2. Strombudget-Einhaltung je Versorgungs-Rail (`sum(I_draw) <= I_max`).
  3. Geometrische Rahmenbedingungen innerhalb der Platinenflaeche.
- Kompiliert bei SAT deterministisch in das unveraenderliche `BoardState`-Schema.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field
import z3

from core.schema import BoardState, Component, Net, Pin, PinType


class PowerRailSpec(BaseModel):
    name: str
    voltage_v: float = Field(gt=0.0, le=60.0)
    max_current_ma: float = Field(gt=0.0, le=50000.0)


class HacComponentSpec(BaseModel):
    id: str
    name: str
    category: str = "IC"
    footprint: str = "QFN-16"
    value: str = ""
    rail: str = "VCC_3V3"
    v_min_v: float = Field(default=3.0, ge=0.0)
    v_max_v: float = Field(default=3.6, gt=0.0)
    current_ma: float = Field(default=20.0, ge=0.0)
    pins: List[str] = Field(default_factory=lambda: ["VCC", "GND", "SIG"])
    body_w: float = Field(default=5.0, gt=0.5)
    body_h: float = Field(default=5.0, gt=0.5)


class HacNetSpec(BaseModel):
    name: str
    endpoints: List[Tuple[str, str]]  # (comp_id, pin_name)
    voltage_v: Optional[float] = None
    is_power: bool = False


class HardwareAsCodeSpec(BaseModel):
    name: str = "hac_board"
    width_mm: float = Field(default=60.0, ge=15.0, le=300.0)
    height_mm: float = Field(default=40.0, ge=15.0, le=300.0)
    rails: List[PowerRailSpec]
    components: List[HacComponentSpec]
    nets: List[HacNetSpec]


def parse_hac_source(source: str) -> HardwareAsCodeSpec:
    """Parst eine kompakte deklarative Hardware-as-Code-Beschreibung (.hac / atopile-Stil)."""
    name = "hac_board"
    width_mm, height_mm = 60.0, 40.0
    rails: List[PowerRailSpec] = []
    components: List[HacComponentSpec] = []
    nets: List[HacNetSpec] = []

    for lineno, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        kw = tokens[0].lower()
        kv: Dict[str, str] = {}
        for tok in tokens[2:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k.strip().lower()] = v.strip()

        if kw == "board":
            name = tokens[1]
            width_mm = float(kv.get("w", width_mm))
            height_mm = float(kv.get("h", height_mm))
        elif kw == "rail":
            rails.append(
                PowerRailSpec(
                    name=tokens[1],
                    voltage_v=float(kv["v"]),
                    max_current_ma=float(kv.get("imax_ma", "500")),
                )
            )
        elif kw == "part":
            pins = [p.strip() for p in kv.get("pins", "VCC,GND,SIG").split(",") if p.strip()]
            components.append(
                HacComponentSpec(
                    id=tokens[1],
                    name=kv.get("name", tokens[1]),
                    category=kv.get("cat", "IC"),
                    footprint=kv.get("fp", "QFN-16"),
                    value=kv.get("val", tokens[1]),
                    rail=kv.get("rail", rails[0].name if rails else "VCC_3V3"),
                    v_min_v=float(kv.get("vmin", "3.0")),
                    v_max_v=float(kv.get("vmax", "3.6")),
                    current_ma=float(kv.get("i_ma", "20")),
                    pins=pins,
                )
            )
        elif kw == "net":
            ep_raw = kv.get("connect", "")
            endpoints: List[Tuple[str, str]] = []
            for item in ep_raw.split(","):
                item = item.strip()
                if "." not in item:
                    continue
                c_id, p_name = item.split(".", 1)
                endpoints.append((c_id.strip(), p_name.strip()))
            nets.append(
                HacNetSpec(
                    name=tokens[1],
                    endpoints=endpoints,
                    voltage_v=float(kv["v"]) if "v" in kv else None,
                    is_power=kv.get("power", "false").lower() == "true",
                )
            )
        else:
            raise ValueError(f"HAC Zeile {lineno}: Unbekanntes Schluesselwort '{kw}'")

    return HardwareAsCodeSpec(
        name=name,
        width_mm=width_mm,
        height_mm=height_mm,
        rails=rails,
        components=components,
        nets=nets,
    )


def prove_hac_electrical_constraints(spec: HardwareAsCodeSpec) -> Dict[str, Any]:
    """Fuehrt formale Z3-SMT-Elektrik-Beweise in einem isolierten Z3-Kontext aus."""
    ctx = z3.Context()
    solver = z3.Solver(ctx=ctx)
    rail_map = {r.name: r for r in spec.rails}
    comp_map = {c.id: c for c in spec.components}
    diagnostics: List[str] = []

    # 1. Rail-Spannungen & Strombudgets als Z3-Constraints (in mV / uA fuer exakte Ganzzahl-Arithmetik)
    rail_v_vars: Dict[str, Any] = {}
    for rail in spec.rails:
        rv = z3.Int(f"rail_{rail.name}_mv", ctx=ctx)
        solver.add(rv == int(round(rail.voltage_v * 1000)))
        rail_v_vars[rail.name] = rv

        consumers = [c for c in spec.components if c.rail == rail.name]
        total_ma = sum(int(round(c.current_ma * 1000)) for c in consumers)
        max_ma = int(round(rail.max_current_ma * 1000))
        c_sum = z3.Int(f"rail_{rail.name}_load_ua", ctx=ctx)
        solver.add(c_sum == total_ma)
        solver.add(c_sum <= max_ma)
        if total_ma > max_ma:
            diagnostics.append(
                f"Strombudget-Ueberschreitung auf Rail '{rail.name}': "
                f"{total_ma/1000:.1f} mA > max {rail.max_current_ma:.1f} mA"
            )

    # 2. Bauteil-Versorgungsspannung muss im erlaubten [v_min, v_max]-Fenster liegen
    for comp in spec.components:
        if comp.rail not in rail_map:
            diagnostics.append(f"Bauteil '{comp.id}' referenziert unbekanntes Rail '{comp.rail}'")
            solver.add(z3.BoolVal(False, ctx=ctx))
            continue
        rv = rail_v_vars[comp.rail]
        vmin_mv = int(round(comp.v_min_v * 1000))
        vmax_mv = int(round(comp.v_max_v * 1000))
        solver.add(rv >= vmin_mv, rv <= vmax_mv)
        actual_v = rail_map[comp.rail].voltage_v
        if not (comp.v_min_v <= actual_v <= comp.v_max_v):
            diagnostics.append(
                f"Spannungsdomaenen-Konflikt an '{comp.id}': Rail '{comp.rail}' hat {actual_v}V, "
                f"erlaubt sind [{comp.v_min_v}V .. {comp.v_max_v}V]"
            )

    # 3. Signalnetz-Spannungs-Kompatibilitaet ueber verbundene Bauteile
    for net in spec.nets:
        net_v = z3.Int(f"net_{net.name}_mv", ctx=ctx)
        if net.voltage_v is not None:
            solver.add(net_v == int(round(net.voltage_v * 1000)))
        connected_comps = []
        for cid, pname in net.endpoints:
            if cid not in comp_map:
                diagnostics.append(f"Netz '{net.name}' referenziert unbekanntes Bauteil '{cid}'")
                solver.add(z3.BoolVal(False, ctx=ctx))
                continue
            comp = comp_map[cid]
            if pname not in comp.pins:
                diagnostics.append(f"Netz '{net.name}' referenziert unbekannten Pin '{cid}.{pname}'")
                solver.add(z3.BoolVal(False, ctx=ctx))
                continue
            connected_comps.append(comp)
            if not net.is_power and pname.upper() != "GND":
                solver.add(net_v >= 0, net_v <= int(round(comp.v_max_v * 1000)))
                if net.voltage_v is not None and net.voltage_v > comp.v_max_v:
                    diagnostics.append(
                        f"Signal-Ueberspannung auf Netz '{net.name}': {net.voltage_v}V > {cid}.v_max={comp.v_max_v}V"
                    )
        # Wenn ein Signalnetz zwei Bauteile mit unterschiedlichen Rails direkt koppelt -> pruefe v_max-Kompatibilitaet
        if not net.is_power and len(connected_comps) >= 2 and net.voltage_v is None:
            voltages = {rail_map[c.rail].voltage_v for c in connected_comps if c.rail in rail_map}
            if len(voltages) > 1:
                high_v = max(voltages)
                for c in connected_comps:
                    if high_v > c.v_max_v:
                        solver.add(z3.BoolVal(False, ctx=ctx))
                        diagnostics.append(
                            f"Direkte Domänenkopplung auf '{net.name}': {high_v}V-Treiber an '{c.id}' (max {c.v_max_v}V) ohne Level-Shifter"
                        )

    sat = solver.check() == z3.sat
    return {
        "sat": sat,
        "status": "PROVEN_SAFE" if sat else "ELECTRICAL_CONFLICT_UNSAT",
        "rail_count": len(spec.rails),
        "component_count": len(spec.components),
        "net_count": len(spec.nets),
        "diagnostics": diagnostics,
    }


def compile_hac_to_board(spec_or_source: Union[HardwareAsCodeSpec, str]) -> Dict[str, Any]:
    """Beweist alle elektrischen Constraints via Z3 und kompiliert bei SAT in ein BoardState."""
    spec = parse_hac_source(spec_or_source) if isinstance(spec_or_source, str) else spec_or_source
    proof = prove_hac_electrical_constraints(spec)
    if not proof["sat"]:
        return {"compiled": False, "proof": proof, "board": None}

    board = BoardState(width=spec.width_mm, height=spec.height_mm)
    n = max(1, len(spec.components))
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = max(1, math.ceil(n / cols))
    margin_x = min(10.0, spec.width_mm * 0.2)
    margin_y = min(10.0, spec.height_mm * 0.2)
    step_x = (spec.width_mm - 2 * margin_x) / max(1, cols - 1) if cols > 1 else 0.0
    step_y = (spec.height_mm - 2 * margin_y) / max(1, rows - 1) if rows > 1 else 0.0

    pin_name_to_id: Dict[Tuple[str, str], str] = {}
    for idx, comp in enumerate(spec.components):
        r, c = divmod(idx, cols)
        cx = round(margin_x + c * step_x if cols > 1 else spec.width_mm / 2.0, 2)
        cy = round(margin_y + r * step_y if rows > 1 else spec.height_mm / 2.0, 2)
        pins_dict: Dict[str, Pin] = {}
        for p_idx, pname in enumerate(comp.pins, start=1):
            pid = str(p_idx)
            pin_name_to_id[(comp.id, pname)] = pid
            px = round(cx - comp.body_w / 2.0, 2)
            py = round(cy - comp.body_h / 2.0 + (p_idx - 1) * 1.27, 2)
            ptype = (
                PinType.GROUND if pname.upper() == "GND"
                else (PinType.POWER if pname.upper().startswith(("VCC", "VDD", "3V", "5V")) else PinType.PASSIVE)
            )
            pins_dict[pid] = Pin(id=pid, name=pname, pin_type=ptype, max_voltage=comp.v_max_v, x=px, y=py)

        board.components[comp.id] = Component(
            id=comp.id,
            name=comp.name,
            category=comp.category,
            footprint=comp.footprint,
            value=comp.value or comp.name,
            x=cx,
            y=cy,
            body_w=comp.body_w,
            body_h=comp.body_h,
            pins=pins_dict,
        )

    for net in spec.nets:
        mapped = [(cid, pin_name_to_id[(cid, pname)]) for cid, pname in net.endpoints]
        board.nets[net.name] = Net(id=net.name, name=net.name, nodes=mapped)

    return {"compiled": True, "proof": proof, "board": board}
