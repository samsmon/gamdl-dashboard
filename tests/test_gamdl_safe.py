import pytest

from app.gamdl_safe import rewrite_storefront, rng


def test_storefront_rewrite():
    assert rewrite_storefront("https://music.apple.com/id/album/x/1", "jp") == "https://music.apple.com/jp/album/x/1"
    assert rewrite_storefront("https://classical.music.apple.com/us/album/1", "jp") == "https://classical.music.apple.com/jp/album/1"


def test_storefront_disabled_or_library_url_untouched():
    assert rewrite_storefront("https://music.apple.com/id/album/x/1", "") == "https://music.apple.com/id/album/x/1"
    assert rewrite_storefront("https://music.apple.com/library/albums/l.abc", "jp") == "https://music.apple.com/library/albums/l.abc"


def test_rng_default_and_env():
    assert rng("GAMDL_TRACK_DELAY", "8-20", {}) == (8.0, 20.0)
    assert rng("GAMDL_TRACK_DELAY", "8-20", {"GAMDL_TRACK_DELAY": "1.5-3"}) == (1.5, 3.0)
    with pytest.raises(ValueError):
        rng("X", "bad", {})
