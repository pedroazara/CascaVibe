"""CascaVibe — API de ingestão de telemetria.

Valida o contrato do firmware 0.2.0 (ver docs/API.md), autentica por token,
persiste com idempotência em SQLite e responde o ACK esperado pelo ESP32.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

if __package__:
    from .dashboard import criar_router
else:
    from dashboard import criar_router

MAX_BODY_BYTES = 32 * 1024
DB_PATH = Path(os.environ.get("CASCAVIBE_DB_PATH", Path(__file__).parent / "cascavibe.db"))

DEVICE_ID_RE = re.compile(r"^cv-[0-9a-f]{12}$")
BOOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ESCALA_ESPERADA = 9.80665 / 16384


class LoteTelemetria(BaseModel):
    schema_version: int
    device_id: str
    boot_id: str
    batch_id: str
    batch_seq: int = Field(ge=0)
    segment_id: int = Field(ge=0)
    firmware_version: str
    sensor: str
    sample_rate_hz: int
    sample_count: int
    first_sample_index: int = Field(ge=0)
    t0_monotonic_us: int = Field(ge=0)
    timestamp_quality: str
    dt_us: int
    encoding: str
    accel_range_g: int
    dlpf_cfg: int
    scale_m_s2_per_count: float
    clipped_samples: int = Field(ge=0, le=500)
    t0_utc: None
    axis_order: list[str]
    samples: list[list[int]]

    @field_validator("schema_version")
    @classmethod
    def _schema_version_fixa(cls, v: int) -> int:
        if v != 1:
            raise ValueError("schema_version deve ser 1")
        return v

    @field_validator("device_id")
    @classmethod
    def _device_id_formato(cls, v: str) -> str:
        if not DEVICE_ID_RE.match(v):
            raise ValueError("device_id deve ser 'cv-' + 12 caracteres hex")
        return v

    @field_validator("boot_id")
    @classmethod
    def _boot_id_formato(cls, v: str) -> str:
        if not BOOT_ID_RE.match(v):
            raise ValueError("boot_id deve ter 32 caracteres hex")
        return v

    @field_validator("sample_rate_hz")
    @classmethod
    def _sample_rate_fixa(cls, v: int) -> int:
        if v != 500:
            raise ValueError("sample_rate_hz deve ser 500")
        return v

    @field_validator("dt_us")
    @classmethod
    def _dt_us_fixo(cls, v: int) -> int:
        if v != 2000:
            raise ValueError("dt_us deve ser 2000")
        return v

    @field_validator("encoding")
    @classmethod
    def _encoding_fixo(cls, v: str) -> str:
        if v != "int16_counts":
            raise ValueError("encoding deve ser 'int16_counts'")
        return v

    @field_validator("axis_order")
    @classmethod
    def _axis_order_fixa(cls, v: list[str]) -> list[str]:
        if v != ["x", "y", "z"]:
            raise ValueError("axis_order deve ser ['x', 'y', 'z']")
        return v

    @field_validator("accel_range_g")
    @classmethod
    def _accel_range_fixa(cls, v: int) -> int:
        if v != 2:
            raise ValueError("accel_range_g deve ser 2")
        return v

    @field_validator("dlpf_cfg")
    @classmethod
    def _dlpf_fixo(cls, v: int) -> int:
        if v != 2:
            raise ValueError("dlpf_cfg deve ser 2")
        return v

    @field_validator("sensor")
    @classmethod
    def _sensor_fixo(cls, v: str) -> str:
        if v != "MPU6050":
            raise ValueError("sensor deve ser 'MPU6050'")
        return v

    @field_validator("timestamp_quality")
    @classmethod
    def _timestamp_quality_fixa(cls, v: str) -> str:
        if v != "estimated_from_fifo":
            raise ValueError("timestamp_quality deve ser 'estimated_from_fifo'")
        return v

    @field_validator("scale_m_s2_per_count")
    @classmethod
    def _escala_dentro_da_tolerancia(cls, v: float) -> float:
        if abs(v - ESCALA_ESPERADA) > 1e-6:
            raise ValueError("scale_m_s2_per_count fora da tolerância esperada")
        return v

    @model_validator(mode="after")
    def _amostras_consistentes(self) -> "LoteTelemetria":
        if self.sample_count != 500:
            raise ValueError("sample_count deve ser 500")
        if len(self.samples) != self.sample_count:
            raise ValueError("samples deve ter sample_count elementos")
        for eixo in self.samples:
            if len(eixo) != 3:
                raise ValueError("cada amostra deve ter exatamente 3 valores [x, y, z]")
            if any(not -32768 <= v <= 32767 for v in eixo):
                raise ValueError("valor de amostra fora do range int16")
        esperado = f"{self.device_id}.{self.boot_id}.{self.batch_seq}"
        if self.batch_id != esperado:
            raise ValueError("batch_id não corresponde a device_id.boot_id.batch_seq")
        return self


class LimiteDeCorpoMiddleware(BaseHTTPMiddleware):
    """Rejeita corpos acima de 32 KiB antes de tentar decodificar o JSON."""

    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Corpo excede 32 KiB"}, status_code=413)
        return await call_next(request)


app = FastAPI(title="CascaVibe Telemetry API")
app.add_middleware(LimiteDeCorpoMiddleware)
app.include_router(criar_router(DB_PATH))

# Passo 2: registro provisório de tokens autorizados por dispositivo.
# token -> device_id. Adicione aqui o device_id real do seu ESP32 (aparece
# no rodapé do painel ou no Serial Monitor no boot) e o token que você
# configurou na seção "API" do painel. Isso ainda vive em código porque
# cadastrar dispositivos via banco/admin fica para uma iteração futura.
TOKENS_AUTORIZADOS: dict[str, str] = {
    "TOKEN_TESTE": "cv-0123456789ab",  # usado nos testes com lote-sintetico.json
    "cascavibe": "cv-f0e4480b65f4",
}

# Conexão única e compartilhada com o SQLite local. check_same_thread=False
# porque o FastAPI pode chamar a partir de threads diferentes; como o volume
# é baixo (poucos ESP32s, ~1 lote/s cada), uma conexão simples basta.
conexao = sqlite3.connect(DB_PATH, check_same_thread=False)
conexao.execute(
    """
    CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        boot_id TEXT NOT NULL,
        batch_seq INTEGER NOT NULL,
        segment_id INTEGER NOT NULL,
        clipped_samples INTEGER NOT NULL,
        primeira_amostra_g TEXT NOT NULL,
        ultima_amostra_g TEXT NOT NULL,
        media_g_xyz TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        recebido_em TEXT NOT NULL
    )
    """
)
conexao.commit()

conexao.execute(
    "CREATE INDEX IF NOT EXISTS batches_device_boot_seq "
    "ON batches(device_id, boot_id, batch_seq)"
)
conexao.execute(
    """
    CREATE TABLE IF NOT EXISTS features (
        batch_id TEXT PRIMARY KEY REFERENCES batches(batch_id),
        rms_x REAL NOT NULL,
        rms_y REAL NOT NULL,
        rms_z REAL NOT NULL,
        rms_total REAL NOT NULL
    )
    """
)
conexao.commit()


@app.get("/")
async def raiz():
    return {"status": "ok", "service": "cascavibe-telemetry-api"}


def para_g(amostra: list[int]) -> list[float]:
    return [round(c / 16384, 4) for c in amostra]


def calcular_rms_ac(lote: LoteTelemetria) -> dict[str, float]:
    """RMS por eixo após remover a média do lote (componente contínua/gravidade),
    em m/s² — mesma abordagem que o próprio painel do ESP32 usa como indicador local
    (ver docs/API.md, seção 8)."""
    n = len(lote.samples)
    escala = lote.scale_m_s2_per_count
    rms_por_eixo = []
    for eixo in zip(*lote.samples):
        media = sum(eixo) / n
        rms_contagem = (sum((c - media) ** 2 for c in eixo) / n) ** 0.5
        rms_por_eixo.append(rms_contagem * escala)
    rms_x, rms_y, rms_z = rms_por_eixo
    return {
        "rms_x": rms_x, "rms_y": rms_y, "rms_z": rms_z,
        "rms_total": (rms_x**2 + rms_y**2 + rms_z**2) ** 0.5,
    }


@app.post("/api/v1/telemetry/batches")
async def receber_lote(lote: LoteTelemetria, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente ou inválido")
    token = authorization.removeprefix("Bearer ").strip()
    device_do_token = TOKENS_AUTORIZADOS.get(token)
    if device_do_token is None:
        raise HTTPException(status_code=401, detail="Token inválido")
    if device_do_token != lote.device_id:
        raise HTTPException(status_code=403, detail="Token não autorizado para este device_id")

    media_g = [
        round(sum(eixo) / len(lote.samples) / 16384, 4)
        for eixo in zip(*lote.samples)
    ]
    payload_json = lote.model_dump_json()

    try:
        conexao.execute(
            """
            INSERT INTO batches
                (batch_id, device_id, boot_id, batch_seq, segment_id, clipped_samples,
                 primeira_amostra_g, ultima_amostra_g, media_g_xyz, payload_json, recebido_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lote.batch_id, lote.device_id, lote.boot_id, lote.batch_seq, lote.segment_id,
                lote.clipped_samples, json.dumps(para_g(lote.samples[0])),
                json.dumps(para_g(lote.samples[-1])), json.dumps(media_g), payload_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        rms = calcular_rms_ac(lote)
        conexao.execute(
            "INSERT INTO features (batch_id, rms_x, rms_y, rms_z, rms_total) "
            "VALUES (?, ?, ?, ?, ?)",
            (lote.batch_id, rms["rms_x"], rms["rms_y"], rms["rms_z"], rms["rms_total"]),
        )
        conexao.commit()
    except sqlite3.IntegrityError:
        # batch_id já existe: a constraint única pegou, inclusive em corrida
        # entre duas requisições concorrentes com o mesmo lote.
        linha = conexao.execute(
            "SELECT payload_json FROM batches WHERE batch_id = ?", (lote.batch_id,)
        ).fetchone()
        if linha is not None and linha[0] == payload_json:
            print(f"[lote repetido, idêntico] {lote.batch_id}")
            return JSONResponse({"accepted": True, "batch_id": lote.batch_id}, status_code=200)
        print(f"[lote repetido, CONTEÚDO DIVERGENTE] {lote.batch_id}")
        raise HTTPException(status_code=409, detail="batch_id já existe com conteúdo diferente")

    print(f"[lote novo] {lote.batch_id} device={lote.device_id} media_g={media_g}")
    return JSONResponse({"accepted": True, "batch_id": lote.batch_id}, status_code=201)


@app.get("/api/v1/telemetry/batches/recentes")
async def listar_recentes(limite: int = 50):
    """Lista os últimos lotes persistidos no SQLite, mais recente primeiro."""
    linhas = conexao.execute(
        """
        SELECT b.batch_id, b.device_id, b.batch_seq, b.segment_id, b.clipped_samples,
               b.primeira_amostra_g, b.ultima_amostra_g, b.media_g_xyz, b.recebido_em,
               f.rms_x, f.rms_y, f.rms_z, f.rms_total
        FROM batches b LEFT JOIN features f ON f.batch_id = b.batch_id
        ORDER BY b.recebido_em DESC LIMIT ?
        """,
        (limite,),
    ).fetchall()
    campos = [
        "batch_id", "device_id", "batch_seq", "segment_id", "clipped_samples",
        "primeira_amostra_g", "ultima_amostra_g", "media_g_xyz", "recebido_em",
        "rms_x", "rms_y", "rms_z", "rms_total",
    ]
    json_fields = {"primeira_amostra_g", "ultima_amostra_g", "media_g_xyz"}
    return [
        {campo: (json.loads(valor) if campo in json_fields and valor is not None else valor)
         for campo, valor in zip(campos, linha)}
        for linha in linhas
    ]
