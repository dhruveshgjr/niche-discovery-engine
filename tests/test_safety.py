from scraper.safety import is_safe


def test_business_language_is_safe():
    assert is_safe("I want to kill the manual process and automate data entry")
    assert is_safe("Our SaaS has no API, exports are manual")
    assert is_safe("")
    assert is_safe(None)


def test_adult_content_blocked():
    assert not is_safe("check out my porn site")
    assert not is_safe("NSFW content here")


def test_illegal_trade_blocked():
    assert not is_safe("where to buy cocaine in bulk")
    assert not is_safe("cvv dumps fresh 2026")


def test_weapons_sales_blocked():
    assert not is_safe("cheap guns for sale")
    assert not is_safe("buy ammunition online")


def test_hate_speech_blocked():
    assert not is_safe("white power forever")
    assert not is_safe("kill all those people")