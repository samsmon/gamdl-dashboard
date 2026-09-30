from app.config import from_env


def test_defaults():
    c = from_env({})
    assert c.port == 8110
    assert c.host == "0.0.0.0"
    assert c.gamdl_cmd == ["/usr/local/bin/gamdl-safe"]
    assert c.extra_args == ["--no-exceptions"]
    assert c.staging_dir == "/mnt/hdd-backup/music/_gamdl-incoming"
    assert c.library_csv == "/mnt/hdd-backup/music/metadata.csv"
    assert c.autostart is True


def test_overrides():
    c = from_env({
        "GAMDL_DASH_PORT": "9000",
        "GAMDL_DASH_GAMDL_CMD": "py -3 tools/fake_gamdl_safe.py",
        "GAMDL_DASH_EXTRA_ARGS": "",
        "GAMDL_DASH_AUTOSTART": "0",
    })
    assert c.port == 9000
    assert c.gamdl_cmd == ["py", "-3", "tools/fake_gamdl_safe.py"]
    assert c.extra_args == []
    assert c.autostart is False
