"""Обычный исходный сервис ДЗ 1: FastAPI, SQL, PostgreSQL, без кеширования."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import Database, QueryStats, get_db, query_stats, settings
from .rules import cancelled_share, load_percent, validate_booking

COOKIE_NAME = "artem_yankovoy_session"
COOKIE_TTL = 12 * 60 * 60
ROOT = Path(__file__).resolve().parent.parent


def now_moscow() -> datetime:
    """В проекте все timestamp хранят местное время Москвы (UTC+03:00)."""
    return datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2_sha256$200000${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != 200_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(expected, digest)
    except (ValueError, TypeError):
        return False


def session_token(user_id: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"id": user_id, "exp": int(time.time()) + COOKIE_TTL},
                                                separators=(",", ":")).encode()).decode()
    signature = hmac.new(settings()["secret_key"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def require_user(request: Request) -> int:
    try:
        payload, signature = request.cookies.get(COOKIE_NAME, "").split(".", 1)
        expected = hmac.new(settings()["secret_key"].encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(payload))
        if not isinstance(data["id"], int) or data["id"] <= 0 or data["exp"] <= time.time():
            raise ValueError
        return data["id"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "Необходима авторизация")


def local_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        raise HTTPException(422, "Передайте местное время Москвы без смещения UTC")
    return value


def validate_period(from_at: datetime, to_at: datetime) -> tuple[datetime, datetime]:
    start, end = local_datetime(from_at), local_datetime(to_at)
    if start >= end:
        raise HTTPException(422, "Начало периода должно предшествовать концу")
    return start, end


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class AppointmentInput(BaseModel):
    slot_id: int = Field(gt=0)
    service_id: int = Field(gt=0)
    client_name: str = Field(min_length=1, max_length=160)


APPOINTMENT_SELECT = """
    SELECT a.id, a.slot_id, a.client_name, a.status, a.start_at, a.end_at,
           a.created_at, a.cancelled_at,
           s.id AS specialist_id, s.full_name AS specialist_name,
           v.id AS service_id, v.name AS service_name, v.duration_minutes, v.price,
           u.id AS user_id, u.username, u.full_name AS user_name
    FROM appointments a
    JOIN specialists s ON s.id = a.specialist_id
    JOIN services v ON v.id = a.service_id
    JOIN users u ON u.id = a.user_id
"""


def appointment_card(row: dict) -> dict:
    return {
        "id": row["id"], "slot_id": row["slot_id"], "client_name": row["client_name"],
        "status": row["status"], "start_at": row["start_at"], "end_at": row["end_at"],
        "created_at": row["created_at"], "cancelled_at": row["cancelled_at"],
        "specialist": {"id": row["specialist_id"], "full_name": row["specialist_name"]},
        "service": {"id": row["service_id"], "name": row["service_name"],
                    "duration_minutes": row["duration_minutes"], "price": float(row["price"])},
        "user": {"id": row["user_id"], "username": row["username"], "full_name": row["user_name"]},
    }


def get_appointment(db: Database, appointment_id: int) -> dict:
    row = db.one(APPOINTMENT_SELECT + " WHERE a.id = %s", (appointment_id,))
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    return appointment_card(row)


def create_app() -> FastAPI:
    app = FastAPI(title="artem_yankovoy — Запись на приём, ДЗ 1", version="1.0.0")

    @app.middleware("http")
    async def measure_request(request: Request, call_next):
        stats = QueryStats()
        token = query_stats.set(stats)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-App-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.3f}"
            response.headers["X-DB-Time-Ms"] = f"{stats.db_ms:.3f}"
            response.headers["X-DB-Queries"] = str(stats.queries)
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            query_stats.reset(token)

    @app.post("/api/login")
    def login(body: LoginInput, response: Response, db: Database = Depends(get_db)):
        row = db.one("SELECT id, username, full_name, password_hash FROM users WHERE username = %s",
                     (body.username,))
        if row is None or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(401, "Неверное имя пользователя или пароль")
        response.set_cookie(COOKIE_NAME, session_token(row["id"]), max_age=COOKIE_TTL,
                            httponly=True, samesite="lax",
                            secure=os.getenv("COOKIE_SECURE", "0") == "1")
        return {"user": {"id": row["id"], "username": row["username"], "full_name": row["full_name"]}}

    @app.post("/api/logout")
    def logout(response: Response, user_id: int = Depends(require_user)):
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @app.get("/api/me")
    def me(user_id: int = Depends(require_user), db: Database = Depends(get_db)):
        row = db.one("SELECT id, username, full_name FROM users WHERE id = %s", (user_id,))
        if row is None:
            raise HTTPException(401, "Учётная запись не найдена")
        return {"user": row}

    @app.get("/api/services")
    def services(user_id: int = Depends(require_user), db: Database = Depends(get_db)):
        rows = db.all("SELECT id, name, duration_minutes, price FROM services ORDER BY id")
        for row in rows:
            row["price"] = float(row["price"])
        return {"items": rows}

    @app.get("/api/specialists")
    def specialists(user_id: int = Depends(require_user), db: Database = Depends(get_db)):
        return {"items": db.all("SELECT id, full_name, service_ids FROM specialists ORDER BY id")}

    @app.get("/api/slots")
    def slots(service_id: int = Query(ge=1), page: int = Query(1, ge=1),
              size: int = Query(20, ge=1, le=100), specialist_id: int | None = Query(None, ge=1),
              from_at: datetime = datetime(2026, 10, 1), to_at: datetime = datetime(2027, 10, 1),
              user_id: int = Depends(require_user), db: Database = Depends(get_db)):
        start, end = validate_period(from_at, to_at)
        service = db.one("SELECT duration_minutes FROM services WHERE id = %s", (service_id,))
        if service is None:
            raise HTTPException(404, "Услуга не найдена")
        if specialist_id is not None and db.one("SELECT id FROM specialists WHERE id = %s", (specialist_id,)) is None:
            raise HTTPException(404, "Специалист не найден")
        params = [service_id, start, end, service["duration_minutes"], service["duration_minutes"]]
        condition = """
            %s = ANY(s.service_ids) AND t.start_at >= %s AND t.end_at <= %s
            AND t.end_at >= t.start_at + %s * interval '1 minute'
            AND NOT EXISTS (
                SELECT 1 FROM appointments a
                WHERE a.specialist_id = t.specialist_id AND a.status <> 'cancelled'
                  AND a.start_at < t.start_at + %s * interval '1 minute'
                  AND a.end_at > t.start_at)
        """
        if specialist_id is not None:
            condition += " AND t.specialist_id = %s"
            params.append(specialist_id)
        source = " FROM slots t JOIN specialists s ON s.id = t.specialist_id WHERE " + condition
        total = db.one("SELECT count(*) AS total" + source, params)["total"]
        rows = db.all("SELECT t.id, t.specialist_id, t.start_at, t.end_at, s.full_name, s.service_ids" + source +
                      " ORDER BY t.start_at, t.id LIMIT %s OFFSET %s", params + [size, (page - 1) * size])
        items = [{"id": row["id"], "specialist_id": row["specialist_id"],
                  "start_at": row["start_at"], "end_at": row["end_at"],
                  "service_ids": row["service_ids"],
                  "specialist": {"id": row["specialist_id"], "full_name": row["full_name"]}} for row in rows]
        return {"items": items, "total": total, "page": page, "size": size}

    @app.get("/api/appointments")
    def appointments(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                     status: str | None = None, specialist_id: int | None = Query(None, ge=1),
                     user_id: int = Depends(require_user), db: Database = Depends(get_db)):
        filters, params = [], []
        if status is not None:
            if status not in ("booked", "cancelled", "completed"):
                raise HTTPException(422, "Неизвестный статус")
            filters.append("a.status = %s")
            params.append(status)
        if specialist_id is not None:
            if db.one("SELECT id FROM specialists WHERE id = %s", (specialist_id,)) is None:
                raise HTTPException(404, "Специалист не найден")
            filters.append("a.specialist_id = %s")
            params.append(specialist_id)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        total = db.one("SELECT count(*) AS total FROM appointments a" + where, params)["total"]
        rows = db.all(APPOINTMENT_SELECT + where + " ORDER BY a.start_at DESC, a.id DESC LIMIT %s OFFSET %s",
                      params + [size, (page - 1) * size])
        return {"items": [appointment_card(row) for row in rows], "total": total, "page": page, "size": size}

    @app.get("/api/appointments/{appointment_id}")
    def appointment(appointment_id: int, user_id: int = Depends(require_user),
                    db: Database = Depends(get_db)):
        if appointment_id <= 0:
            raise HTTPException(422, "Идентификатор должен быть положительным")
        return get_appointment(db, appointment_id)

    @app.post("/api/appointments", status_code=201)
    def book(body: AppointmentInput, user_id: int = Depends(require_user),
             db: Database = Depends(get_db)):
        client_name = body.client_name.strip()
        if not client_name:
            raise HTTPException(422, "Имя клиента не может быть пустым")
        slot = db.one("SELECT id, specialist_id, start_at, end_at FROM slots WHERE id = %s", (body.slot_id,))
        service = db.one("SELECT id, duration_minutes FROM services WHERE id = %s", (body.service_id,))
        if slot is None:
            raise HTTPException(404, "Слот не найден")
        if service is None:
            raise HTTPException(404, "Услуга не найдена")
        # Блокируется специалист, а не слот: это защищает и пересекающиеся разные слоты.
        specialist = db.one("SELECT id, service_ids FROM specialists WHERE id = %s FOR UPDATE",
                             (slot["specialist_id"],))
        end = slot["start_at"] + timedelta(minutes=service["duration_minutes"])
        busy = db.all("""SELECT start_at, end_at FROM appointments
                         WHERE specialist_id = %s AND status <> 'cancelled'
                           AND start_at < %s AND end_at > %s""",
                      (specialist["id"], end, slot["start_at"]))
        try:
            end = validate_booking(slot["start_at"], slot["end_at"], service["duration_minutes"],
                                   service["id"], specialist["service_ids"],
                                   ((row["start_at"], row["end_at"]) for row in busy))
        except ValueError as exc:
            if "пересекается" in str(exc):
                raise HTTPException(409, str(exc))
            raise HTTPException(422, str(exc))
        row = db.one("""INSERT INTO appointments
                         (user_id, specialist_id, service_id, slot_id, client_name, status,
                          start_at, end_at, created_at)
                         VALUES (%s,%s,%s,%s,%s,'booked',%s,%s,%s) RETURNING id""",
                     (user_id, specialist["id"], service["id"], slot["id"], client_name,
                      slot["start_at"], end, now_moscow()))
        card = get_appointment(db, row["id"])
        db.commit()
        return card

    @app.post("/api/appointments/{appointment_id}/cancel")
    def cancel(appointment_id: int, user_id: int = Depends(require_user),
               db: Database = Depends(get_db)):
        if appointment_id <= 0:
            raise HTTPException(422, "Идентификатор должен быть положительным")
        row = db.one("SELECT id, user_id, status FROM appointments WHERE id = %s FOR UPDATE", (appointment_id,))
        if row is None:
            raise HTTPException(404, "Запись не найдена")
        if row["user_id"] != user_id:
            raise HTTPException(403, "Можно отменять только собственные записи")
        if row["status"] == "completed":
            raise HTTPException(409, "Завершённый приём нельзя отменить")
        if row["status"] != "cancelled":
            db.execute("UPDATE appointments SET status = 'cancelled', cancelled_at = %s WHERE id = %s",
                       (now_moscow(), appointment_id))
        card = get_appointment(db, appointment_id)
        db.commit()
        return card

    @app.get("/api/summary")
    def summary(from_at: datetime = datetime(2026, 10, 1), to_at: datetime = datetime(2027, 10, 1),
                user_id: int = Depends(require_user), db: Database = Depends(get_db)):
        start, end = validate_period(from_at, to_at)
        rows = db.all("""
            WITH period AS (SELECT %s::timestamp AS start_at, %s::timestamp AS end_at),
            slot_totals AS (
                SELECT t.specialist_id,
                       sum(extract(epoch FROM least(t.end_at,p.end_at)-greatest(t.start_at,p.start_at))/60) AS slot_minutes
                FROM slots t CROSS JOIN period p
                WHERE t.start_at < p.end_at AND t.end_at > p.start_at GROUP BY t.specialist_id),
            appointment_totals AS (
                SELECT a.specialist_id,
                       coalesce(sum(extract(epoch FROM least(a.end_at,p.end_at)-greatest(a.start_at,p.start_at))/60)
                           FILTER (WHERE a.status <> 'cancelled' AND a.start_at < p.end_at AND a.end_at > p.start_at),0) AS booked_minutes,
                       count(*) FILTER (WHERE a.start_at >= p.start_at AND a.start_at < p.end_at) AS total_appointments,
                       count(*) FILTER (WHERE a.status = 'cancelled' AND a.start_at >= p.start_at AND a.start_at < p.end_at) AS cancelled_appointments
                FROM appointments a CROSS JOIN period p
                WHERE a.start_at < p.end_at AND a.end_at > p.start_at GROUP BY a.specialist_id)
            SELECT s.id, s.full_name, coalesce(t.slot_minutes,0) AS slot_minutes,
                   coalesce(a.booked_minutes,0) AS booked_minutes,
                   coalesce(a.total_appointments,0) AS total_appointments,
                   coalesce(a.cancelled_appointments,0) AS cancelled_appointments
            FROM specialists s LEFT JOIN slot_totals t ON t.specialist_id=s.id
            LEFT JOIN appointment_totals a ON a.specialist_id=s.id ORDER BY s.id
        """, (start, end))
        for row in rows:
            row["slot_minutes"] = float(row["slot_minutes"])
            row["booked_minutes"] = float(row["booked_minutes"])
            row["load_percent"] = load_percent(row["booked_minutes"], row["slot_minutes"])
        total = sum(row["total_appointments"] for row in rows)
        cancelled = sum(row["cancelled_appointments"] for row in rows)
        return {"period": {"from_at": start, "to_at": end}, "total_appointments": total,
                "cancelled_appointments": cancelled, "cancelled_share": cancelled_share(cancelled, total),
                "specialists": rows}

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})

    # Каталог создаёт клиентская часть; проверка отложена до фактического запроса.
    app.mount("/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static")
    return app


app = create_app()
