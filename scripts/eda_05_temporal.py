#!/usr/bin/env python
"""Fase 5 (EDA) — séries temporais (mês e ano), sazonalidade, categorias e taxas do alvo no tempo.

Uso:
    python scripts/eda_05_temporal.py --base cat
    python scripts/eda_05_temporal.py --base beneficios
    python scripts/eda_05_temporal.py --base cat --sem-figuras
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
from inss_acidentes.common.eda_temporal import ParamsTemporal, executar_temporal  # noqa: E402
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
    log = logging.getLogger("eda_temporal")

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

    p = ParamsTemporal.from_dict(cfg.temporal)
    if not p.colunas_data:
        log.error("Nada a fazer: defina `temporal.colunas_data` em configs/%s/eda.yaml.", args.base)
        return 1
    res = executar_temporal(df, cfg, p, gerar_figuras=not args.sem_figuras)
    log.info("Tabelas em %s", res["dir_tabelas"])
    if not args.sem_figuras:
        log.info("Figuras em %s", res["dir_figuras"])

    print("\nCobertura temporal (janela %s a %s):" % (p.inicio, p.fim))
    cols = ["coluna", "n_validos", "n_nulos", "data_min", "data_max", "n_antes_inicio", "n_depois_fim",
            "meses_sem_registro"]
    print(res["cobertura"][cols].to_string(index=False))
    for rot, t in res["taxas"].items():
        if "arquivo" in t:
            g = t["arquivo"]
            print(f"\nTaxa de {rot} por {p.controle_arquivo}: mín {g['taxa'].min():.3%} · máx {g['taxa'].max():.3%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
