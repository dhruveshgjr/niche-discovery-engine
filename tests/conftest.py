import pytest

from config import Settings
from database.db import create_db_engine, init_db


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/test.db",
        demo_mode=True,
        items_per_source=5,
        request_delay_seconds=0,
        yc_delay_seconds=0,
        llm_max_retries=1,
    )


@pytest.fixture
def engine(settings):
    eng = create_db_engine(settings.resolved_database_url)
    init_db(eng)
    return eng