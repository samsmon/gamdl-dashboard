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


TOKEN_DOMAIN, TOKEN_NAME = ".music.apple.com", "media-user-token"
TOKEN_MAX = 4096


def valid_token(token: str) -> bool:
    return bool(token) and len(token) <= TOKEN_MAX and token.isprintable() and not any(c.isspace() for c in token)


def set_media_user_token(path: str, token: str, now: float | None = None) -> None:
    """Replace only the media-user-token line (Netscape format), or append it. Backs the file up first,
    keeps mode/owner, writes atomically. The token value is never logged or returned."""
    import shutil
    if not valid_token(token):
        raise ValueError("invalid token format")
    now = time.time() if now is None else now
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.read().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []
    new_line = f"{TOKEN_DOMAIN}\tTRUE\t/\tTRUE\t{int(now) + 180 * 86400}\t{TOKEN_NAME}\t{token}\n"
    out, replaced = [], False
    for ln in lines:
        body = ln.rstrip("\r\n")
        probe = body[len("#HttpOnly_"):] if body.startswith("#HttpOnly_") else body
        f = probe.split("\t")
        if len(f) >= 7 and f[0] == TOKEN_DOMAIN and f[5] == TOKEN_NAME:
            if not replaced:
                f[6] = token
                ending = ln[len(body):] or "\n"
                out.append("\t".join(f) + ending)
                replaced = True
            continue  # drop duplicate token lines
        out.append(ln)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(new_line)
    if lines:
        shutil.copy2(path, f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}")
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write("".join(out))
        if os.path.exists(path):
            st = os.stat(path)
            os.chmod(tmp, st.st_mode & 0o7777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except (OSError, AttributeError):
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
