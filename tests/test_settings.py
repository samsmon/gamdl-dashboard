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


# Hardening tests
def test_bool_rejected_for_int_fields():
    """Booleans should be rejected for integer fields, not silently converted."""
    with pytest.raises(ValueError, match="must be an integer"):
        validate({"error_threshold": True})
    with pytest.raises(ValueError, match="must be an integer"):
        validate({"error_threshold": False})
    with pytest.raises(ValueError, match="must be an integer"):
        validate({"track_retries": True})


def test_bool_rejected_for_score_fields():
    """Booleans should be rejected for score fields, not silently converted."""
    with pytest.raises(ValueError, match="must be a number"):
        validate({"library_exact": True})
    with pytest.raises(ValueError, match="must be a number"):
        validate({"library_exact": False})


def test_non_integral_float_rejected():
    """Non-integral floats like 3.9 should be rejected, but 3.0 accepted."""
    with pytest.raises(ValueError, match="must be an integer"):
        validate({"error_threshold": 3.9})
    # 3.0 should be accepted (integral float)
    assert validate({"error_threshold": 3.0}) == {"error_threshold": 3}


def test_inf_nan_rejected():
    """Infinity and NaN should be rejected with ValueError, not OverflowError."""
    with pytest.raises(ValueError, match="must be between 0 and 1"):
        validate({"library_exact": float("inf")})
    with pytest.raises(ValueError, match="must be between 0 and 1"):
        validate({"library_exact": float("-inf")})
    with pytest.raises(ValueError, match="must be between 0 and 1"):
        validate({"library_exact": float("nan")})


def test_range_round_trip():
    """Range output from validate should be parseable by parse_range."""
    result = validate({"track_delay": "10-3600"})
    lo, hi = parse_range(result["track_delay"])
    assert lo == 10.0 and hi == 3600.0


def test_range_upper_bound():
    """Ranges exceeding 86400 s should be rejected."""
    with pytest.raises(ValueError, match="must not exceed 86400"):
        validate({"track_delay": "10-10000000"})


def test_range_boundary_at_floors():
    """Ranges at exactly the floor values should be accepted."""
    assert validate({"track_delay": "5-5"}) == {"track_delay": "5-5"}
    assert validate({"album_delay": "30-30"}) == {"album_delay": "30-30"}
    assert validate({"retry_backoff": "30-30"}) == {"retry_backoff": "30-30"}


def test_range_below_floor_boundary():
    """Ranges slightly below the floor should be rejected."""
    with pytest.raises(ValueError, match="minimum must be at least 5"):
        validate({"track_delay": "4.9-9"})
    with pytest.raises(ValueError, match="minimum must be at least 30"):
        validate({"album_delay": "29.9-40"})
