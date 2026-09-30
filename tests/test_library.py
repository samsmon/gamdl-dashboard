import csv
import os

from app.library import Library, RemoteAlbum, is_lossless_codec, norm_title

HEADER = ["Title", "Artist", "Album", "Album Artist", "Track Number", "Total Tracks", "Codec", "Duration", "Path"]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def album_rows(album, artist, titles, durs, codec, folder):
    return [[t, artist, album, artist, i + 1, len(titles), codec, d, f"E:/Music\\{folder}\\{i + 1:02d} {t}.x"]
            for i, (t, d) in enumerate(zip(titles, durs))]


TITLES = ["心の奥", "草々不一", "溜息"]
DURS = [200.0, 180.5, 240.0]


def remote(**kw):
    base = dict(title="溜息", artist="ロクデナシ", tracks=3, track_titles=TITLES, durations=DURS)
    base.update(kw)
    return RemoteAlbum(**base)


def lib(tmp_path, *groups):
    p = str(tmp_path / "metadata.csv")
    write_csv(p, [r for g in groups for r in g])
    return Library(p)


def test_norm_title_and_codec():
    assert norm_title("Party!! - Single") == "party"
    assert norm_title("Song [WEB-FLAC 24bit／44.1kHz]") == "song"
    assert norm_title("ＡＢＣ　Ｄ") == "abc d"
    assert is_lossless_codec("audio/flac") and is_lossless_codec("audio/x-wav") and is_lossless_codec("alac")
    assert not is_lossless_codec("audio/mpeg") and not is_lossless_codec("")


def test_lossless_copy_is_in_library_lossless(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "Rokudenashi (ロクデナシ) ~", TITLES, DURS, "audio/flac", "Lossless\\J-Pop\\A\\溜息"))
    m = L.match(remote())
    assert m.status == "in_library_lossless" and m.confidence >= 0.85 and m.lossless is True
    assert m.album == "溜息"


def test_lossy_only_copy_is_flagged_lossy(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/mpeg", "Lossy\\A\\溜息"))
    assert L.match(remote()).status == "in_library_lossy"


def test_lossless_wins_over_near_duplicate_lossy(tmp_path):
    L = lib(tmp_path,
            album_rows("溜息", "x", TITLES, DURS, "audio/mpeg", "Lossy\\A\\溜息"),
            album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    assert L.match(remote()).status == "in_library_lossless"


def test_title_only_evidence_can_never_be_in_library(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    m = L.match(RemoteAlbum(title="溜息", artist=""))
    assert m.status == "similar" and m.confidence <= 0.80


def test_different_album_is_new(tmp_path):
    L = lib(tmp_path, album_rows("Other", "x", ["a", "b"], [100.0, 90.0], "audio/flac", "Lossless\\A\\Other"))
    assert L.match(remote()).status == "new"


def test_renamed_album_with_same_tracks_is_similar(tmp_path):
    L = lib(tmp_path, album_rows("Tameiki (Romaji title)", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\Tameiki"))
    m = L.match(remote())
    assert m.status == "similar", m


def test_duration_mismatch_lowers_score(tmp_path):
    off = lib(tmp_path, album_rows("溜息", "x", TITLES, [100.0, 100.0, 100.0], "audio/flac", "Lossless\\A\\溜息"))
    conf_off = off.match(remote()).confidence
    same = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    assert conf_off < same.match(remote()).confidence


def test_blank_remote_durations_are_ignored_not_fatal(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    m = L.match(remote(durations=[None, None, None]))
    assert m.status in ("in_library_lossless", "similar") and m.confidence > 0.5


def test_artist_bonus_from_kanji_variant(tmp_path):
    rows = album_rows("溜息", "Rokudenashi (ロクデナシ) ~", TITLES, [100.0, 100.0, 100.0], "audio/flac", "L\\A\\溜息")
    with_artist = lib(tmp_path, rows).match(remote(artist="ロクデナシ")).confidence
    without = lib(tmp_path, rows).match(remote(artist="Someone Else")).confidence
    assert with_artist > without


def test_unknown_when_nothing_to_compare(tmp_path):
    L = lib(tmp_path, album_rows("a", "x", ["t"], [1.0], "audio/flac", "L\\A\\a"))
    assert L.match(RemoteAlbum(title="", artist="")).status == "unknown"


def test_missing_and_broken_files_are_unavailable(tmp_path):
    assert Library(str(tmp_path / "none.csv")).match(remote()).status == "unavailable"
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"\x80\x81\x82\xff\xfe")
    assert Library(str(bad)).match(remote()).status == "unavailable"


def test_reload_when_file_changes(tmp_path):
    p = str(tmp_path / "m.csv")
    write_csv(p, [])
    L = Library(p)
    assert L.match(remote()).status == "new"
    write_csv(p, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "L\\A\\溜息"))
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert L.match(remote()).status == "in_library_lossless"


def test_bad_duration_cells_do_not_crash(tmp_path):
    rows = album_rows("溜息", "x", TITLES, ["", "abc", "240"], "audio/flac", "L\\A\\溜息")
