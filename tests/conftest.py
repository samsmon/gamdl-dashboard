import pytest

from app.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.sqlite"))
