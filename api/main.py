"""CascaVibe — API de ingestão de telemetria (esqueleto, Passo 1).

Valida o contrato do firmware 0.2.0 (ver docs/API.md) e responde o ACK
esperado pelo ESP32. Ainda sem autenticação real nem persistência — isso
entra nos próximos passos.
"""

import re
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

MAX_BODY_BYTES = 32 * 1024

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

# Buffer temporário só para você acompanhar o que está chegando agora.
# Fica em RAM, some ao reiniciar o servidor — o Passo 3 troca isso por
# persistência de verdade com idempotência.
ultimos_lotes: deque[dict] = deque(maxlen=50)


@app.get("/")
async def raiz():
    return {"status": "ok", "service": "cascavibe-telemetry-api"}


@app.post("/api/v1/telemetry/batches", status_code=201)
async def receber_lote(lote: LoteTelemetria, authorization: str | None = Header(default=None)):
    # Passo 2 (próximo): validar o token contra o device_id em vez de só
    # exigir a presença do header.
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente ou inválido")

    def para_g(amostra: list[int]) -> list[float]:
        return [round(c / 16384, 4) for c in amostra]

    media_g = [
        round(sum(eixo) / len(lote.samples) / 16384, 4)
        for eixo in zip(*lote.samples)
    ]

    resumo = {
        "recebido_em": datetime.now(timezone.utc).isoformat(),
        "device_id": lote.device_id,
        "batch_id": lote.batch_id,
        "batch_seq": lote.batch_seq,
        "segment_id": lote.segment_id,
        "clipped_samples": lote.clipped_samples,
        "primeira_amostra_g": para_g(lote.samples[0]),
        "ultima_amostra_g": para_g(lote.samples[-1]),
        "media_g_xyz": media_g,
    }
    ultimos_lotes.appendleft(resumo)
    print(f"[lote recebido] {resumo}")

    # Passo 3 (próximo): persistir com idempotência em
    # (device_id, boot_id, batch_seq) antes de confirmar de verdade.
    return {"accepted": True, "batch_id": lote.batch_id}


@app.get("/api/v1/telemetry/batches/recentes")
async def listar_recentes():
    """Lista os últimos lotes recebidos (em memória, só para debug agora)."""
    return list(ultimos_lotes)
