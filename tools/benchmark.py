"""Sequential reproducible HW1 measurements, without an artificial load test."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
import psycopg
from psycopg import sql
from app.db import load_env


def percentile(values, p):
    """Nearest-rank percentile: x[ceil(p*n)-1]."""
    return sorted(values)[math.ceil(p * len(values)) - 1]


def main():
    load_env()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--profiles", nargs="+", choices=["small", "work"], default=["small", "work"])
    p.add_argument("--repeats", type=int, default=40)
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--output", type=Path, default=ROOT / "results")
    args = p.parse_args()
    if args.repeats < 20 or args.warmup < 1:
        p.error("At least 20 repeats and at least 1 warmup are required")
    password = os.environ["DEMO_PASSWORD"]
    dsn = os.environ["DATABASE_URL"]
    schema = os.environ.get("APP_SCHEMA", os.environ.get("PERSONAL_PREFIX", "artem_yankovoy"))
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"conditions": {"python": platform.python_version(), "platform": platform.platform(),
        "repeats": args.repeats, "warmup": args.warmup, "concurrency": 1,
        "username": "artem_yankovoy", "prefix": schema,
        "percentile_method": "nearest rank ceil(p*n), median of middle two for even n",
        "measurement": "perf_counter around complete HTTP response; server timing around middleware and execute/fetch/explicit write commit",
        "date": time.strftime("%Y-%m-%d")}, "profiles": {}}
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            result["conditions"]["postgresql"] = cur.fetchone()[0]
    try:
        import psutil
        result["conditions"].update({"ram_gib": round(psutil.virtual_memory().total / 1024**3, 2),
            "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False)})
    except ImportError:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            result["conditions"]["cpu"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except (ImportError, OSError):
        result["conditions"]["cpu"] = platform.processor()
    all_samples = []
    for profile in args.profiles:
        print(f"Seeding {profile}", flush=True)
        seed_log = subprocess.check_output([sys.executable, str(ROOT / "seed.py"), "--profile", profile, "--reset"],
            cwd=ROOT, env=os.environ, text=True, encoding="utf-8")
        (args.output / f"seed-{profile}.txt").write_text(seed_log, encoding="utf-8")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
            counts = {}
            for table in ["users", "specialists", "services", "slots", "appointments"]:
                counts[table] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            # The final slot is explicitly outside seeded appointments.
            reserved = conn.execute("SELECT sl.id, sl.specialist_id, sp.service_ids FROM slots sl JOIN specialists sp ON sp.id=sl.specialist_id WHERE NOT EXISTS (SELECT 1 FROM appointments a WHERE a.slot_id=sl.id AND a.status<>'cancelled') ORDER BY sl.id DESC LIMIT 1").fetchone()
            if not reserved:
                raise RuntimeError("No free benchmark slot")
            slot_id, specialist_id, service_ids = reserved
            service_id = service_ids[0]
            created = []
            def cleanup():
                while created:
                    conn.execute("DELETE FROM appointments WHERE id=%s", (created.pop(),))
            client = httpx.Client(base_url=args.base_url, timeout=180, trust_env=False)
            def login():
                r = client.post("/api/login", json={"username": "artem_yankovoy", "password": password})
                r.raise_for_status()
            login()
            payload = {"slot_id": slot_id, "service_id": service_id, "client_name": "Контрольный замер"}
            def make_cancel_target():
                r = client.post("/api/appointments", json=payload)
                r.raise_for_status()
                ident = r.json()["id"]
                created.append(ident)
                return ident
            operations = [
                ("login", "POST /api/login", lambda: None, lambda _: client.post("/api/login", json={"username":"artem_yankovoy", "password":password})),
                ("logout", "POST /api/logout", lambda: login(), lambda _: client.post("/api/logout")),
                ("me", "GET /api/me", lambda: login(), lambda _: client.get("/api/me")),
                ("services", "GET /api/services", lambda: login(), lambda _: client.get("/api/services")),
                ("specialists", "GET /api/specialists", lambda: login(), lambda _: client.get("/api/specialists")),
                ("slots", "GET /api/slots", lambda: login(), lambda _: client.get("/api/slots", params={"service_id":service_id,"page":1,"size":20,"from_at":"2026-10-01T00:00:00","to_at":"2027-10-01T00:00:00"})),
                ("appointments", "GET /api/appointments", lambda: login(), lambda _: client.get("/api/appointments", params={"page":1,"size":20,"status":"booked"})),
                ("card", "GET /api/appointments/{id}", lambda: login(), lambda _: client.get("/api/appointments/1")),
                ("create", "POST /api/appointments", lambda: login(), lambda _: client.post("/api/appointments", json=payload)),
                ("cancel", "POST /api/appointments/{id}/cancel", make_cancel_target, lambda ident: client.post(f"/api/appointments/{ident}/cancel")),
                ("summary", "GET /api/summary", lambda: login(), lambda _: client.get("/api/summary", params={"from_at":"2026-10-01T00:00:00","to_at":"2027-10-01T00:00:00"})),
            ]
            profile_result = {"counts": counts, "seed_log": seed_log, "operations": {},
                "mutation_policy": "1 temporary appointment at a time; removed with SQL outside timed request; baseline count restored",
                "parameters": {"reserved_slot_id":slot_id,"service_id":service_id,"list_size":20,"summary_from":"2026-10-01T00:00:00","summary_to":"2027-10-01T00:00:00"}}
            for key, label, prepare, request in operations:
                login()
                samples = []
                for i in range(args.warmup + args.repeats):
                    cleanup()
                    prepared = prepare()
                    start = time.perf_counter()
                    response = request(prepared)
                    duration = (time.perf_counter() - start) * 1000
                    response.raise_for_status()
                    if key == "create":
                        created.append(response.json()["id"])
                    if i >= args.warmup:
                        sample = {"profile":profile,"operation":key,"repeat":i-args.warmup+1,
                            "http_ms":round(duration,6), "server_ms":float(response.headers["X-App-Time-Ms"]),
                            "db_ms":float(response.headers["X-DB-Time-Ms"]),
                            "queries":int(response.headers["X-DB-Queries"]),"status":response.status_code}
                        sample["code_ms"] = round(sample["server_ms"] - sample["db_ms"], 6)
                        samples.append(sample)
                        all_samples.append(sample)
                cleanup()
                values = [s["http_ms"] for s in samples]
                representative = sorted(samples, key=lambda s:s["server_ms"])[(len(samples)-1)//2]
                profile_result["operations"][key] = {"label":label,"p50_ms":statistics.median(values),
                    "p95_ms":percentile(values,0.95),"max_ms":max(values),"min_ms":min(values),
                    "decomposition":representative,"samples":samples}
                print(f"{profile} {label}: p50={statistics.median(values):.3f} p95={percentile(values,.95):.3f} max={max(values):.3f}", flush=True)
            profile_result["temporary_creates"] = 2 * (args.warmup + args.repeats)
            profile_result["remaining_added_rows"] = conn.execute("SELECT count(*) FROM appointments").fetchone()[0] - counts["appointments"]
            result["profiles"][profile] = profile_result
            client.close()
        (args.output / "measurements.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        with (args.output / "raw-samples.csv").open("w",encoding="utf-8-sig",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=list(all_samples[0]))
            writer.writeheader();writer.writerows(all_samples)
    if {"small", "work"}.issubset(result["profiles"]):
        for key,op in result["profiles"]["work"]["operations"].items():
            op["growth_p50"] = op["p50_ms"] / result["profiles"]["small"]["operations"][key]["p50_ms"]
        (args.output / "measurements.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Measurements saved",flush=True)


if __name__ == "__main__":
    main()
