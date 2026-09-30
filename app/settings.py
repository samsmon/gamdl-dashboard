import re
import math

TRACK_FLOOR = 5.0
ALBUM_FLOOR = 30.0
RETRY_FLOOR = 30.0

DEFAULTS = {
    "track_delay": "8-20",
    "album_delay": "60-180",
    "error_threshold": 3,
    "low_disk_gb": 20,
    "max_tracks_per_24h": 150,
    "auto_resume_after_cap": False,
    "storefront": "jp",
    "preview_max_tracks": 100,
    "library_exact": 0.85,
    "library_similar": 0.55,
    "track_retries": 2,
    "retry_backoff": "120-300",
    "release_check_hours": 6,
}


def parse_range(s: str) -> tuple:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", str(s))
    if not m:
        raise ValueError(f"bad range {s!r}, expected 'min-max' seconds")
    lo, hi = float(m[1]), float(m[2])
    if lo > hi:
        raise ValueError(f"range {s!r}: min is greater than max")
    return lo, hi


def _range(key: str, floor: float):
    def check(v):
        lo, hi = parse_range(v)
        if lo < floor:
            raise ValueError(f"{key}: minimum must be at least {floor:g} s")
        if hi > 86400:
            raise ValueError(f"{key}: maximum must not exceed 86400 s")
        # Format without exponent notation so output round-trips through parse_range
        lo_str = format(lo, "f").rstrip("0").rstrip(".")
        hi_str = format(hi, "f").rstrip("0").rstrip(".")
        return f"{lo_str}-{hi_str}"
    return check


def _int(key: str, lo: int, hi: int):
    def check(v):
        if isinstance(v, bool):
            raise ValueError(f"{key}: must be an integer")
        try:
            # Accept only integers, integral floats (e.g., 3.0), and digit strings
            if isinstance(v, float):
                if v != int(v):  # Reject non-integral floats like 3.9
                    raise ValueError(f"{key}: must be an integer")
                n = int(v)
            else:
                n = int(v)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{key}: must be an integer")
        if not lo <= n <= hi:
            raise ValueError(f"{key}: must be between {lo} and {hi}")
        return n
    return check


def _bool(key: str):
    def check(v):
        if isinstance(v, bool):
            return v
        raise ValueError(f"{key}: must be true or false")
    return check


def _storefront(v):
    v = str(v).strip().lower()
    if v and not re.fullmatch(r"[a-z]{2}", v):
        raise ValueError("storefront: must be a 2-letter code, or empty to disable the rewrite")
    return v


def _score(key: str):
    def check(v):
        if isinstance(v, bool):
            raise ValueError(f"{key}: must be a number")
        try:
            n = float(v)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{key}: must be a number")
        # Check for NaN and inf
        if math.isnan(n) or math.isinf(n):
            raise ValueError(f"{key}: must be between 0 and 1")
        if not 0.0 < n <= 1.0:
            raise ValueError(f"{key}: must be between 0 and 1")
        return n
    return check


def check_merged(s: dict) -> None:
    """Cross-field rules, applied to the full settings dict after a patch is merged."""
    if not s["library_similar"] < s["library_exact"]:
        raise ValueError("library_similar must be lower than library_exact")


_RULES = {
    "library_exact": _score("library_exact"),
    "library_similar": _score("library_similar"),
    "track_retries": _int("track_retries", 0, 10),
    "retry_backoff": _range("retry_backoff", RETRY_FLOOR),
    "track_delay": _range("track_delay", TRACK_FLOOR),
    "album_delay": _range("album_delay", ALBUM_FLOOR),
    "error_threshold": _int("error_threshold", 1, 20),
    "low_disk_gb": _int("low_disk_gb", 0, 2000),
    "max_tracks_per_24h": _int("max_tracks_per_24h", 0, 5000),
    "auto_resume_after_cap": _bool("auto_resume_after_cap"),
    "storefront": _storefront,
    "preview_max_tracks": _int("preview_max_tracks", 1, 5000),
    "release_check_hours": _int("release_check_hours", 1, 168),
}


def validate(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k not in _RULES:
            raise ValueError(f"unknown setting: {k}")
        out[k] = _RULES[k](v)
    return out
