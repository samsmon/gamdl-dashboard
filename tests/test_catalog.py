import sqlite3

from app.catalog import Catalog, artist_variants, norm, staging_match


def make_catalog(path, rows):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE tracks(id INTEGER PRIMARY KEY AUTOINCREMENT, relative_path TEXT UNIQUE,
                  filename TEXT, category TEXT, format TEXT, size_bytes INTEGER, is_lossless INTEGER)""")
    for rp, lossless in rows:
        db.execute("INSERT INTO tracks(relative_path,filename,category,format,size_bytes,is_lossless) VALUES(?,?,?,?,?,?)",
                   (rp, rp.split("/")[-1], rp.split("/")[1], "FLAC", 1, lossless))
    db.commit()
    db.close()


ROWS = [
    ("Lossless/J-Pop/Ryokuoushoku Shakai (緑黄色社会) ~/Party!!/01. A.flac", 1),
    ("Lossless/J-Pop/Ryokuoushoku Shakai (緑黄色社会) ~/Party!!/02. B.flac", 1),
    ("Lossless/J-Pop/Various Artists ~/+A -PLUS A-/03 溜息の色.flac", 1),
    ("Lossy/J-Pop/Someone ~/Old Album/01 x.mp3", 0),
]


def test_norm_and_variants():
    assert norm("Party!!") == "party"
    assert norm("ＡＢＣ　Ｄ") == "abc d"
    assert artist_variants("Ryokuoushoku Shakai (緑黄色社会) ~") >= {"ryokuoushoku shakai", "緑黄色社会"}


def test_exact_match_by_kanji_artist(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    m = Catalog(p).match("緑黄色社会", "Party!!")
    assert m.level == "exact" and m.tracks == 2 and m.lossless is True
    assert m.paths == ["Lossless/J-Pop/Ryokuoushoku Shakai (緑黄色社会) ~/Party!!"]


def test_single_suffix_ignored_and_likely_on_other_artist(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    m = Catalog(p).match("Someone Else", "Party!! - Single")
    assert m.level == "likely"


def test_lossy_only_is_not_lossless(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    m = Catalog(p).match("Someone", "Old Album")
    assert m.level == "exact" and m.lossless is False


def test_none(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    assert Catalog(p).match("Nobody", "Nothing Here").level == "none"


def test_missing_file_is_unavailable_not_error(tmp_path):
    m = Catalog(str(tmp_path / "missing.sqlite")).match("a", "b")
    assert m.level == "unavailable"


def test_corrupt_file_is_unavailable(tmp_path):
    p = tmp_path / "bad.sqlite"; p.write_bytes(b"not a database at all")
    assert Catalog(str(p)).match("a", "b").level == "unavailable"


def test_reload_when_file_changes(tmp_path):
    import os
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS[:1])
    c = Catalog(p)
    assert c.match("x", "Old Album").level == "none"
    db = sqlite3.connect(p)
    db.execute("INSERT INTO tracks(relative_path,filename,category,format,size_bytes,is_lossless) VALUES(?,?,?,?,?,?)",
               ("Lossy/J-Pop/Someone ~/Old Album/01 x.mp3", "01 x.mp3", "J-Pop", "MP3", 1, 0))
    db.commit(); db.close()
    st = os.stat(p); os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert c.match("Someone", "Old Album").level == "exact"


def test_staging_match(tmp_path):
    (tmp_path / "緑黄色社会" / "Party!!").mkdir(parents=True)
    (tmp_path / ".tmp").mkdir()
    assert staging_match(str(tmp_path), "緑黄色社会", "Party!!").level == "exact"
    assert staging_match(str(tmp_path), "x", "Nope").level == "none"
    assert staging_match(str(tmp_path / "gone"), "x", "y").level == "none"
