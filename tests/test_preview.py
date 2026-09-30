import json

from app.preview import Preview, PreviewService, parse_iso_duration, parse_page
from app.urls import normalize

LD = {
    "@context": "http://schema.org", "@type": "MusicAlbum", "name": "溜息",
    "byArtist": [{"@type": "MusicGroup", "name": "アーティスト"}],
    "numTracks": 5, "datePublished": "2025-03-12",
}
HTML = f'<html><head><script type="application/ld+json">{json.dumps(LD, ensure_ascii=False)}</script></head></html>'


def test_parse_jsonld():
    p = parse_page(HTML)
    assert (p.title, p.artist, p.tracks, p.year, p.source) == ("溜息", "アーティスト", 5, "2025", "web")


def test_parse_jsonld_counts_track_list():
    ld = dict(LD); del ld["numTracks"]; ld["track"] = [{"name": "a"}, {"name": "b"}]
    p = parse_page(f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    assert p.tracks == 2


def test_parse_iso_duration():
    assert parse_iso_duration("PT3M25S") == 205.0
    assert parse_iso_duration("PT1H2M3.5S") == 3723.5
    assert parse_iso_duration("PT45S") == 45.0
    assert parse_iso_duration("junk") is None and parse_iso_duration(None) is None


def test_parse_track_titles_and_durations_into_remote_album():
    ld = dict(LD); del ld["numTracks"]
    ld["track"] = [{"@type": "MusicRecording", "name": "心の奥", "duration": "PT3M20S"},
                   {"@type": "MusicRecording", "name": "草々不一"}]
    p = parse_page(f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>')
    assert p.track_titles == ["心の奥", "草々不一"] and p.durations == [200.0, None] and p.tracks == 2
    r = p.remote()
    assert (r.title, r.artist, r.tracks, r.track_titles, r.durations) == ("溜息", "アーティスト", 2, ["心の奥", "草々不一"], [200.0, None])


def test_parse_og_fallback():
    p = parse_page('<meta property="og:title" content="Frozen Flower - Single by X on Apple Music">')
    assert p.title == "Frozen Flower - Single by X" and p.source == "web"


def test_parse_nothing_returns_none():
    assert parse_page("<html></html>") is None
    assert parse_page('<script type="application/ld+json">{broken</script>') is None


async def test_fetch_serializes_with_delay_and_survives_errors():
    calls, sleeps = [], []

    async def getter(url):
        calls.append(url)
        if "fail" in url:
            raise RuntimeError("boom")
        return HTML

    async def sleep(s): sleeps.append(s)

    svc = PreviewService(getter=getter, sleep=sleep, rand=lambda a, b: a)
    a = normalize("https://music.apple.com/jp/album/1")
    b = normalize("https://music.apple.com/jp/album/2")
    r1 = await svc.fetch(a)
    r2 = await svc.fetch(b)
    assert r1.title == "溜息" and sleeps == [3.0]
    assert calls == [a.normalized, b.normalized]

    async def failing(url): raise RuntimeError("boom")
    svc2 = PreviewService(getter=failing, sleep=sleep, rand=lambda a, b: a)
    r = await svc2.fetch(a)
    assert r == Preview("", "", None, None, "none")


def test_parse_page_coerces_odd_jsonld_values():
    ld = {"@type": "MusicAlbum", "name": {"x": 1}, "byArtist": {"name": 5}, "numTracks": "5",
          "track": [{"name": 7, "duration": 123}, {"name": "b", "duration": "PT1M"}]}
    p = parse_page(f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    assert p.title == "" and p.artist == "" and p.tracks == 5
    assert p.track_titles == ["7", "b"] or p.track_titles == ["", "b"]
    assert p.durations == [None, 60.0]
    ld["numTracks"] = {"n": 1}
    assert parse_page(f'<script type="application/ld+json">{json.dumps(ld)}</script>').tracks == 2
    assert parse_iso_duration(123) is None


def test_host_allowed():
    from app.preview import _host_allowed
    assert _host_allowed("https://music.apple.com/jp/album/1")
    assert _host_allowed("https://classical.music.apple.com/x")
    assert _host_allowed("https://foo.apple.com/x")
    assert not _host_allowed("https://evil.example/x")
    assert not _host_allowed("https://apple.com.evil.example/x")
    assert not _host_allowed("https://notapple.com/x")
