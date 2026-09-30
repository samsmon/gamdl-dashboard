import asyncio
import json
import contextlib
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import disk, forecast
from app.bus import EventBus
from app.catalog import Catalog, staging_match
from app.config import Config, from_env
from app.cookies import cookie_status
from app.releases import ReleaseError, Watcher
from app.library import Library, LibraryMatch
from app.preview import PreviewService
from app.runner import Runner
from app.store import Store
from app.urls import UrlError, normalize, parse_many

STATIC = Path(__file__).resolve().parent.parent / "static"
PCACHE_MAX = 500
NEEDS_FORCE = {"in_library_lossless", "similar", "in_staging"}


class ParseIn(BaseModel):
    text: str = Field(max_length=50000)


class PreviewIn(BaseModel):
    url: str


class QueueItemIn(BaseModel):
    url: str
    title: str | None = None
    artist: str | None = None
    tracks: int | None = None
    force: bool = False


class QueueIn(BaseModel):
    items: list[QueueItemIn] = Field(max_length=200)


class ReorderIn(BaseModel):
    ids: list[int] = Field(max_length=1000)


class FollowIn(BaseModel):
    input: str = Field(max_length=500)
    label_filter: str | None = Field(default=None, max_length=100)


class FollowPatch(BaseModel):
    label_filter: str | None = Field(default=None, max_length=100)


class BulkIn(BaseModel):
    ids: list[int] = Field(max_length=500)
    action: str


def _public(item: dict, live=None) -> dict:
    out = dict(item)
    out["findings"] = json.loads(item["findings"]) if item.get("findings") else []
    if live is not None:
        out["live"] = live
    return out


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or from_env()
    store, bus = Store(cfg.db_path), EventBus()
    runner = Runner(store, bus, cfg)
    catalog = Catalog(cfg.catalog_path)
    library = Library(cfg.library_csv)
    watcher = Watcher(store, bus)

    @asynccontextmanager
    async def lifespan(app):
        if store.recover_after_crash():
            store.set_flag("paused", "1")
            store.set_banner("recovered", "restarted while a download was running; resume to continue")
        if not cfg.autostart:
            store.set_flag("paused", "1")  # the runner loop always runs; autostart=0 only starts it paused
        task = asyncio.create_task(runner.run_forever())
        wtask = asyncio.create_task(watcher.run_forever()) if cfg.watch else None
        yield
        runner.stop()
        watcher.stop()
        for t in (task, wtask):
            if t:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    app = FastAPI(lifespan=lifespan)
    app.state.watcher = watcher

    @app.middleware("http")
    async def refuse_cross_origin_writes(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            foreign = origin is not None and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower()
            if foreign or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    def storefront() -> str:
        return store.get_settings()["storefront"]

    def snapshot() -> dict:
        s, now = store.get_settings(), time.time()
        items = [_public(i, dict(runner.live) if i["id"] == runner.current_id else None) for i in store.list_items()]
        return {
            "now": now, "items": items, "paused": store.get_flag("paused") == "1", "banner": store.get_banner(),
            "settings": s, "disk": {"free_bytes": disk.free_bytes(cfg.disk_path)},
            "cookies": cookie_status(cfg.cookies_path),
            "cap": {"used": store.count_tracks_since(now - 86400), "limit": s["max_tracks_per_24h"]},
            "forecast_bytes": forecast.queue_bytes(store), "errors": sum(i["errors"] for i in items),
            "releases": {"new": store.count_releases("new"), "follows": len(store.list_follows())},
        }

    def need(item_id: int) -> dict:
        item = store.get_item(item_id)
        if not item:
            raise HTTPException(404, "no such item")
        return item

    @app.get("/api/state")
    def state():
        return snapshot()

    @app.post("/api/parse")
    def parse(body: ParseIn):
        rows = []
        for r in parse_many(body.text, storefront()):
            p = r.parsed
            dup = bool(p and store.find_by_key(p.kind, p.id, p.track_id))
            rows.append({"raw": r.raw, "error": r.error, "url": p.normalized if p else None,
                         "kind": p.kind if p else None, "id": p.id if p else None,
                         "storefront": p.storefront if p else None, "duplicate": dup})
        return {"results": rows}

    @app.post("/api/preview")
    async def preview(body: PreviewIn):
        try:
            p = normalize(body.url, storefront())
        except UrlError as e:
            raise HTTPException(422, str(e))
        svc = app.state.previews
        pv = await svc.fetch(p)
        s = store.get_settings()
        source, stg = "metadata", None
        try:
            lib = await asyncio.to_thread(library.match, pv.remote(), s["library_exact"], s["library_similar"])
            if lib.status == "unavailable" and pv.title:
                # metadata.csv unreadable: fall back to names from catalog.sqlite, which can never say "in library"
                cm = await asyncio.to_thread(catalog.match, pv.artist, pv.title)
                source = "catalog"
                hit = cm.level in ("exact", "likely")
                lib = LibraryMatch("similar" if hit else "new", 0.0, "", cm.paths[0] if hit else "",
                                   cm.lossless if hit else None, ["metadata.csv unavailable: catalog names only"])
            stg = await asyncio.to_thread(staging_match, cfg.staging_dir, pv.artist, pv.title) if pv.title else None
        except Exception:  # a broken library/catalog must never turn a preview into a 500
            lib = LibraryMatch("unknown", 0.0, "", "", None, ["library check failed"])
            source, stg = "metadata", None
        status = lib.status
        if status in ("new", "unknown", "unavailable") and stg and stg.level != "none":
            status = "in_staging"
        app.state.pcache[p.normalized] = status
        app.state.pcache.move_to_end(p.normalized)
        while len(app.state.pcache) > PCACHE_MAX:
            app.state.pcache.popitem(last=False)
        return {
            "preview": asdict(pv),
            "library": {**asdict(lib), "status": status, "source": source, "metadata_mtime": library.mtime(),
                        "default_checked": status not in NEEDS_FORCE, "needs_force": status in NEEDS_FORCE},
            "staging": asdict(stg) if stg else None,
            "duplicate": bool(store.find_by_key(p.kind, p.id, p.track_id)),
            "large": bool(pv.tracks and pv.tracks > s["preview_max_tracks"]),
        }

    app.state.previews = PreviewService(getter=None)
    app.state.pcache = OrderedDict()

    @app.post("/api/queue")
    def queue(body: QueueIn):
        results = []
        for it in body.items:
            try:
                p = normalize(it.url, storefront())
            except UrlError as e:
                results.append({"url": it.url, "id": None, "error": str(e)})
                continue
            if store.find_by_key(p.kind, p.id, p.track_id):
                results.append({"url": p.normalized, "id": None, "error": "already in queue or history"})
                continue
            status = app.state.pcache.get(p.normalized)
            if status in NEEDS_FORCE and not it.force:
                results.append({"url": p.normalized, "id": None,
                                "error": f"{status.replace('_', ' ')}: use download anyway to queue it"})
                continue
            new_id = store.add_item(p.normalized, p.original, p.kind, p.id, p.track_id, it.title, it.artist, it.tracks)
            if status:
                store.update_item(new_id, library_status=status)
            results.append({"url": p.normalized, "id": new_id, "error": None})
        runner.wake()
        bus.publish({"type": "state"})
        return {"results": results}

    @app.post("/api/queue/reorder")
    def reorder(body: ReorderIn):
        store.reorder(body.ids)
        bus.publish({"type": "state"})
        return {"ok": True}

    @app.get("/api/queue/{item_id}")
    def item_detail(item_id: int):
        item = _public(need(item_id))
        item["tracks"] = store.list_tracks(item_id)
        return item

    @app.post("/api/queue/{item_id}/{action}")
    async def item_action(item_id: int, action: str):
        item = need(item_id)
        running = item_id == runner.current_id
        if action == "cancel":
            await runner.cancel(item_id)
        elif action in ("retry", "retry_original"):
            if running:
                raise HTTPException(409, "item is running")
            other = store.find_by_key(item["kind"], item["ext_id"], item["track_id"])
            if other and other["id"] != item_id:
                raise HTTPException(409, f"already queued as item {other['id']}")
            fields = {"status": "queued", "error_msg": None, "errors": 0, "attempts": 0, "not_before": None}
            if action == "retry_original":
                try:
                    fields["url"] = normalize(item["original_url"], None).normalized
                except UrlError as e:
                    raise HTTPException(422, str(e))
            store.update_item(item_id, **fields)
            runner.wake()
        elif action == "remove":
            if running:
                raise HTTPException(409, "cancel the running item first")
            store.remove_item(item_id)
        else:
            raise HTTPException(404, "unknown action")
        bus.publish({"type": "state"})
        return {"ok": True}

    @app.post("/api/pause")
    async def pause():
        await runner.pause()
        return {"ok": True}

    @app.post("/api/resume")
    async def resume():
        await runner.resume()
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings():
        return store.get_settings()

    @app.put("/api/settings")
    def put_settings(body: dict):
        try:
            out = store.put_settings(body)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        bus.publish({"type": "state"})
        return out

    # ---- followed artists and their releases --------------------------------
    def _enqueue_release(rel: dict):
        p = normalize(rel["url"], storefront())
        if store.find_by_key(p.kind, p.id, p.track_id):
            return None, "already in queue or history"
        return store.add_item(p.normalized, rel["url"], p.kind, p.id, p.track_id, rel["title"], rel["artist"], rel["track_count"]), None

    @app.get("/api/follows")
    def follows():
        news = store.new_counts()
        return [{**f, "new": news.get(f["id"], 0)} for f in store.list_follows()]

    @app.post("/api/follows")
    async def follow(body: FollowIn):
        sf = storefront() or "jp"
        try:
            artist_id, name = await watcher.client.resolve(body.input, sf)
        except ReleaseError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            raise HTTPException(502, f"Apple lookup failed: {type(e).__name__}")
        fid, created = store.add_follow(artist_id, name, sf, (body.label_filter or "").strip() or None)
        if created:
            asyncio.create_task(watcher.check_one(fid))  # baseline the back catalogue right away
        bus.publish({"type": "releases"})
        return {"id": fid, "artist_id": artist_id, "name": name, "created": created}

    @app.put("/api/follows/{follow_id}")
    def patch_follow(follow_id: int, body: FollowPatch):
        if not store.get_follow(follow_id):
            raise HTTPException(404, "no such follow")
        store.update_follow(follow_id, label_filter=(body.label_filter or "").strip() or None)
        bus.publish({"type": "releases"})
        return store.get_follow(follow_id)

    @app.delete("/api/follows/{follow_id}")
    def unfollow(follow_id: int):
        store.remove_follow(follow_id)
        bus.publish({"type": "releases"})
        return {"ok": True}

    @app.post("/api/follows/check")
    async def check_follows():
        asyncio.create_task(watcher.check_all(force=True))  # own task: works even when the polling loop is off
        return {"ok": True}

    @app.get("/api/releases")
    def releases(status: str = "new"):
        if status not in ("new", "seen", "added", "dismissed", "all"):
            raise HTTPException(422, "status must be new, seen, added, dismissed or all")
        rows = store.list_releases(None if status == "all" else status)
        return [{**r, "queued": bool(store.find_by_key("album", r["collection_id"], None))} for r in rows]

    @app.post("/api/releases/bulk")
    def releases_bulk(body: BulkIn):
        if body.action not in ("add", "dismiss", "restore"):
            raise HTTPException(422, "action must be add, dismiss or restore")
        results = []
        for rid in body.ids:
            rel = store.get_release(rid)
            if not rel:
                results.append({"id": rid, "error": "no such release"})
                continue
            if body.action == "add":
                item_id, err = _enqueue_release(rel)
                if item_id or err:  # already queued counts as handled
                    store.set_release_status(rid, "added")
                results.append({"id": rid, "item_id": item_id, "error": err})
            else:
                store.set_release_status(rid, "dismissed" if body.action == "dismiss" else "new")
                results.append({"id": rid, "error": None})
        runner.wake()
        bus.publish({"type": "state"})
        bus.publish({"type": "releases"})
        return {"results": results}

    @app.get("/api/history")
    def history():
        return [_public(i) for i in store.done_items()]

    @app.get("/api/log")
    def log(limit: int = 200):
        return store.tail_log(max(1, min(limit, 2000)))

    @app.get("/api/events")
    async def events():
        sub = bus.subscribe()

        async def gen():
            try:
                yield ": hello\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(sub.get(), 15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    STATIC.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return app
