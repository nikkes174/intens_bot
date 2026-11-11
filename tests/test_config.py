from app.config import get_settings, is_admin, parse_admin_ids


def test_parse_admin_ids_supports_single_and_multiple_values():
    assert parse_admin_ids("123456789") == (123456789,)
    assert parse_admin_ids("123, 456, 123, invalid, -7, 0") == (123, 456)


def test_is_admin_accepts_every_configured_administrator(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", (101, 202))

    assert is_admin(101)
    assert is_admin(202)
    assert not is_admin(303)
