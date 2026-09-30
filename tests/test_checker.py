import os
import time

from app.checker import check_album, dir_size, find_output_dirs, load_rules


def album(tmp_path, artist="Artist ~", name="Album", files=("01 A.m4a", "01 A.lrc", "Cover.jpg")):
    d = tmp_path / artist / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x")
    return d


def test_clean_album(tmp_path):
    assert check_album(album(tmp_path), 1) == []


def test_missing_cover_lrc_and_count(tmp_path):
    d = album(tmp_path, files=("01 A.m4a", "02 B.m4a"))
    f = check_album(d, 3)
    assert any("Cover.jpg" in x for x in f)
    assert sum("lyrics" in x for x in f) == 2
    assert any("2 audio files, expected 3" in x for x in f)


def test_zero_byte_and_no_audio(tmp_path):
    d = album(tmp_path, files=("Cover.jpg",))
    (d / "01 A.m4a").write_bytes(b"")
    assert any("zero-byte" in x for x in check_album(d, None))
    d2 = album(tmp_path, name="Empty", files=("Cover.jpg",))
    assert any("no audio" in x for x in check_album(d2, None))


def test_artist_naming_rules(tmp_path):
    d = album(tmp_path, artist="緑黄色社会")
    f = check_album(d, 1)
    assert any("' ~'" in x for x in f)
    assert any("Romaji" in x for x in f)
    d2 = album(tmp_path, artist="Ryokuoushoku Shakai (緑黄色社会) ~", name="B")
    assert not any("artist folder" in x for x in check_album(d2, 1))


def test_forbidden_chars(tmp_path):
    # Test with Windows-legal character (# is forbidden in rules but legal in filenames on Windows/Linux)
    d = album(tmp_path, files=("01 A#.m4a", "01 A#.lrc", "Cover.jpg"))
    rules = {**load_rules(), "forbidden_chars": "#"}
    assert any("forbidden character" in x for x in check_album(d, 1, rules))
    # Platform-independent: default rules don't flag clean album (coverage by test_clean_album)
    clean = album(tmp_path, artist="Clean ~", name="Album2")
    assert not any("forbidden character" in x for x in check_album(clean, 1))


def test_find_output_dirs_by_mtime(tmp_path):
    old = album(tmp_path, artist="Old ~", name="Old")
    os.utime(old, (1000, 1000))
    new = album(tmp_path, artist="New ~", name="New")
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / "junk").mkdir()
    found = find_output_dirs(str(tmp_path), since=time.time() - 60)
    assert found == [new]
    assert find_output_dirs(str(tmp_path / "gone"), 0) == []


def test_dir_size(tmp_path):
    d = album(tmp_path)
    assert dir_size([d]) == 3


def test_rules_file_loads():
    r = load_rules()
    assert r["cover_name"] == "Cover.jpg" and ".m4a" in r["audio_ext"]
