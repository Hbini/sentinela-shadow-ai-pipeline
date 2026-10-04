"""Executa o pipeline completo de ponta a ponta.

    python run_pipeline.py              # coleta + ETL + features + modelo
    python run_pipeline.py --sem-gerar  # reaproveita data/raw/sentinela_eventos.jsonl

Fontes -> PostgreSQL (staging + dw) -> ETL Python -> Features -> ML -> Metricas
"""
from __future__ import annotations

import argparse
import logging
import time

from src.etl import pipeline_etl
from src.features import build_features
from src.models import train


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline Sentinela Shadow AI")
    ap.add_argument("--sem-gerar", action="store_true", help="nao regenera os logs simulados")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("pipeline")
    t0 = time.time()

    log.info("[1/3] Coleta + ETL -> PostgreSQL")
    q = pipeline_etl.executar(regerar_logs=not args.sem_gerar)
    log.info("      contagens dw: %s", q["contagens_dw"])

    log.info("[2/3] Engenharia de features")
    base = build_features.executar()

    log.info("[3/3] Treinamento e avaliacao")
    r = train.executar(base)
    m = r["teste"][r["modelo_escolhido"]]
    log.info("Modelo final: %s | ROC-AUC=%.3f F1=%.3f Accuracy=%.3f",
             r["modelo_escolhido"], m["roc_auc"], m["f1"], m["accuracy"])
    log.info("Concluido em %.1f s. Veja reports/metricas.json e reports/figuras/", time.time() - t0)


if __name__ == "__main__":
    main()
