from restoration.tiling import plan_tiles


def test_tiles_cover_image_and_have_overlap():
    width, height = 2400, 1600
    tiles = plan_tiles(width, height, tile_size=1024, overlap=96)
    assert len(tiles) > 1
    assert min(t.x for t in tiles) == 0
    assert min(t.y for t in tiles) == 0
    assert max(t.x + t.width for t in tiles) == width
    assert max(t.y + t.height for t in tiles) == height
    assert all(t.width > 0 and t.height > 0 for t in tiles)
    assert all(t.index == i for i, t in enumerate(tiles))


def test_invalid_overlap_rejected():
    import pytest
    with pytest.raises(ValueError):
        plan_tiles(100, 100, tile_size=128, overlap=128)


def test_single_tile_for_small_image():
    tiles = plan_tiles(100, 80, tile_size=512, overlap=64)
    assert len(tiles) == 1
    assert tiles[0].width == 100 and tiles[0].height == 80
