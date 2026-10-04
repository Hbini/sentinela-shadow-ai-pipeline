"""FONTE 3: catalogo publico de dominios de IA (repositorio GitHub).

Fonte: projeto comunitario abixb/llm-hosts-blocklist (listas de hosts de
servicos de IA generativa). O script lista os arquivos .txt do repositorio
pela API publica do GitHub, baixa e normaliza os dominios.
Se a coleta falhar, usa o snapshot em data/cache/catalogo_ia_snapshot.txt.
"""
from __future__ import annotations

import logging
import re

import pandas as pd
import requests

from src import config

log = logging.getLogger(__name__)

_DOMINIO = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3}){3}\s+)?([a-z0-9][a-z0-9.\-]*\.[a-z]{2,})$")


def _normalizar(linhas: list[str]) -> list[str]:
    dominios = set()
    for linha in linhas:
        linha = linha.strip().lower()
        if not linha or linha.startswith(("#", "!", "//")):
            continue
        linha = linha.split("#")[0].strip()
        linha = linha.replace("||", "").replace("^", "")
        m = _DOMINIO.match(linha)
        if m:
            dom = m.group(1).lstrip(".")
            if dom.startswith("www."):
                dom = dom[4:]
            dominios.add(dom)
    return sorted(dominios)


def _urls_candidatas() -> list[str]:
    if config.URL_CATALOGO_IA:
        return [config.URL_CATALOGO_IA]
    api = f"https://api.github.com/repos/{config.GITHUB_REPO_CATALOGO}/contents"
    resp = requests.get(api, timeout=config.HTTP_TIMEOUT)
    resp.raise_for_status()
    return [
        item["download_url"]
        for item in resp.json()
        if item.get("type") == "file"
        and item.get("download_url")
        and (item["name"].endswith(".txt") or "host" in item["name"].lower())
    ]


def coletar_catalogo() -> pd.DataFrame:
    origem = f"github:{config.GITHUB_REPO_CATALOGO}"
    try:
        linhas: list[str] = []
        for url in _urls_candidatas():
            r = requests.get(url, timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            linhas.extend(r.text.splitlines())
        dominios = _normalizar(linhas)
        if len(dominios) < 10:
            raise ValueError("lista muito curta")
        (config.EXTERNAL / "catalogo_ia_github.txt").write_text("\n".join(dominios), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        cache = config.CACHE / "catalogo_ia_snapshot.txt"
        log.warning("GitHub indisponivel (%s). Usando snapshot %s", exc, cache.name)
        dominios = _normalizar(cache.read_text(encoding="utf-8").splitlines())
        origem = "snapshot_local"

    df = pd.DataFrame({"dominio": dominios})
    df["origem"] = origem
    log.info("Catalogo externo: %d dominios (origem=%s)", len(df), origem)
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(coletar_catalogo().head())
