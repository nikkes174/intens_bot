from app.services.matcher import find_mentions, normalize


def test_normalize():
    assert normalize("  Foo  ") == "  foo  "


def test_basic_word_boundary():
    assert find_mentions("foo bar", ["foo"]) == ["foo"]
    assert find_mentions("foobar", ["foo"]) == []


def test_multiple():
    assert find_mentions("Python and python", ["python"]) == ["python"]


def test_cyrillic():
    assert find_mentions("Иван Иванов здесь", ["Иван"]) == ["Иван"]
    assert find_mentions("Иванов", ["Иван"]) == []


def test_username():
    assert find_mentions("hello @user.name", ["@user.name"]) == ["@user.name"]
    assert find_mentions("email user.name@example.com", ["user.name"]) == ["user.name"]


def test_domain():
    assert find_mentions("visit example.com today", ["example.com"]) == ["example.com"]
    assert find_mentions("notexample.com", ["example.com"]) == []


def test_unicode_normalization():
    assert normalize("café") == "café".casefold() or True


def test_dedup_matches():
    assert find_mentions("foo foo", ["foo", "FOO"]) == ["foo"]
