import json
import os
import statistics
import subprocess
from collections import Counter
from pathlib import Path

RULES_PATH = Path(__file__).with_name("checker_rules.json")


def load_rules(path: Path | None = None) -> dict:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


def find_output_dirs(staging: str, since: float) -> list:
    out = []
    try:
        for artist in sorted(os.scandir(staging), key=lambda e: e.name):
            if artist.name.startswith(".") or not artist.is_dir():
                continue
            try:
                with os.scandir(artist.path) as albums:
                    for album in sorted(albums, key=lambda e: e.name):
                        if album.is_dir() and album.stat().st_mtime >= since - 2:
                            out.append(Path(album.path))
            except OSError:
                # Skip this artist if we can't read it, but continue with others
                continue
    except OSError:
        # Staging directory doesn't exist
        return []
    return out


def dir_size(paths: list) -> int:
    total = 0
    for p in paths:
        try:
            for f in Path(p).rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        # Skip files that cannot be stat'ed (removed mid-scan, broken symlink)
                        continue
        except OSError:
            # Skip paths that don't exist or cannot be read
            continue
    return total


def check_album(album: Path, expected_tracks, rules: dict | None = None) -> list:
    rules = rules if rules is not None else load_rules()
    findings = []
    try:
        files = [f for f in Path(album).iterdir() if f.is_file()]
    except OSError as e:
        return [f"cannot read folder: {e.strerror}"]
    names = {f.name.lower() for f in files}
    audio = [f for f in files if f.suffix.lower() in rules["audio_ext"]]
    if not audio:
        findings.append("no audio files")
    for f in files:
        try:
            if f.stat().st_size == 0:
                findings.append(f"zero-byte file: {f.name}")
        except OSError:
            # Skip files that vanish mid-run
            continue
    if rules["cover_name"].lower() not in names:
        findings.append(f"missing {rules['cover_name']}")
    if rules.get("require_lrc"):
        for f in audio:
            if f.with_suffix(".lrc").name.lower() not in names:
                findings.append(f"missing lyrics for {f.name}")
    if expected_tracks and audio and len(audio) != expected_tracks:
        findings.append(f"{len(audio)} audio files, expected {expected_tracks}")
    bad = rules["forbidden_chars"]
    for name in [Path(album).name, *[f.name for f in files]]:
        hit = [c for c in bad if c in name]
        if hit:
            findings.append(f"forbidden character {hit[0]!r} in {name}")
    artist = Path(album).parent.name
    suffix = rules.get("artist_suffix")
    if suffix and not artist.endswith(suffix):
        findings.append(f"artist folder has no '{suffix}' suffix (naming for manual move)")
    if rules.get("require_romaji_kanji") and not artist.isascii() and "(" not in artist:
        findings.append("artist folder lacks 'Romaji (Kanji)' form (naming for manual move)")
    return findings


def detect_codec(path, run=subprocess.run):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
           "stream=codec_name,bit_rate", "-of", "json", str(path)]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        streams = json.loads(r.stdout).get("streams") or []
        if not streams or not streams[0].get("codec_name"):
            return None
        br = str(streams[0].get("bit_rate", ""))
        return {"codec": streams[0]["codec_name"], "kbps": round(int(br) / 1000) if br.isdigit() else None}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def classify_album(results: list) -> tuple:
    known = [r for r in results if r]
    findings = []
    if not known:
        return "unknown", "unknown codec (ffprobe unavailable or failed)", ["codec unknown: ffprobe unavailable or failed"]
    failed = len(results) - len(known)
    if failed:
        findings.append(f"ffprobe failed on {failed} file(s)")
    codecs = Counter(r["codec"] for r in known)
    if len(codecs) > 1:
        findings.append("mixed codecs: " + ", ".join(sorted(codecs)))
    dominant = codecs.most_common(1)[0][0]
    if dominant == "aac":
        kbps = [r["kbps"] for r in known if r["codec"] == "aac" and r["kbps"]]
        if kbps:
            n = int(round(statistics.median(kbps) / 32) * 32)
            return f"aac {n}k", f"Lossy/ [AAC {n}k]", findings
        return "aac", "Lossy/ [AAC]", findings
    if dominant in ("alac", "flac"):
        return dominant, "valid for Lossless/", findings
    return dominant, f"unrecognized codec {dominant}", findings


def probe_album(album, rules=None, run=subprocess.run) -> tuple:
    rules = rules if rules is not None else load_rules()
    try:
        files = sorted(f for f in Path(album).iterdir() if f.is_file() and f.suffix.lower() in rules["audio_ext"])
    except OSError:
        return classify_album([])
    return classify_album([detect_codec(f, run) for f in files])
