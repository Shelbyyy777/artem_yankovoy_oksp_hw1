"""Воспроизводимое наполнение PostgreSQL; сброс только личной схемы."""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
import random

from psycopg import sql

from app.db import ROOT, connect, settings
from app.main import hash_password

PROFILES = {
    "small": {"users": 1, "specialists": 10, "services": 10, "slots": 500, "appointments": 300},
    "work": {"users": 1, "specialists": 200, "services": 30, "slots": 200_000, "appointments": 50_000},
}
FIRST_DAY = datetime(2026, 10, 1)
LAST_DAY = datetime(2027, 9, 30)
RANDOM_SEED = 42
SERVICE_NAMES = [
    "Первичная консультация", "Повторная консультация", "Диагностический приём",
    "Расширенная консультация", "Комплексное обследование", "Плановая консультация",
    "Контрольный осмотр", "Консультация по результатам анализов", "Профилактический приём",
    "Индивидуальная программа", "Оценка состояния", "Составление плана лечения",
    "Консультация терапевта", "Консультация кардиолога", "Консультация невролога",
    "Консультация эндокринолога", "Консультация ортопеда", "Консультация офтальмолога",
    "Консультация отоларинголога", "Консультация дерматолога", "Консультация гастроэнтеролога",
    "Консультация диетолога", "Консультация реабилитолога", "Консультация пульмонолога",
    "Предварительное обследование", "Послеоперационный осмотр", "Диспансерный осмотр",
    "Проверка назначений", "План реабилитации", "Заключительная консультация",
]
SURNAMES = ["Иванов", "Петров", "Смирнов", "Кузнецов", "Соколов", "Попов", "Лебедев", "Козлов",
            "Новиков", "Морозов", "Волков", "Алексеев", "Васильев", "Зайцев", "Павлов", "Семёнов",
            "Голубев", "Виноградов", "Богданов", "Воробьёв"]
NAMES = ["Александр", "Дмитрий", "Сергей", "Андрей", "Алексей", "Михаил", "Николай", "Павел",
         "Роман", "Владимир", "Иван", "Максим", "Артём", "Евгений", "Олег", "Виктор", "Игорь",
         "Денис", "Кирилл", "Антон"]


def copy_rows(cursor, table: str, columns: list[str], rows) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)))
    with cursor.copy(statement) as stream:
        for row in rows:
            stream.write_row(row)


def apply_schema(connection, reset: bool = False) -> None:
    config = settings()
    with connection.cursor() as cursor:
        if reset:
            # settings() ограничивает APP_SCHEMA префиксом и его тестовой схемой.
            cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(config["schema"])))
        text = (ROOT / "schema.sql").read_text(encoding="utf-8")
        cursor.execute(text.replace("__SCHEMA__", sql.Identifier(config["schema"]).as_string(connection)))


def seed(profile: str = "small", reset: bool = False) -> dict:
    if profile not in PROFILES:
        raise ValueError("Профиль должен быть small или work")
    config = settings()
    counts = PROFILES[profile]
    rng = random.Random(RANDOM_SEED)
    durations = [30, 45, 60, 90, 120]
    specialists = []
    for sid in range(1, counts["specialists"] + 1):
        service_ids = sorted({1 + ((sid - 1) * 3 + j) % counts["services"] for j in range(5)})
        name = f"{SURNAMES[(sid - 1) % len(SURNAMES)]} {NAMES[((sid - 1) // len(SURNAMES)) % len(NAMES)]} Сергеевич №{sid:03d}"
        specialists.append((sid, name, service_ids))
    per_specialist = counts["slots"] // counts["specialists"]
    slots = []
    candidate_slot_ids = []
    for sid in range(1, counts["specialists"] + 1):
        for position in range(per_specialist):
            slot_id = (sid - 1) * per_specialist + position + 1
            if position >= per_specialist - 3:
                # Последний день свободен для воспроизводимых сценариев бронирования.
                start = LAST_DAY + timedelta(hours=9 + 2 * (position - per_specialist + 3))
            elif profile == "work":
                start = FIRST_DAY + timedelta(days=position // 3, hours=9 + 2 * (position % 3))
            else:
                start = FIRST_DAY + timedelta(days=(position * 364) // (per_specialist - 3), hours=9)
            slots.append((slot_id, sid, start, start + timedelta(hours=2)))
            if position < per_specialist - 3:
                candidate_slot_ids.append(slot_id)
    selected_slots = rng.sample(candidate_slot_ids, counts["appointments"])
    statuses = ["booked", "booked", "booked", "cancelled", "cancelled",
                "completed", "completed", "completed", "completed", "completed"]
    appointments = []
    for aid, slot_id in enumerate(selected_slots, 1):
        _, sid, start, _ = slots[slot_id - 1]
        service_ids = specialists[sid - 1][2]
        service_id = service_ids[rng.randrange(len(service_ids))]
        duration = durations[(service_id - 1) % len(durations)]
        status = statuses[(aid - 1) % 10]
        appointments.append((aid, 1, sid, service_id, slot_id,
                             f"Клиент {aid:06d}", status, start, start + timedelta(minutes=duration),
                             start - timedelta(days=7), start - timedelta(days=1) if status == "cancelled" else None))
    username = os.getenv("DEMO_USERNAME", config["prefix"])
    password = os.getenv("DEMO_PASSWORD", "CHANGE_ME")
    # Фиксированная соль используется только для воспроизводимого учебного наполнения.
    salt = hashlib.sha256((config["prefix"] + ":seed42").encode()).hexdigest()[:32]
    with connect() as connection:
        apply_schema(connection, reset)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM users")
            if cursor.fetchone()["n"]:
                raise RuntimeError("Схема уже содержит данные. Для явного повторного наполнения укажите --reset")
            copy_rows(cursor, "users", ["id", "username", "full_name", "password_hash"],
                      [(1, username, "Янковой Артем Александрович", hash_password(password, salt))])
            copy_rows(cursor, "services", ["id", "name", "duration_minutes", "price"],
                      [(i, SERVICE_NAMES[i-1], durations[(i-1) % 5], 900 + 150 * i)
                       for i in range(1, counts["services"] + 1)])
            copy_rows(cursor, "specialists", ["id", "full_name", "service_ids"], specialists)
            copy_rows(cursor, "slots", ["id", "specialist_id", "start_at", "end_at"], slots)
            copy_rows(cursor, "appointments", ["id", "user_id", "specialist_id", "service_id", "slot_id",
                      "client_name", "status", "start_at", "end_at", "created_at", "cancelled_at"], appointments)
            actual, table_checksums = {}, {}
            for table in ("users", "specialists", "services", "slots", "appointments"):
                cursor.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table)))
                actual[table] = cursor.fetchone()["n"]
                cursor.execute(sql.SQL("""SELECT md5(coalesce(string_agg(row_to_json(t)::text,
                                           E'\\n' ORDER BY id),'')) AS checksum FROM {} t""")
                               .format(sql.Identifier(table)))
                table_checksums[table] = cursor.fetchone()["checksum"]
                cursor.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, true)", (table, actual[table]))
                cursor.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))
            cursor.execute("SELECT count(*) AS n FROM appointments WHERE status = 'cancelled'")
            cancelled_count = cursor.fetchone()["n"]
    # Контрольная сумма вычисляется из строк, фактически прочитанных PostgreSQL.
    payload = json.dumps(table_checksums, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return {"project": config["prefix"] + "_hw1", "schema": config["schema"], "profile": profile,
            "random_seed": RANDOM_SEED, "counts": actual, "cancelled_appointments": cancelled_count,
            "content_sha256": digest, "table_checksums_md5": table_checksums, "username": username,
            "period": {"from_at": FIRST_DAY.isoformat(), "to_at": "2027-10-01T00:00:00"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Наполнение только личной схемы artem_yankovoy")
    parser.add_argument("--profile", choices=PROFILES, default="small")
    parser.add_argument("--reset", action="store_true", help="Явно удалить и создать заново только личную схему")
    parser.add_argument("--schema-only", action="store_true", help="Создать только схему без данных")
    args = parser.parse_args()
    if args.schema_only:
        with connect() as connection:
            apply_schema(connection, args.reset)
        print(json.dumps({"schema": settings()["schema"], "created": True}, ensure_ascii=False))
    else:
        print(json.dumps(seed(args.profile, args.reset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
