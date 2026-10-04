"""FONTE 1: logs de eventos da extensao Sentinela + cadastros pseudonimizados.

POR QUE DADOS SIMULADOS?
O Sentinela (produto do TCC) ainda esta em piloto: nao ha volume real de
eventos e o uso de registros reais de alunos exigiria consentimento e base
legal (LGPD). Por isso este modulo SIMULA, com semente fixa, os mesmos
artefatos que o piloto produz:

* data/raw/sentinela_eventos.jsonl  -> eventos enviados pela extensao ao
  webhook do n8n (um JSON por linha, mesmo esquema da extensao v0.2);
* data/raw/cadastro_usuarios.csv    -> cadastro pseudonimizado (hash) vindo
  do sistema academico;
* data/raw/cadastro_estacoes.csv    -> inventario de estacoes;
* data/raw/politica_sentinela.csv   -> catalogo interno de servicos de IA.

O comportamento simulado usa as FONTES REAIS 2 e 3 (feriados e calendario
academico) para modular volume e comportamento. Problemas de qualidade
tipicos de producao sao injetados de proposito (duplicatas da fila de
reenvio, dominios com www/maiusculas, nulos, categorias inconsistentes)
para serem tratados no ETL.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from src import config

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ catalogo interno
# dominio, servico, categoria, politica (allow | warn | block)
POLITICA = [
    ("chatgpt.com", "ChatGPT", "chat_llm", "warn"),
    ("chat.openai.com", "ChatGPT (legado)", "chat_llm", "warn"),
    ("gemini.google.com", "Gemini (Workspace edu)", "chat_llm", "allow"),
    ("copilot.microsoft.com", "Copilot (licenca institucional)", "chat_llm", "allow"),
    ("claude.ai", "Claude", "chat_llm", "warn"),
    ("chat.deepseek.com", "DeepSeek", "chat_llm", "warn"),
    ("perplexity.ai", "Perplexity", "pesquisa_academica", "warn"),
    ("meta.ai", "Meta AI", "chat_llm", "warn"),
    ("poe.com", "Poe", "chat_llm", "warn"),
    ("character.ai", "Character.AI", "chat_llm", "block"),
    ("quillbot.com", "QuillBot", "escrita_parafrase", "warn"),
    ("grammarly.com", "Grammarly", "escrita_parafrase", "warn"),
    ("rytr.me", "Rytr", "escrita_parafrase", "warn"),
    ("humanizeai.pro", "Humanize AI", "escrita_parafrase", "block"),
    ("undetectable.ai", "Undetectable AI", "escrita_parafrase", "block"),
    ("stealthwriter.ai", "StealthWriter", "escrita_parafrase", "block"),
    ("photomath.com", "Photomath", "resolucao_exercicios", "warn"),
    ("brainly.com.br", "Brainly", "resolucao_exercicios", "warn"),
    ("gauthmath.com", "Gauth", "resolucao_exercicios", "warn"),
    ("cursor.com", "Cursor", "codigo", "warn"),
    ("replit.com", "Replit", "codigo", "warn"),
    ("v0.dev", "v0", "codigo", "warn"),
    ("midjourney.com", "Midjourney", "imagem_video", "warn"),
    ("leonardo.ai", "Leonardo", "imagem_video", "warn"),
    ("suno.com", "Suno", "audio", "warn"),
    ("elevenlabs.io", "ElevenLabs", "audio", "warn"),
    ("gamma.app", "Gamma", "apresentacao", "warn"),
    ("scispace.com", "SciSpace", "pesquisa_academica", "warn"),
    ("consensus.app", "Consensus", "pesquisa_academica", "warn"),
    ("chatpdf.com", "ChatPDF", "pesquisa_academica", "warn"),
]
# servicos de IA que NAO estao no catalogo interno (Shadow AI "descoberta"):
# a extensao os reconhece pelo catalogo externo e emite alerta (warn).
NAO_CATALOGADOS = [
    ("kimi.com", "chat_llm"), ("chat.qwen.ai", "chat_llm"), ("grok.com", "chat_llm"),
    ("jenni.ai", "escrita_parafrase"), ("wordtune.com", "escrita_parafrase"),
    ("mathway.com", "resolucao_exercicios"), ("studocu.com", "resolucao_exercicios"),
    ("bolt.new", "codigo"), ("lovable.dev", "codigo"), ("ideogram.ai", "imagem_video"),
    ("paperpal.com", "pesquisa_academica"), ("elicit.com", "pesquisa_academica"),
]

CURSOS = ["Ciencia da Computacao", "Sistemas de Informacao", "Engenharia", "Administracao",
          "Direito", "Pedagogia", "Enfermagem"]
CURSOS_TI = {"Ciencia da Computacao", "Sistemas de Informacao", "Engenharia"}

# pesos (escala logit) do comportamento simulado "ignorar alerta e prosseguir"
EFEITO_CATEGORIA = {"escrita_parafrase": 0.9, "resolucao_exercicios": 0.8, "chat_llm": 0.4,
                    "codigo": 0.3, "apresentacao": 0.3, "pesquisa_academica": -0.1,
                    "audio": -0.2, "imagem_video": -0.3}
EFEITO_PERFIL = {"aluno": 0.5, "docente": -0.2, "administrativo": -0.6}
EFEITO_LOCAL = {"biblioteca": 0.2, "laboratorio": 0.0, "sala_docentes": -0.2, "administrativo": -0.5}
EFEITO_EXPOSICAO = {"nenhuma": 0.0, "contexto_baixo": -0.25, "contexto_alto": -0.8}


def _h(prefixo: str, i: int) -> str:
    return hashlib.sha256(f"{prefixo}-{i}-sentinela".encode()).hexdigest()[:16]


def _calendario(feriados: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
    dias = pd.date_range(config.INICIO_SEMESTRE, config.FIM_SEMESTRE, freq="D")
    df = pd.DataFrame({"data": dias.date})
    df["dow"] = dias.dayofweek
    fer = set(pd.to_datetime(feriados["data"]).dt.date)
    df["feriado"] = df["data"].isin(fer)
    df["tipo_periodo"] = "aulas"
    for _, r in cal.iterrows():
        m = (df["data"] >= r["data_inicio"]) & (df["data"] <= r["data_fim"])
        df.loc[m, "tipo_periodo"] = r["tipo"]
    return df


def gerar(feriados: pd.DataFrame, calendario: pd.DataFrame, seed: int = config.SEED) -> dict:
    rng = np.random.default_rng(seed)
    cal = calendario.copy()
    cal["data_inicio"] = pd.to_datetime(cal["data_inicio"]).dt.date
    cal["data_fim"] = pd.to_datetime(cal["data_fim"]).dt.date
    dias = _calendario(feriados, cal)

    # ---------------------------------------------------------------- estacoes
    locais = (["laboratorio"] * 60) + (["biblioteca"] * 25) + (["administrativo"] * 20) + (["sala_docentes"] * 15)
    estacoes = pd.DataFrame({
        "station_id": [_h("st", i) for i in range(len(locais))],
        "local": locais,
        "bloco": rng.choice(["A", "B", "C", "D"], len(locais)),
    })

    # ---------------------------------------------------------------- usuarios
    n_users = 650
    perfil = rng.choice(["aluno", "docente", "administrativo"], n_users, p=[0.80, 0.12, 0.08])
    usuarios = pd.DataFrame({"user_hash": [_h("us", i) for i in range(n_users)], "perfil": perfil})
    usuarios["curso"] = np.where(usuarios.perfil == "administrativo", None,
                                 rng.choice(CURSOS, n_users, p=[.2, .15, .2, .15, .12, .1, .08]))
    usuarios["semestre"] = np.where(usuarios.perfil == "aluno", rng.integers(1, 11, n_users), None)
    usuarios["turno"] = rng.choice(["manha", "noite"], n_users, p=[0.35, 0.65])
    # treinamento de letramento em IA: parte antes do semestre, parte durante (abril)
    trein = rng.random(n_users)
    datas_trein = []
    for t in trein:
        if t < 0.18:
            datas_trein.append(date(2025, 11, 20))
        elif t < 0.35:
            datas_trein.append(date(2026, 4, 22) + timedelta(days=int(rng.integers(0, 10))))
        else:
            datas_trein.append(None)
    usuarios["data_treinamento_ia"] = datas_trein
    latente = rng.normal(0, 0.8, n_users)  # propensao individual (nao observada)
    atividade = rng.gamma(2.0, 0.6, n_users)  # intensidade de uso
    usuarios_int = usuarios.assign(_lat=latente, _atv=atividade)

    st_por_local = estacoes.groupby("local")["station_id"].apply(list).to_dict()
    pref_local = {"aluno": (["laboratorio", "biblioteca"], [0.65, 0.35]),
                  "docente": (["sala_docentes", "laboratorio"], [0.7, 0.3]),
                  "administrativo": (["administrativo"], [1.0])}

    serv_cat = [(d, c, p) for d, _, c, p in POLITICA] + [(d, c, "warn") for d, c in NAO_CATALOGADOS]
    pesos_serv = np.array([3.0 if c == "chat_llm" else 1.0 for _, c, _ in serv_cat])
    pesos_serv[0] = 8.0  # chatgpt domina
    pesos_serv = pesos_serv / pesos_serv.sum()

    fator_periodo = {"aulas": 1.0, "inicio_aulas": 0.6, "recesso": 0.1, "entrega_trabalhos": 1.5,
                     "semana_prova": 1.7, "evento_academico": 0.8, "encerramento": 0.5}

    eventos = []
    ignorou_hoje: dict[str, int] = {}
    for _, d in dias.iterrows():
        if d["dow"] == 6:
            continue
        fator = fator_periodo[d["tipo_periodo"]] * (0.35 if d["dow"] == 5 else 1.0)
        if d["feriado"]:
            fator *= 0.05
        ignorou_hoje.clear()
        lam = usuarios_int["_atv"].to_numpy() * fator * 0.55
        n_ev = rng.poisson(lam)
        for idx in np.nonzero(n_ev)[0]:
            u = usuarios_int.iloc[idx]
            locs, pl = pref_local[u.perfil]
            local = rng.choice(locs, p=pl)
            st = rng.choice(st_por_local[local])
            if u.turno == "noite" and u.perfil == "aluno":
                horas = rng.integers(18, 23, n_ev[idx])
            else:
                horas = rng.integers(7, 19, n_ev[idx])
            alertas_hoje = 0
            for h in sorted(horas):
                k = rng.choice(len(serv_cat), p=pesos_serv)
                dom, cat, pol = serv_cat[k]
                ts = datetime(d["data"].year, d["data"].month, d["data"].day, int(h),
                              int(rng.integers(0, 60)), int(rng.integers(0, 60)))
                # BRT (UTC-3) -> UTC, como a extensao envia
                ts_utc = (ts + timedelta(hours=3)).replace(tzinfo=timezone.utc)
                exposicao = rng.choice(["nenhuma", "contexto_baixo", "contexto_alto"], p=[0.55, 0.30, 0.15])
                ev = {
                    "event_id": str(uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | int(rng.integers(0, 2**63)))),
                    "ts": ts_utc.isoformat().replace("+00:00", "Z"),
                    "station_id": st,
                    "user_hash": u.user_hash,
                    "domain": dom,
                    "action": pol,
                    "exposure": exposicao,
                    "ext_version": "0.2.1" if d["data"] >= date(2026, 4, 15) else "0.2.0",
                    "browser": rng.choice(["chrome", "edge"], p=[0.8, 0.2]),
                    "decision": None,
                    "time_to_decision_ms": None,
                }
                if pol == "warn":
                    treinado = u.data_treinamento_ia is not None and u.data_treinamento_ia <= d["data"]
                    z = (-0.9 + EFEITO_PERFIL[u.perfil] + EFEITO_CATEGORIA[cat] + EFEITO_LOCAL[local]
                         + EFEITO_EXPOSICAO[exposicao] + u._lat
                         + (0.7 if d["tipo_periodo"] == "semana_prova" else 0)
                         + (0.5 if d["tipo_periodo"] == "entrega_trabalhos" else 0)
                         + (0.3 if h >= 19 else 0)
                         + (0.6 if (cat == "codigo" and u.curso in CURSOS_TI) else 0)
                         + (0.25 if (u.semestre is not None and u.semestre <= 2) else 0)
                         + (-0.2 if dom in dict(NAO_CATALOGADOS) else 0)
                         + (-0.7 if treinado else 0)
                         + 0.18 * np.log1p(alertas_hoje))  # fadiga de alerta
                    p = 1 / (1 + np.exp(-z))
                    prosseguiu = rng.random() < p
                    ev["decision"] = "proceeded" if prosseguiu else "left"
                    # tempo de decisao: depende do proprio desfecho (variavel com VAZAMENTO)
                    ev["time_to_decision_ms"] = int(rng.gamma(2, 900 if prosseguiu else 2600))
                    alertas_hoje += 1
                eventos.append(ev)

    # ------------------------------------------------ problemas de qualidade (de proposito)
    n = len(eventos)
    for i in rng.choice(n, int(n * 0.03), replace=False):  # reenvio da fila -> duplicata
        eventos.append(dict(eventos[i]))
    for i in rng.choice(n, int(n * 0.05), replace=False):
        eventos[i]["domain"] = "www." + eventos[i]["domain"].upper()
    for i in rng.choice(n, int(n * 0.01), replace=False):
        eventos[i]["exposure"] = None
    for i in rng.choice(n, 40, replace=False):
        eventos[i]["user_hash"] = "desconhecido"

    usuarios_out = usuarios.copy()
    bagunca = rng.choice(len(usuarios_out), 30, replace=False)
    usuarios_out.loc[bagunca[:15], "perfil"] = usuarios_out.loc[bagunca[:15], "perfil"].str.upper() + " "
    usuarios_out.loc[bagunca[15:], "perfil"] = usuarios_out.loc[bagunca[15:], "perfil"].str.title()
    usuarios_out = pd.concat([usuarios_out, usuarios_out.sample(8, random_state=seed)], ignore_index=True)

    politica = pd.DataFrame(POLITICA, columns=["dominio", "servico", "categoria", "politica"])
    politica.loc[politica.sample(3, random_state=seed).index, "categoria"] = politica["categoria"].str.upper()
    # categorias dos nao catalogados (classificacao manual do gestor, fila de revisao)
    revisao = pd.DataFrame(NAO_CATALOGADOS, columns=["dominio", "categoria"])

    rng.shuffle(eventos)
    with open(config.RAW / "sentinela_eventos.jsonl", "w", encoding="utf-8") as f:
        for ev in eventos:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    usuarios_out.to_csv(config.RAW / "cadastro_usuarios.csv", index=False)
    estacoes.to_csv(config.RAW / "cadastro_estacoes.csv", index=False)
    politica.to_csv(config.RAW / "politica_sentinela.csv", index=False)
    revisao.to_csv(config.RAW / "fila_revisao_catalogo.csv", index=False)
    log.info("Eventos gerados: %d (com duplicatas), usuarios: %d, estacoes: %d",
             len(eventos), len(usuarios_out), len(estacoes))
    return {"eventos": len(eventos), "usuarios": len(usuarios_out), "estacoes": len(estacoes)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from src.extract.coletar_feriados import coletar_feriados

    gerar(coletar_feriados(), pd.read_csv(config.RAW / "calendario_academico_2026.csv"))
