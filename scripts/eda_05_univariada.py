#!/usr/bin/env python
"""Fase 5 (EDA) — análise univariada para CAT ou Benefícios (BC).

Uso:
    python scripts/eda_05_univariada.py --base cat
    python scripts/eda_05_univariada.py --base beneficios
    python scripts/eda_05_univariada.py --base cat --so-tipos     # só mostra a classificação de colunas
    python scripts/eda_05_univariada.py --base cat --sem-figuras  # só tabelas
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402

from inss_acidentes.common.eda_univariada import (  # noqa: E402
    ConfigEDA,
    classificar_colunas,
    executar_univariada,
    preparar_dataframe,
)

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", choices=["cat", "beneficios"], required=True)
    ap.add_argument("--config", default=None, help="YAML (padrão: configs/<base>/eda.yaml)")
    ap.add_argument("--entrada", default=None, help="parquet de entrada (sobrescreve o YAML)")
    ap.add_argument("--so-tipos", action="store_true", help="apenas imprime a classificação de colunas")
    ap.add_argument("--sem-figuras", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("eda_univariada")

    cfg_path = Path(args.config) if args.config else RAIZ / "configs" / args.base / "eda.yaml"
    cfg = ConfigEDA.from_yaml(cfg_path, RAIZ)
    if args.entrada:
        cfg.entrada = Path(args.entrada)
    if not cfg.entrada.exists():
        log.error("Arquivo de entrada não encontrado: %s", cfg.entrada)
        return 1

    log.info("Lendo %s", cfg.entrada)
    df = preparar_dataframe(pd.read_parquet(cfg.entrada), cfg)
    log.info("%s linhas × %s colunas", f"{len(df):,}".replace(",", "."), df.shape[1])

    if args.so_tipos:
        for tipo, cols in classificar_colunas(df, cfg).items():
            print(f"\n[{tipo}] ({len(cols)})")
            for c in cols:
                print(f"  - {c}  ({df[c].dtype}, {df[c].nunique(dropna=True)} distintos)")
        return 0

    res = executar_univariada(df, cfg, gerar_figuras=not args.sem_figuras)
    log.info("Tabelas em %s", cfg.saida_tabelas)
    if not args.sem_figuras:
        log.info("Figuras em %s", cfg.saida_figuras)
    sinalizadas = res["resumo"].query("observacao != ''")[["variavel", "observacao"]]
    if len(sinalizadas):
        print("\nVariáveis sinalizadas:")
        print(sinalizadas.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
