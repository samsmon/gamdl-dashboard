import pytest

from app.settings import DEFAULTS, check_merged, parse_range, validate


def test_defaults_present():
    assert DEFAULTS["track_delay"] == "8-20"
    assert DEFAULTS["album_delay"] == "60-180"
    assert DEFAULTS["error_threshold"] == 3
    assert DEFAULTS["max_tracks_per_24h"] == 150
    assert DEFAULTS["storefront"] == "jp"
    assert DEFAULTS["library_exact"] == 0.85 and DEFAULTS["library_similar"] == 0.55
    assert DEFAULTS["track_retries"] == 2 and DEFAULTS["retry_backoff"] == "120-300"


def test_empty_storefront_allowed_and_cross_field_rule():
    assert validate({"storefront": ""}) == {"storefront": ""}
    with pytest.raises(ValueError):
        check_merged({**DEFAULTS, "library_similar": 0.9, "library_exact": 0.8})
    check_merged(DEFAULTS)


def test_parse_range():
    assert parse_range("8-20") == (8.0, 20.0)


@pytest.mark.parametrize("patch", [
    {"track_delay": "2-4"},          # below 5 s floor
    {"album_delay": "10-20"},        # below 30 s floor
    {"track_delay": "20-8"},         # min > max
    {"track_delay": "abc"},
    {"error_threshold": 0},
    {"storefront": "japan"},
    {"max_tracks_per_24h": -1},
    {"retry_backoff": "10-20"},      # below 30 s floor
    {"track_retries": 99},
    {"library_exact": 1.5},
    {"unknown_key": 1},
])
def test_rejects(patch):
    with pytest.raises(ValueError):
        validate(patch)


def test_accepts_and_cleans():
    out = validate({"track_delay": " 10-30 ", "error_threshold": "5", "storefront": "JP", "auto_resume_after_cap": True})
    assert out == {"track_delay": "10-30", "error_threshold": 5, "storefront": "jp", "auto_resume_after_cap": True}
