DEFAULT_TRACK_BYTES = 9_000_000
_UNKNOWN_TRACKS = 10


def avg_track_bytes(store) -> int:
    total_bytes, total_tracks = store.history_totals()
    if total_bytes > 0 and total_tracks > 0:
        return total_bytes // total_tracks
    return DEFAULT_TRACK_BYTES


def estimate_bytes(store, item: dict) -> int:
    tracks = item.get("expected_tracks") or item.get("track_n") or _UNKNOWN_TRACKS
    return tracks * avg_track_bytes(store)


def queue_bytes(store) -> int:
    return sum(estimate_bytes(store, i) for i in store.list_items() if i["status"] == "queued")
