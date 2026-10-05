"""Самостоятельные данные в отдельной личной схеме; рабочие данные не меняются."""

from pathlib import Path

import psycopg
from psycopg import sql
import pytest
from fastapi.testclient import TestClient

from app.db import settings
from app.main import create_app, hash_password

TEST_SCHEMA = "artem_yankovoy_test"
ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "test-password"
PASSWORD_HASH = hash_password(PASSWORD, "artem-yankovoy-fixed-test-salt")


@pytest.fixture
def dataset(monkeypatch):
    monkeypatch.setenv("PERSONAL_PREFIX", "artem_yankovoy")
    monkeypatch.setenv("APP_SCHEMA", TEST_SCHEMA)
    monkeypatch.setenv("SECRET_KEY", "artem-yankovoy-isolated-test-session-key")
    config = settings()
    assert config["schema"] == TEST_SCHEMA
    schema_source = (ROOT / "schema.sql").read_text(encoding="utf-8").replace("__SCHEMA__", TEST_SCHEMA)
    with psycopg.connect(config["database_url"]) as connection:
        connection.execute(schema_source)
        # Только пять таблиц, созданных тестами в фиксированной отдельной схеме.
        connection.execute(sql.SQL("TRUNCATE {}.appointments, {}.slots, {}.specialists, {}.services, {}.users RESTART IDENTITY").format(*[sql.Identifier(TEST_SCHEMA)] * 5))
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO users(username,full_name,password_hash) VALUES (%s,%s,%s)", [("demo", "Тестовый пользователь", PASSWORD_HASH), ("other", "Другой пользователь", PASSWORD_HASH)])
            cursor.executemany("INSERT INTO services(name,duration_minutes,price) VALUES (%s,%s,%s)", [("Консультация", 30, 1500), ("Длительная консультация", 90, 3000), ("Диагностика", 45, 2200)])
            cursor.executemany("INSERT INTO specialists(full_name,service_ids) VALUES (%s,%s)", [("Первый специалист", [1, 2]), ("Второй специалист", [1, 3])])
            cursor.executemany("INSERT INTO slots(specialist_id,start_at,end_at) VALUES (%s,%s,%s)", [
                (1,"2026-10-05 09:00","2026-10-05 10:00"), (1,"2026-10-05 09:15","2026-10-05 10:15"),
                (1,"2026-10-05 09:30","2026-10-05 10:30"), (1,"2026-10-05 11:00","2026-10-05 12:00"),
                (1,"2026-10-05 13:00","2026-10-05 14:00"), (1,"2026-10-05 15:00","2026-10-05 16:00"),
                (2,"2026-10-05 09:00","2026-10-05 10:00"), (2,"2026-10-06 09:00","2026-10-06 10:00"),
                (2,"2026-10-06 10:00","2026-10-06 10:15"), (1,"2026-10-07 10:00","2026-10-07 12:00"),
            ])
            cursor.executemany("""INSERT INTO appointments(user_id,specialist_id,service_id,slot_id,client_name,status,start_at,end_at,created_at,cancelled_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'2026-10-01 12:00',%s)""", [
                (1,1,1,1,"Клиент первый","booked","2026-10-05 09:00","2026-10-05 09:30",None),
                (1,1,1,4,"Клиент второй","cancelled","2026-10-05 11:00","2026-10-05 11:30","2026-10-02 12:00"),
                (1,1,1,5,"Клиент третий","completed","2026-10-05 13:00","2026-10-05 13:30",None),
                (2,2,1,7,"Другой клиент","booked","2026-10-05 09:00","2026-10-05 09:30",None),
                (1,2,3,8,"Клиент пятый","cancelled","2026-10-06 09:00","2026-10-06 09:45","2026-10-02 13:00"),
            ])
    return config


@pytest.fixture
def client(dataset):
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def authorized_client(client):
    response = client.post("/api/login", json={"username": "demo", "password": PASSWORD})
    assert response.status_code == 200
    return client
