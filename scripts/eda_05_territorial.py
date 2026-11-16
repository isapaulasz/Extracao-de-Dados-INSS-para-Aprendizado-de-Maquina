#!/usr/bin/env python
"""Fase 5 (EDA) — ranking por UF e região (volume e taxa do alvo com IC de Wilson).

Uso:
    python scripts/eda_05_territorial.py --base cat
    python scripts/eda_05_territorial.py --base beneficios
    python scripts/eda_05_territorial.py --base cat --sem-figuras
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402

from inss_acidentes.common.eda_bivariada import ParamsBivariada, adicionar_derivadas  # noqa: E402
from inss_acidentes.common.eda_territorial import ParamsTerritorial, executar_territorial  # noqa: E402
from inss_acidentes.common.eda_univariada import ConfigEDA, preparar_dataframe  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", choices=["cat", "beneficios"], required=True)
    ap.add_argument("--config", default=None, help="YAML (padrão: configs/<base>/eda.yaml)")
    ap.add_argument("--entrada", default=None, help="parquet de entrada (sobrescreve o YAML)")
    ap.add_argument("--sem-figuras", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("eda_territorial")

    cfg = ConfigEDA.from_yaml(Path(args.config) if args.config else RAIZ / "configs" / args.base / "eda.yaml", RAIZ)
    if args.entrada:
        cfg.entrada = Path(args.entrada)
    if not cfg.entrada.exists():
        log.error("Arquivo de entrada não encontrado: %s", cfg.entrada)
        return 1

    log.info("Lendo %s", cfg.entrada)
    df = preparar_dataframe(pd.read_parquet(cfg.entrada), cfg)
    # indicadores derivados (ex.: tem_data_despacho) são definidos na seção `bivariada` do YAML
    df = adicionar_derivadas(df, ParamsBivariada.from_dict(cfg.bivariada))
    log.info("%s linhas × %s colunas", f"{len(df):,}".replace(",", "."), df.shape[1])

    p = ParamsTerritorial.from_dict(cfg.territorial)
    if not (p.coluna_uf or p.coluna_regiao):
        log.error("Nada a fazer: defina `territorial.coluna_uf` em configs/%s/eda.yaml.", args.base)
        return 1
    res = executar_territorial(df, cfg, p, gerar_figuras=not args.sem_figuras)
    log.info("Tabelas em %s", res["dir_tabelas"])
    if not args.sem_figuras:
        log.info("Figuras em %s", res["dir_figuras"])

    for col, r in res["volumes"].items():
        print(f"\nTop 10 por volume — {col}:")
        print(r.head(10)[["sigla", "n", "pct_total", "pct_acum_informadas"]].to_string(index=False))
    for (col, alvo, pos), t in res["taxas"].items():
        print(f"\nTop 5 taxas de {alvo} = {pos} por {col} (n ≥ {p.min_n_taxa}):")
        print(t[t["n_suficiente"]].head(5)[["sigla", "n", "n_pos", "taxa", "razao_taxa_nacional"]]
              .to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
