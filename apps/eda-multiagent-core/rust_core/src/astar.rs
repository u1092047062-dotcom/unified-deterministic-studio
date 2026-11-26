//! Reine A*-Logik (ohne PyO3): von der Python-Erweiterung und der
//! Tauri-Desktop-App (src-tauri) gleichermaßen genutzt.
//!
//! Datenlayout: Knotenindex = (layer * ny + y) * nx + x (flach, 1D).
//! - `obstacles`: 1D-Bitset, (total + 7) / 8 Bytes, Bit i = Zelle i blockiert.
//! - `occ_count`: u8 je Zelle — Anzahl belegenden Netze.
//! - `occ_self`:  u8 je Zelle — 1, wenn das zu routende Netz die Zelle belegt.
//!   Sharing (fremde Netze) = occ_count - occ_self.
//! - `history`:   f64 je Zelle (Verhandlungsgedächtnis).

use std::cmp::{Ordering, Reverse};
use std::collections::BinaryHeap;

const SQRT2: f64 = std::f64::consts::SQRT_2;

#[derive(Clone, Copy, PartialEq)]
struct HeapNode {
    f: f64,
    cost: f64,
    x: u32,
    y: u32,
    layer: u32,
}

impl Eq for HeapNode {}

impl Ord for HeapNode {
    fn cmp(&self, other: &Self) -> Ordering {
        // Identisch zur Python-Heap-Ordnung (f, cost, x, y, layer).
        self.f
            .total_cmp(&other.f)
            .then_with(|| self.cost.total_cmp(&other.cost))
            .then(self.x.cmp(&other.x))
            .then(self.y.cmp(&other.y))
            .then(self.layer.cmp(&other.layer))
    }
}

impl PartialOrd for HeapNode {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Negotiated-A* auf dem flachen Gitter. Fehler bei passenden Pufferlängen.
pub fn route_negotiated(
    nx: usize,
    ny: usize,
    sx: usize,
    sy: usize,
    tx: usize,
    ty: usize,
    obstacles: &[u8],
    occ_count: &[u8],
    occ_self: &[u8],
    history: &[f64],
    p_fac: f64,
    via_cost: f64,
) -> Result<Vec<(u32, u32, u32)>, String> {
    let layers = 2usize;
    let total = nx * ny * layers;
    if obstacles.len() != (total + 7) / 8
        || occ_count.len() != total
        || occ_self.len() != total
        || history.len() != total
    {
        return Err("Pufferlängen passen nicht zum Gitter (nx * ny * 2)".to_string());
    }
    if sx >= nx || sy >= ny || tx >= nx || ty >= ny {
        return Ok(Vec::new());
    }

    let idx = |x: usize, y: usize, l: usize| -> usize { (l * ny + y) * nx + x };
    let is_obstacle = |x: usize, y: usize, l: usize| -> bool {
        let i = idx(x, y, l);
        obstacles[i >> 3] & (1u8 << (i & 7)) != 0
    };
    // Fremd-Netze auf einer Zelle = Belegungen minus eigenes Netz.
    let sharing = |i: usize| -> f64 { (occ_count[i] as f64) - (occ_self[i] as f64) };

    let start_i = idx(sx, sy, 0);
    let mut g = vec![f64::INFINITY; total];
    let mut came = vec![-1i64; total];
    let mut heap: BinaryHeap<Reverse<HeapNode>> = BinaryHeap::new();
    g[start_i] = 0.0;
    heap.push(Reverse(HeapNode {
        f: 0.0,
        cost: 0.0,
        x: sx as u32,
        y: sy as u32,
        layer: 0,
    }));

    while let Some(Reverse(node)) = heap.pop() {
        let x = node.x as usize;
        let y = node.y as usize;
        let layer = node.layer as usize;
        let c_cost = node.cost;

        if (x, y) == (tx, ty) {
            // Pfad rekonstruieren; Startknoten liegt immer auf Layer 0.
            let mut path: Vec<(u32, u32, u32)> = Vec::new();
            let mut cur = idx(x, y, layer) as i64;
            while cur != start_i as i64 && cur >= 0 {
                let l = (cur as usize) / (nx * ny);
                let rem = (cur as usize) % (nx * ny);
                // idx = (l * ny + y) * nx + x  ->  x = rem % nx, y = rem / nx
                path.push(((rem % nx) as u32, (rem / nx) as u32, l as u32));
                cur = came[cur as usize];
            }
            path.push((sx as u32, sy as u32, 0));
            path.reverse();
            return Ok(path);
        }

        // Orthogonale und diagonale Nachbarzüge (wie Python-Reihenfolge).
        for (dx, dy) in [
            (1i64, 0i64),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (-1, 1),
            (1, -1),
            (-1, -1),
        ] {
            let nx_i = x as i64 + dx;
            let ny_i = y as i64 + dy;
            if nx_i < 0 || ny_i < 0 || nx_i >= nx as i64 || ny_i >= ny as i64 {
                continue;
            }
            let nxi = nx_i as usize;
            let nyi = ny_i as usize;
            if is_obstacle(nxi, nyi, layer) {
                continue;
            }
            let diagonal = dx != 0 && dy != 0;
            // Konservative Diagonale: beide Seitzen frei (kein Eckenschneiden),
            // Congestion/History über {Ziel, Seite A, Seite B} (max).
            let cells: [(usize, usize, usize); 3] = if diagonal {
                [(nxi, nyi, layer), (nxi, y, layer), (x, nyi, layer)]
            } else {
                [(nxi, nyi, layer), (nxi, nyi, layer), (nxi, nyi, layer)]
            };
            let mut sharing_max = f64::NEG_INFINITY;
            let mut hist_max = f64::NEG_INFINITY;
            let mut all_free = true;
            for &(cx, cy, cl) in &cells {
                if is_obstacle(cx, cy, cl) {
                    all_free = false;
                    break;
                }
                let i = idx(cx, cy, cl);
                sharing_max = sharing_max.max(sharing(i));
                hist_max = hist_max.max(history[i]);
            }
            if !all_free {
                continue;
            }
            let node_i = idx(nxi, nyi, layer);
            let step = if diagonal { SQRT2 } else { 1.0 };
            let new_cost = c_cost + step + hist_max + sharing_max * p_fac;
            if new_cost < g[node_i] {
                g[node_i] = new_cost;
                let h = (nxi as f64 - tx as f64).hypot(nyi as f64 - ty as f64);
                heap.push(Reverse(HeapNode {
                    f: new_cost + h,
                    cost: new_cost,
                    x: nxi as u32,
                    y: nyi as u32,
                    layer: layer as u32,
                }));
                came[node_i] = idx(x, y, layer) as i64;
            }
        }

        // Via-Wechsel auf die andere Lage.
        let other = 1 - layer;
        if !is_obstacle(x, y, other) {
            let via_i = idx(x, y, other);
            let via_total = c_cost + via_cost + sharing(via_i) * p_fac;
            if via_total < g[via_i] {
                g[via_i] = via_total;
                let h = (x as f64 - tx as f64).hypot(y as f64 - ty as f64);
                heap.push(Reverse(HeapNode {
                    f: via_total + h,
                    cost: via_total,
                    x: x as u32,
                    y: y as u32,
                    layer: other as u32,
                }));
                came[via_i] = idx(x, y, layer) as i64;
            }
        }
    }

    Ok(Vec::new())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn straight_line_open_field() {
        let total = 8 * 8 * 2;
        let path = route_negotiated(
            8,
            8,
            1,
            4,
            6,
            4,
            &vec![0u8; (total + 7) / 8],
            &vec![0u8; total],
            &vec![0u8; total],
            &vec![0.0; total],
            0.5,
            5.0,
        )
        .expect("puffer ok");
        assert_eq!(path.first(), Some(&(1, 4, 0)));
        assert_eq!(path.last(), Some(&(6, 4, 0)));
        assert_eq!(path.len(), 6); // gerade Strecke
    }

    #[test]
    fn buffer_mismatch_is_error() {
        let err = route_negotiated(
            4,
            4,
            0,
            0,
            3,
            3,
            &[0u8; 2], // zu kurz
            &vec![0u8; 32],
            &vec![0u8; 32],
            &vec![0.0; 32],
            0.5,
            5.0,
        );
        assert!(err.is_err());
    }

    #[test]
    fn out_of_bounds_terminals_are_empty() {
        let total = 4 * 4 * 2;
        let path = route_negotiated(
            4,
            4,
            0,
            0,
            9,
            9,
            &vec![0u8; (total + 7) / 8],
            &vec![0u8; total],
            &vec![0u8; total],
            &vec![0.0; total],
            0.5,
            5.0,
        )
        .expect("puffer ok");
        assert!(path.is_empty());
    }
}
