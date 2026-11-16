#!/usr/bin/env python
"""Fase 5 (EDA) — informação mútua entre variáveis e candidatos a alvo + desbalanceamento dos alvos.

Uso:
    python scripts/eda_05_informacao_mutua.py --base cat
    python scripts/eda_05_informacao_mutua.py --base beneficios
    python scripts/eda_05_informacao_mutua.py --base cat --so-alvos    # só a tabela de desbalanceamento
    python scripts/eda_05_informacao_mutua.py --base cat --sem-figuras
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from inss_acidentes.common.eda_informacao_mutua import (  # noqa: E402
    ParamsMI,
    executar_mi,
    proibidas_do_mapa,
    resumo_alvo,
)
from inss_acidentes.common.eda_bivariada import preparar_bivariada  # noqa: E402
from inss_acidentes.common.eda_univariada import ConfigEDA, preparar_dataframe  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", choices=["cat", "beneficios"], required=True)
    ap.add_argument("--config", default=None, help="YAML (padrão: configs/<base>/eda.yaml)")
    ap.add_argument("--entrada", default=None, help="parquet de entrada (sobrescreve o YAML)")
    ap.add_argument("--so-alvos", action="store_true", help="só a tabela de desbalanceamento dos alvos")
    ap.add_argument("--sem-figuras", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("eda_mi")

    cfg = ConfigEDA.from_yaml(Path(args.config) if args.config else RAIZ / "configs" / args.base / "eda.yaml", RAIZ)
    if args.entrada:
        cfg.entrada = Path(args.entrada)
    if not cfg.entrada.exists():
        log.error("Arquivo de entrada não encontrado: %s", cfg.entrada)
        return 1

    log.info("Lendo %s", cfg.entrada)
    df = preparar_dataframe(pd.read_parquet(cfg.entrada), cfg)
    log.info("%s linhas × %s colunas", f"{len(df):,}".replace(",", "."), df.shape[1])

    if args.so_alvos:
        df, _, _, _ = preparar_bivariada(df, cfg)
        t = pd.DataFrame([resumo_alvo(df[a], a) for a in cfg.candidatos_alvo if a in df.columns])
        cols = ["alvo", "n_validos", "n_classes", "classe_minoritaria", "pct_minoritaria", "razao_maj_min", "nivel"]
        print(t[cols].to_string(index=False))
        return 0

    features_yaml = RAIZ / "configs" / args.base / "features.yaml"
    mapa = {}
    if features_yaml.exists():
        mapa = proibidas_do_mapa(yaml.safe_load(features_yaml.read_text(encoding="utf-8")),
                                 cfg.mi.get("alvos_vazamento") or {})
    p = ParamsMI.from_dict(cfg.mi, mapa)

    res = executar_mi(df, cfg, p, gerar_figuras=not args.sem_figuras)
    log.info("Tabelas em %s", res["dir_tabelas"])
    if not args.sem_figuras:
        log.info("Figuras em %s", res["dir_figuras"])

    print("\nDesbalanceamento dos candidatos a alvo:")
    cols = ["cenario", "alvo", "n_validos", "n_classes", "classe_minoritaria", "pct_minoritaria", "nivel"]
    print(res["desbalanceamento"][cols].to_string(index=False))
    for (cenario, alvo), rank in res["rankings"].items():
        print(f"\nTop 8 por informação mútua — alvo {alvo} (cenário: {cenario}):")
        print(rank[["variavel", "u_alvo", "v_corrigido", "recomendacao"]].head(8).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
