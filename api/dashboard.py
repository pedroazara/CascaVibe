"""Painel local e consultas somente de leitura das amostras persistidas."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse


def abrir_banco(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def consultar_sinal(path: Path, device_id: str, boot_id: str | None, segundos: int):
    with closing(abrir_banco(path)) as db:
        if boot_id is None:
            row = db.execute(
                "SELECT boot_id FROM batches WHERE device_id = ? "
                "ORDER BY recebido_em DESC, rowid DESC LIMIT 1", (device_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Nenhuma coleta encontrada para este dispositivo")
            boot_id = row["boot_id"]
        rows = db.execute(
            "SELECT payload_json, recebido_em FROM batches "
            "WHERE device_id = ? AND boot_id = ? ORDER BY batch_seq DESC LIMIT ?",
            (device_id, boot_id, segundos + 1),
        ).fetchall()
    if not rows:
        raise HTTPException(404, "Sessão não encontrada")

    batches = [(json.loads(row["payload_json"]), row["recebido_em"]) for row in reversed(rows)]
    latest = batches[-1][0]
    end_us = latest["t0_monotonic_us"] + (latest["sample_count"] - 1) * latest["dt_us"]
    start_us = end_us - segundos * 1_000_000
    traces, previous = [], None
    batch_count = clipped = 0
    for batch, received in batches:
        points = []
        for i, xyz in enumerate(batch["samples"]):
            t_us = batch["t0_monotonic_us"] + i * batch["dt_us"]
            if start_us < t_us <= end_us:
                points.append([(t_us - end_us) / 1_000_000,
                               *[c * batch["scale_m_s2_per_count"] for c in xyz]])
                clipped += int(any(abs(c) >= 32700 for c in xyz))
        if not points:
            continue
        contiguous = (
            previous is not None
            and previous["segment_id"] == batch["segment_id"]
            and previous["batch_seq"] + 1 == batch["batch_seq"]
            and previous["first_sample_index"] + previous["sample_count"] == batch["first_sample_index"]
            and abs(batch["t0_monotonic_us"] - (previous["t0_monotonic_us"]
                    + previous["sample_count"] * previous["dt_us"])) <= batch["dt_us"] * 2
        )
        if contiguous:
            traces[-1]["points"].extend(points)
        else:
            traces.append({"segment_id": batch["segment_id"], "points": points})
        previous = batch
        batch_count += 1

    last_received = max(received for _, received in batches)
    return {
        "device_id": device_id, "boot_id": boot_id,
        "last_received_at": last_received,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "sample_rate_hz": latest["sample_rate_hz"], "window_seconds": segundos,
        "sample_count": sum(len(trace["points"]) for trace in traces),
        "batch_count": batch_count, "clipped_samples": clipped,
        "batch_seq": latest["batch_seq"], "traces": traces,
        "unit": "m/s²", "time_reference": "seconds_relative_to_latest_sample",
    }


def consultar_serie(path: Path, device_id: str, segundos: int):
    """Une o histórico do dispositivo numa linha temporal, inclusive entre boots.

    O primeiro recebimento de cada boot ancora sua última amostra no relógio
    do servidor. Essa aproximação é fixa: atrasos posteriores da rede não
    comprimem nem deslocam os intervalos nominais entre amostras do sensor.
    Não é uma medição UTC da captura.
    """
    with closing(abrir_banco(path)) as db:
        # Each batch covers one second. Extra candidates accommodate a short
        # backlog and overlapping estimated clocks around device restarts.
        rows = db.execute(
            "SELECT boot_id, payload_json, recebido_em FROM batches WHERE device_id = ? "
            "ORDER BY recebido_em DESC, rowid DESC LIMIT ?", (device_id, segundos * 2 + 16)
        ).fetchall()
        if not rows:
            raise HTTPException(404, "Nenhuma coleta encontrada para este dispositivo")
        boots = {row["boot_id"] for row in rows}
        offsets = {}
        for boot in boots:
            first = db.execute(
                "SELECT payload_json, recebido_em FROM batches WHERE device_id = ? AND boot_id = ? "
                "ORDER BY rowid LIMIT 1", (device_id, boot)
            ).fetchone()
            batch = json.loads(first["payload_json"])
            end_us = batch["t0_monotonic_us"] + (batch["sample_count"] - 1) * batch["dt_us"]
            offsets[boot] = round(datetime.fromisoformat(first["recebido_em"]).timestamp() * 1_000_000) - end_us

    batches = [json.loads(row["payload_json"]) for row in rows]
    # boot_id is also a database column; use it for fixtures and persisted identity.
    for batch, row in zip(batches, rows):
        batch["boot_id"] = row["boot_id"]
    batches.sort(key=lambda b: (offsets[b["boot_id"]] + b["t0_monotonic_us"], b["batch_seq"]))
    end_us = max(offsets[b["boot_id"]] + b["t0_monotonic_us"] +
                 (b["sample_count"] - 1) * b["dt_us"] for b in batches)
    start_us = end_us - segundos * 1_000_000
    traces, previous, active = [], {}, {}
    clipped = count = 0
    for batch in batches:
        boot = batch["boot_id"]
        points = []
        for i, xyz in enumerate(batch["samples"]):
            t_us = offsets[boot] + batch["t0_monotonic_us"] + i * batch["dt_us"]
            if start_us < t_us <= end_us:
                points.append([(t_us - end_us) / 1_000_000,
                               *[c * batch["scale_m_s2_per_count"] for c in xyz]])
                clipped += int(any(abs(c) >= 32700 for c in xyz))
        if not points:
            continue
        prior = previous.get(boot)
        contiguous = (prior is not None and prior["segment_id"] == batch["segment_id"]
                      and prior["batch_seq"] + 1 == batch["batch_seq"]
                      and prior["first_sample_index"] + prior["sample_count"] == batch["first_sample_index"]
                      and abs(batch["t0_monotonic_us"] - prior["t0_monotonic_us"]
                              - prior["sample_count"] * prior["dt_us"]) <= batch["dt_us"] * 2)
        if not contiguous:
            active[boot] = {"boot_id": boot, "segment_id": batch["segment_id"], "points": []}
            traces.append(active[boot])
        active[boot]["points"].extend(points)
        previous[boot] = batch
        count += 1
    latest = json.loads(rows[0]["payload_json"])
    return {
        "device_id": device_id, "boot_id": rows[0]["boot_id"],
        "last_received_at": rows[0]["recebido_em"],
        "server_time": datetime.now(timezone.utc).isoformat(),
        "sample_rate_hz": latest["sample_rate_hz"], "window_seconds": segundos,
        "sample_count": sum(len(trace["points"]) for trace in traces),
        "batch_count": count, "clipped_samples": clipped, "batch_seq": latest["batch_seq"],
        "traces": traces, "unit": "m/s²", "time_reference": "estimated_server_timeline",
        "end_time_ms": end_us / 1000,
        "boot_count": len({trace["boot_id"] for trace in traces}),
    }


def criar_router(db_path: Path):
    router = APIRouter()
    static = Path(__file__).parent / "static"
    # Explicit file routes keep this router usable with both uvicorn entry points.
    @router.get("/painel", include_in_schema=False)
    @router.get("/painel/", include_in_schema=False)
    def painel():
        return FileResponse(static / "index.html")

    @router.get("/painel/static/{arquivo}", include_in_schema=False)
    def arquivo_estatico(arquivo: str):
        if arquivo not in {"dashboard.css", "dashboard.js", "cascavibe-logo.svg"}:
            raise HTTPException(404)
        return FileResponse(static / arquivo)

    @router.get("/api/v1/dashboard/devices")
    def dispositivos():
        with closing(abrir_banco(db_path)) as db:
            return [dict(row) for row in db.execute(
                "SELECT device_id, COUNT(*) AS batch_count, MAX(recebido_em) AS last_received_at "
                "FROM batches GROUP BY device_id ORDER BY last_received_at DESC"
            )]

    @router.get("/api/v1/dashboard/sessions")
    def sessoes(device_id: str = Query(min_length=1, max_length=80)):
        with closing(abrir_banco(db_path)) as db:
            return [dict(row) for row in db.execute(
                "SELECT boot_id, COUNT(*) AS batch_count, MIN(recebido_em) AS first_received_at, "
                "MAX(recebido_em) AS last_received_at FROM batches WHERE device_id = ? "
                "GROUP BY boot_id ORDER BY last_received_at DESC LIMIT 100", (device_id,)
            )]

    @router.get("/api/v1/dashboard/signal")
    def sinal(device_id: str = Query(min_length=1, max_length=80),
              boot_id: str | None = Query(default=None, min_length=1, max_length=80),
              segundos: int = Query(default=10, ge=1, le=60)):
        return consultar_sinal(db_path, device_id, boot_id, segundos)

    @router.get("/api/v1/dashboard/timeseries")
    def serie(device_id: str = Query(min_length=1, max_length=80),
              segundos: int = Query(default=30, ge=1, le=60)):
        return consultar_serie(db_path, device_id, segundos)

    return router
