"""EDA (Fase 5): ranking por UF e região.

Decisões metodológicas (documentar em docs/decisoes_metodologicas.md):

* **Contagem absoluta mede volume, não risco.** SP lidera qualquer ranking só por ter mais trabalhadores.
  Por isso, além do ranking de volume, cada UF recebe a **taxa do alvo** (ex.: % de CATs com óbito) com
  intervalo de Wilson 95% e a razão contra a taxa nacional. Normalizar por população ocupada exigiria uma
  tabela externa (IBGE) que não está no projeto: fica como extensão (``data/external/``).
* **Qual UF?** CAT: ``uf_segurado`` (residência do segurado; decisão da Fase 4, porque ``uf_acidente`` tem
  29% de ausentes). BC: ``uf`` (residência do beneficiário). O artigo deve dizer que não é a UF do acidente.
* **UF não informada** (NAO_INFORMADO) permanece na tabela de volume e é marcada ``informada = False``;
  fica fora da comparação de taxas.
* **UFs pequenas:** a taxa só é exibida no gráfico se ``n >= min_n_taxa``; a tabela traz todas.
* Mapa coroplético não é gerado: exige malha do IBGE e geopandas. O ranking cobre o mesmo objetivo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, PercentFormatter

from .eda_temporal import intervalo_wilson, mascara_positiva
from .eda_univariada import COR_ACUM, COR_BARRA, COR_OUTRAS, ConfigEDA, _milhar, _nome_arquivo, _salvar

log = logging.getLogger(__name__)

UF_SIGLAS = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA", "CEARA": "CE",
    "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA", "MATO GROSSO": "MT",
    "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG", "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR",
    "PERNAMBUCO": "PE", "PIAUI": "PI", "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR", "SANTA CATARINA": "SC",
    "SAO PAULO": "SP", "SERGIPE": "SE", "TOCANTINS": "TO",
}
NAO_INFORMADAS = {"NAO_INFORMADO", "NAO INFORMADO", "ZERADO", "OUTROS", "DESCONHECIDA"}


@dataclass
class ParamsTerritorial:
    coluna_uf: str | None = None
    coluna_regiao: str | None = None
    taxas_alvo: list = field(default_factory=list)     # [{coluna, positiva}]
    min_n_taxa: int = 200

    @classmethod
    def from_dict(cls, d: dict | None) -> "ParamsTerritorial":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__ and v is not None})


def _rotulo(x: str) -> str:
    return UF_SIGLAS.get(str(x).strip().upper(), str(x))


def ranking_volume(s: pd.Series) -> pd.DataFrame:
    """Volume por categoria (UF ou região), com % do total e % acumulado entre as informadas."""
    vc = s.value_counts(dropna=False)
    vc = vc[vc > 0]
    out = pd.DataFrame({"categoria": [("(ausente)" if pd.isna(i) else str(i)) for i in vc.index],
                        "n": vc.to_numpy()})
    out["informada"] = ~out["categoria"].str.strip().str.upper().isin(NAO_INFORMADAS | {"(AUSENTE)"})
    out["pct_total"] = out["n"] / out["n"].sum()
    inf = out["informada"]
    out["pct_informadas"] = np.where(inf, out["n"] / out.loc[inf, "n"].sum(), np.nan)
    out = out.sort_values(["informada", "n"], ascending=[False, False], ignore_index=True)
    out["posicao"] = np.where(out["informada"], np.arange(1, len(out) + 1), np.nan)
    out["pct_acum_informadas"] = out["pct_informadas"].fillna(0).cumsum().where(out["informada"])
    out.insert(1, "sigla", out["categoria"].map(_rotulo))
    return out


def taxa_por_categoria(df: pd.DataFrame, col: str, col_alvo: str, positiva, min_n: int = 200) -> pd.DataFrame:
    """Taxa do alvo por categoria, com IC de Wilson e razão contra a taxa nacional (só categorias informadas)."""
    ok = df[col].notna() & df[col_alvo].notna()
    pos = mascara_positiva(df[col_alvo], positiva)
    g = (pd.DataFrame({"cat": df.loc[ok, col], "pos": pos[ok]}).groupby("cat", observed=True)["pos"]
         .agg(["size", "sum"]))
    out = pd.DataFrame({"categoria": g.index.astype(str), "n": g["size"].to_numpy(), "n_pos": g["sum"].to_numpy()})
    out["informada"] = ~out["categoria"].str.strip().str.upper().isin(NAO_INFORMADAS)
    out = out[out["informada"]].copy()
    total_pos, total_n = int(out["n_pos"].sum()), int(out["n"].sum())
    taxa_nac = total_pos / total_n if total_n else np.nan
    out["taxa"] = out["n_pos"] / out["n"].where(out["n"] > 0, np.nan)
    out["ic_inf"], out["ic_sup"] = intervalo_wilson(out["n_pos"], out["n"])
    out["taxa_nacional"] = taxa_nac
    out["razao_taxa_nacional"] = out["taxa"] / taxa_nac if taxa_nac else np.nan
    out["pct_dos_positivos"] = out["n_pos"] / total_pos if total_pos else np.nan
    out["n_suficiente"] = out["n"] >= min_n
    out.insert(1, "sigla", out["categoria"].map(_rotulo))
    return out.sort_values("taxa", ascending=False, ignore_index=True)


def plotar_ranking_volume(r: pd.DataFrame, titulo: str) -> Figure:
    t = r.iloc[::-1]
    fig = Figure(figsize=(7.5, 0.28 * len(t) + 1.8))
    ax = fig.subplots()
    cores = [COR_BARRA if i else COR_OUTRAS for i in t["informada"]]
    ax.barh(t["sigla"], t["n"], color=cores)
    for y, (n, p) in enumerate(zip(t["n"], t["pct_total"])):
        ax.text(n, y, f" {_milhar(n)} ({p:.1%})", va="center", fontsize=7)
    ax.set_xlim(0, float(t["n"].max()) * 1.25)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _milhar(v)))
    ax.set_xlabel("Registros")
    ax.set_title(f"{titulo}\n(cinza: não informada)", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plotar_taxa_categorias(t: pd.DataFrame, titulo: str, min_n: int = 200) -> Figure:
    d = t[t["n_suficiente"]].sort_values("taxa")
    fig = Figure(figsize=(7.5, 0.28 * max(len(d), 3) + 1.8))
    ax = fig.subplots()
    err = np.vstack([d["taxa"] - d["ic_inf"], d["ic_sup"] - d["taxa"]])
    ax.barh(d["sigla"], d["taxa"], xerr=err, color=COR_BARRA, ecolor=COR_ACUM, capsize=2)
    if len(d):
        ax.axvline(float(d["taxa_nacional"].iloc[0]), color=COR_ACUM, ls="--", lw=1, label="taxa nacional")
        ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=2 if (len(d) and d["taxa"].max() < 0.05) else 0))
    ax.set_title(f"{titulo}\n(IC 95% de Wilson; só categorias com n ≥ {min_n})", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def executar_territorial(df: pd.DataFrame, cfg: ConfigEDA, p: ParamsTerritorial, gerar_figuras: bool = True) -> dict:
    dir_t: Path = cfg.saida_tabelas.parent / "territorial"
    dir_f: Path = cfg.saida_figuras.parent / "territorial"
    dir_t.mkdir(parents=True, exist_ok=True)
    dir_f.mkdir(parents=True, exist_ok=True)
    csv = dict(index=False, encoding="utf-8-sig")

    volumes, taxas = {}, {}
    for col in [c for c in (p.coluna_uf, p.coluna_regiao) if c]:
        if col not in df.columns:
            log.warning("territorial: coluna '%s' não existe — ignorada.", col)
            continue
        nome = _nome_arquivo(col)
        r = ranking_volume(df[col])
        r.to_csv(dir_t / f"ranking_{nome}.csv", **csv)
        volumes[col] = r
        if gerar_figuras:
            _salvar(plotar_ranking_volume(r, f"Volume por {col}"), dir_f / f"ranking_{nome}", cfg.formatos, cfg.dpi)
        for alvo in p.taxas_alvo:
            if alvo["coluna"] not in df.columns:
                log.warning("territorial: alvo '%s' não existe — ignorado.", alvo["coluna"])
                continue
            t = taxa_por_categoria(df, col, alvo["coluna"], alvo["positiva"], p.min_n_taxa)
            rot = f"{nome}_{_nome_arquivo(alvo['coluna'])}_{_nome_arquivo(str(alvo['positiva']))}"
            t.to_csv(dir_t / f"taxa_{rot}.csv", **csv)
            taxas[(col, alvo["coluna"], str(alvo["positiva"]))] = t
            if gerar_figuras and len(t):
                _salvar(plotar_taxa_categorias(t, f"{alvo['coluna']} = {alvo['positiva']} por {col}", p.min_n_taxa),
                        dir_f / f"taxa_{rot}", cfg.formatos, cfg.dpi)
    return {"volumes": volumes, "taxas": taxas, "dir_tabelas": dir_t, "dir_figuras": dir_f}
