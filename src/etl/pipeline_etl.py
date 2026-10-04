"""ETL em Python: fontes -> staging -> transformacao -> modelo estrela (dw).

Etapas
  E  extrair as fontes (API BrasilAPI, GitHub, CSV do calendario, logs JSONL
     da extensao e cadastros) e carrega-las SEM ALTERACAO em staging.*
  T  limpar e padronizar em pandas (duplicatas, dominios, fuso horario,
     categorias, chaves orfas) registrando um relatorio de qualidade
  L  carregar dimensoes e fato no esquema dw e criar as views analiticas
"""
from __future__ import annotations

import json
import logging
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from src import config
from src.extract.coletar_catalogo_ia import coletar_catalogo
from src.extract.coletar_feriados import coletar_feriados
from src.extract.gerar_eventos_sentinela import gerar

log = logging.getLogger(__name__)
NOMES_DIA = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


# ============================================================== infraestrutura
def garantir_banco() -> Engine:
    """Cria o banco sentinela_dw se nao existir e devolve o engine."""
    admin = create_engine(config.pg_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        existe = c.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": config.PG_DB}).scalar()
        if not existe:
            c.execute(text(f'CREATE DATABASE "{config.PG_DB}" ENCODING \'UTF8\''))
            log.info("Banco %s criado", config.PG_DB)
    admin.dispose()
    return create_engine(config.pg_url())


def executar_sql(engine: Engine, arquivo: str) -> None:
    sql = (config.SQL_DIR / arquivo).read_text(encoding="utf-8")
    with engine.begin() as c:
        c.exec_driver_sql(sql)
    log.info("SQL executado: %s", arquivo)


# ============================================================== E: extracao
def extrair(regerar_logs: bool = True) -> dict[str, pd.DataFrame]:
    feriados = coletar_feriados()
    calendario = pd.read_csv(config.RAW / "calendario_academico_2026.csv")
    catalogo = coletar_catalogo()
    if regerar_logs or not (config.RAW / "sentinela_eventos.jsonl").exists():
        gerar(feriados, calendario)
    eventos = pd.read_json(config.RAW / "sentinela_eventos.jsonl", lines=True, dtype=False)
    return {
        "stg_eventos": eventos,
        "stg_usuarios": pd.read_csv(config.RAW / "cadastro_usuarios.csv"),
        "stg_estacoes": pd.read_csv(config.RAW / "cadastro_estacoes.csv"),
        "stg_politica": pd.read_csv(config.RAW / "politica_sentinela.csv"),
        "stg_revisao_catalogo": pd.read_csv(config.RAW / "fila_revisao_catalogo.csv"),
        "stg_catalogo_externo": catalogo,
        "stg_feriados": feriados,
        "stg_calendario": calendario,
    }


def carregar_staging(engine: Engine, brutos: dict[str, pd.DataFrame]) -> None:
    for tabela, df in brutos.items():
        df.to_sql(tabela, engine, schema="staging", if_exists="append", index=False,
                  method="multi", chunksize=5000)
        log.info("staging.%s: %d linhas", tabela, len(df))


# ============================================================== T: transformacao
def _dominio(s: pd.Series) -> pd.Series:
    return s.str.strip().str.lower().str.replace(r"^www\.", "", regex=True)


def transformar(b: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], dict]:
    q: dict = {}

    # ---------- dim_data (calendario + feriados da API)
    dias = pd.date_range(config.INICIO_SEMESTRE, config.FIM_SEMESTRE, freq="D")
    dd = pd.DataFrame({"data": dias.date})
    fer = b["stg_feriados"].assign(data=pd.to_datetime(b["stg_feriados"]["data"]).dt.date)
    fer = fer.drop_duplicates("data").set_index("data")["nome"]
    dd["data_id"] = dias.strftime("%Y%m%d").astype(int)
    dd["ano"], dd["mes"], dd["dia"] = dias.year, dias.month, dias.day
    dd["dia_semana"] = dias.dayofweek
    dd["nome_dia"] = dd["dia_semana"].map(dict(enumerate(NOMES_DIA)))
    dd["fim_de_semana"] = dd["dia_semana"] >= 5
    dd["nome_feriado"] = dd["data"].map(fer)
    dd["feriado"] = dd["nome_feriado"].notna()
    dd["tipo_periodo"] = "aulas"
    cal = b["stg_calendario"].copy()
    for _, r in cal.iterrows():
        m = (dd["data"] >= pd.to_datetime(r["data_inicio"]).date()) & (dd["data"] <= pd.to_datetime(r["data_fim"]).date())
        dd.loc[m, "tipo_periodo"] = r["tipo"]
    dd["semana_prova"] = dd["tipo_periodo"].eq("semana_prova")
    dd["entrega_trabalhos"] = dd["tipo_periodo"].eq("entrega_trabalhos")
    dd["semana_letiva"] = ((pd.to_datetime(dd["data"]) - pd.Timestamp(config.INICIO_SEMESTRE)).dt.days // 7 + 1)

    # ---------- dim_usuario
    u = b["stg_usuarios"].copy()
    q["usuarios_brutos"] = len(u)
    u["perfil"] = u["perfil"].str.strip().str.lower()
    u = u.drop_duplicates("user_hash")
    q["usuarios_duplicados_removidos"] = q["usuarios_brutos"] - len(u)
    u["area_ti"] = u["curso"].isin(["Ciencia da Computacao", "Sistemas de Informacao", "Engenharia"])
    u["semestre"] = u["semestre"].astype("Int64")
    u["data_treinamento_ia"] = pd.to_datetime(u["data_treinamento_ia"]).dt.date
    dim_usuario = u[["user_hash", "perfil", "curso", "area_ti", "semestre", "turno", "data_treinamento_ia"]]

    # ---------- dim_estacao
    dim_estacao = b["stg_estacoes"].drop_duplicates("station_id")[["station_id", "local", "bloco"]]

    # ---------- dim_servico (politica interna + fila de revisao + catalogo externo GitHub)
    pol = b["stg_politica"].copy()
    pol["dominio"] = _dominio(pol["dominio"])
    pol["categoria"] = pol["categoria"].str.strip().str.lower()
    q["categorias_padronizadas"] = int((b["stg_politica"]["categoria"] != pol["categoria"]).sum())
    pol["catalogado_interno"] = True
    rev = b["stg_revisao_catalogo"].copy()
    rev["dominio"] = _dominio(rev["dominio"])
    rev = rev[~rev["dominio"].isin(pol["dominio"])]
    rev = rev.assign(servico=rev["dominio"], politica="warn", catalogado_interno=False)
    serv = pd.concat([pol, rev], ignore_index=True)
    externo = set(_dominio(b["stg_catalogo_externo"]["dominio"]))
    serv["no_catalogo_externo"] = serv["dominio"].isin(externo)
    q["servicos_no_catalogo_externo"] = int(serv["no_catalogo_externo"].sum())
    q["tamanho_catalogo_externo"] = len(externo)
    dim_servico = serv[["dominio", "servico", "categoria", "politica", "catalogado_interno", "no_catalogo_externo"]]

    # ---------- fato
    e = b["stg_eventos"].copy()
    q["eventos_brutos"] = len(e)
    e = e.drop_duplicates("event_id")
    q["eventos_duplicados_removidos"] = q["eventos_brutos"] - len(e)
    dom_antes = e["domain"].copy()
    e["domain"] = _dominio(e["domain"])
    q["dominios_normalizados"] = int((dom_antes != e["domain"]).sum())
    q["exposicao_nula_imputada"] = int(e["exposure"].isna().sum())
    e["exposure"] = e["exposure"].fillna("nao_informado")
    e["ts_local"] = (pd.to_datetime(e["ts"], utc=True).dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None))

    orfaos = ~e["user_hash"].isin(dim_usuario["user_hash"]) | ~e["station_id"].isin(dim_estacao["station_id"]) \
        | ~e["domain"].isin(dim_servico["dominio"])
    quarentena = e[orfaos].copy()
    quarentena = pd.DataFrame({
        "event_id": quarentena["event_id"],
        "motivo": "chave sem correspondencia nas dimensoes",
        "registro": quarentena.drop(columns=["ts_local"]).apply(lambda r: json.dumps(r.to_dict(), default=str), axis=1),
    }) if len(quarentena) else pd.DataFrame(columns=["event_id", "motivo", "registro"])
    q["eventos_quarentena"] = int(orfaos.sum())
    e = e[~orfaos]

    fato = pd.DataFrame({
        "evento_id": e["event_id"],
        "data_id": e["ts_local"].dt.strftime("%Y%m%d").astype(int),
        "user_hash": e["user_hash"], "station_id": e["station_id"], "dominio": e["domain"],
        "ts_local": e["ts_local"], "hora": e["ts_local"].dt.hour,
        "acao": e["action"], "exposicao": e["exposure"], "decisao": e["decision"],
        "prosseguiu": e["decision"].map({"proceeded": True, "left": False}),
        "tempo_decisao_ms": e["time_to_decision_ms"].astype("Int64"),
        "versao_extensao": e["ext_version"], "navegador": e["browser"],
    })
    q["eventos_carregados"] = len(fato)
    q["alertas_warn"] = int((fato["acao"] == "warn").sum())
    q["taxa_prosseguiu"] = round(float(fato["prosseguiu"].dropna().astype(float).mean()), 4)
    return {"dim_data": dd, "dim_usuario": dim_usuario, "dim_estacao": dim_estacao,
            "dim_servico": dim_servico, "fato": fato, "quarentena": quarentena}, q


# ============================================================== L: carga
def carregar_dw(engine: Engine, t: dict[str, pd.DataFrame]) -> None:
    cols_data = ["data_id", "data", "ano", "mes", "dia", "dia_semana", "nome_dia", "fim_de_semana", "feriado",
                 "nome_feriado", "tipo_periodo", "semana_prova", "entrega_trabalhos", "semana_letiva"]
    t["dim_data"][cols_data].to_sql("dim_data", engine, schema="dw", if_exists="append", index=False)
    for dim in ("dim_usuario", "dim_estacao", "dim_servico"):
        t[dim].to_sql(dim, engine, schema="dw", if_exists="append", index=False)

    with engine.connect() as c:
        sk_u = pd.read_sql("SELECT usuario_sk, user_hash FROM dw.dim_usuario", c)
        sk_e = pd.read_sql("SELECT estacao_sk, station_id FROM dw.dim_estacao", c)
        sk_s = pd.read_sql("SELECT servico_sk, dominio FROM dw.dim_servico", c)
    fato = (t["fato"].merge(sk_u, on="user_hash").merge(sk_e, on="station_id").merge(sk_s, on="dominio")
            .drop(columns=["user_hash", "station_id", "dominio"]))
    fato.to_sql("fato_evento_acesso", engine, schema="dw", if_exists="append", index=False,
                method="multi", chunksize=5000)
    if len(t["quarentena"]):
        t["quarentena"].to_sql("quarentena_eventos", engine, schema="dw", if_exists="append", index=False,
                               dtype={"registro": JSONB})
    log.info("dw carregado: fato=%d linhas", len(fato))


def validar(engine: Engine) -> pd.DataFrame:
    sql = """
    SELECT 'dim_data' AS tabela, COUNT(*) AS linhas FROM dw.dim_data UNION ALL
    SELECT 'dim_usuario', COUNT(*) FROM dw.dim_usuario UNION ALL
    SELECT 'dim_estacao', COUNT(*) FROM dw.dim_estacao UNION ALL
    SELECT 'dim_servico', COUNT(*) FROM dw.dim_servico UNION ALL
    SELECT 'fato_evento_acesso', COUNT(*) FROM dw.fato_evento_acesso UNION ALL
    SELECT 'quarentena_eventos', COUNT(*) FROM dw.quarentena_eventos UNION ALL
    SELECT 'vw_base_alertas', COUNT(*) FROM dw.vw_base_alertas"""
    with engine.connect() as c:
        return pd.read_sql(sql, c)


def executar(regerar_logs: bool = True) -> dict:
    engine = garantir_banco()
    executar_sql(engine, "01_schema.sql")
    brutos = extrair(regerar_logs)
    carregar_staging(engine, brutos)
    transf, qualidade = transformar(brutos)
    carregar_dw(engine, transf)
    executar_sql(engine, "02_views.sql")
    contagens = validar(engine)
    qualidade["fontes"] = {
        "feriados": brutos["stg_feriados"]["origem"].iloc[0],
        "catalogo_externo": brutos["stg_catalogo_externo"]["origem"].iloc[0],
        "calendario": "data/raw/calendario_academico_2026.csv",
        "eventos": "data/raw/sentinela_eventos.jsonl (simulado, semente %d)" % config.SEED,
    }
    qualidade["contagens_dw"] = dict(zip(contagens["tabela"], contagens["linhas"].astype(int)))
    qualidade["executado_em"] = str(pd.Timestamp.now().round("s"))
    (config.REPORTS / "qualidade_dados.json").write_text(
        json.dumps(qualidade, ensure_ascii=False, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
        encoding="utf-8")
    log.info("Relatorio de qualidade: %s", qualidade)
    return qualidade


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    executar()
