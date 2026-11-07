from utils import clamp, parse_date, parse_json_loose, retry_call, truncate_text


def test_parse_json_plain():
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_parse_json_fenced():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_prose():
    assert parse_json_loose('Sure! Here is the result: {"a": [1, 2]} — hope this helps') == {"a": [1, 2]}


def test_parse_json_invalid():
    try:
        parse_json_loose("no json at all")
        assert False, "should raise"
    except ValueError:
        pass


def test_truncate_keeps_head_and_tail():
    text = "HEAD" + "x" * 100 + "TAIL"
    out = truncate_text(text, 20)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert len(out) < len(text)
    assert truncate_text("short", 100) == "short"


def test_retry_call_succeeds_after_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert retry_call(flaky, attempts=3, base_delay=0, retry_on=(ConnectionError,)) == "ok"
    assert calls["n"] == 3


def test_retry_call_exhausts():
    def always_fails():
        raise ConnectionError("down")

    try:
        retry_call(always_fails, attempts=2, base_delay=0, retry_on=(ConnectionError,))
        assert False, "should raise"
    except ConnectionError:
        pass


def test_clamp():
    assert clamp(15) == 10.0
    assert clamp(-3) == 0.0
    assert clamp(4.5) == 4.5


def test_parse_date():
    assert parse_date("2026-11-01T10:00:00Z").year == 2026
    assert parse_date(None) is None
    assert parse_date("garbage") is None