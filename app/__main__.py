import uvicorn

from app.api import create_app
from app.config import from_env

cfg = from_env()
uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
