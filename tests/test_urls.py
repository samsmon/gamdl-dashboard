import pytest

from app.urls import UrlError, normalize, parse_many

JP = "https://music.apple.com/jp/album/%E6%BA%9C%E6%81%AF/1791035368"
ID = "https://music.apple.com/id/album/frozen-flower/1851922484"


def test_jp_url_normalizes_and_drops_slug():
    p = normalize(JP)
    assert p.normalized == "https://music.apple.com/jp/album/1791035368"
    assert p.kind == "album" and p.id == "1791035368" and p.storefront == "jp"


def test_id_storefront_is_rewritten_to_jp():
    p = normalize(ID)
    assert p.normalized == "https://music.apple.com/jp/album/1851922484"
    assert p.original == ID


def test_uppercase_storefront_and_whitespace():
    p = normalize("  https://music.apple.com/US/album/x/123  ")
    assert p.normalized == "https://music.apple.com/jp/album/123"


def test_keep_original_storefront_when_none():
    assert normalize(ID, storefront=None).normalized == "https://music.apple.com/id/album/1851922484"


def test_empty_storefront_disables_rewrite():
    assert normalize(ID, storefront="").normalized == "https://music.apple.com/id/album/1851922484"


def test_song_in_album_keeps_track_id():
    p = normalize("https://music.apple.com/us/album/x/123?i=456")
    assert p.track_id == "456"
    assert p.normalized == "https://music.apple.com/jp/album/123?i=456"
    assert p.key == ("album", "123", "456")


def test_playlist_id():
    p = normalize("https://music.apple.com/us/playlist/mix/pl.u-abc123")
    assert p.normalized == "https://music.apple.com/jp/playlist/pl.u-abc123"


@pytest.mark.parametrize("bad", [
    "https://example.com/jp/album/1",
    "ftp://music.apple.com/jp/album/1",
    "https://music.apple.com/jp/album/notanumber",
    "https://music.apple.com/jp/video/1",
    "https://music.apple.com/jp",
    "not a url",
])
def test_rejects_bad_urls(bad):
    with pytest.raises(UrlError):
        normalize(bad)


def test_parse_many_mixed_separators_and_duplicates():
    text = f"{JP}\n{ID}, {JP}   https://example.com/x"
    r = parse_many(text)
    assert [x.error is None for x in r] == [True, True, False, False]
    assert r[2].error == "duplicate"
    assert "Apple Music" in r[3].error
    assert r[0].parsed.normalized.endswith("/1791035368")


def test_parse_many_empty():
    assert parse_many("  \n ") == []
