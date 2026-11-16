"""EDA (Fase 5): séries temporais por mês e por ano.

Decisões metodológicas (documentar em docs/decisoes_metodologicas.md):

* **Eixo de tempo explícito.** Cada coluna de data em ``temporal.colunas_data`` gera sua própria série, porque
  medem coisas diferentes (CAT: ocorrência × registro; BC: competência × DIB). Nada é misturado.
* **Janela fixa** (``inicio``–``fim``, a do estudo). Datas fora da janela não somem em silêncio: entram em
  ``cobertura_temporal.csv`` (``n_antes_inicio`` / ``n_depois_fim``). Meses sem registro viram 0, não buracos.
* **Taxas com intervalo de Wilson 95%.** Para alvos raros (óbito ~0,5%) a taxa mensal é ruidosa; a banda deixa
  claro o que é oscilação amostral.
* **Taxa por arquivo de origem** (``controle_arquivo``): a auditoria do CAT mostrou que o % de CATs com
  benefício cai de 23% para 2,4% entre arquivos (provável data de extração). Se a taxa de um alvo mudar por
  arquivo mais do que no tempo, o que se vê é artefato de extração, não fenômeno.
* A suavização (média móvel centrada) é só visual; as tabelas trazem as contagens brutas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, PercentFormatter

from .eda_univariada import COR_ACUM, COR_BARRA, ConfigEDA, _milhar, _nome_arquivo, _salvar, _truncar

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Parâmetros
# --------------------------------------------------------------------------- #
@dataclass
class ParamsTemporal:
    inicio: str = "2012-01"
    fim: str = "2018-12"
    colunas_data: list = field(default_factory=list)          # séries de contagem
    eixo_categorias: str | None = None                        # coluna de data usada nas séries por categoria
    series_categoria: list = field(default_factory=list)      # colunas categóricas
    top_n_categorias: int = 6
    eixo_taxas: str | None = None                             # coluna de data usada nas taxas
    taxas_alvo: list = field(default_factory=list)            # [{coluna, positiva}]
    controle_arquivo: str | None = "arquivo_origem"
    suavizacao_meses: int = 12

    @classmethod
    def from_dict(cls, d: dict | None) -> "ParamsTemporal":
        d = d or {}
        p = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__ and v is not None})
        p.eixo_categorias = p.eixo_categorias or (p.colunas_data[0] if p.colunas_data else None)
        p.eixo_taxas = p.eixo_taxas or (p.colunas_data[0] if p.colunas_data else None)
        return p


# --------------------------------------------------------------------------- #
# Estatística
# --------------------------------------------------------------------------- #
def intervalo_wilson(k, n, z: float = 1.96) -> tuple[np.ndarray, np.ndarray]:
    """Intervalo de Wilson para proporções (funciona com k = 0 e com n pequeno)."""
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = k / n
        den = 1 + z**2 / n
        centro = (p + z**2 / (2 * n)) / den
        marg = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
        lo, hi = np.clip(centro - marg, 0, 1), np.clip(centro + marg, 0, 1)
    lo, hi = np.where(n > 0, lo, np.nan), np.where(n > 0, hi, np.nan)
    return lo, hi


def mascara_positiva(s: pd.Series, positiva) -> pd.Series:
    """True onde a série vale ``positiva`` (aceita categoria, texto, bool e boolean com NA)."""
    alvo = str(positiva).strip().upper()
    if isinstance(s.dtype, pd.CategoricalDtype):
        cats = pd.Index(s.cat.categories.astype(str)).str.strip().str.upper()
        ids = np.flatnonzero(np.asarray(cats == alvo))
        return pd.Series(np.isin(s.cat.codes.to_numpy(), ids), index=s.index)
    return s.astype("string").str.strip().str.upper().eq(alvo).fillna(False).astype(bool)


def serie_mensal(s: pd.Series, inicio: str, fim: str) -> tuple[pd.Series, dict]:
    """Contagem por mês (índice PeriodIndex completo, zeros preenchidos) + cobertura da coluna."""
    idx = pd.period_range(inicio, fim, freq="M")
    per = s.dropna().dt.to_period("M")
    info = {"n_total": len(s), "n_validos": len(per), "n_nulos": len(s) - len(per),
            "data_min": s.min(), "data_max": s.max(),
            "n_antes_inicio": int((per < idx[0]).sum()), "n_depois_fim": int((per > idx[-1]).sum())}
    cont = per.value_counts().reindex(idx, fill_value=0).sort_index().astype("int64")
    cont.index.name = "periodo"
    info["n_na_janela"] = int(cont.sum())
    info["meses_sem_registro"] = int((cont == 0).sum())
    return cont, info


def serie_anual(mensal: pd.Series) -> pd.DataFrame:
    a = mensal.groupby(mensal.index.year).sum().rename("n").to_frame()
    a.index.name = "ano"
    a["meses_com_dado"] = mensal.groupby(mensal.index.year).apply(lambda x: int((x > 0).sum()))
    a["var_pct_ano_anterior"] = a["n"].pct_change() * 100
    return a.reset_index()


def sazonalidade(mensal: pd.Series) -> pd.DataFrame:
    """Média por mês do calendário e índice sazonal (média do mês / média geral)."""
    m = mensal.groupby(mensal.index.month).mean().rename("media_mensal").to_frame()
    m.index.name = "mes"
    m["indice_sazonal"] = m["media_mensal"] / mensal.mean() if mensal.mean() else np.nan
    return m.reset_index()


def _periodos(df: pd.DataFrame, col_data: str) -> pd.Series:
    return df[col_data].dt.to_period("M")


def taxa_mensal(df: pd.DataFrame, col_data: str, col_alvo: str, positiva, inicio: str, fim: str) -> pd.DataFrame:
    idx = pd.period_range(inicio, fim, freq="M")
    per = _periodos(df, col_data)
    ok = per.notna() & df[col_alvo].notna()
    g = (pd.DataFrame({"per": per[ok], "pos": mascara_positiva(df[col_alvo], positiva)[ok]})
         .groupby("per", observed=True)["pos"].agg(["size", "sum"]))
    g = g.reindex(idx, fill_value=0)
    out = pd.DataFrame({"periodo": idx.astype(str), "n": g["size"].to_numpy(), "n_pos": g["sum"].to_numpy()})
    out["taxa"] = np.where(out["n"] > 0, out["n_pos"] / out["n"].where(out["n"] > 0, 1), np.nan)
    out["ic_inf"], out["ic_sup"] = intervalo_wilson(out["n_pos"], out["n"])
    return out


def taxa_por_grupo(df: pd.DataFrame, col_grupo: str, col_alvo: str, positiva) -> pd.DataFrame:
    ok = df[col_grupo].notna() & df[col_alvo].notna()
    g = (pd.DataFrame({"grupo": df.loc[ok, col_grupo], "pos": mascara_positiva(df[col_alvo], positiva)[ok]})
         .groupby("grupo", observed=True)["pos"].agg(["size", "sum"]))
    out = pd.DataFrame({"grupo": g.index.astype(str), "n": g["size"].to_numpy(), "n_pos": g["sum"].to_numpy()})
    out["taxa"] = out["n_pos"] / out["n"].where(out["n"] > 0, np.nan)
    out["ic_inf"], out["ic_sup"] = intervalo_wilson(out["n_pos"], out["n"])
    return out.sort_values("grupo", ignore_index=True)


def serie_categoria(df: pd.DataFrame, col_data: str, col_cat: str, inicio: str, fim: str, top_n: int) -> pd.DataFrame:
    """Contagem mensal por categoria (``top_n`` maiores + 'Outras')."""
    idx = pd.period_range(inicio, fim, freq="M")
    per = _periodos(df, col_data)
    ok = per.notna() & df[col_cat].notna()
    s = df.loc[ok, col_cat]
    if not isinstance(s.dtype, pd.CategoricalDtype):
        s = s.astype("category")
    s = s.cat.remove_unused_categories()
    top = s.value_counts().head(top_n).index.tolist()
    if len(s.cat.categories) > len(top):
        s = s.cat.add_categories(["Outras"])
        s = s.where(s.isin(top), "Outras")
    t = (pd.DataFrame({"per": per[ok], "cat": s}).groupby(["per", "cat"], observed=True).size()
         .unstack(fill_value=0).reindex(idx, fill_value=0))
    t = t.loc[:, [c for c in t.columns if t[c].sum() > 0]]
    t.columns = [str(c) for c in t.columns]
    t.index = t.index.astype(str)
    t.index.name = "periodo"
    return t


# --------------------------------------------------------------------------- #
# Gráficos
# --------------------------------------------------------------------------- #
def _x(idx) -> pd.DatetimeIndex:
    return pd.PeriodIndex(idx, freq="M").to_timestamp()


def plotar_serie_mensal(mensal: pd.Series, titulo: str, janela: int = 12) -> Figure:
    fig = Figure(figsize=(9.5, 4.4))
    ax = fig.subplots()
    x = _x(mensal.index)
    ax.plot(x, mensal.to_numpy(), color=COR_BARRA, lw=1, alpha=0.55, label="mensal")
    ax.plot(x, mensal.rolling(janela, center=True, min_periods=max(1, janela // 2)).mean().to_numpy(),
            color=COR_ACUM, lw=2, label=f"média móvel {janela} meses")
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _milhar(v)))
    ax.set_ylabel("Registros por mês")
    ax.set_title(f"{titulo}\nn na janela = {_milhar(float(mensal.sum()))}", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plotar_serie_anual(anual: pd.DataFrame, titulo: str) -> Figure:
    fig = Figure(figsize=(6.5, 4.2))
    ax = fig.subplots()
    ax.bar(anual["ano"].astype(str), anual["n"], color=COR_BARRA)
    for i, (n, v) in enumerate(zip(anual["n"], anual["var_pct_ano_anterior"])):
        txt = _milhar(n) + ("" if np.isnan(v) else f"\n({v:+.0f}%)")
        ax.text(i, n, txt, ha="center", va="bottom", fontsize=7)
    ax.set_ylim(0, float(anual["n"].max()) * 1.18)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _milhar(v)))
    ax.set_ylabel("Registros por ano")
    ax.set_title(titulo, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plotar_sazonalidade(saz: pd.DataFrame, titulo: str) -> Figure:
    fig = Figure(figsize=(6.5, 4.0))
    ax = fig.subplots()
    ax.bar(saz["mes"].astype(str), saz["indice_sazonal"], color=COR_BARRA)
    ax.axhline(1.0, color=COR_ACUM, ls="--", lw=1)
    ax.set_xlabel("Mês do calendário")
    ax.set_ylabel("Índice sazonal (1 = média)")
    ax.set_title(titulo, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plotar_categorias(t: pd.DataFrame, titulo: str) -> Figure:
    fig = Figure(figsize=(9.5, 4.8))
    ax = fig.subplots()
    ax.stackplot(_x(t.index), t.to_numpy().T, labels=[_truncar(c, 30) for c in t.columns], alpha=0.9)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _milhar(v)))
    ax.set_ylabel("Registros por mês")
    ax.set_title(titulo, fontsize=10)
    ax.legend(frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plotar_taxa_mensal(t: pd.DataFrame, titulo: str, janela: int = 12) -> Figure:
    fig = Figure(figsize=(9.5, 4.2))
    ax = fig.subplots()
    x = _x(pd.PeriodIndex(t["periodo"], freq="M"))
    ax.fill_between(x, t["ic_inf"], t["ic_sup"], color=COR_BARRA, alpha=0.2, label="IC 95% (Wilson)")
    ax.plot(x, t["taxa"], color=COR_BARRA, lw=1.2, label="taxa mensal")
    ax.plot(x, t["taxa"].rolling(janela, center=True, min_periods=max(1, janela // 2)).mean(),
            color=COR_ACUM, lw=2, label=f"média móvel {janela} meses")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=2 if t["taxa"].max() < 0.05 else 0))
    ax.set_ylim(bottom=0)
    ax.set_title(titulo, fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plotar_taxa_grupos(t: pd.DataFrame, titulo: str) -> Figure:
    fig = Figure(figsize=(max(6.5, 0.55 * len(t) + 2), 4.6))
    ax = fig.subplots()
    x = np.arange(len(t))
    err = np.vstack([t["taxa"] - t["ic_inf"], t["ic_sup"] - t["taxa"]])
    ax.bar(x, t["taxa"], color=COR_BARRA, yerr=err, ecolor=COR_ACUM, capsize=2)
    ax.set_xticks(x, [_truncar(g, 24) for g in t["grupo"]], rotation=60, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=2 if t["taxa"].max() < 0.05 else 0))
    ax.set_title(titulo, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def _rot(alvo: dict) -> str:
    return f"{_nome_arquivo(alvo['coluna'])}_{_nome_arquivo(str(alvo['positiva']))}"


def executar_temporal(df: pd.DataFrame, cfg: ConfigEDA, p: ParamsTemporal, gerar_figuras: bool = True) -> dict:
    dir_t: Path = cfg.saida_tabelas.parent / "temporal"
    dir_f: Path = cfg.saida_figuras.parent / "temporal"
    dir_t.mkdir(parents=True, exist_ok=True)
    dir_f.mkdir(parents=True, exist_ok=True)
    csv = dict(index=False, encoding="utf-8-sig")
    fmt, dpi = cfg.formatos, cfg.dpi

    def existe(c: str | None, tipo: str) -> bool:
        if c is None or c not in df.columns:
            log.warning("temporal: coluna '%s' (%s) não existe — ignorada.", c, tipo)
            return False
        return True

    cobertura, mensais = [], {}
    for col in p.colunas_data:
        if not existe(col, "data"):
            continue
        if not pd.api.types.is_datetime64_any_dtype(df[col]):
            log.warning("temporal: '%s' não é datetime (veja padroes_data/padroes_periodo no YAML) — ignorada.", col)
            continue
        mensal, info = serie_mensal(df[col], p.inicio, p.fim)
        mensais[col] = mensal
        cobertura.append({"coluna": col, **info})
        anual, saz = serie_anual(mensal), sazonalidade(mensal)
        n = _nome_arquivo(col)
        mensal.rename("n").reset_index().assign(periodo=lambda d: d["periodo"].astype(str)).to_csv(
            dir_t / f"serie_mensal_{n}.csv", **csv)
        anual.to_csv(dir_t / f"serie_anual_{n}.csv", **csv)
        saz.to_csv(dir_t / f"sazonalidade_{n}.csv", **csv)
        if gerar_figuras:
            _salvar(plotar_serie_mensal(mensal, f"Registros por mês — {col}", p.suavizacao_meses),
                    dir_f / f"serie_mensal_{n}", fmt, dpi)
            _salvar(plotar_serie_anual(anual, f"Registros por ano — {col}"), dir_f / f"serie_anual_{n}", fmt, dpi)
            _salvar(plotar_sazonalidade(saz, f"Sazonalidade — {col}"), dir_f / f"sazonalidade_{n}", fmt, dpi)
        if info["n_antes_inicio"] or info["n_depois_fim"]:
            log.warning("  %s: %s linhas antes de %s e %s depois de %s (fora da janela).", col,
                        _milhar(info["n_antes_inicio"]), p.inicio, _milhar(info["n_depois_fim"]), p.fim)
    t_cob = pd.DataFrame(cobertura)
    t_cob.to_csv(dir_t / "cobertura_temporal.csv", **csv)

    categorias = {}
    if p.series_categoria and existe(p.eixo_categorias, "eixo das categorias"):
        for col in p.series_categoria:
            if not existe(col, "categoria"):
                continue
            t = serie_categoria(df, p.eixo_categorias, col, p.inicio, p.fim, p.top_n_categorias)
            categorias[col] = t
            n = f"{_nome_arquivo(col)}_por_{_nome_arquivo(p.eixo_categorias)}"
            t.reset_index().to_csv(dir_t / f"serie_categoria_{n}.csv", **csv)
            if gerar_figuras:
                _salvar(plotar_categorias(t, f"{col} por mês ({p.eixo_categorias})"),
                        dir_f / f"serie_categoria_{n}", fmt, dpi)

    taxas = {}
    if p.taxas_alvo and existe(p.eixo_taxas, "eixo das taxas"):
        for alvo in p.taxas_alvo:
            if not existe(alvo["coluna"], "alvo"):
                continue
            rot = _rot(alvo)
            titulo = f"{alvo['coluna']} = {alvo['positiva']}"
            t = taxa_mensal(df, p.eixo_taxas, alvo["coluna"], alvo["positiva"], p.inicio, p.fim)
            t.to_csv(dir_t / f"taxa_mensal_{rot}.csv", **csv)
            taxas[rot] = {"mensal": t}
            if gerar_figuras:
                _salvar(plotar_taxa_mensal(t, f"Taxa mensal de {titulo} (por {p.eixo_taxas})", p.suavizacao_meses),
                        dir_f / f"taxa_mensal_{rot}", fmt, dpi)
            if p.controle_arquivo and p.controle_arquivo in df.columns:
                g = taxa_por_grupo(df, p.controle_arquivo, alvo["coluna"], alvo["positiva"])
                g.to_csv(dir_t / f"taxa_por_arquivo_{rot}.csv", **csv)
                taxas[rot]["arquivo"] = g
                if gerar_figuras:
                    _salvar(plotar_taxa_grupos(g, f"Taxa de {titulo} por {p.controle_arquivo}"),
                            dir_f / f"taxa_por_arquivo_{rot}", fmt, dpi)
    return {"cobertura": t_cob, "mensais": mensais, "categorias": categorias, "taxas": taxas,
            "dir_tabelas": dir_t, "dir_figuras": dir_f}
