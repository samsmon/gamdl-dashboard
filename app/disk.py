import shutil


def free_bytes(path: str):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
