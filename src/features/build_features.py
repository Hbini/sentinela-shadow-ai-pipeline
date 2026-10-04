"""Preparacao dos dados e engenharia de features.

Le a view dw.vw_base_alertas (features historicas ja calculadas no
PostgreSQL com funcoes de janela) e cria as features finais em pandas.
Saida: data/processed/base_modelo.csv
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from src import config

log = logging.getLogger(__name__)

ALVO = "alvo_prosseguiu"

NUMERICAS = [
    "hora_sin", "hora_cos", "dia_semana", "semana_letiva", "semestre_num",
    "alertas_previos_dia", "log_alertas_previos_dia", "n_alertas_hist", "log_eventos_hist",
    "taxa_ignorar_hist", "taxa_bloqueio_hist",
]
BINARIAS = [
    "noturno", "calouro", "semana_prova", "entrega_trabalhos", "feriado", "area_ti",
    "treinado_ia", "catalogado_interno", "no_catalogo_externo", "sem_historico",
]
CATEGORICAS = ["perfil", "turno", "local", "categoria", "exposicao", "navegador", "curso"]

# colunas que NUNCA entram no modelo (identificadores ou vazamento)
EXCLUIDAS = {
    "evento_id": "identificador",
    "ts_local": "usado apenas para o split temporal",
    "dominio": "alta cardinalidade; representado por categoria",
    "tempo_decisao_ms": "VAZAMENTO: medido depois da decisao (alvo)",
    "n_ignorados_hist": "substituida pela taxa suavizada",
}


def ler_base(engine=None) -> pd.DataFrame:
    engine = engine or create_engine(config.pg_url())
    with engine.connect() as c:
        df = pd.read_sql("SELECT * FROM dw.vw_base_alertas ORDER BY ts_local, evento_id", c)
    log.info("Base de alertas lida do PostgreSQL: %s", df.shape)
    return df


def criar_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # tempo (codificacao ciclica da hora)
    out["hora_sin"] = np.sin(2 * np.pi * out["hora"] / 24)
    out["hora_cos"] = np.cos(2 * np.pi * out["hora"] / 24)
    out["noturno"] = (out["hora"] >= 19).astype(int)
    # usuario
    out["semestre_num"] = out["semestre"].fillna(0).astype(int)
    out["calouro"] = out["semestre_num"].between(1, 2).astype(int)
    out["curso"] = out["curso"].fillna("nao_se_aplica")
    # historico comportamental (taxa suavizada de Laplace evita 0/0 e extremos)
    out["taxa_ignorar_hist"] = (out["n_ignorados_hist"] + 1) / (out["n_alertas_hist"] + 2)
    out["taxa_bloqueio_hist"] = out["n_bloqueios_hist"] / out["n_eventos_hist"].clip(lower=1)
    out["log_eventos_hist"] = np.log1p(out["n_eventos_hist"])
    out["log_alertas_previos_dia"] = np.log1p(out["alertas_previos_dia"])
    out["sem_historico"] = (out["n_alertas_hist"] == 0).astype(int)
    for c in BINARIAS:
        out[c] = out[c].astype(int)
    cols = ["evento_id", "ts_local", "data", "dominio", "tempo_decisao_ms"] + NUMERICAS + BINARIAS + CATEGORICAS + [ALVO]
    return out[cols]


def executar() -> pd.DataFrame:
    base = criar_features(ler_base())
    destino = config.PROCESSED / "base_modelo.csv"
    base.to_csv(destino, index=False)
    log.info("Features salvas em %s: %s", destino, base.shape)
    return base


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    executar()
