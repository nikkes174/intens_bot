"""Import a CSV, JSON array, or JSONL media catalogue into the registry.

Imported rows are deliberately disabled and unverified.  Discovery or an
administrator must validate an endpoint before it is enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections.abc import Iterator
from pathlib import Path

from app.database import close_database, init_database
from app.services.web_monitor.media_registry import bulk_import_sources


def _rows_from_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _rows_from_json(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        if path.suffix.casefold() in {".jsonl", ".ndjson"}:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield {str(key): str(item or "") for key, item in value.items()}
            return
        values = json.load(handle)
    if not isinstance(values, list):
        raise TypeError("JSON должен содержать массив объектов")
    for value in values:
        if isinstance(value, dict):
            yield {str(key): str(item or "") for key, item in value.items()}


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    if path.suffix.casefold() == ".csv":
        yield from _rows_from_csv(path)
    elif path.suffix.casefold() in {".json", ".jsonl", ".ndjson"}:
        yield from _rows_from_json(path)
    else:
        raise ValueError("Поддерживаются только CSV, JSON и JSONL")


async def _run(path: Path) -> None:
    await init_database()
    try:
        result = await bulk_import_sources(read_rows(path))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="CSV, JSON-массив или JSONL")
    args = parser.parse_args()
    asyncio.run(_run(args.file))


if __name__ == "__main__":
    main()
