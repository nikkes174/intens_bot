from sqlalchemy import create_engine, inspect, text

from app.database import _migrate_sqlite


def test_old_web_watch_table_gets_person_type_without_losing_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE web_watches ("
                "id INTEGER PRIMARY KEY, "
                "query VARCHAR(255) NOT NULL, "
                "clarification VARCHAR(255), "
                "enabled BOOLEAN NOT NULL, "
                "created_at DATETIME"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO web_watches "
                "(id, query, clarification, enabled, created_at) "
                "VALUES (1, 'Иван Иванов', NULL, 1, NULL)"
            )
        )

        _migrate_sqlite(connection)

        columns = {column["name"] for column in inspect(connection).get_columns("web_watches")}
        row = connection.execute(
            text("SELECT query, watch_type FROM web_watches WHERE id = 1")
        ).one()

    assert "watch_type" in columns
    assert row == ("Иван Иванов", "person")
