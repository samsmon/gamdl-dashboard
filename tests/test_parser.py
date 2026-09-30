from app.parser import (AlbumDelay, Finished, Line, LineSplitter, Progress, TrackDelay,
                        TrackError, TrackSkip, TrackStart, UrlStart, parse_line)


def kinds(evs):
    return [type(e).__name__ for e in evs]


def test_track_start_with_ansi():
    evs = parse_line('\x1b[32m[INFO     19:03:47]\x1b[0m [Track   1/17 ] Downloading "The City Where Whales Fall"')
    ts = evs[0]
    assert isinstance(ts, TrackStart)
    assert (ts.i, ts.total, ts.title) == (1, 17, "The City Where Whales Fall")


def test_skip():
    evs = parse_line('[WARNING  19:04:00] [Track   6/17 ] Skipping "End Roll": file exists')
    s = evs[0]
    assert isinstance(s, TrackSkip) and s.reason == "file exists" and s.i == 6


def test_url_start_and_finished():
    u = parse_line('[INFO     19:00:00] URL   1/3  Processing "https://music.apple.com/jp/album/1"')[0]
    assert isinstance(u, UrlStart) and (u.n, u.total) == (1, 3)
    f = parse_line("[INFO     19:08:12] Finished with 2 error(s)")[0]
    assert isinstance(f, Finished) and f.errors == 2


def test_delays():
    assert parse_line("[gamdl-safe] track delay 5s")[0] == TrackDelay(5)
    assert parse_line("[gamdl-safe] album delay 87s")[0] == AlbumDelay(87)


def test_progress_only_progress_event():
    evs = parse_line("[download]  4.4% of ~ 22.57KiB at 8.49KiB/s (frag 0/23)")
    assert kinds(evs) == ["Progress"]
    assert evs[0].pct == 4.4 and evs[0].size == "22.57KiB" and evs[0].speed == "8.49KiB/s"


def test_progress_without_speed():
    evs = parse_line("[download] 100% of 5.00MiB")
    assert evs[0].pct == 100.0 and evs[0].speed is None


def test_error_classification():
    e = parse_line('[ERROR    19:00:00] Error downloading "X": HTTP 429 Too Many Requests')
    assert any(isinstance(x, TrackError) for x in e)
    line = [x for x in e if isinstance(x, Line)][0]
    assert line.level == "ERROR" and line.cls == "rate_limit"
    a = parse_line("[ERROR    19:00:00] 401 Unauthorized: invalid cookies")
    assert [x for x in a if isinstance(x, Line)][0].cls == "auth"


def test_titles_with_numbers_are_not_rate_limits():
    evs = parse_line('[WARNING  19:00:00] [Track   2/9 ] Skipping "Room 403": already exists')
    assert [x for x in evs if isinstance(x, Line)][0].cls == "other"
    evs = parse_line('[ERROR    19:00:00] Error downloading "429 Days": timeout')
    assert [x for x in evs if isinstance(x, Line)][0].cls == "other"


def test_info_lines_never_classified():
    evs = parse_line("[INFO     19:00:00] retrying after 429")
    assert [x for x in evs if isinstance(x, Line)][0].cls == "other"


def test_blank_and_garbage_are_safe():
    assert parse_line("   ") == []
    assert kinds(parse_line("\x00\x01 weird")) == ["Line"]


def test_splitter_handles_cr_and_split_reads():
    s = LineSplitter()
    assert s.feed("[download]  1.0% of 5MiB\r[download]  2.") == ["[download]  1.0% of 5MiB"]
    assert s.feed("0% of 5MiB\rnext\nlast") == ["[download]  2.0% of 5MiB", "next"]
    assert s.flush() == ["last"]
    assert s.flush() == []


def test_unprefixed_traceback_inherits_error_level():
    line = "httpx.HTTPStatusError: Client error '429 Too Many Requests'"
    ln = parse_line(line, prev_level="ERROR")[-1]
    assert ln.level == "ERROR" and ln.cls == "rate_limit"
    assert parse_line(line, prev_level="INFO")[-1].cls == "other"
    assert parse_line(line)[-1].cls == "other"
