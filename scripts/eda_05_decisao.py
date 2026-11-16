#!/usr/bin/env python
"""Fase 5 (EDA), fechamento — desbalanceamento dos alvos e variáveis por técnica.

Lê só as tabelas que a EDA já gravou em reports/<base>/tables/eda/ (não precisa do parquet).

Uso:
    python scripts/eda_05_decisao.py --base cat
    python scripts/eda_05_decisao.py --base beneficios
    python scripts/eda_05_decisao.py --base cat --sem-figuras

Saídas (reports/<base>/tables/eda/decisao/):
    plano_desbalanceamento.csv      baselines, positivos por partição, métricas e estratégia por técnica
    heterogeneidade_taxas.csv       (CAT) estabilidade da prevalência por arquivo e território
    variaveis_por_tecnica.csv       status e motivo de cada variável por alvo × técnica
    matriz_variaveis_tecnica.csv    mesma informação em formato largo (tabela do artigo)
    resumo_decisao.csv              contagem por status
    suporte_itens.csv, cobertura_suporte.csv   (BC) itens e suporte mínimo para Apriori
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from inss_acidentes.common import eda_decisao as ed  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", choices=["cat", "beneficios"], required=True)
    ap.add_argument("--config", default=None, help="YAML (padrão: configs/<base>/decisao.yaml)")
    ap.add_argument("--sem-figuras", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("eda_decisao")

    cfg = ed.carregar_cfg(Path(args.config) if args.config else RAIZ / "configs" / args.base / "decisao.yaml")
    tab = RAIZ / cfg["tabelas_eda"]
    saida = RAIZ / cfg["saidas"]["tabelas"]
    fig_dir = RAIZ / cfg["saidas"]["figuras"]
    saida.mkdir(parents=True, exist_ok=True)

    def gravar(df, nome):
        df.to_csv(saida / nome, index=False, encoding="utf-8-sig")
        log.info("%s: %d linhas", saida / nome, len(df))

    f_alvos = tab / "informacao_mutua" / "desbalanceamento_alvos.csv"
    f_resumo = tab / "univariada" / "resumo_variaveis.csv"
    f_pares = tab / "bivariada" / "associacoes_pares.csv"
    f_mi = tab / "informacao_mutua" / "mi_todos_alvos.csv"
    faltam = [f for f in (f_alvos, f_resumo, f_pares) if not f.exists()]
    if faltam:
        log.error("Rode antes a EDA (make eda-%s). Faltam: %s", "cat" if args.base == "cat" else "bc",
                  ", ".join(str(f) for f in faltam))
        return 1
    resumo, pares = ed.ler_csv(f_resumo), ed.ler_csv(f_pares)

    # 1) desbalanceamento
    plano = ed.plano_desbalanceamento(ed.ler_csv(f_alvos), cfg)
    gravar(plano, "plano_desbalanceamento.csv")
    if not args.sem_figuras:
        fig_dir.mkdir(parents=True, exist_ok=True)
        fig = ed.plotar_desbalanceamento(plano)
        fig.savefig(fig_dir / "desbalanceamento_alvos.png", dpi=150)
        log.info("figura: %s", fig_dir / "desbalanceamento_alvos.png")

    # 2) estabilidade da prevalência
    if cfg.get("heterogeneidade"):
        gravar(ed.tabela_heterogeneidade(tab, cfg), "heterogeneidade_taxas.csv")

    # 3) variáveis por técnica
    if args.base == "cat":
        if not f_mi.exists():
            log.error("Falta %s (rode make eda-cat-mi).", f_mi)
            return 1
        dec = ed.decidir_variaveis(ed.ler_csv(f_mi), resumo, pares, cfg)
        gravar(ed.matriz_variaveis(dec), "matriz_variaveis_tecnica.csv")
    else:
        dec = ed.decidir_variaveis_mineracao(resumo, pares, cfg)
        itens = ed.suporte_itens(dec, tab / cfg["mineracao"]["frequencias"], "apriori")
        gravar(itens, "suporte_itens.csv")
        gravar(ed.cobertura_suporte(itens, cfg["mineracao"]["suportes_teste"], cfg["mineracao"]["limiar_dominancia"]),
               "cobertura_suporte.csv")
    gravar(dec, "variaveis_por_tecnica.csv")
    res = dec.groupby([c for c in ("alvo", "tecnica", "status") if c in dec.columns]).size().rename("n_variaveis").reset_index()
    gravar(res, "resumo_decisao.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
