"""Tests fuer Hardware-as-Code Z3-Beweise, IPC-2152/Crosstalk/S-Param-Physik & KiCad 9 IPC (Phase 7)."""

from __future__ import annotations

from core.board_synthesizer import build_template_board
from core.em_thermal_sim import generate_openems_fdtd_script, simulate_board_physics
from core.hardware_as_code import compile_hac_to_board
from core.kicad9_ipc import build_kicad9_ipc_sync_payload, detect_kicad9_ipc
from core.schema import BoardState, Trace


VALID_HAC = """
board sensor_node w=55 h=35
rail VCC_3V3 v=3.3 imax_ma=500
part U1 name=ESP32 fp=QFN-32 rail=VCC_3V3 vmin=3.0 vmax=3.6 i_ma=180 pins=VCC,GND,SDA,SCL
part U2 name=BME280 fp=LGA-8 rail=VCC_3V3 vmin=1.8 vmax=3.6 i_ma=5 pins=VCC,GND,SDA,SCL
net I2C_SDA connect=U1.SDA,U2.SDA
net I2C_SCL connect=U1.SCL,U2.SCL
"""

CONFLICT_HAC = """
board bad_voltage w=55 h=35
rail VCC_5V v=5.0 imax_ma=500
rail VCC_3V3 v=3.3 imax_ma=200
part U1 name=ARDUINO_5V fp=TQFP-32 rail=VCC_5V vmin=4.5 vmax=5.5 i_ma=50 pins=VCC,GND,TX
part U2 name=ESP32_3V3 fp=QFN-32 rail=VCC_3V3 vmin=3.0 vmax=3.6 i_ma=180 pins=VCC,GND,RX
net UART_DIRECT connect=U1.TX,U2.RX
"""


def test_hac_z3_proves_valid_circuit_and_rejects_5v_to_3v3_conflict():
    ok = compile_hac_to_board(VALID_HAC)
    assert ok["compiled"] is True
    assert ok["proof"]["sat"] is True
    assert set(ok["board"].components.keys()) == {"U1", "U2"}
    assert "I2C_SDA" in ok["board"].nets

    bad = compile_hac_to_board(CONFLICT_HAC)
    assert bad["compiled"] is False
    assert bad["proof"]["sat"] is False
    assert any("Level-Shifter" in d for d in bad["proof"]["diagnostics"])


def test_physics_sim_detects_thermal_hotspot_and_parallel_crosstalk():
    board = BoardState(width=60.0, height=40.0)
    # Duenne 0.15mm Leiterbahn mit 3.5A -> garantierter IPC-2152-Hotspot!
    # Plus zwei eng parallele Signalleitungen (0.15 mm Abstand ueber 20 mm) -> Crosstalk!
    board.traces.append(Trace(net_id="PWR_MOTOR", layer="TOP", width=0.15, path=[(5.0, 5.0), (45.0, 5.0)]))
    board.traces.append(Trace(net_id="CLK_A", layer="TOP", width=0.2, path=[(10.0, 20.0), (35.0, 20.0)]))
    board.traces.append(Trace(net_id="DATA_B", layer="TOP", width=0.2, path=[(10.0, 20.15), (35.0, 20.15)]))

    report = simulate_board_physics(board, net_currents_a={"PWR_MOTOR": 3.5, "CLK_A": 0.02, "DATA_B": 0.02})
    assert report["passed"] is False
    assert report["hotspot_count"] == 1
    assert report["crosstalk_violation_count"] >= 1
    assert len(report["s_parameter_sweep"]) == 6
    assert "openEMS" in generate_openems_fdtd_script(board)


def test_kicad9_ipc_bridge_and_sync_payload():
    board = build_template_board("esp32_i2c_sensor")
    ipc_info = detect_kicad9_ipc()
    assert "Protocol Buffers" in ipc_info["ipc_transport"]
    payload = build_kicad9_ipc_sync_payload(board)
    assert payload["protocol"] == "kicad.ipc.v9.board_sync"
    assert len(payload["footprints"]) == len(board.components)
