"""Две независимые генерации каждого профиля в личной тестовой схеме."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from app.db import load_env, settings
from seed import seed

TABLES = {"users", "specialists", "services", "slots", "appointments"}


def confirm_generated_schema(config: dict) -> None:
    """Не сбрасывать схему с таблицами или пользователями вне этого проекта."""
    expected = config["prefix"] + "_test"
    if config["schema"] != expected:
        raise RuntimeError("Проверка разрешена только в личной тестовой схеме")
    with psycopg.connect(config["database_url"], connect_timeout=10) as connection:
        rows = connection.execute(
            "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema=%s",
            (expected,),
        ).fetchall()
        if any(name not in TABLES or kind != "BASE TABLE" for name, kind in rows):
            raise RuntimeError("В тестовой схеме есть объекты вне пяти таблиц проекта; сброс отклонён")
        if any(name == "users" for name, _ in rows):
            users = connection.execute(
                sql.SQL("SELECT username,full_name FROM {}.users").format(sql.Identifier(expected))
            ).fetchall()
            allowed = {
                (config["prefix"], "Янковой Артем Александрович"),
                ("demo", "Тестовый пользователь"),
                ("other", "Другой пользователь"),
            }
            if any(tuple(user) not in allowed for user in users):
                raise RuntimeError("В тестовой схеме есть неучебные пользователи; сброс отклонён")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset-generated-schema", action="store_true",
        help="Явно разрешить пересоздание только учебной схемы PERSONAL_PREFIX_test",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "results/reproducibility.json")
    args = parser.parse_args()
    if not args.reset_generated_schema:
        parser.error("Нужен явный --reset-generated-schema: проверка пересоздаёт личную тестовую схему")
    load_env()
    original = settings()
    os.environ["APP_SCHEMA"] = original["prefix"] + "_test"
    config = settings()
    confirm_generated_schema(config)
    result = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "prefix": config["prefix"],
        "schema": config["schema"],
        "method": "two independent seeded runs; counts and ordered row checksums read by PostgreSQL",
        "profiles": {},
    }
    for profile in ("small", "work"):
        print(f"{config['schema']}: {profile}, запуск 1/2", flush=True)
        first = seed(profile=profile, reset=True)
        print(f"{config['schema']}: {profile}, запуск 2/2", flush=True)
        second = seed(profile=profile, reset=True)
        counts_equal = first["counts"] == second["counts"]
        checksums_equal = first["table_checksums_md5"] == second["table_checksums_md5"]
        sha_equal = first["content_sha256"] == second["content_sha256"]
        result["profiles"][profile] = {
            "run1": first, "run2": second,
            "counts_equal": counts_equal,
            "table_checksums_equal": checksums_equal,
            "content_sha256_equal": sha_equal,
            "ok": counts_equal and checksums_equal and sha_equal,
        }
        print(f"{profile}: counts_equal={counts_equal}, table_checksums_equal={checksums_equal}, SHA256_equal={sha_equal}", flush=True)
    result["ok"] = all(profile["ok"] for profile in result["profiles"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Результат: {args.output}; воспроизводимость={result['ok']}", flush=True)
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
