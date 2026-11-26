"""Deterministischer Netlist-/DesignSpec-Synthesizer fuer eda-multiagent-core.

Wandelt strukturierte Hardware-Spezifikationen (oder kuratierte Templates)
deterministisch in ein valides core.schema.BoardState um, ohne core/schema.py
zu veraendern und ohne LLM-Geometrie-Heuristiken.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.schema import BoardState, Component, Net, Pin, PinType, Via


class PinSpec(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    pin_type: PinType = PinType.PASSIVE
    max_voltage: float = Field(default=3.3, gt=0.0, le=60.0)
    dx: Optional[float] = None
    dy: Optional[float] = None


class ComponentSpec(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=64)
    footprint: str = Field(min_length=1, max_length=64)
    value: Optional[str] = None
    body_w: float = Field(default=4.0, gt=0.2, le=100.0)
    body_h: float = Field(default=3.0, gt=0.2, le=100.0)
    x: Optional[float] = None
    y: Optional[float] = None
    rotation: float = 0.0
    pins: List[PinSpec] = Field(default_factory=list)


class NetSpec(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    nodes: List[Tuple[str, str]] = Field(default_factory=list)


class ViaSpec(BaseModel):
    net_id: str = Field(min_length=1, max_length=64)
    x: float
    y: float
    diameter: float = Field(default=0.6, ge=0.3, le=3.0)


class DesignSpec(BaseModel):
    name: str = Field(default="custom-board", min_length=1, max_length=128)
    width: float = Field(default=100.0, ge=15.0, le=500.0)
    height: float = Field(default=80.0, ge=15.0, le=500.0)
    components: List[ComponentSpec] = Field(default_factory=list)
    nets: List[NetSpec] = Field(default_factory=list)
    vias: List[ViaSpec] = Field(default_factory=list)


def synthesize_board(spec: DesignSpec) -> BoardState:
    """Konvertiert ein DesignSpec deterministisch und validiert in ein BoardState."""
    board = BoardState(width=spec.width, height=spec.height)

    seen_ids = set()
    n_comps = max(len(spec.components), 1)
    cols = max(1, math.ceil(math.sqrt(n_comps)))
    rows = max(1, math.ceil(n_comps / cols))
    margin_x = min(15.0, spec.width * 0.2)
    margin_y = min(15.0, spec.height * 0.2)
    usable_w = max(spec.width - 2.0 * margin_x, 5.0)
    usable_h = max(spec.height - 2.0 * margin_y, 5.0)

    for idx, cspec in enumerate(spec.components):
        if cspec.id in seen_ids:
            raise ValueError(f"Doppelte Komponenten-ID im DesignSpec: '{cspec.id}'")
        seen_ids.add(cspec.id)

        col = idx % cols
        row = idx // cols
        default_x = round(
            margin_x + (usable_w * (col + 0.5) / cols),
            2,
        )
        default_y = round(
            margin_y + (usable_h * (row + 0.5) / rows),
            2,
        )
        cx = float(cspec.x) if cspec.x is not None else default_x
        cy = float(cspec.y) if cspec.y is not None else default_y
        if not (0.0 <= cx <= spec.width and 0.0 <= cy <= spec.height):
            raise ValueError(
                f"Komponente '{cspec.id}' liegt ausserhalb der Board-Grenzen ({spec.width}x{spec.height})"
            )

        pins: Dict[str, Pin] = {}
        pin_count = max(len(cspec.pins), 1)
        for p_idx, pspec in enumerate(cspec.pins):
            if pspec.id in pins:
                raise ValueError(f"Doppelte Pin-ID '{pspec.id}' bei Komponente '{cspec.id}'")
            if pspec.dx is not None and pspec.dy is not None:
                px = round(cx + pspec.dx, 3)
                py = round(cy + pspec.dy, 3)
            else:
                # Deterministische Pin-Verteilung entlang linker/rechter Gehaeusekante
                side = -1.0 if p_idx < math.ceil(pin_count / 2) else 1.0
                side_idx = p_idx if side < 0 else p_idx - math.ceil(pin_count / 2)
                side_total = math.ceil(pin_count / 2) if side < 0 else max(1, pin_count - math.ceil(pin_count / 2))
                offset_y = ((side_idx + 0.5) / side_total - 0.5) * cspec.body_h
                px = round(cx + side * (cspec.body_w / 2.0), 3)
                py = round(cy + offset_y, 3)
            pins[pspec.id] = Pin(
                id=pspec.id,
                name=pspec.name,
                pin_type=pspec.pin_type,
                max_voltage=pspec.max_voltage,
                x=px,
                y=py,
            )

        board.components[cspec.id] = Component(
            id=cspec.id,
            name=cspec.name,
            category=cspec.category,
            footprint=cspec.footprint,
            value=cspec.value,
            x=cx,
            y=cy,
            rotation=cspec.rotation,
            body_w=cspec.body_w,
            body_h=cspec.body_h,
            pins=pins,
        )

    for nspec in spec.nets:
        if nspec.id in board.nets:
            raise ValueError(f"Doppelte Netz-ID im DesignSpec: '{nspec.id}'")
        for comp_id, pin_id in nspec.nodes:
            if comp_id not in board.components:
                raise ValueError(f"Netz '{nspec.id}' referenziert unbekannte Komponente '{comp_id}'")
            if pin_id not in board.components[comp_id].pins:
                raise ValueError(
                    f"Netz '{nspec.id}' referenziert unbekannten Pin '{comp_id}.{pin_id}'"
                )
        board.nets[nspec.id] = Net(id=nspec.id, name=nspec.name, nodes=list(nspec.nodes))

    for vspec in spec.vias:
        if vspec.net_id not in board.nets:
            raise ValueError(f"Via referenziert unbekanntes Netz '{vspec.net_id}'")
        if not (0.0 <= vspec.x <= spec.width and 0.0 <= vspec.y <= spec.height):
            raise ValueError("Via liegt ausserhalb der Board-Grenzen")
        board.vias.append(
            Via(net_id=vspec.net_id, x=vspec.x, y=vspec.y, diameter=vspec.diameter)
        )

    return board


def _template_esp32_i2c_sensor() -> DesignSpec:
    return DesignSpec(
        name="esp32_i2c_sensor",
        width=100.0,
        height=80.0,
        components=[
            ComponentSpec(
                id="U1",
                name="ESP32",
                category="MCU",
                footprint="QFN-32",
                value="ESP32-WROOM-32E",
                x=28.0,
                y=40.0,
                body_w=6.0,
                body_h=6.0,
                pins=[
                    PinSpec(id="1", name="GPIO21_SDA", pin_type=PinType.OUTPUT, dx=3.0, dy=2.0),
                    PinSpec(id="2", name="GPIO22_SCL", pin_type=PinType.OUTPUT, dx=3.0, dy=0.0),
                    PinSpec(id="3", name="GND", pin_type=PinType.GROUND, dx=3.0, dy=-2.0),
                ],
            ),
            ComponentSpec(
                id="R1",
                name="R_SDA",
                category="Resistor",
                footprint="0805",
                value="10k",
                x=68.0,
                y=46.0,
                body_w=2.0,
                body_h=1.25,
                pins=[
                    PinSpec(id="1", name="A", pin_type=PinType.PASSIVE, dx=-1.0, dy=0.0),
                    PinSpec(id="2", name="B", pin_type=PinType.PASSIVE, dx=1.0, dy=0.0),
                ],
            ),
            ComponentSpec(
                id="R2",
                name="R_SCL",
                category="Resistor",
                footprint="0805",
                value="10k",
                x=68.0,
                y=34.0,
                body_w=2.0,
                body_h=1.25,
                pins=[
                    PinSpec(id="1", name="A", pin_type=PinType.PASSIVE, dx=-1.0, dy=0.0),
                    PinSpec(id="2", name="B", pin_type=PinType.PASSIVE, dx=1.0, dy=0.0),
                ],
            ),
            ComponentSpec(
                id="C1",
                name="C_DECOUP",
                category="Capacitor",
                footprint="0805",
                value="100nF",
                x=28.0,
                y=24.0,
                body_w=2.0,
                body_h=1.25,
                pins=[
                    PinSpec(id="1", name="P", pin_type=PinType.PASSIVE, dx=-1.0, dy=0.0),
                    PinSpec(id="2", name="N", pin_type=PinType.GROUND, dx=1.0, dy=0.0),
                ],
            ),
        ],
        nets=[
            NetSpec(id="NET_SDA", name="I2C_SDA", nodes=[("U1", "1"), ("R1", "1")]),
            NetSpec(id="NET_SCL", name="I2C_SCL", nodes=[("U1", "2"), ("R2", "1")]),
            NetSpec(id="GND", name="GND", nodes=[("U1", "3"), ("C1", "2")]),
        ],
        vias=[ViaSpec(net_id="GND", x=85.0, y=15.0)],
    )


def _template_stm32_usb_controller() -> DesignSpec:
    return DesignSpec(
        name="stm32_usb_controller",
        width=100.0,
        height=80.0,
        components=[
            ComponentSpec(
                id="U1",
                name="STM32",
                category="MCU",
                footprint="LQFP-48",
                value="STM32F103C8T6",
                x=30.0,
                y=40.0,
                body_w=7.0,
                body_h=7.0,
                pins=[
                    PinSpec(id="1", name="UART_TX", pin_type=PinType.OUTPUT, dx=3.5, dy=2.0),
                    PinSpec(id="2", name="UART_RX", pin_type=PinType.INPUT, dx=3.5, dy=-2.0),
                    PinSpec(id="3", name="GND", pin_type=PinType.GROUND, dx=-3.5, dy=0.0),
                ],
            ),
            ComponentSpec(
                id="J1",
                name="USB_UART",
                category="Connector",
                footprint="USB-C",
                value="USB-C",
                x=84.0,
                y=40.0,
                body_w=6.0,
                body_h=8.0,
                pins=[
                    PinSpec(id="1", name="TX", pin_type=PinType.INPUT, dx=-3.0, dy=2.0),
                    PinSpec(id="2", name="RX", pin_type=PinType.OUTPUT, dx=-3.0, dy=-2.0),
                ],
            ),
        ],
        nets=[
            NetSpec(id="UART_TX", name="UART_TX", nodes=[("U1", "1"), ("J1", "1")]),
            NetSpec(id="UART_RX", name="UART_RX", nodes=[("U1", "2"), ("J1", "2")]),
            NetSpec(id="GND", name="GND", nodes=[("U1", "3")]),
        ],
        vias=[ViaSpec(net_id="GND", x=15.0, y=15.0)],
    )


def _template_rp2040_power_node() -> DesignSpec:
    return DesignSpec(
        name="rp2040_power_node",
        width=80.0,
        height=60.0,
        components=[
            ComponentSpec(
                id="U1",
                name="RP2040",
                category="MCU",
                footprint="QFN-56",
                value="RP2040",
                x=25.0,
                y=30.0,
                body_w=7.0,
                body_h=7.0,
                pins=[
                    PinSpec(id="1", name="GPIO_LED", pin_type=PinType.OUTPUT, dx=3.5, dy=1.0),
                    PinSpec(id="2", name="GND", pin_type=PinType.GROUND, dx=3.5, dy=-2.0),
                ],
            ),
            ComponentSpec(
                id="R1",
                name="R_LED",
                category="Resistor",
                footprint="0805",
                value="10k",
                x=55.0,
                y=31.0,
                body_w=2.0,
                body_h=1.25,
                pins=[
                    PinSpec(id="1", name="A", pin_type=PinType.PASSIVE, dx=-1.0, dy=0.0),
                    PinSpec(id="2", name="B", pin_type=PinType.PASSIVE, dx=1.0, dy=0.0),
                ],
            ),
        ],
        nets=[
            NetSpec(id="LED_SIG", name="LED_SIG", nodes=[("U1", "1"), ("R1", "1")]),
            NetSpec(id="GND", name="GND", nodes=[("U1", "2")]),
        ],
        vias=[ViaSpec(net_id="GND", x=68.0, y=12.0)],
    )


BOARD_TEMPLATES = {
    "esp32_i2c_sensor": _template_esp32_i2c_sensor,
    "stm32_usb_controller": _template_stm32_usb_controller,
    "rp2040_power_node": _template_rp2040_power_node,
}


def list_templates() -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    for key in sorted(BOARD_TEMPLATES.keys()):
        spec = BOARD_TEMPLATES[key]()
        items.append(
            {
                "id": key,
                "width_mm": spec.width,
                "height_mm": spec.height,
                "components": len(spec.components),
                "nets": len(spec.nets),
            }
        )
    return items


def build_template_board(template_id: str) -> BoardState:
    factory = BOARD_TEMPLATES.get(template_id.strip().lower())
    if factory is None:
        available = ", ".join(sorted(BOARD_TEMPLATES.keys()))
        raise ValueError(f"Unbekanntes Board-Template '{template_id}'. Verfuegbar: {available}")
    return synthesize_board(factory())
