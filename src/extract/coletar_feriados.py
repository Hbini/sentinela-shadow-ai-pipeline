"""FONTE 2: feriados nacionais (API publica BrasilAPI).

Baixa os feriados do ano letivo e grava o JSON bruto em data/external/.
Se a API estiver indisponivel (sem internet, proxy etc.), usa o snapshot
versionado em data/cache/feriados_<ano>.json, para manter a reprodutibilidade.
"""
from __future__ import annotations

import json
import logging

import pandas as pd
import requests

from src import config

log = logging.getLogger(__name__)


def coletar_feriados(ano: int = config.ANO_LETIVO) -> pd.DataFrame:
    destino = config.EXTERNAL / f"feriados_{ano}.json"
    url = config.BRASILAPI_FERIADOS.format(ano=ano)
    origem = "brasilapi"
    try:
        resp = requests.get(url, timeout=config.HTTP_TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        if not isinstance(dados, list) or not dados:
            raise ValueError("resposta vazia")
    except Exception as exc:  # noqa: BLE001
        cache = config.CACHE / f"feriados_{ano}.json"
        log.warning("BrasilAPI indisponivel (%s). Usando snapshot %s", exc, cache.name)
        dados = json.loads(cache.read_text(encoding="utf-8"))
        origem = "snapshot_local"

    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    df = pd.DataFrame(dados)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.rename(columns={"date": "data", "name": "nome", "type": "tipo"})
    df["origem"] = origem
    log.info("Feriados coletados: %d (origem=%s)", len(df), origem)
    return df[["data", "nome", "tipo", "origem"]]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(coletar_feriados())
