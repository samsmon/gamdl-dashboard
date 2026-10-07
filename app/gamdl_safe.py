"""gamdl wrapper that adds randomized delays so bulk downloads don't hammer Apple's API.

gamdl has no native rate limiting (only retries on 429/5xx), so this monkeypatches
AppleMusicDownloader in-process, then runs the normal gamdl CLI. All gamdl args pass through.

  python -m app.gamdl_safe -r urls.txt      # one URL per line, processed sequentially
  python -m app.gamdl_safe <album-url> ...

Delays (seconds, "min-max", random uniform), override via env:
  GAMDL_TRACK_DELAY   default 8-20     after each downloaded track
  GAMDL_ALBUM_DELAY   default 60-180   before each URL (album/playlist/artist) after the first
  GAMDL_STOREFRONT    default jp       rewrite URL storefront (id/us/...) so titles are in original language
                                       (jp gives e.g. "01 心の奥.m4a" where id gave "Deep Down"); set empty to disable
  GAMDL_SAFE_LOCK     default <tmpdir>/gamdl-safe.lock
Single instance only (flock on the lock file; no lock on Windows).
"""
import asyncio
import os
import random
import re
import sys
import tempfile


def rng(name, default, env=None):
    env = os.environ if env is None else env
    lo, hi = (float(x) for x in env.get(name, default).split("-"))
    return lo, hi


def rewrite_storefront(url, storefront):
    """Rewrite music.apple.com/<storefront>/ to the given one so titles come back in the original language."""
    if not storefront:
        return url
    return re.sub(r"(https?://(?:classical\.)?music\.apple\.com/)[a-z]{2}/", rf"\g<1>{storefront}/", url, count=1)


def forced_storefront(env=None):
    """Storefront to force for metadata/URLs; empty = original storefront (used by the dashboard's region fallback run)."""
    env = os.environ if env is None else env
    return env.get("GAMDL_STOREFRONT", "jp").strip().lower()


def acquire_lock(path=None):
    """Hold an exclusive lock for the process lifetime; refuse to run in parallel. Returns the open file (keep a reference)."""
    try:
        import fcntl
    except ImportError:  # Windows
        return None
    path = path or os.environ.get("GAMDL_SAFE_LOCK") or os.path.join(tempfile.gettempdir(), "gamdl-safe.lock")
    lock = open(path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("gamdl-safe already running (lock held) - refusing to run in parallel")
    return lock


def main():
    track = rng("GAMDL_TRACK_DELAY", "8-20")
    album = rng("GAMDL_ALBUM_DELAY", "60-180")
    storefront = forced_storefront()  # "" disables rewriting
    lock = acquire_lock()  # noqa: F841 (must stay referenced)

    from gamdl.downloader.downloader import AppleMusicDownloader
    from gamdl.downloader.exceptions import (
        GamdlDownloaderMediaFileExistsError,
        GamdlDownloaderSyncedLyricsOnlyError,
    )

    from gamdl.api.apple_music import AppleMusicApi

    if storefront:
        # gamdl 3.x ignores the URL storefront and uses the account storefront (id), whose catalog is romaji;
        # force the metadata storefront so titles/artists come back in the original language.
        orig_init = AppleMusicApi.__init__

        def init_forced(self, *a, **kw):
            orig_init(self, *a, **kw)
            print(f"[gamdl-safe] metadata storefront {self.storefront} -> {storefront}", flush=True)
            self.storefront = storefront

        AppleMusicApi.__init__ = init_forced

    orig_get = AppleMusicDownloader.get_download_item_from_url
    orig_download = AppleMusicDownloader.download
    state = {"first": True}

    async def get(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            new = rewrite_storefront(args[0], storefront)
            if new != args[0]:
                print(f"[gamdl-safe] storefront -> {storefront}: {new}", flush=True)
            args = (new, *args[1:])
        elif isinstance(kwargs.get("url"), str):
            kwargs["url"] = rewrite_storefront(kwargs["url"], storefront)
        if not state["first"]:
            d = random.uniform(*album)
            print(f"[gamdl-safe] album delay {d:.0f}s", flush=True)
            await asyncio.sleep(d)
        state["first"] = False
        async for item in orig_get(self, *args, **kwargs):
            yield item

    async def download_delayed(self, item):
        if item.media.partial:
            return await orig_download(self, item)  # gamdl's first pass over each track is a no-op (returns early): no fetch, no delay
        try:
            await orig_download(self, item)
        except (GamdlDownloaderMediaFileExistsError, GamdlDownloaderSyncedLyricsOnlyError):
            raise  # nothing fetched, no need to wait
        except BaseException:
            await asyncio.sleep(random.uniform(*track) * 2)  # errors back off harder
            raise
        d = random.uniform(*track)
        print(f"[gamdl-safe] track delay {d:.0f}s", flush=True)
        await asyncio.sleep(d)

    AppleMusicDownloader.get_download_item_from_url = get
    AppleMusicDownloader.download = download_delayed

    from gamdl.cli.cli import main as gamdl_main

    gamdl_main()


if __name__ == "__main__":
    main()
