import os
import shlex
from dataclasses import dataclass
from typing import Mapping


@dataclass
class Config:
    db_path: str
    gamdl_cmd: list
    extra_args: list
    staging_dir: str
    disk_path: str
    cookies_path: str
    catalog_path: str
    host: str
    port: int
    autostart: bool
    library_csv: str = ""


def _split(value: str) -> list:
    return shlex.split(value, posix=(os.name != "nt"))


def from_env(env: Mapping[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    return Config(
        db_path=e.get("GAMDL_DASH_DB", "data/dashboard.sqlite"),
        gamdl_cmd=_split(e.get("GAMDL_DASH_GAMDL_CMD", "/usr/local/bin/gamdl-safe")),
        extra_args=_split(e.get("GAMDL_DASH_EXTRA_ARGS", "--no-exceptions")),
        staging_dir=e.get("GAMDL_DASH_STAGING", "/mnt/hdd-backup/music/_gamdl-incoming"),
        disk_path=e.get("GAMDL_DASH_DISK_PATH", "/mnt/hdd-backup"),
        cookies_path=e.get("GAMDL_DASH_COOKIES", "/root/.gamdl/cookies.txt"),
        catalog_path=e.get("GAMDL_DASH_CATALOG", "/mnt/hdd-backup/music/catalog.sqlite"),
        host=e.get("GAMDL_DASH_HOST", "0.0.0.0"),
        port=int(e.get("GAMDL_DASH_PORT", "8110")),
        autostart=e.get("GAMDL_DASH_AUTOSTART", "1") not in ("0", "false", "no"),
        library_csv=e.get("GAMDL_DASH_LIBRARY_CSV", "/mnt/hdd-backup/music/metadata.csv"),
    )
