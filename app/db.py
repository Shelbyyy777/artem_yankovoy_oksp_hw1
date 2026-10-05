"""Соединения PostgreSQL и измерение времени доступа к базе данных."""

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import os
import re
import time

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    """Простой .env без подстановки переменных; системное окружение приоритетнее."""
    path = ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def settings() -> dict:
    load_env()
    prefix = os.getenv("PERSONAL_PREFIX", "artem_yankovoy")
    schema = os.getenv("APP_SCHEMA", prefix)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", prefix):
        raise ValueError("PERSONAL_PREFIX должен быть безопасным SQL-идентификатором")
    if schema not in (prefix, prefix + "_test"):
        raise ValueError("APP_SCHEMA допускает только личную схему и личную тестовую схему")
    return {
        "prefix": prefix,
        "schema": schema,
        "database_url": os.getenv("DATABASE_URL", "postgresql://app:changeme@localhost:5432/artem_yankovoy_hw1"),
        "secret_key": os.getenv("SECRET_KEY", "changeme-local-homework-key"),
    }


@dataclass
class QueryStats:
    db_ms: float = 0.0
    queries: int = 0


query_stats: ContextVar[QueryStats | None] = ContextVar("query_stats", default=None)


class Database:
    def __init__(self, connection):
        self.connection = connection

    def _query(self, sql, params=None, mode="all"):
        started = time.perf_counter()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params)
                if mode == "one":
                    return cursor.fetchone()
                if mode == "all":
                    return cursor.fetchall()
                return cursor.rowcount
        finally:
            stats = query_stats.get()
            if stats is not None:
                stats.queries += 1
                stats.db_ms += (time.perf_counter() - started) * 1000

    def one(self, sql, params=None):
        return self._query(sql, params, "one")

    def all(self, sql, params=None):
        return self._query(sql, params, "all")

    def execute(self, sql, params=None):
        return self._query(sql, params, "none")

    def commit(self):
        """Фиксация записи до ответа: клиент получает уже сохранённый результат."""
        started = time.perf_counter()
        try:
            self.connection.commit()
        finally:
            stats = query_stats.get()
            if stats is not None:
                stats.db_ms += (time.perf_counter() - started) * 1000


def connect():
    config = settings()
    return psycopg.connect(config["database_url"], row_factory=dict_row,
                           options=f"-c search_path={config['schema']},public")


def get_db():
    """Одна транзакция на запрос; исключения откатывают изменения."""
    with connect() as connection:
        yield Database(connection)
