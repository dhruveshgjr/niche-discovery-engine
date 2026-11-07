import httpx

from config import Settings, resolve_demo_mode


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"demo_mode": None, **overrides})


def test_explicit_demo_mode_wins():
    assert resolve_demo_mode(_settings(demo_mode=True)) is True
    assert resolve_demo_mode(_settings(demo_mode=False)) is False


def test_unreachable_lm_studio_means_demo(monkeypatch):
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", raise_connect_error)
    assert resolve_demo_mode(_settings()) is True


def test_non_dict_model_list_means_demo(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return ["unexpected-shape"]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())
    assert resolve_demo_mode(_settings()) is True


def test_loaded_lm_studio_means_live(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"id": "qwen"}]}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())
    assert resolve_demo_mode(_settings()) is False


def test_empty_model_list_means_demo(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": []}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())
    assert resolve_demo_mode(_settings()) is True
