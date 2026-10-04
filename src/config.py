"""Configuracoes centrais do pipeline (caminhos, banco e parametros)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # .env e opcional
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
EXTERNAL = DATA / "external"
CACHE = DATA / "cache"
PROCESSED = DATA / "processed"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"
FIGS = REPORTS / "figuras"
SQL_DIR = ROOT / "sql"

for _p in (RAW, EXTERNAL, CACHE, PROCESSED, MODELS, FIGS):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- banco
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_DB = os.getenv("PG_DB", "sentinela_dw")

# ---------------------------------------------------------------- fontes
ANO_LETIVO = int(os.getenv("ANO_LETIVO", "2026"))
INICIO_SEMESTRE = os.getenv("INICIO_SEMESTRE", "2026-02-02")
FIM_SEMESTRE = os.getenv("FIM_SEMESTRE", "2026-06-30")
DATA_CORTE_TESTE = os.getenv("DATA_CORTE_TESTE", "2026-06-01")  # split temporal

BRASILAPI_FERIADOS = "https://brasilapi.com.br/api/feriados/v1/{ano}"
GITHUB_REPO_CATALOGO = os.getenv("GITHUB_REPO_CATALOGO", "abixb/llm-hosts-blocklist")
URL_CATALOGO_IA = os.getenv("URL_CATALOGO_IA", "")  # opcional: URL raw direta

SEED = int(os.getenv("SEED", "42"))
HTTP_TIMEOUT = 20


def pg_url(db: str | None = None) -> str:
    """URL SQLAlchemy para o PostgreSQL (senha via variavel PG_PASSWORD)."""
    from urllib.parse import quote_plus

    senha = quote_plus(PG_PASSWORD) if PG_PASSWORD else ""
    auth = f"{PG_USER}:{senha}" if senha else PG_USER
    return f"postgresql+psycopg2://{auth}@{PG_HOST}:{PG_PORT}/{db or PG_DB}"
