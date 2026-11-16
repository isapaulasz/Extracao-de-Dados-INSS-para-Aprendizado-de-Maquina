#!/usr/bin/env python
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402

from inss_acidentes.common.eda_bivariada import executar_bivariada, preparar_bivariada  # noqa: E402
from inss_acidentes.common.eda_univariada import ConfigEDA, preparar_dataframe  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", choices=["cat", "beneficios"], required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--entrada", default=None)
    ap.add_argument("--so-selecao", action="store_true", help="só mostra variáveis avaliadas e não avaliadas")
    ap.add_argument("--sem-figuras", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("eda_bivariada")

    cfg = ConfigEDA.from_yaml(Path(args.config) if args.config else RAIZ / "configs" / args.base / "eda.yaml", RAIZ)
    if args.entrada:
        cfg.entrada = Path(args.entrada)
    if not cfg.entrada.exists():
        log.error("Arquivo de entrada não encontrado: %s", cfg.entrada)
        return 1

    log.info("Lendo %s", cfg.entrada)
    df = preparar_dataframe(pd.read_parquet(cfg.entrada), cfg)
    log.info("%s linhas × %s colunas", f"{len(df):,}".replace(",", "."), df.shape[1])

    if args.so_selecao:
        _, codif, nao, _ = preparar_bivariada(df, cfg)
        print(f"\nAvaliadas ({len(codif)}):")
        for c, o in codif.items():
            print(f"  - {c:38s} {o.k:4d} níveis" + (f"  ({o.n_agrupados} raros -> Outras)" if o.n_agrupados else ""))
        print(f"\nNão avaliadas ({len(nao)}):")
        print(nao.to_string(index=False))
        return 0

    res = executar_bivariada(df, cfg, gerar_figuras=not args.sem_figuras)
    log.info("Tabelas em %s", res["dir_tabelas"])
    log.info("Figuras em %s", res["dir_figuras"])
    print(f"\nTop 15 pares por V de Cramér (corrigido):")
    print(res["pares"][["var_a", "var_b", "v_corrigido", "n"]].head(15).to_string(index=False))
    for alvo, rank in res["rankings"].items():
        print(f"\nTop 8 associações com {alvo}:")
        print(rank[["variavel", "v_corrigido", "possivel_vazamento_ou_derivada"]].head(8).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
