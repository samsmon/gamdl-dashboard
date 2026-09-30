import sys
from pathlib import Path

import pytest

from app.bus import EventBus
from app.config import Config
from app.runner import Runner

FAKE = str(Path(__file__).resolve().parent.parent / "tools" / "fake_gamdl_safe.py")
NOW = 1_000_000.0


@pytest.fixture
def env(tmp_path, monkeypatch, store):
    monkeypatch.setenv("FAKE_OUT", str(tmp_path / "staging"))
    monkeypatch.setenv("FAKE_SLEEP", "0.01")
    cfg = Config(db_path=":memory:", gamdl_cmd=[sys.executable, FAKE], extra_args=[],
                 staging_dir=str(tmp_path / "staging"), disk_path=str(tmp_path), cookies_path="",
                 catalog_path="", host="127.0.0.1", port=0, autostart=False)
    # keep the disk gate and retries out of tests that don't target them
    store.put_settings({"low_disk_gb": 0, "track_retries": 0})
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    runner = Runner(store, EventBus(), cfg, sleep=fake_sleep, clock=lambda: NOW, rand=lambda a, b: a)
    return runner, store, sleeps, monkeypatch


def add(store, n, **kw):
    return store.add_item(f"https://music.apple.com/jp/album/{n}", "o", "album", str(n), **kw)


async def test_success_marks_done_with_output_and_findings(env):
    runner, store, _, _ = env
    a = add(store, 1, expected_tracks=3)
    assert await runner.step() is True
    it = store.get_item(a)
    assert it["status"] == "done" and it["errors"] == 0
    assert it["output_path"].endswith("Album") and it["size_bytes"] >= 3000
    assert it["codec"] == "unknown"  # the fake writes junk bytes, so ffprobe cannot identify them (or is absent)
    assert "Lossy" not in (it["classification"] or "") and "unknown codec" in it["classification"]
    assert it["track_n"] == 3
    assert [t["status"] for t in store.list_tracks(a)] == ["done"] * 3
    assert store.count_tracks_since(0) == 3
    assert runner.current_id is None


async def test_album_delay_between_items_and_never_overlapping(env):
    runner, store, sleeps, _ = env
    a, b = add(store, 1), add(store, 2)
    await runner.step()
    assert sleeps == [] or sum(sleeps) == 0
    await runner.step()
    assert sum(sleeps) == 60  # album_delay lower bound, elapsed time is 0 with the frozen clock
    ia, ib = store.get_item(a), store.get_item(b)
    assert ia["finished_at"] <= ib["started_at"]


async def test_concurrent_steps_are_serialized(env):
    import asyncio
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    mp.setenv("FAKE_SLOW", "0.2")
    a, b = add(store, 1), add(store, 2)
    await asyncio.gather(runner.step(), runner.step())
    assert store.get_item(a)["status"] == "done" and store.get_item(b)["status"] == "done"
    ia, ib = store.get_item(a), store.get_item(b)
    assert ia["finished_at"] <= ib["started_at"]


async def test_rate_limit_auto_pauses_and_requeues(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "rate_limit")
    mp.setenv("FAKE_TRACKS", "6")
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "queued"
    assert store.get_flag("paused") == "1"
    assert store.get_banner()["kind"] == "rate_limited"
    assert await runner.step() is False  # paused: nothing runs


async def test_auth_failure_pauses_with_cookie_banner(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "auth")
    a = add(store, 1)
    await runner.step()
    assert store.get_banner()["kind"] == "cookies"
    assert store.get_item(a)["status"] == "queued"


async def test_resume_clears_pause_and_banner(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "rate_limit")
    mp.setenv("FAKE_TRACKS", "6")
    add(store, 1)
    await runner.step()
    mp.setenv("FAKE_SCENARIO", "ok")
    await runner.resume()
    assert store.get_flag("paused") is None and store.get_banner() is None
    assert await runner.step() is True


async def test_crash_without_finished_is_error(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "crash")
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "error" and "exit" in it["error_msg"]


async def test_flaky_track_is_retried_after_backoff_then_succeeds(env, tmp_path):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 2})
    mp.setenv("FAKE_SCENARIO", "flaky")
    mp.setenv("FAKE_STATE", str(tmp_path / "state"))
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "queued" and it["attempts"] == 1 and it["not_before"] > NOW
    assert "retry 1/2" in it["error_msg"]
    assert store.get_flag("paused") is None  # a plain retry never pauses the queue
    assert await runner.step() is False  # still backing off
    runner._clock = lambda: NOW + 10_000
    assert await runner.step() is True
    it = store.get_item(a)
    assert it["status"] == "done" and it["attempts"] == 1 and it["not_before"] is None


async def test_retries_exhausted_becomes_error(env):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 1})
    mp.setenv("FAKE_SCENARIO", "fail")
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "queued"
    runner._clock = lambda: NOW + 10_000
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "error" and it["attempts"] == 1 and it["errors"] == 1


async def test_backoff_lets_other_items_run_but_still_one_at_a_time(env, tmp_path):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 1})
    mp.setenv("FAKE_SCENARIO", "flaky")
    mp.setenv("FAKE_STATE", str(tmp_path / "state"))
    a, b = add(store, 1), add(store, 2)
    await runner.step()  # a fails once and backs off
    await runner.step()  # b runs while a waits (state file now exists, so b succeeds)
    assert store.get_item(b)["status"] == "done" and store.get_item(a)["status"] == "queued"


async def test_rate_limit_is_never_retried(env):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 2})
    mp.setenv("FAKE_SCENARIO", "rate_limit")
    mp.setenv("FAKE_TRACKS", "6")
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["attempts"] == 0 and it["not_before"] is None
    assert store.get_banner()["kind"] == "rate_limited" and store.get_flag("paused") == "1"


async def test_lock_held_requeues_and_shows_busy(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "busy")
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "queued"
    assert store.get_banner()["kind"] == "busy" and store.get_flag("paused") == "1"


async def test_missing_binary_pauses_with_banner(env):
    runner, store, _, _ = env
    runner.cfg.gamdl_cmd = ["/definitely/not/here"]
    a = add(store, 1)
    await runner.step()
    assert store.get_banner()["kind"] == "gamdl_missing"
    assert store.get_item(a)["status"] == "error"


async def test_cap_pauses_before_starting(env):
    runner, store, _, _ = env
    store.put_settings({"max_tracks_per_24h": 2})
    for _ in range(2):
        store.record_track(NOW - 10)
    a = add(store, 1)
    assert await runner.step() is False
    assert store.get_banner()["kind"] == "cap_reached"
    assert store.get_item(a)["status"] == "queued"


async def test_low_disk_pauses_before_starting(env):
    runner, store, _, _ = env
    store.put_settings({"low_disk_gb": 2000})
    a = add(store, 1)
    assert await runner.step() is False
    assert store.get_banner()["kind"] in ("disk_low", "forecast")


async def test_cancel_queued_item_and_cancel_while_paused(env):
    runner, store, _, _ = env
    a = add(store, 1)
    await runner.pause()
    await runner.cancel(a)
    assert store.get_item(a)["status"] == "cancelled"
    assert await runner.step() is False


async def test_pause_while_idle_is_harmless(env):
    runner, store, _, _ = env
    await runner.pause()
    assert store.get_flag("paused") == "1"
    await runner.resume()
    assert await runner.step() is False  # empty queue


async def test_track_delay_and_skip_events_update_store(env):
    runner, store, _, _ = env
    a = add(store, 1)
    await runner.step()
    log = " ".join(r["text"] for r in store.tail_log(50))
    assert "Downloading" in log and "track delay" in log


async def _until(cond, timeout=10):
    import asyncio
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.01)


async def test_pause_mid_run_requeues_and_stops(env):
    import asyncio
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    mp.setenv("FAKE_SLOW", "0.5")
    mp.setenv("FAKE_TRACKS", "5")
    a = add(store, 1)
    t = asyncio.create_task(runner.step())
    await _until(lambda: runner.live.get("delay_kind") == "track")
    await runner.pause()
    await t
    assert store.get_item(a)["status"] == "queued"
    assert store.get_flag("paused") == "1"
    assert store.count_tracks_since(0) <= 2
    assert runner.proc is None and runner.current_id is None


async def test_cancel_running_item(env):
    import asyncio
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    mp.setenv("FAKE_SLOW", "0.5")
    a = add(store, 1)
    t = asyncio.create_task(runner.step())
    await _until(lambda: runner.proc is not None and runner.live)
    await runner.cancel(a)
    await t
    assert store.get_item(a)["status"] == "cancelled"
    assert runner.proc is None and runner.current_id is None


async def test_cancel_during_album_delay_never_starts(env):
    runner, store, sleeps, _ = env
    a, b = add(store, 1), add(store, 2)
    await runner.step()

    async def cancelling_sleep(s):
        sleeps.append(s)
        await runner.cancel(b)

    runner._sleep = cancelling_sleep
    await runner.step()
    it = store.get_item(b)
    assert it["status"] == "cancelled" and it["started_at"] is None
    assert runner.proc is None


async def test_internal_error_kills_child_and_pauses(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    a = add(store, 1)

    def boom(*args, **kw):
        raise RuntimeError("boom")

    mp.setattr(runner, "_on_line", boom)
    await runner.step()  # must not raise
    it = store.get_item(a)
    assert it["status"] == "error" and it["error_msg"] == "internal error: RuntimeError"
    assert store.get_banner()["kind"] == "internal_error" and store.get_flag("paused") == "1"
    assert runner.proc is None and runner.current_id is None
    assert await runner.step() is False


async def test_describe_output_failure_still_marks_done(env):
    runner, store, _, mp = env

    def boom(item):
        raise ValueError("x")

    mp.setattr(runner, "_describe_output", boom)
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "done" and "post-download check failed: ValueError" in it["findings"]


async def test_stop_terminates_running_child_and_requeues(env):
    import asyncio
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    mp.setenv("FAKE_SLOW", "5")
    a = add(store, 1)
    t = asyncio.create_task(runner.step())
    await _until(lambda: runner.proc is not None and runner.live)
    runner.stop()
    await asyncio.wait_for(t, 10)
    assert store.get_item(a)["status"] == "queued"
    assert runner.proc is None


async def test_task_cancellation_terminates_child_and_requeues(env):
    import asyncio
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    mp.setenv("FAKE_SLOW", "5")
    a = add(store, 1)
    t = asyncio.create_task(runner.step())
    await _until(lambda: runner.proc is not None and runner.live)
    proc = runner.proc
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert store.get_item(a)["status"] == "queued"
    assert runner.proc is None and proc.returncode is not None


async def test_album_delay_survives_restart(env):
    runner, store, sleeps, _ = env
    a = add(store, 1)
    store.update_item(a, status="done", finished_at=NOW - 10)
    add(store, 2)
    assert runner._last_finish is None
    assert await runner.step() is True
    assert sum(sleeps) == 50


async def test_skip_event_marks_track_skipped(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "skip")
    a = add(store, 1)
    await runner.step()
    t2 = [t for t in store.list_tracks(a) if t["idx"] == 2][0]
    assert t2["status"] == "skipped" and "file exists" in t2["reason"]


async def test_errors_without_429_text_still_halt(env):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 2})
    mp.setenv("FAKE_SCENARIO", "errors_no_text")
    mp.setenv("FAKE_TRACKS", "6")
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "queued" and it["attempts"] == 0 and it["not_before"] is None
    assert store.get_banner()["kind"] == "rate_limited" and store.get_flag("paused") == "1"
    assert await runner.step() is False


async def test_traceback_429_lines_halt(env):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 2})
    mp.setenv("FAKE_SCENARIO", "traceback_429")
    mp.setenv("FAKE_TRACKS", "6")
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "queued" and it["attempts"] == 0
    assert store.get_banner()["kind"] == "rate_limited" and store.get_flag("paused") == "1"


async def test_verdict_halts_even_when_user_cancelled(env):
    from app.guard import Verdict
    from app.runner import _Run
    runner, store, _, _ = env
    a = add(store, 1)
    runner._cancel = True
    await runner._finish(store.get_item(a), _Run(verdict=Verdict("rate_limited", "storm")), 0)
    assert store.get_item(a)["status"] == "cancelled"
    assert store.get_banner()["kind"] == "rate_limited" and store.get_flag("paused") == "1"


def _library_csv(path, album, titles, codec="audio/flac"):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Artist", "Album", "Album Artist", "Track Number", "Total Tracks", "Codec", "Duration", "Path"])
        for i, t in enumerate(titles, 1):
            w.writerow([t, "Artist", album, "Artist", i, len(titles), codec, 100.0, f"E:/Music/L/{album}/{i:02d} {t}.x"])


async def test_finished_download_gets_library_fun_fact(env, tmp_path):
    from app.library import Library
    runner, store, _, _ = env
    csv_path = tmp_path / "metadata.csv"
    _library_csv(csv_path, "Album", ["Song 1", "Song 2", "Song 3"])
    runner.library = Library(str(csv_path))
    a = add(store, 1, expected_tracks=3)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "done"
    assert it["library_status"] == "in_library_lossless"
    assert "already in your library, lossless" in it["library_note"]


async def test_fun_fact_says_new_for_unknown_album_and_stays_silent_without_library(env, tmp_path):
    from app.library import Library
    runner, store, _, _ = env
    csv_path = tmp_path / "metadata.csv"
    _library_csv(csv_path, "Something Else", ["x", "y"])
    runner.library = Library(str(csv_path))
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["library_status"] == "new" and "not in your library" in it["library_note"]
    runner.library = Library(str(tmp_path / "missing.csv"))  # unreadable library: no note, download still succeeds
    b = add(store, 2)
    await runner.step()
    it = store.get_item(b)
    assert it["status"] == "done" and it["library_note"] is None


async def test_missing_title_and_artist_are_filled_from_the_output_folder(env):
    runner, store, _, _ = env
    a = add(store, 1)  # queued without a title, like every url added through the UI
    await runner.step()
    it = store.get_item(a)
    assert it["title"] == "Album" and it["artist"] == "Artist ~"
    b = add(store, 2, title="Keep me", artist="Me")
    await runner.step()
    it = store.get_item(b)
    assert it["title"] == "Keep me" and it["artist"] == "Me"  # only blanks are filled


async def test_album_start_prefills_name_kind_artist_and_every_track_as_queued(env):
    from app.releases import Itunes
    runner, store, _, _ = env

    async def getter(params):
        coll = {"wrapperType": "collection", "collectionId": 1, "collectionName": "Fresh - Single", "artistName": "Someone", "trackCount": 3,
                "releaseDate": "2026-05-01T07:00:00Z", "primaryGenreName": "Pop", "copyright": "\u2117 2026 Label X",
                "artworkUrl100": "https://is1-ssl.mzstatic.com/a/100x100bb.jpg"}
        songs = [{"wrapperType": "track", "kind": "song", "trackName": f"Song {i}", "trackNumber": i, "discNumber": 1, "trackTimeMillis": 1000 * i,
                  "artistName": "Someone feat. Guest" if i == 2 else "Someone"} for i in (1, 2, 3)]
        return {"results": [coll, *songs]}

    runner.cfg.prefetch = True
    runner.itunes = Itunes(getter=getter)
    a = add(store, 1)
    await runner._prefetch(store.get_item(a))
    it = store.get_item(a)
    assert it["title"] == "Fresh - Single" and it["artist"] == "Someone" and it["track_n"] == 3
    assert [(t["idx"], t["title"], t["status"]) for t in store.list_tracks(a)] == [(1, "Song 1", "queued"), (2, "Song 2", "queued"), (3, "Song 3", "queued")]
    import json
    meta = json.loads(it["meta"])
    assert meta["release_date"] == "2026-05-01" and meta["genre"] == "Pop" and meta["copyright"].endswith("Label X") and meta["total_ms"] == 6000
    assert meta["artwork"] == "https://is1-ssl.mzstatic.com/a/300x300bb.jpg" and meta["kind"] == "single"
    assert [(t["duration_ms"], t["artist"]) for t in store.list_tracks(a)] == [(1000, "Someone"), (2000, "Someone feat. Guest"), (3000, "Someone")]
    await runner.step()  # the real run keeps the rows and moves them along
    assert [t["status"] for t in store.list_tracks(a)] == ["done"] * 3
    assert store.get_item(a)["title"] == "Fresh - Single"
    assert [t["duration_ms"] for t in store.list_tracks(a)] == [1000, 2000, 3000]  # durations survive the status updates


async def test_prefetch_failure_never_breaks_the_download(env):
    from app.releases import Itunes
    runner, store, _, _ = env

    async def boom(params):
        raise RuntimeError("apple is down")

    runner.cfg.prefetch = True
    runner.itunes = Itunes(getter=boom)
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "done"
