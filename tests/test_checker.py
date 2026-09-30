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
    # Test with Windows-legal character for cross-platform compatibility
    d = album(tmp_path, files=("01 A#.m4a", "01 A#.lrc", "Cover.jpg"))
    rules = {**load_rules(), "forbidden_chars": "#"}
    assert any("forbidden character" in x for x in check_album(d, 1, rules))
    # Default rules contain only OS-level forbidden chars; clean album passes
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


def test_check_album_nonexistent_dir(tmp_path):
    # check_album must not raise on missing directory, returns a finding instead
    nonexistent = tmp_path / "Missing ~" / "Album"
    findings = check_album(nonexistent, 1)
    assert len(findings) > 0
    assert any("cannot read folder" in x for x in findings)


def test_dir_size_nonexistent_path(tmp_path):
    # dir_size must ignore paths that don't exist
    nonexistent = tmp_path / "missing_folder"
    assert dir_size([nonexistent]) == 0


def test_find_output_dirs_skips_plain_file_artist(tmp_path):
    # find_output_dirs must skip artist-level plain files but keep other albums
    good_album = album(tmp_path, artist="Good ~", name="Album1")
    # Create a plain file at artist level (not a directory)
    (tmp_path / "BadFile ~").write_bytes(b"x")
    # Create another good artist with album
    good_album2 = album(tmp_path, artist="Good2 ~", name="Album2")
    found = find_output_dirs(str(tmp_path), since=time.time() - 60)
    assert len(found) == 2
    assert good_album in found
    assert good_album2 in found
