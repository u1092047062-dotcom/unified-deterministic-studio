from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class Tile:
    index: int
    x: int
    y: int
    width: int
    height: int
    overlap_left: int = 0
    overlap_top: int = 0
    overlap_right: int = 0
    overlap_bottom: int = 0


def _positions(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    stride = tile_size - overlap
    # Keep the requested overlap constant; the final tile may be smaller rather
    # than shifting a full-size tile backwards and creating a huge duplicate area.
    return list(range(0, length, stride))


def plan_tiles(width: int, height: int, tile_size: int = 1024, overlap: int = 96) -> list[Tile]:
    """Plan full-coverage tiles. `overlap` is the actual shared border width."""
    if width <= 0 or height <= 0 or tile_size <= 0:
        raise ValueError("Bildabmessungen und Kachelgröße müssen positiv sein")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("Überlappung muss >= 0 und kleiner als tile_size sein")
    xs = _positions(width, tile_size, overlap)
    ys = _positions(height, tile_size, overlap)
    tiles: list[Tile] = []
    for y in ys:
        for x in xs:
            w, h = min(tile_size, width - x), min(tile_size, height - y)
            left = max(0, (xs[xs.index(x) - 1] + tile_size - x)) if xs.index(x) > 0 else 0
            top = max(0, (ys[ys.index(y) - 1] + tile_size - y)) if ys.index(y) > 0 else 0
            right = max(0, (x + w - (xs[xs.index(x) + 1] if xs.index(x) + 1 < len(xs) else width))) if xs.index(x) + 1 < len(xs) else 0
            bottom = max(0, (y + h - (ys[ys.index(y) + 1] if ys.index(y) + 1 < len(ys) else height))) if ys.index(y) + 1 < len(ys) else 0
            tiles.append(Tile(len(tiles), x, y, w, h, left, top, right, bottom))
    return tiles
