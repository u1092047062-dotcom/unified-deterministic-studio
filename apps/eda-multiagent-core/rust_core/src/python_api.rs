//! PyO3-Bindings: dünner Wrapper um die reine A*-Logik (astar.rs).
//! Nur mit dem Feature `python` kompiliert (Standard).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::astar;

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn route_negotiated_fast(
    nx: usize,
    ny: usize,
    sx: usize,
    sy: usize,
    tx: usize,
    ty: usize,
    obstacles: Vec<u8>,
    occ_count: Vec<u8>,
    occ_self: Vec<u8>,
    history_bytes: Vec<u8>,
    p_fac: f64,
    via_cost: f64,
) -> PyResult<Vec<(u32, u32, u32)>> {
    if history_bytes.len() % 8 != 0 {
        return Err(PyValueError::new_err(
            "history-Buffer muss ein Vielfaches von 8 Bytes (f64, LE) sein",
        ));
    }
    let history: Vec<f64> = history_bytes
        .chunks_exact(8)
        .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
        .collect();
    astar::route_negotiated(
        nx, ny, sx, sy, tx, ty, &obstacles, &occ_count, &occ_self, &history, p_fac, via_cost,
    )
    .map_err(PyValueError::new_err)
}

#[pymodule]
fn core_fast(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(route_negotiated_fast, m)?)?;
    Ok(())
}
