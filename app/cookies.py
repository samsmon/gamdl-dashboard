import os
import time


def cookie_status(path: str, now: float | None = None) -> dict:
    """Existence, file age and earliest apple.com expiry. Cookie names/values are never kept."""
    now = time.time() if now is None else now
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {"exists": False, "age_days": None, "expiry_days": None, "expired": False}
    earliest = None
    has_token = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#") or not line.strip():
                    continue
                fields = line.split("\t")
                if len(fields) < 7:
                    continue
                if fields[0] == ".music.apple.com" and fields[5] == "media-user-token" and fields[6].strip():
                    has_token = True  # gamdl requires exactly this domain + name
                d = fields[0].lstrip(".")
                if d != "apple.com" and not d.endswith(".apple.com"):
                    continue
                try:
                    exp = int(fields[4])
                except ValueError:
                    continue
                if exp > 0 and (earliest is None or exp < earliest):
                    earliest = exp
    except OSError:
        pass
    expiry_days = None if earliest is None else int((earliest - now) // 86400)
    return {
        "exists": True,
        "age_days": max(0.0, (now - mtime) / 86400),
        "expiry_days": expiry_days,
        "expired": earliest is not None and earliest <= now,
        "has_token": has_token,
        "problem": None if has_token else "no media-user-token cookie on .music.apple.com (export cookies.txt while on music.apple.com, logged in)",
    }
