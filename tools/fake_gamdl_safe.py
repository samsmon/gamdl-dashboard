#!/usr/bin/env python3
"""Stand-in for gamdl-safe used by tests and local dev. Scenario via FAKE_SCENARIO:
ok | rate_limit | auth | slow | crash | busy | flaky | fail. Never talks to the network.
flaky/fail: track 2 raises a non-rate-limit error ("read timeout"); flaky succeeds on the next
run when FAKE_STATE points at a file path that survives between runs."""
import os
import sys
import time
from pathlib import Path

scenario = os.environ.get("FAKE_SCENARIO", "ok")
nap = float(os.environ.get("FAKE_SLEEP", "0.02"))
tracks = int(os.environ.get("FAKE_TRACKS", "3"))
out = os.environ.get("FAKE_OUT")
url = sys.argv[-1]


def say(s):
    sys.stdout.write(s)
    sys.stdout.flush()


if scenario == "busy":
    say("gamdl-safe already running (lock held) - refusing to run in parallel\n")
    sys.exit(1)

say(f'[INFO     19:00:00] URL   1/1  Processing "{url}"\n')
failed = 0
for i in range(1, tracks + 1):
    if scenario == "skip" and i == 2:
        say('[WARNING  19:00:02] [Track   2/3  ] Skipping "Song 2": file exists\n')
        continue
    say(f'[INFO     19:00:{i:02d}] [Track {i:3d}/{tracks:<3d}] Downloading "Song {i}"\n')
    for pct in ("10.0", "55.5", "100.0"):
        say(f"[download]  {pct}% of ~ 5.00MiB at 1.00MiB/s (frag 1/2)\r")
        time.sleep(nap)
    say("\n")
    if scenario == "rate_limit":
        say(f'[ERROR    19:00:{i:02d}] Error downloading "Song {i}": HTTP 429 Too Many Requests\n')
        continue
    if scenario in ("flaky", "fail") and i == 2:
        # "flaky" fails track 2 only the first time (state file), "fail" fails it every time
        state = os.environ.get("FAKE_STATE")
        if not (scenario == "flaky" and state and Path(state).exists()):
            if state:
                Path(state).write_text("1")
            say('[ERROR    19:00:02] Error downloading "Song 2": read timeout\n')
            failed += 1
            continue
    if scenario == "auth":
        say('[ERROR    19:00:01] Error downloading "Song 1": 401 Unauthorized, invalid cookies\n')
        time.sleep(30)
        continue
    if out:
        d = Path(out) / "Artist ~" / "Album"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i:02d} Song {i}.m4a").write_bytes(b"x" * 1000)
        (d / f"{i:02d} Song {i}.lrc").write_bytes(b"x")
        (d / "Cover.jpg").write_bytes(b"x")
    say("[gamdl-safe] track delay 1s\n")
    time.sleep(nap)
    if scenario == "slow":
        time.sleep(float(os.environ.get("FAKE_SLOW", "0.5")))
    if scenario == "crash":
        sys.exit(3)
n_err = tracks if scenario == "rate_limit" else failed
say(f"[INFO     19:00:59] Finished with {n_err} error(s)\n")
