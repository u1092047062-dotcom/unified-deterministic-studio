from core.solver import MCUPackage, PinMultiplexSolver, PinRequirement


def _mcu(**overrides) -> MCUPackage:
    base = dict(
        name="ESP32",
        pin_capabilities={
            1: ["SDA", "I2C"],
            2: ["SDA", "GPIO"],
            3: ["SCL", "I2C"],
            4: ["GPIO"],
        },
        strapping_pins=[2],
    )
    base.update(overrides)
    return MCUPackage(**base)


def test_basic_assignment():
    result = PinMultiplexSolver.solve_pinout(_mcu(), [
        PinRequirement(function_name="SDA", signal_type="I2C"),
        PinRequirement(function_name="SCL", signal_type="I2C"),
    ])
    assert result is not None
    assert result["SDA"] in (1, 2)
    assert result["SCL"] == 3


def test_distinctness_unsat_returns_none():
    # SDA und SCL können beide nur auf Pin 2 -> Distinct erzwingt UNSAT
    mcu = _mcu(pin_capabilities={2: ["SDA", "SCL", "I2C"]})
    result = PinMultiplexSolver.solve_pinout(mcu, [
        PinRequirement(function_name="SDA", signal_type="I2C"),
        PinRequirement(function_name="SCL", signal_type="I2C"),
    ])
    assert result is None


def test_no_valid_pin_returns_none():
    result = PinMultiplexSolver.solve_pinout(_mcu(), [
        PinRequirement(function_name="CAN_TX", signal_type="CAN"),
    ])
    assert result is None


def test_strapping_protection_is_explicit():
    # Nicht-kritisch: Pin 2 (Strapping) erlaubt.
    ok = PinMultiplexSolver.solve_pinout(_mcu(), [
        PinRequirement(function_name="SDA", signal_type="I2C"),
    ])
    assert ok is not None

    # Kritisch: Pin 2 tabu -> muss auf Pin 1 ausweichen.
    crit = PinMultiplexSolver.solve_pinout(_mcu(), [
        PinRequirement(function_name="SDA", signal_type="I2C", is_critical=True),
    ])
    assert crit is not None
    assert crit["SDA"] == 1


def test_strapping_unsat_when_only_option_blocked():
    mcu = _mcu(pin_capabilities={2: ["SDA", "I2C"]}, strapping_pins=[2])
    result = PinMultiplexSolver.solve_pinout(mcu, [
        PinRequirement(function_name="SDA", signal_type="I2C", is_critical=True),
    ])
    assert result is None


def test_voltage_constraint_excludes_pin():
    # Pin 2 verkraftet nur 1.8 V; Anforderung 3.3 V -> Pin 2 tabu.
    mcu = _mcu(pin_voltages={2: 1.8})
    result = PinMultiplexSolver.solve_pinout(mcu, [
        PinRequirement(function_name="SDA", signal_type="I2C", required_voltage=3.3),
    ])
    assert result is not None
    assert result["SDA"] == 1

    # 1.8-V-Anforderung: beide Pins zulässig.
    result2 = PinMultiplexSolver.solve_pinout(mcu, [
        PinRequirement(function_name="SDA", signal_type="I2C", required_voltage=1.8),
    ])
    assert result2 is not None
    assert result2["SDA"] in (1, 2)


def test_solver_is_deterministic():
    reqs = [PinRequirement(function_name="SDA", signal_type="I2C"),
            PinRequirement(function_name="SCL", signal_type="I2C")]
    assert PinMultiplexSolver.solve_pinout(_mcu(), reqs) == PinMultiplexSolver.solve_pinout(_mcu(), reqs)
