"""Treinamento e avaliacao do modelo de classificacao.

Problema: dado um ALERTA do Sentinela, prever se o usuario vai ignora-lo e
prosseguir no acesso ao servico de IA (alvo_prosseguiu = 1).

* split TEMPORAL: treino = fev a mai/2026, teste = jun/2026 (simula producao)
* validacao cruzada TimeSeriesSplit (5 dobras) no treino para escolher o modelo
* metricas: Accuracy, Precision, Recall, F1, ROC-AUC, PR-AUC, Brier
* experimento de controle: o mesmo modelo COM a variavel vazada
  tempo_decisao_ms, para mostrar por que ela foi excluida
"""
from __future__ import annotations

import json
import logging

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.dummy import DummyClassifier  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    ConfusionMatrixDisplay, PrecisionRecallDisplay, RocCurveDisplay, accuracy_score,
    average_precision_score, brier_score_loss, f1_score, precision_score, recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit, cross_validate  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

from src import config  # noqa: E402
from src.features.build_features import ALVO, BINARIAS, CATEGORICAS, NUMERICAS  # noqa: E402

log = logging.getLogger(__name__)
FEATURES = NUMERICAS + BINARIAS + CATEGORICAS


def preprocessador(escalar: bool = True, extra_num: list[str] | None = None) -> ColumnTransformer:
    num = NUMERICAS + (extra_num or [])
    return ColumnTransformer([
        ("num", StandardScaler() if escalar else "passthrough", num),
        ("bin", "passthrough", BINARIAS),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAS),
    ])


def candidatos() -> dict[str, Pipeline]:
    s = config.SEED
    return {
        "Baseline (classe majoritaria)": Pipeline([("prep", preprocessador()),
                                                   ("clf", DummyClassifier(strategy="most_frequent"))]),
        "Regressao Logistica": Pipeline([("prep", preprocessador()),
                                         ("clf", LogisticRegression(max_iter=2000, C=1.0))]),
        "Random Forest": Pipeline([("prep", preprocessador(escalar=False)),
                                   ("clf", RandomForestClassifier(n_estimators=300, min_samples_leaf=20,
                                                                  n_jobs=-1, random_state=s))]),
        "Gradient Boosting (HistGB)": Pipeline([("prep", preprocessador(escalar=False)),
                                                ("clf", HistGradientBoostingClassifier(
                                                    learning_rate=0.06, max_iter=300, max_leaf_nodes=31,
                                                    l2_regularization=1.0, random_state=s))]),
    }


def metricas(y, proba, limiar: float = 0.5) -> dict:
    pred = (proba >= limiar).astype(int)
    return {
        "accuracy": accuracy_score(y, pred), "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred), "f1": f1_score(y, pred),
        "roc_auc": roc_auc_score(y, proba), "pr_auc": average_precision_score(y, proba),
        "brier": brier_score_loss(y, proba),
    }


def dividir(df: pd.DataFrame):
    df = df.sort_values(["ts_local", "evento_id"]).reset_index(drop=True)
    corte = pd.Timestamp(config.DATA_CORTE_TESTE)
    treino = df[pd.to_datetime(df["ts_local"]) < corte]
    teste = df[pd.to_datetime(df["ts_local"]) >= corte]
    return treino, teste


def executar(df: pd.DataFrame | None = None) -> dict:
    if df is None:
        df = pd.read_csv(config.PROCESSED / "base_modelo.csv")
    treino, teste = dividir(df)
    Xtr, ytr = treino[FEATURES], treino[ALVO]
    Xte, yte = teste[FEATURES], teste[ALVO]
    log.info("Treino %s (%.1f%% positivos) | Teste %s (%.1f%% positivos)",
             Xtr.shape, 100 * ytr.mean(), Xte.shape, 100 * yte.mean())

    # ---------------------------------------------- 1) selecao por validacao cruzada temporal
    tscv = TimeSeriesSplit(n_splits=5)
    cv_res = {}
    for nome, pipe in candidatos().items():
        r = cross_validate(pipe, Xtr, ytr, cv=tscv, scoring=["roc_auc", "f1", "accuracy"], n_jobs=1)
        cv_res[nome] = {k.replace("test_", ""): float(np.mean(v)) for k, v in r.items() if k.startswith("test_")}
        cv_res[nome]["roc_auc_std"] = float(np.std(r["test_roc_auc"]))
        log.info("CV %-30s AUC=%.4f F1=%.4f", nome, cv_res[nome]["roc_auc"], cv_res[nome]["f1"])
    escolhido = max((n for n in cv_res if not n.startswith("Baseline")), key=lambda n: cv_res[n]["roc_auc"])
    log.info("Modelo escolhido: %s", escolhido)

    # ---------------------------------------------- 2) treino final e avaliacao no teste (junho)
    teste_res, probas = {}, {}
    modelos = candidatos()
    for nome, pipe in modelos.items():
        pipe.fit(Xtr, ytr)
        probas[nome] = pipe.predict_proba(Xte)[:, 1]
        teste_res[nome] = metricas(yte, probas[nome])
    final = modelos[escolhido]

    # ---------------------------------------------- 3) controle de vazamento
    vaz_pipe = Pipeline([("prep", preprocessador(escalar=False, extra_num=["tempo_decisao_ms"])),
                         ("clf", HistGradientBoostingClassifier(random_state=config.SEED))])
    vaz_pipe.fit(treino[FEATURES + ["tempo_decisao_ms"]], ytr)
    vaz = metricas(yte, vaz_pipe.predict_proba(teste[FEATURES + ["tempo_decisao_ms"]])[:, 1])

    # ---------------------------------------------- 4) importancia por permutacao (no teste)
    amostra = teste.sample(min(5000, len(teste)), random_state=config.SEED)
    imp = permutation_importance(final, amostra[FEATURES], amostra[ALVO], scoring="roc_auc",
                                 n_repeats=5, random_state=config.SEED, n_jobs=1)
    importancias = (pd.DataFrame({"feature": FEATURES, "queda_auc": imp.importances_mean,
                                  "desvio": imp.importances_std})
                    .sort_values("queda_auc", ascending=False).reset_index(drop=True))

    # ---------------------------------------------- 5) analise de negocio: priorizacao
    p = probas[escolhido]
    decil = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False)
    lift = (pd.DataFrame({"decil": 10 - decil, "y": yte.to_numpy(), "p": p})
            .groupby("decil").agg(alertas=("y", "size"), taxa_real=("y", "mean"), prob_media=("p", "mean"))
            .reset_index())
    top20 = yte.to_numpy()[p >= np.quantile(p, 0.8)].mean()

    # ---------------------------------------------- 6) artefatos
    joblib.dump(final, config.MODELS / "modelo_sentinela.joblib")
    _figuras(cv_res, teste_res, probas, yte, escolhido, final, Xte, importancias, lift)
    resultado = {
        "problema": "classificacao binaria: alerta ignorado (prosseguiu=1) x respeitado (0)",
        "split": {"treino": [str(treino.ts_local.min())[:10], str(treino.ts_local.max())[:10]],
                  "teste": [str(teste.ts_local.min())[:10], str(teste.ts_local.max())[:10]],
                  "n_treino": len(treino), "n_teste": len(teste),
                  "taxa_positiva_treino": float(ytr.mean()), "taxa_positiva_teste": float(yte.mean())},
        "n_features_entrada": len(FEATURES),
        "validacao_cruzada": cv_res, "modelo_escolhido": escolhido, "teste": teste_res,
        "controle_vazamento": {"com_tempo_decisao_ms": vaz, "sem (modelo final)": teste_res[escolhido]},
        "taxa_real_top20pct_risco": float(top20),
        "lift_por_decil": lift.to_dict(orient="records"),
        "importancia_permutacao_top10": importancias.head(10).to_dict(orient="records"),
    }
    (config.REPORTS / "metricas.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    importancias.to_csv(config.REPORTS / "importancia_features.csv", index=False)
    log.info("Teste (%s): %s", escolhido, {k: round(v, 4) for k, v in teste_res[escolhido].items()})
    return resultado


def _figuras(cv_res, teste_res, probas, yte, escolhido, final, Xte, importancias, lift):
    # comparacao de modelos
    nomes = list(teste_res)
    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(nomes))
    ax.bar(x - 0.2, [cv_res[n]["roc_auc"] for n in nomes], 0.4, label="CV temporal (treino)")
    ax.bar(x + 0.2, [teste_res[n]["roc_auc"] for n in nomes], 0.4, label="Teste (jun/2026)")
    ax.set_xticks(x, [n.replace(" (", "\n(") for n in nomes], fontsize=8)
    ax.set_ylim(0.4, 1)
    ax.set_ylabel("ROC-AUC")
    ax.set_title("Comparacao de modelos")
    ax.legend()
    fig.tight_layout()
    fig.savefig(config.FIGS / "comparacao_modelos.png", dpi=130)
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(11, 4.5))
    for n in nomes[1:]:
        RocCurveDisplay.from_predictions(yte, probas[n], name=n, ax=axs[0])
        PrecisionRecallDisplay.from_predictions(yte, probas[n], name=n, ax=axs[1])
    axs[0].plot([0, 1], [0, 1], "k--", lw=0.8)
    axs[0].set_title("Curva ROC (teste)")
    axs[1].set_title("Curva Precision-Recall (teste)")
    fig.tight_layout()
    fig.savefig(config.FIGS / "curvas_roc_pr.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.8, 4.2))
    ConfusionMatrixDisplay.from_predictions(yte, (probas[escolhido] >= 0.5).astype(int),
                                            display_labels=["respeitou", "ignorou"], cmap="Blues", ax=ax)
    ax.set_title(f"Matriz de confusao\n{escolhido}")
    fig.tight_layout()
    fig.savefig(config.FIGS / "matriz_confusao.png", dpi=130)
    plt.close(fig)

    top = importancias.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.barh(top["feature"], top["queda_auc"], xerr=top["desvio"], color="#2a6f97")
    ax.set_xlabel("Queda media de ROC-AUC ao embaralhar a feature")
    ax.set_title("Importancia por permutacao (teste)")
    fig.tight_layout()
    fig.savefig(config.FIGS / "importancia_features.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(lift["decil"], lift["taxa_real"], color="#e07a1f")
    ax.axhline(yte.mean(), color="k", ls="--", lw=1, label=f"media geral ({yte.mean():.2f})")
    ax.set_xlabel("Decil de risco previsto (1 = maior risco)")
    ax.set_ylabel("Taxa real de alerta ignorado")
    ax.set_title("Priorizacao: taxa real por decil de risco")
    ax.set_xticks(range(1, 11))
    ax.legend()
    fig.tight_layout()
    fig.savefig(config.FIGS / "lift_decis.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    executar()
