from app.guard import Guard
from app.parser import Line, TrackDelay, TrackError


def err(cls, level="ERROR"):
    return Line(level, "x", cls)


def test_three_consecutive_rate_limits_pause():
    g = Guard(3)
    assert g.on_event(err("rate_limit")) is None
    assert g.on_event(err("rate_limit")) is None
    v = g.on_event(err("rate_limit"))
    assert v.kind == "rate_limited" and "3" in v.reason


def test_success_resets_count():
    g = Guard(3)
    g.on_event(err("rate_limit")); g.on_event(err("rate_limit"))
    g.on_event(TrackDelay(5))
    assert g.on_event(err("rate_limit")) is None
    assert g.consecutive == 1


def test_auth_pauses_immediately_with_cookie_verdict():
    assert Guard(3).on_event(err("auth")).kind == "cookies"


def test_other_and_info_lines_ignored():
    g = Guard(1)
    assert g.on_event(err("other")) is None
    assert g.on_event(Line("INFO", "x", "other")) is None


def test_reset():
    g = Guard(2)
    g.on_event(err("rate_limit"))
    g.reset()
    assert g.consecutive == 0


def test_consecutive_track_errors_give_verdict_without_text():
    g = Guard(3)
    assert g.on_event(TrackError("a")) is None
    assert g.on_event(TrackError("b")) is None
    v = g.on_event(TrackError("c"))
    assert v.kind == "rate_limited" and "3 consecutive track errors" in v.reason


def test_track_delay_resets_track_error_count():
    g = Guard(3)
    g.on_event(TrackError("a")); g.on_event(TrackError("b"))
    g.on_event(TrackDelay(5))
    assert g.on_event(TrackError("c")) is None


def test_reset_clears_track_errors():
    g = Guard(2)
    g.on_event(TrackError("a"))
    g.reset()
    assert g.on_event(TrackError("b")) is None
