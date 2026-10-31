import pytest

from app.gamdl_safe import forced_storefront, rewrite_storefront, rng


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


def test_forced_storefront_default_and_empty_fallback():
    assert forced_storefront({}) == "jp"
    assert forced_storefront({"GAMDL_STOREFRONT": " ID "}) == "id"
    assert forced_storefront({"GAMDL_STOREFRONT": ""}) == ""  # fallback run: nothing forced
    url = "https://music.apple.com/id/album/1656674454"
    assert rewrite_storefront(url, forced_storefront({"GAMDL_STOREFRONT": ""})) == url
