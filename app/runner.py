import asyncio
import codecs
import os
import random
import signal
import subprocess
import time
from dataclasses import dataclass

from app import checker, disk, forecast
from app.guard import Guard
from app.parser import (AlbumDelay, Finished, Line, LineSplitter, Progress, TrackDelay, TrackError,
                        TrackSkip, TrackStart, UrlStart, parse_line)
from app.settings import parse_range

DAY = 86400


@dataclass
class _Run:
    errors: int = 0
    finished: int | None = None
    busy: bool = False
    verdict: object = None
    paused_stop: bool = False
    tracks_done: int = 0
    last_lines: list = None


def _group_kwargs() -> dict:
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


class Runner:
    def __init__(self, store, bus, cfg, sleep=asyncio.sleep, clock=time.time, rand=random.uniform):
        self.store, self.bus, self.cfg = store, bus, cfg
        self._sleep, self._clock, self._rand = sleep, clock, rand
        self.proc = None
        self.current_id = None
        self.live: dict = {}
        self.guard = Guard(3)
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stopping = False
        self._last_finish = None
        self._pause_after_track = False
        self._cancel = False

    # ---- control -------------------------------------------------------
    def _state(self):
        self.bus.publish({"type": "state"})

    def wake(self):
        self._wake.set()

    def stop(self):
        self._stopping = True
        self._wake.set()

    async def pause(self):
        self.store.set_flag("paused", "1")
        if self.proc is not None:
            self._pause_after_track = True
        self._state()

    async def resume(self):
        self.store.set_flag("paused", None)
        self.store.clear_banner()
        self.guard.reset()
        self._pause_after_track = False
        self.wake()
        self._state()

    async def cancel(self, item_id: int):
        item = self.store.get_item(item_id)
        if not item:
            return
        if item_id == self.current_id and self.proc is not None:
            self._cancel = True
            self._signal(signal.SIGTERM)
        elif item["status"] in ("queued", "waiting"):
            self.store.update_item(item_id, status="cancelled", finished_at=self._clock())
        self._state()

    async def run_forever(self):
        while not self._stopping:
            if not await self.step():
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass

    # ---- scheduling ----------------------------------------------------
    def _halt(self, kind: str, reason: str):
        self.store.set_flag("paused", "1")
        self.store.set_banner(kind, reason)
        self._state()

    def _preflight(self, item):
        s = self.store.get_settings()
        now = self._clock()
        cap = s["max_tracks_per_24h"]
        if cap > 0 and self.store.count_tracks_since(now - DAY) >= cap:
            oldest = self.store.oldest_track_since(now - DAY)
            when = int(oldest + DAY) if oldest else 0
            return "cap_reached", f"{cap} tracks in 24 h; oldest counted track expires at epoch {when}"
        free = disk.free_bytes(self.cfg.disk_path)
        if free is not None:
            floor = s["low_disk_gb"] * 1_000_000_000
            if free < floor:
                return "disk_low", f"{free // 10**9} GB free, below {s['low_disk_gb']} GB"
            est = forecast.estimate_bytes(self.store, item)
            if free - est < floor:
                return "forecast", f"item needs about {est // 10**9} GB, {free // 10**9} GB free"
        return None

    async def step(self) -> bool:
        async with self._lock:
            banner = self.store.get_banner()
            s = self.store.get_settings()
            if (banner and banner["kind"] == "cap_reached" and s["auto_resume_after_cap"]
                    and self.store.count_tracks_since(self._clock() - DAY) < s["max_tracks_per_24h"]):
                await self.resume()
            if self.store.get_flag("paused") == "1":
                return False
            item = self.store.next_queued(self._clock())  # skips items still backing off (not_before)
            if not item:
                return False
            problem = self._preflight(item)
            if problem:
                self._halt(*problem)
                return False
            if not await self._album_delay(item):
                return False
            await self._run_item(item)
            return True

    async def _album_delay(self, item) -> bool:
        if self._last_finish is None:
            return True
        lo, hi = parse_range(self.store.get_settings()["album_delay"])
        remaining = self._rand(lo, hi) - (self._clock() - self._last_finish)
        if remaining <= 0:
            return True
        self.store.update_item(item["id"], status="waiting")
        self.current_id = item["id"]
        self.live = {"delay_kind": "album", "delay_until": self._clock() + remaining}
        self._state()
        try:
            while remaining > 0:
                cur = self.store.get_item(item["id"])
                if not cur or cur["status"] != "waiting":
                    return False  # cancelled or removed during the delay
                if self.store.get_flag("paused") == "1" or self._stopping:
                    return False
                nap = min(1.0, remaining)
                await self._sleep(nap)
                remaining -= nap
            return True
        finally:
            self.current_id, self.live = None, {}
            if self.store.get_item(item["id"])["status"] == "waiting":
                self.store.update_item(item["id"], status="queued")
            self._state()

    # ---- process -------------------------------------------------------
    def _signal(self, sig):
        p = self.proc
        if p is None or p.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(p.pid, sig)
            elif sig == signal.SIGTERM:
                p.terminate()
            else:
                p.kill()
        except (ProcessLookupError, PermissionError):
            return
        asyncio.get_running_loop().create_task(self._kill_later(p))

    async def _kill_later(self, p):
        try:
            await asyncio.wait_for(p.wait(), 10)
        except asyncio.TimeoutError:
            try:
                if os.name == "posix":
                    os.killpg(p.pid, signal.SIGKILL)
                else:
                    p.kill()
            except (ProcessLookupError, PermissionError):
                pass

    async def _run_item(self, item):
        iid = item["id"]
        s = self.store.get_settings()
        env = os.environ.copy()
        env.update(GAMDL_TRACK_DELAY=s["track_delay"], GAMDL_ALBUM_DELAY=s["album_delay"],
                   GAMDL_STOREFRONT=s["storefront"])
        self.guard.threshold = s["error_threshold"]
        self.current_id, self.live = iid, {"track_pct": 0.0}
        self._pause_after_track = self._cancel = False
        self.store.clear_tracks(iid)
        self.store.update_item(iid, status="downloading", started_at=self._clock(), finished_at=None,
                               error_msg=None, errors=0, track_i=None, track_n=None, findings=None)
        self._state()
        argv = [*self.cfg.gamdl_cmd, *self.cfg.extra_args, item["url"]]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, **_group_kwargs())
        except OSError as e:
            self.store.update_item(iid, status="error", error_msg=f"cannot start gamdl-safe: {e}",
                                   finished_at=self._clock())
            self._halt("gamdl_missing", f"cannot start {self.cfg.gamdl_cmd[0]}")
            self.current_id, self.live = None, {}
            return
        run = _Run(last_lines=[])
        splitter, dec = LineSplitter(), codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await self.proc.stdout.read(4096)
            if not chunk:
                break
            for line in splitter.feed(dec.decode(chunk)):
                self._on_line(iid, line, run)
        for line in splitter.flush():
            self._on_line(iid, line, run)
        rc = await self.proc.wait()
        self.proc = None
        await self._finish(item, run, rc)

    def _on_line(self, iid, raw, run):
        for ev in parse_line(raw):
            if isinstance(ev, (TrackStart, TrackSkip, TrackError, TrackDelay)):
                self.bus.publish({"type": "tracks", "item_id": iid})  # UI reloads the open track list
            v = self.guard.on_event(ev)
            if v and run.verdict is None:
                run.verdict = v
                self._signal(signal.SIGTERM)
            if isinstance(ev, UrlStart):
                self.store.update_item(iid, url_i=ev.n, url_n=ev.total)
            elif isinstance(ev, TrackStart):
                self.store.upsert_track(iid, ev.i, ev.title, "downloading")
                self.store.update_item(iid, track_i=ev.i, track_n=ev.total, current_title=ev.title, status="downloading")
                self.live = {"track_pct": 0.0, "speed": None}
                self._progress(iid)
            elif isinstance(ev, Progress):
                self.live.update(track_pct=ev.pct, speed=ev.speed, delay_kind=None, delay_until=None)
                self._progress(iid)
            elif isinstance(ev, TrackSkip):
                self.store.upsert_track(iid, ev.i, ev.title, "skipped", ev.reason)
            elif isinstance(ev, TrackError):
                run.errors += 1
                self.store.update_item(iid, errors=run.errors)
                cur = self.store.get_item(iid)
                if cur and cur["track_i"]:
                    self.store.upsert_track(iid, cur["track_i"], cur["current_title"], "error")
            elif isinstance(ev, TrackDelay):
                cur = self.store.get_item(iid)
                if cur and cur["track_i"]:
                    self.store.upsert_track(iid, cur["track_i"], cur["current_title"], "done")
                self.store.record_track(self._clock())
                run.tracks_done += 1
                self.live = {"track_pct": 100.0, "delay_kind": "track", "delay_until": self._clock() + ev.seconds}
                self.store.update_item(iid, status="waiting")
                self._progress(iid)
                if self._pause_after_track and run.verdict is None:
                    run.paused_stop = True
                    self._signal(signal.SIGTERM)
            elif isinstance(ev, AlbumDelay):
                self.live = {"delay_kind": "album", "delay_until": self._clock() + ev.seconds}
                self._progress(iid)
            elif isinstance(ev, Finished):
                run.finished = ev.errors
            elif isinstance(ev, Line):
                if "already running" in ev.text:
                    run.busy = True
                self.store.add_log(iid, ev.level, ev.text, self._clock())
                self.bus.publish({"type": "log", "item_id": iid, "level": ev.level, "text": ev.text, "ts": self._clock()})

    def _progress(self, iid):
        self.bus.publish({"type": "progress", "item_id": iid, "pct": self.live.get("track_pct"),
                          "speed": self.live.get("speed"), "delay_kind": self.live.get("delay_kind"),
                          "delay_until": self.live.get("delay_until")})

    def _fail(self, item, fields: dict, msg: str, errors=None):
        """Track errors that are not rate-limit/auth: retry the album after a backoff, then give up.
        gamdl skips files that already exist (overwrite=false), so a re-run only fetches what is missing."""
        s = self.store.get_settings()
        attempts = self.store.get_item(item["id"])["attempts"]
        if attempts < s["track_retries"]:
            lo, hi = parse_range(s["retry_backoff"])
            fields.update(status="queued", attempts=attempts + 1, not_before=self._clock() + self._rand(lo, hi),
                          error_msg=f"{msg}; retry {attempts + 1}/{s['track_retries']}")
        else:
            fields.update(status="error", error_msg=msg)
        if errors is not None:
            fields["errors"] = errors

    async def _finish(self, item, run, rc):
        iid, now = item["id"], self._clock()
        fields: dict = {"finished_at": now}
        if run.busy:
            fields.update(status="queued")
            self._halt("busy", "another gamdl-safe instance holds the lock")
        elif self._cancel:
            fields.update(status="cancelled")
        elif run.verdict:  # 429/403/auth always wins: never retried automatically
            fields.update(status="queued", error_msg=run.verdict.reason)
            self._halt(run.verdict.kind, run.verdict.reason)
        elif run.paused_stop:
            fields.update(status="queued")
        elif rc == 0 and run.finished is not None:
            errors = max(run.errors, run.finished)
            if errors == 0:
                fields.update(await asyncio.to_thread(self._describe_output, item))
                fields.update(status="done", errors=0, not_before=None)
                self.guard.reset()
            else:
                self._fail(item, fields, f"finished with {errors} error(s)", errors)
        else:
            self._fail(item, fields, f"gamdl-safe exited with code {rc} before finishing")
        self.store.update_item(iid, **fields)
        self.current_id, self.live = None, {}
        self._cancel = self._pause_after_track = False
        self._last_finish = now
        self._state()

    def _describe_output(self, item) -> dict:
        import json
        cur = self.store.get_item(item["id"])
        dirs = checker.find_output_dirs(self.cfg.staging_dir, cur["started_at"] or 0)
        if not dirs:
            return {}
        rules = checker.load_rules()
        findings: list = []
        codec, classification = "unknown", ""
        for d in dirs:  # runs in a worker thread (asyncio.to_thread): ffprobe is blocking
            for f in checker.check_album(d, cur["track_n"] or cur["expected_tracks"], rules):
                findings.append(f"{d.name}: {f}")
            codec, classification, codec_findings = checker.probe_album(d, rules)
            findings.extend(f"{d.name}: {f}" for f in codec_findings)
        path = str(dirs[0]) if len(dirs) == 1 else str(dirs[0].parent)
        return {"output_path": path, "size_bytes": checker.dir_size(dirs), "findings": json.dumps(findings),
                "codec": codec, "classification": classification}
