import json

from app.store import Store


def add(store, n, kind="album"):
    return store.add_item(f"https://music.apple.com/jp/{kind}/{n}", f"orig{n}", kind, str(n))


def test_add_list_order_and_next(store):
    a, b = add(store, 1), add(store, 2)
    assert [i["id"] for i in store.list_items()] == [a, b]
    assert store.next_queued()["id"] == a
    store.update_item(a, status="done")
    assert store.next_queued()["id"] == b


def test_next_queued_honors_not_before(store):
    a, b = add(store, 1), add(store, 2)
    store.update_item(a, not_before=5000.0, attempts=1)
    assert store.next_queued(now=1000.0)["id"] == b      # a is backing off, b goes first
    store.update_item(b, status="done")
    assert store.next_queued(now=1000.0) is None
    assert store.next_queued(now=6000.0)["id"] == a


def test_new_columns_default(store):
    a = add(store, 1)
    it = store.get_item(a)
    assert it["attempts"] == 0 and it["not_before"] is None and it["codec"] is None
    store.update_item(a, codec="aac 256k", classification="Lossy/ [AAC 256k]", library_status="new")
    assert store.get_item(a)["classification"] == "Lossy/ [AAC 256k]"


def test_reorder(store):
    a, b, c = add(store, 1), add(store, 2), add(store, 3)
    store.reorder([c, a, b])
    assert [i["id"] for i in store.list_items()] == [c, a, b]
    assert store.next_queued()["id"] == c


def test_find_by_key_ignores_cancelled(store):
    a = add(store, 1)
    assert store.find_by_key("album", "1", None)["id"] == a
    store.update_item(a, status="cancelled")
    assert store.find_by_key("album", "1", None) is None


def test_update_rejects_unknown_column(store):
    a = add(store, 1)
    try:
        store.update_item(a, evil="x")
        assert False
    except ValueError:
        pass


def test_recover_after_crash(store):
    a, b = add(store, 1), add(store, 2)
    store.update_item(a, status="downloading")
    store.update_item(b, status="waiting")
    assert store.recover_after_crash() == 2
    assert {i["status"] for i in store.list_items()} == {"queued"}
    assert store.recover_after_crash() == 0


def test_tracks_and_clear(store):
    a = add(store, 1)
    store.upsert_track(a, 1, "T1", "downloading")
    store.upsert_track(a, 1, "T1", "done")
    store.upsert_track(a, 2, "T2", "skipped", "exists")
    rows = store.list_tracks(a)
    assert [(r["idx"], r["status"]) for r in rows] == [(1, "done"), (2, "skipped")]
    store.clear_tracks(a)
    assert store.list_tracks(a) == []


def test_log_tail_and_cap(store):
    for i in range(2100):
        store.add_log(None, "INFO", f"l{i}", float(i))
    tail = store.tail_log(3)
    assert [t["text"] for t in tail] == ["l2097", "l2098", "l2099"]
    assert len(store.tail_log(5000)) <= 2000


def test_track_counter(store):
    for t in (100.0, 200.0, 300.0):
        store.record_track(t)
    assert store.count_tracks_since(150.0) == 2
    assert store.oldest_track_since(150.0) == 200.0
    assert store.oldest_track_since(999.0) is None


def test_settings_and_flags_persist(tmp_path):
    p = str(tmp_path / "s.sqlite")
    s = Store(p)
    assert s.get_settings()["storefront"] == "jp"
    s.put_settings({"error_threshold": 5})
    s.set_flag("paused", "1")
    s.set_banner("rate_limited", "3 errors")
    s2 = Store(p)
    assert s2.get_settings()["error_threshold"] == 5
    assert s2.get_flag("paused") == "1"
    assert s2.get_banner() == {"kind": "rate_limited", "reason": "3 errors"}
    s2.clear_banner()
    assert s2.get_banner() is None
    s2.set_flag("paused", None)
    assert s2.get_flag("paused") is None


def test_history_totals(store):
    a, b = add(store, 1), add(store, 2)
    store.update_item(a, status="done", size_bytes=90, track_n=10, findings=json.dumps(["x"]))
    store.update_item(b, status="error", size_bytes=999, track_n=5)
    assert store.history_totals() == (90, 10)
    assert [i["id"] for i in store.done_items()] == [a]
