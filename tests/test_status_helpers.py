import os

from app.cookies import cookie_status
from app.disk import free_bytes
from app.forecast import DEFAULT_TRACK_BYTES, avg_track_bytes, estimate_bytes, queue_bytes

NOW = 2_000_000_000.0


def write_cookies(tmp_path, *expiries, extra=""):
    lines = ["# Netscape HTTP Cookie File"]
    for i, e in enumerate(expiries):
        lines.append(f".apple.com\tTRUE\t/\tTRUE\t{e}\tname{i}\tSECRET-VALUE-{i}")
    lines.append(f".example.com\tTRUE\t/\tTRUE\t{int(NOW) + 5}\tother\tzzz")
    lines.append(extra)
    p = tmp_path / "cookies.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def test_missing_file(tmp_path):
    s = cookie_status(str(tmp_path / "nope.txt"), NOW)
    assert s == {"exists": False, "age_days": None, "expiry_days": None, "expired": False}


def test_earliest_expiry_only_apple_and_no_secrets(tmp_path):
    p = write_cookies(tmp_path, int(NOW) + 10 * 86400, int(NOW) + 3 * 86400)
    os.utime(p, (NOW - 86400, NOW - 86400))
    s = cookie_status(p, NOW)
    assert s["exists"] and s["expiry_days"] == 3 and not s["expired"]
    assert round(s["age_days"]) == 1
    assert "SECRET" not in repr(s)


def test_token_must_be_media_user_token_on_music_domain(tmp_path):
    good = write_cookies(tmp_path, extra=f".music.apple.com\tTRUE\t/\tTRUE\t{int(NOW)+86400}\tmedia-user-token\tTOK")
    s = cookie_status(good, NOW)
    assert s["has_token"] and s["problem"] is None and "TOK" not in repr(s)
    wrong_domain = write_cookies(tmp_path, extra=f".apple.com\tTRUE\t/\tTRUE\t{int(NOW)+86400}\tmedia-user-token\tTOK")
    s = cookie_status(wrong_domain, NOW)
    assert not s["has_token"] and "media-user-token" in s["problem"]
    other = write_cookies(tmp_path, int(NOW) + 86400)  # e.g. a Navidrome/browser cookie export, no Apple token
    assert not cookie_status(other, NOW)["has_token"]


def test_httponly(tmp_path):
    p = write_cookies(tmp_path, extra=f"#HttpOnly_.apple.com\tTRUE\t/\tTRUE\t{int(NOW)+86400*30}\tx\ty")
    s = cookie_status(p, NOW)
    assert s["expiry_days"] == 30 and not s["expired"]


def test_session_cookie(tmp_path):
    # Session cookie with expiry 0 should not set earliest; future expiry should be used
    p = write_cookies(tmp_path, int(NOW) + 86400 * 5, 0)
    s = cookie_status(p, NOW)
    assert s["expiry_days"] == 5 and not s["expired"]


def test_expired(tmp_path):
    p = write_cookies(tmp_path, int(NOW) - 100)
    s = cookie_status(p, NOW)
    assert s["expired"] is True and s["expiry_days"] < 0


def test_evilapple_com_ignored(tmp_path):
    lines = ["# Netscape HTTP Cookie File"]
    lines.append(f".evilapple.com\tTRUE\t/\tTRUE\t{int(NOW) + 86400}\tname\tvalue")
    lines.append(f".apple.com\tTRUE\t/\tTRUE\t{int(NOW) + 86400 * 10}\treal\tvalue")
    p = tmp_path / "cookies.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    s = cookie_status(str(p), NOW)
    assert s["expiry_days"] == 10


def test_disk_free_and_bad_path(tmp_path):
    assert free_bytes(str(tmp_path)) > 0
    assert free_bytes(str(tmp_path / "does" / "not" / "exist")) is None


def add_done(store, size, tracks):
    i = store.add_item("u", "o", "album", str(size))
    store.update_item(i, status="done", size_bytes=size, track_n=tracks)


def test_forecast_uses_history_then_default(store):
    assert avg_track_bytes(store) == DEFAULT_TRACK_BYTES
    add_done(store, 100_000_000, 10)
    assert avg_track_bytes(store) == 10_000_000
    q = store.add_item("u", "o", "album", "9", expected_tracks=12)
    assert estimate_bytes(store, store.get_item(q)) == 120_000_000
    assert queue_bytes(store) == 120_000_000


def test_forecast_unknown_track_count(store):
    q = store.add_item("u", "o", "album", "9")
    assert estimate_bytes(store, store.get_item(q)) == 10 * DEFAULT_TRACK_BYTES
