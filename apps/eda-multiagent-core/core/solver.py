from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from z3 import Distinct, Int, Or, Solver, sat

class PinRequirement(BaseModel):
    function_name: str
    signal_type: str
    required_voltage: float = 3.3
    is_critical: bool = False  # True -> Strapping-Pins sind tabu

class MCUPackage(BaseModel):
    name: str
    pin_capabilities: Dict[int, List[str]]
    strapping_pins: List[int] = Field(default_factory=list)
    # Maximalspannung pro Pin (Default 3.3 V, siehe Schema-Pin).
    pin_voltages: Dict[int, float] = Field(default_factory=dict)

class PinMultiplexSolver:
    @staticmethod
    def solve_pinout(mcu: MCUPackage, requirements: List[PinRequirement]) -> Optional[Dict[str, int]]:
        solver = Solver()
        z3_vars: Dict[str, Int] = {}

        for req in requirements:
            var = Int(f"pin_{req.function_name}")
            z3_vars[req.function_name] = var
            valid_pins = [
                pin_idx for pin_idx, caps in mcu.pin_capabilities.items()
                if (req.function_name in caps or req.signal_type in caps)
                and mcu.pin_voltages.get(pin_idx, 3.3) >= req.required_voltage
            ]
            if not valid_pins:
                return None
            solver.add(Or([var == p for p in valid_pins]))

        solver.add(Distinct(list(z3_vars.values())))

        for req in requirements:
            if req.is_critical:
                for strap_pin in mcu.strapping_pins:
                    solver.add(z3_vars[req.function_name] != strap_pin)

        if solver.check() == sat:
            model = solver.model()
            return {func_name: model[var].as_long() for func_name, var in z3_vars.items()}
        return None
