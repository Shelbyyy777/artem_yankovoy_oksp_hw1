"""Контракт проверяется через приложение и реальный PostgreSQL без запущенного HTTP-сервера."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import COOKIE_NAME, create_app
from .conftest import PASSWORD

PERIOD = {"from_at": "2026-10-01T00:00:00", "to_at": "2026-10-08T00:00:00"}
CARD_KEYS = {"id", "slot_id", "client_name", "status", "start_at", "end_at", "created_at", "cancelled_at", "specialist", "service", "user"}


def assert_card(card):
    assert set(card) == CARD_KEYS
    assert set(card["specialist"]) == {"id", "full_name"}
    assert set(card["service"]) == {"id", "name", "duration_minutes", "price"}
    assert set(card["user"]) == {"id", "username", "full_name"}
    assert "password_hash" not in card["user"]


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/me", None), ("POST", "/api/logout", None),
    ("GET", "/api/services", None), ("GET", "/api/specialists", None),
    ("GET", "/api/slots?service_id=1", None), ("GET", "/api/appointments", None),
    ("GET", "/api/appointments/1", None), ("GET", "/api/summary", None),
    ("POST", "/api/appointments", {"slot_id": 6,"service_id": 1,"client_name": "Клиент"}),
    ("POST", "/api/appointments/1/cancel", None),
])
def test_all_protected_operations_require_authorization(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert isinstance(response.json()["detail"], str)


def test_login_me_and_logout_contract(client):
    bad = client.post("/api/login", json={"username": "demo", "password": "wrong"})
    assert bad.status_code == 401
    response = client.post("/api/login", json={"username": "demo", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"user": {"id": 1,"username": "demo","full_name": "Тестовый пользователь"}}
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert client.get("/api/me").json() == response.json()
    assert client.post("/api/logout").json() == {"ok": True}
    assert client.get("/api/me").status_code == 401


def test_tampered_session_is_unauthorized(authorized_client):
    cookie = authorized_client.cookies.get(COOKIE_NAME)
    authorized_client.cookies.clear()
    authorized_client.cookies.set(COOKIE_NAME, cookie[:-1] + ("0" if cookie[-1] != "0" else "1"))
    assert authorized_client.get("/api/appointments").status_code == 401


def test_reference_lists_contract(authorized_client):
    services = authorized_client.get("/api/services")
    specialists = authorized_client.get("/api/specialists")
    assert services.status_code == specialists.status_code == 200
    assert len(services.json()["items"]) == 3
    assert services.json()["items"][0] == {"id":1,"name":"Консультация","duration_minutes":30,"price":1500.0}
    assert specialists.json()["items"] == [{"id":1,"full_name":"Первый специалист","service_ids":[1,2]}, {"id":2,"full_name":"Второй специалист","service_ids":[1,3]}]


def test_appointment_list_pagination_filters_and_card(authorized_client):
    first = authorized_client.get("/api/appointments", params={"page":1,"size":2})
    second = authorized_client.get("/api/appointments", params={"page":2,"size":2})
    assert first.status_code == second.status_code == 200
    assert set(first.json()) == {"items","total","page","size"}
    assert first.json()["total"] == 5
    assert first.json()["page"] == 1 and first.json()["size"] == 2
    assert [item["id"] for item in first.json()["items"]] == [5,3]
    assert [item["id"] for item in second.json()["items"]] == [2,4]
    filtered = authorized_client.get("/api/appointments", params={"status":"booked","specialist_id":1}).json()
    assert filtered["total"] == 1
    assert [item["id"] for item in filtered["items"]] == [1]
    card = authorized_client.get("/api/appointments/1")
    assert card.status_code == 200
    assert_card(card.json())
    assert card.json()["client_name"] == "Клиент первый"
    assert card.json()["user"]["id"] == 1


def test_free_slots_apply_duration_specialist_and_conflict_rules(authorized_client):
    response = authorized_client.get("/api/slots", params={"service_id":1, "page":1,"size":100, **PERIOD})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"items","total","page","size"}
    assert data["total"] == 5
    assert [item["id"] for item in data["items"]] == [3,4,6,8,10]
    assert set(data["items"][0]) == {"id","specialist_id","start_at","end_at","specialist","service_ids"}
    long_service = authorized_client.get("/api/slots", params={"service_id":2,**PERIOD}).json()
    assert [item["id"] for item in long_service["items"]] == [10]
    second_specialist = authorized_client.get("/api/slots", params={"service_id":1,"specialist_id":2,**PERIOD}).json()
    assert [item["id"] for item in second_specialist["items"]] == [8]
    page = authorized_client.get("/api/slots", params={"service_id":1,"page":2,"size":2,**PERIOD}).json()
    assert [item["id"] for item in page["items"]] == [6,8]


def test_booking_cancellation_frees_slot_and_rebooking_succeeds(authorized_client):
    body = {"slot_id":6,"service_id":1,"client_name":"  Новый клиент  "}
    created = authorized_client.post("/api/appointments", json=body)
    assert created.status_code == 201
    card = created.json()
    assert_card(card)
    assert card["status"] == "booked" and card["client_name"] == "Новый клиент"
    assert card["start_at"] == "2026-10-05T15:00:00"
    assert card["end_at"] == "2026-10-05T15:30:00"
    assert card["user"]["id"] == 1
    assert authorized_client.post("/api/appointments", json=body).status_code == 409
    free_before = authorized_client.get("/api/slots",params={"service_id":1,**PERIOD}).json()
    assert 6 not in [item["id"] for item in free_before["items"]]
    cancelled = authorized_client.post(f"/api/appointments/{card['id']}/cancel")
    assert cancelled.status_code == 200
    assert_card(cancelled.json())
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["cancelled_at"]
    # Повторная отмена не создаёт дополнительной отмены и не меняет её дату.
    assert authorized_client.post(f"/api/appointments/{card['id']}/cancel").json() == cancelled.json()
    free_after = authorized_client.get("/api/slots",params={"service_id":1,**PERIOD}).json()
    assert 6 in [item["id"] for item in free_after["items"]]
    assert authorized_client.post("/api/appointments",json=body).status_code == 201


def test_overlapping_different_slot_rejected_but_adjacent_allowed(authorized_client):
    overlapping = authorized_client.post("/api/appointments",json={"slot_id":2,"service_id":1,"client_name":"Пересечение"})
    adjacent = authorized_client.post("/api/appointments",json={"slot_id":3,"service_id":1,"client_name":"Соседний приём"})
    assert overlapping.status_code == 409
    assert adjacent.status_code == 201
    assert adjacent.json()["start_at"] == "2026-10-05T09:30:00"


def test_cancel_owner_and_completed_restrictions(authorized_client):
    assert authorized_client.post("/api/appointments/4/cancel").status_code == 403
    assert authorized_client.post("/api/appointments/3/cancel").status_code == 409
    assert authorized_client.get("/api/appointments/4").json()["status"] == "booked"
    assert authorized_client.get("/api/appointments/3").json()["status"] == "completed"


def test_summary_counts_all_data_and_calculates_load(authorized_client):
    response = authorized_client.get("/api/summary",params=PERIOD)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"period","total_appointments","cancelled_appointments","cancelled_share","specialists"}
    assert data["period"] == PERIOD
    assert data["total_appointments"] == 5
    assert data["cancelled_appointments"] == 2
    assert data["cancelled_share"] == pytest.approx(0.4)
    first,second = data["specialists"]
    assert set(first) == {"id","full_name","slot_minutes","booked_minutes","load_percent","total_appointments","cancelled_appointments"}
    assert first["slot_minutes"] == 480 and first["booked_minutes"] == 60
    assert first["load_percent"] == 12.5
    assert second["slot_minutes"] == 135 and second["booked_minutes"] == 30
    assert second["load_percent"] == 22.22


def test_summary_clips_intervals_and_empty_period(authorized_client):
    data = authorized_client.get("/api/summary", params={"from_at":"2026-10-05T09:15:00","to_at":"2026-10-05T09:45:00"}).json()
    assert data["specialists"][0]["slot_minutes"] == 75
    assert data["specialists"][0]["booked_minutes"] == 15
    assert data["specialists"][0]["load_percent"] == 20
    assert data["total_appointments"] == 0  # Приёмы №1 и №4 начались до левой границы периода.
    empty = authorized_client.get("/api/summary", params={"from_at":"2028-01-01T00:00:00","to_at":"2028-01-02T00:00:00"}).json()
    assert empty["total_appointments"] == empty["cancelled_appointments"] == 0
    assert empty["cancelled_share"] == 0
    assert all(row["slot_minutes"] == row["booked_minutes"] == row["load_percent"] == 0 for row in empty["specialists"])


@pytest.mark.parametrize("method,path,body", [
    ("POST","/api/login",{"username":"","password":""}),
    ("GET","/api/appointments?page=0",None), ("GET","/api/appointments?size=101",None),
    ("GET","/api/appointments?status=unknown",None), ("GET","/api/appointments/0",None),
    ("GET","/api/slots?service_id=0",None), ("GET","/api/slots?service_id=1&size=0",None),
    ("GET","/api/summary?from_at=2026-10-08T00:00:00&to_at=2026-10-01T00:00:00",None),
    ("GET","/api/summary?from_at=2026-10-01T00:00:00Z",None),
    ("POST","/api/appointments",{"slot_id":0,"service_id":1,"client_name":"Клиент"}),
    ("POST","/api/appointments",{"slot_id":6,"service_id":1,"client_name":"  "}),
    ("POST","/api/appointments",{"slot_id":6,"service_id":3,"client_name":"Чужая услуга"}),
    ("POST","/api/appointments",{"slot_id":6,"service_id":2,"client_name":"Долгая услуга"}),
    ("POST","/api/appointments/0/cancel",None),
])
def test_invalid_parameters_return_422(authorized_client,method,path,body):
    response = authorized_client.request(method,path,json=body)
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("method,path,body", [
    ("GET","/api/appointments/999999",None),
    ("POST","/api/appointments/999999/cancel",None),
    ("GET","/api/appointments?specialist_id=999999",None),
    ("GET","/api/slots?service_id=999999",None),
    ("GET","/api/slots?service_id=1&specialist_id=999999",None),
    ("POST","/api/appointments",{"slot_id":999999,"service_id":1,"client_name":"Клиент"}),
    ("POST","/api/appointments",{"slot_id":6,"service_id":999999,"client_name":"Клиент"}),
])
def test_missing_entities_return_404(authorized_client,method,path,body):
    assert authorized_client.request(method,path,json=body).status_code == 404


def test_responses_disable_cache_and_expose_real_timing(authorized_client):
    response = authorized_client.get("/api/appointments")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert float(response.headers["X-App-Time-Ms"]) >= float(response.headers["X-DB-Time-Ms"]) >= 0
    assert int(response.headers["X-DB-Queries"]) >= 2


def test_concurrent_booking_of_overlapping_slots_has_one_winner(dataset):
    with psycopg.connect(dataset["database_url"],options=f"-c search_path={dataset['schema']},public") as connection:
        connection.execute("INSERT INTO slots(specialist_id,start_at,end_at) VALUES (1,'2026-10-05 15:15','2026-10-05 16:15')")
    barrier = Barrier(2)
    def book(slot_id,username):
        with TestClient(create_app()) as client:
            assert client.post("/api/login",json={"username":username,"password":PASSWORD}).status_code == 200
            barrier.wait(timeout=10)
            return client.post("/api/appointments",json={"slot_id":slot_id,"service_id":1,"client_name":"Конкурентная запись"}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(book,6,"demo"),executor.submit(book,11,"other")]
        assert sorted(future.result(timeout=20) for future in futures) == [201,409]
    with psycopg.connect(dataset["database_url"],options=f"-c search_path={dataset['schema']},public") as connection:
        count = connection.execute("SELECT count(*) FROM appointments WHERE specialist_id=1 AND status='booked' AND start_at >= '2026-10-05 15:00'").fetchone()[0]
        assert count == 1
