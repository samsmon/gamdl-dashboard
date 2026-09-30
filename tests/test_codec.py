import json
import subprocess
from types import SimpleNamespace

from app.checker import classify_album, detect_codec, probe_album


def fake_run(payload, code=0):
    def run(cmd, **kw):
        assert cmd[0] == "ffprobe" and "-select_streams" in cmd
        return SimpleNamespace(returncode=code, stdout=json.dumps(payload))
    return run


def test_detect_aac_with_bitrate():
    r = detect_codec("x.m4a", fake_run({"streams": [{"codec_name": "aac", "bit_rate": "256000"}]}))
    assert r == {"codec": "aac", "kbps": 256}


def test_detect_alac_without_bitrate():
    r = detect_codec("x.m4a", fake_run({"streams": [{"codec_name": "alac"}]}))
    assert r == {"codec": "alac", "kbps": None}


def test_detect_failures_return_none():
    assert detect_codec("x", fake_run({"streams": []})) is None
    assert detect_codec("x", fake_run({}, code=1)) is None

    def missing(cmd, **kw):
        raise FileNotFoundError("ffprobe")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    assert detect_codec("x", missing) is None
    assert detect_codec("x", slow) is None


def test_classify_aac_rounds_to_32():
    label, cls, findings = classify_album([{"codec": "aac", "kbps": 255}, {"codec": "aac", "kbps": 258}])
    assert (label, cls, findings) == ("aac 256k", "Lossy/ [AAC 256k]", [])


def test_classify_alac_is_lossless_valid():
    label, cls, _ = classify_album([{"codec": "alac", "kbps": 900}])
    assert label == "alac" and cls == "valid for Lossless/"


def test_classify_mixed_and_partial_failures():
    label, cls, findings = classify_album([{"codec": "aac", "kbps": 256}, {"codec": "alac", "kbps": None}, None])
    assert any("mixed codecs" in f for f in findings)
    assert any("ffprobe failed on 1 file" in f for f in findings)


def test_classify_all_unknown():
    label, cls, findings = classify_album([None, None])
    assert label == "unknown" and findings and "ffprobe" in findings[0]


def test_extension_is_not_trusted(tmp_path):
    d = tmp_path / "A" / "B"
    d.mkdir(parents=True)
    (d / "01 x.m4a").write_bytes(b"x")
    label, cls, _ = probe_album(d, run=fake_run({"streams": [{"codec_name": "alac"}]}))
    assert label == "alac"
    label, cls, _ = probe_album(d, run=fake_run({"streams": [{"codec_name": "aac", "bit_rate": "128000"}]}))
    assert cls == "Lossy/ [AAC 128k]"


def test_probe_album_nonexistent_dir(tmp_path):
    nonexistent = tmp_path / "Missing" / "Album"
    label, cls, findings = probe_album(nonexistent, run=fake_run({}))
    assert label == "unknown"
    assert len(findings) > 0
