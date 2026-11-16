"""EDA univariada (Fase 5) — funções genéricas, reutilizadas por CAT e Benefícios.

Entrega:
  * frequências por variável categórica (tabela CSV);
  * Pareto das categóricas (barras + acumulado + linha de corte);
  * histogramas e estatísticas das numéricas;
  * resumo por variável com flags que ajudam a decidir quem entra em cada técnica.

Nenhuma função depende do nome de uma coluna: tudo vem do YAML (``configs/<base>/eda.yaml``)
ou da detecção automática de tipos.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, PercentFormatter

log = logging.getLogger(__name__)

COR_BARRA = "#1f4e79"
COR_OUTRAS = "#9e9e9e"
COR_ACUM = "#c0392b"


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
@dataclass
class ConfigEDA:
    base: str
    entrada: Path
    saida_figuras: Path
    saida_tabelas: Path
    categoricas: list[str] = field(default_factory=list)
    numericas: list[str] = field(default_factory=list)
    datas: list[str] = field(default_factory=list)
    excluir: list[str] = field(default_factory=list)
    candidatos_alvo: list[str] = field(default_factory=list)   # não recebem aviso de 'quase constante'
    padroes_excluir: list[str] = field(default_factory=lambda: [r"^id($|_)"])
    padroes_data: list[str] = field(default_factory=lambda: [r"^data_"])            # colunas convertidas p/ datetime
    padroes_periodo: list[str] = field(default_factory=lambda: [r"^competencia_"])  # inteiros AAAAMM -> datetime
    limiar_cat_numerica: int = 15      # numérica com <= N valores distintos vira categórica
    top_n_pareto: int = 20
    limiar_pareto: float = 0.80
    alta_cardinalidade: int = 50
    limiar_dominancia: float = 0.95
    limiar_nulos: float = 0.30
    max_linhas_csv: int = 1000
    recortar_percentil: float | None = 99.5   # recorte visual do histograma (None = sem recorte)
    formatos: list[str] = field(default_factory=lambda: ["png"])
    dpi: int = 150
    bivariada: dict = field(default_factory=dict)   # seção `bivariada:` do YAML (ver eda_bivariada.py)

    @classmethod
    def from_yaml(cls, caminho: str | Path, raiz: str | Path = ".") -> "ConfigEDA":
        raiz = Path(raiz)
        d = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
        col = d.get("colunas") or {}
        uni = d.get("univariada") or {}
        gra = d.get("graficos") or {}
        padrao = cls.__dataclass_fields__
        kw = dict(
            base=d["base"],
            entrada=raiz / d["entrada"],
            saida_figuras=raiz / d["saidas"]["figuras"],
            saida_tabelas=raiz / d["saidas"]["tabelas"],
            categoricas=col.get("categoricas") or [],
            numericas=col.get("numericas") or [],
            datas=col.get("datas") or [],
            excluir=col.get("excluir") or [],
            candidatos_alvo=col.get("candidatos_alvo") or [],
        )
        if col.get("padroes_excluir") is not None:
            kw["padroes_excluir"] = col["padroes_excluir"]
        for k in ("padroes_data", "padroes_periodo"):
            if col.get(k) is not None:
                kw[k] = col[k]
        kw["bivariada"] = d.get("bivariada") or {}
        for k in ("limiar_cat_numerica", "top_n_pareto", "limiar_pareto", "alta_cardinalidade",
                  "limiar_dominancia", "limiar_nulos", "max_linhas_csv", "recortar_percentil"):
            if k in uni:
                kw[k] = uni[k]
        for k in ("formatos", "dpi"):
            if k in gra:
                kw[k] = gra[k]
        assert all(k in padrao for k in kw)
        return cls(**kw)


# --------------------------------------------------------------------------- #
# Preparação e classificação de colunas
# --------------------------------------------------------------------------- #
_FORMATOS_DATA = ["ISO8601", "%d/%m/%Y", "%Y%m%d", "%d%m%Y", "%d/%m/%y"]


def _para_datetime(serie: pd.Series, periodo: bool = False) -> tuple[pd.Series, float]:
    """Converte texto/categoria/inteiro em datetime64, parseando só os valores distintos.

    Testa vários formatos e fica com o que converte mais valores. ``periodo=True`` trata
    inteiros AAAAMM (ex.: 201801 -> 2018-01-01). Retorna (série, fração de linhas convertidas).
    """
    cat = pd.Categorical(serie)
    cats = pd.Series(cat.categories.astype(str)).str.strip()
    formatos = ["%Y%m"] if periodo else _FORMATOS_DATA
    if periodo:
        cats = cats.str.replace(r"\.0$", "", regex=True)
    melhor, melhor_ok = None, -1
    for fmt in formatos:
        try:
            p = pd.to_datetime(cats, format=fmt, errors="coerce")
        except (ValueError, TypeError):
            continue
        ok = int(p.notna().sum())
        if ok > melhor_ok:
            melhor, melhor_ok = p, ok
        if ok == len(cats):
            break
    if melhor is None:
        return serie, 0.0
    codigos = cat.codes
    out = pd.DatetimeIndex(melhor).take(codigos, allow_fill=True, fill_value=pd.NaT)
    validas = int((codigos >= 0).sum())
    frac = int(out.notna().sum()) / validas if validas else 1.0
    return pd.Series(out, index=serie.index, name=serie.name), frac


def preparar_dataframe(df: pd.DataFrame, cfg: "ConfigEDA | None" = None) -> pd.DataFrame:
    """Tipa o dataframe para a EDA.

    * com ``cfg``: converte para datetime as colunas listadas em ``datas`` ou que casam com
      ``padroes_data`` e os períodos AAAAMM (``padroes_periodo``);
    * converte texto restante em ``category`` (menos memória, value_counts mais rápido).
    """
    df = df.copy()
    if cfg is not None:
        excl = set(cfg.excluir)
        for c in df.columns:
            if c in excl or pd.api.types.is_datetime64_any_dtype(df[c]):
                continue
            e_periodo = c not in cfg.datas and any(re.search(p, c, re.IGNORECASE) for p in cfg.padroes_periodo)
            e_data = c in cfg.datas or any(re.search(p, c, re.IGNORECASE) for p in cfg.padroes_data)
            if not (e_periodo or e_data):
                continue
            novo, frac = _para_datetime(df[c], periodo=e_periodo)
            if frac < 0.99:
                log.warning("Coluna '%s': só %.1f%% dos valores foram convertidos para data.", c, 100 * frac)
            else:
                log.info("Coluna '%s' convertida para datetime (%.2f%% das linhas válidas).", c, 100 * frac)
            df[c] = novo
    for c in df.columns:
        s = df[c]
        if not (pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s)
                or pd.api.types.is_datetime64_any_dtype(s) or isinstance(s.dtype, pd.CategoricalDtype)):
            df[c] = s.astype("category")
    return df


def classificar_colunas(df: pd.DataFrame, cfg: ConfigEDA) -> dict[str, list[str]]:
    """Classifica cada coluna em categorica / numerica / data / excluida.

    Auto-detecção primeiro; listas explícitas do YAML sobrescrevem a detecção.
    """
    excl_exato = set(cfg.excluir)
    padroes = [re.compile(p, re.IGNORECASE) for p in cfg.padroes_excluir]
    tipos: dict[str, list[str]] = {"categoricas": [], "numericas": [], "datas": [], "excluidas": []}

    for c in df.columns:
        s = df[c]
        if c in excl_exato or any(p.search(c) for p in padroes):
            tipos["excluidas"].append(c)
        elif pd.api.types.is_datetime64_any_dtype(s):
            tipos["datas"].append(c)
        elif pd.api.types.is_bool_dtype(s):
            tipos["categoricas"].append(c)
        elif pd.api.types.is_numeric_dtype(s):
            k = "categoricas" if s.nunique(dropna=True) <= cfg.limiar_cat_numerica else "numericas"
            tipos[k].append(c)
        else:
            tipos["categoricas"].append(c)

    for chave in ("categoricas", "numericas", "datas"):
        for c in getattr(cfg, chave):
            if c not in df.columns:
                log.warning("Coluna '%s' (YAML: %s) não existe no dataset — ignorada.", c, chave)
                continue
            if c in excl_exato:
                continue
            for lista in tipos.values():
                if c in lista:
                    lista.remove(c)
            tipos[chave].append(c)

    for k, lista in tipos.items():
        presentes = set(lista)
        tipos[k] = [c for c in df.columns if c in presentes]
    return tipos


# --------------------------------------------------------------------------- #
# Categóricas: frequências e Pareto
# --------------------------------------------------------------------------- #
def frequencias(serie: pd.Series) -> pd.DataFrame:
    """Tabela de frequências sobre os valores **válidos** (ausentes ficam no resumo)."""
    vc = serie.value_counts(dropna=True, sort=True)
    vc = vc[vc > 0]
    total = vc.sum()
    if total == 0:
        return pd.DataFrame(columns=["rank", "categoria", "n", "pct", "pct_acum"])
    out = pd.DataFrame({"categoria": vc.index.astype(str), "n": vc.to_numpy()})
    out["pct"] = out["n"] / total
    out["pct_acum"] = out["pct"].cumsum()
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    return out


def n_categorias_para_limiar(freq: pd.DataFrame, limiar: float) -> int:
    if freq.empty:
        return 0
    return int((freq["pct_acum"] < limiar - 1e-12).sum()) + 1


def _truncar(txt: str, n: int = 28) -> str:
    return txt if len(txt) <= n else txt[: n - 1] + "…"


def _milhar(x: float, _pos=None) -> str:
    return f"{x:,.0f}".replace(",", ".")


def plotar_pareto(freq: pd.DataFrame, titulo: str, top_n: int = 20, limiar: float = 0.80,
                  pct_nulos: float = 0.0) -> Figure | None:
    """Pareto: barras (% de cada categoria) + curva acumulada + linha de corte.

    As categorias além de ``top_n`` não são desenhadas; um quadro informa quantas são e quanto somam
    (agregá-las em uma barra distorceria a escala das categorias individuais).
    """
    if freq.empty:
        return None
    topo, resto = freq.head(top_n), freq.iloc[top_n:]
    rotulos = [_truncar(c) for c in topo["categoria"]]
    pcts = topo["pct"].tolist()
    acum = topo["pct_acum"].tolist()
    cores = [COR_BARRA] * len(topo)

    x = np.arange(len(rotulos))
    fig = Figure(figsize=(min(11.0, max(6.5, 0.4 * len(rotulos) + 2.5)), 5.2))
    ax = fig.subplots()
    ax.bar(x, pcts, color=cores, width=0.8)
    ax.set_ylim(0, max(pcts) * 1.18)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("% das ocorrências")
    ax.set_xticks(x)
    ax.set_xticklabels(rotulos, rotation=45 if len(rotulos) > 4 else 0,
                       ha="right" if len(rotulos) > 4 else "center", fontsize=8)
    if len(rotulos) <= 12:
        for xi, p in zip(x, pcts):
            ax.text(xi, p, f"{p:.1%}", ha="center", va="bottom", fontsize=7)

    ax2 = ax.twinx()
    ax2.plot(x, acum, color=COR_ACUM, marker="o", ms=3, lw=1.5)
    ax2.axhline(limiar, color=COR_ACUM, ls="--", lw=1, alpha=0.5)
    ax2.set_ylim(0, 1.05)
    ax2.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax2.set_ylabel("% acumulado", color=COR_ACUM)

    if len(resto):
        ax.text(0.99, 0.62, f"+ {len(resto)} categorias menores\n({resto['pct'].sum():.1%} do total)",
                transform=ax.transAxes, ha="right", va="top", fontsize=8, color="#555555",
                bbox=dict(boxstyle="round", fc="white", ec="#cccccc"))
    k = n_categorias_para_limiar(freq, limiar)
    ax.set_title(f"{titulo}\n{len(freq)} categorias · {k} cobrem {limiar:.0%} · "
                 f"ausentes: {pct_nulos:.1%}", fontsize=10)
    ax.spines[["top"]].set_visible(False)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- #
# Numéricas: estatísticas e histograma
# --------------------------------------------------------------------------- #
def estatisticas_numericas(serie: pd.Series) -> dict:
    x = pd.to_numeric(serie, errors="coerce")
    v = x.dropna()
    n, nulos = len(x), int(x.isna().sum())
    base = {"n": n, "nulos": nulos, "pct_nulos": nulos / n if n else np.nan}
    if v.empty:
        return base
    q = v.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    iqr = q[0.75] - q[0.25]
    fora = int(((v < q[0.25] - 1.5 * iqr) | (v > q[0.75] + 1.5 * iqr)).sum())
    return {
        **base,
        "media": v.mean(), "dp": v.std(), "min": v.min(),
        "p01": q[0.01], "p05": q[0.05], "q1": q[0.25], "mediana": q[0.5], "q3": q[0.75],
        "p95": q[0.95], "p99": q[0.99], "max": v.max(),
        "assimetria": v.skew(), "curtose": v.kurt(),
        "n_zeros": int((v == 0).sum()), "n_negativos": int((v < 0).sum()),
        "n_outliers_iqr": fora, "pct_outliers_iqr": fora / len(v),
    }


def plotar_histograma(serie: pd.Series, titulo: str, recortar_percentil: float | None = 99.5) -> Figure | None:
    """Histograma com média/mediana. Inteiros em faixa curta usam 1 barra por valor."""
    v = pd.to_numeric(serie, errors="coerce").dropna()
    if v.empty:
        return None
    n_total = len(v)
    media, mediana = float(v.mean()), float(v.median())   # sempre sobre os dados completos
    nota = ""
    if recortar_percentil is not None:
        corte = v.quantile(recortar_percentil / 100)
        acima = int((v > corte).sum())
        if acima:
            v = v[v <= corte]
            nota = f" · exibido até P{recortar_percentil:g} ({acima:,} valores acima)".replace(",", ".")
    inteiro = bool(np.allclose(v, np.round(v)))
    if inteiro and (v.max() - v.min()) <= 120:
        bins = np.arange(v.min() - 0.5, v.max() + 1.5, 1)
    else:
        bins = 50

    fig = Figure(figsize=(7.5, 4.6))
    ax = fig.subplots()
    ax.hist(v, bins=bins, color=COR_BARRA, edgecolor="white", linewidth=0.3)
    ax.axvline(media, color=COR_ACUM, ls="--", lw=1.2, label=f"média = {media:.1f}")
    ax.axvline(mediana, color="#27ae60", ls="-", lw=1.2, label=f"mediana = {mediana:.1f}")
    ax.yaxis.set_major_formatter(FuncFormatter(_milhar))
    ax.set_ylabel("Frequência")
    ax.set_title(f"{titulo}\nn = {_milhar(n_total)}{nota}", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- #
# Resumo por variável (apoio à seleção de variáveis)
# --------------------------------------------------------------------------- #
def _nome_arquivo(col: str) -> str:
    return re.sub(r"[^\w\-]+", "_", col, flags=re.UNICODE).strip("_")


def _salvar(fig: Figure, destino: Path, formatos: list[str], dpi: int) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    for ext in formatos:
        fig.savefig(destino.with_suffix(f".{ext}"), dpi=dpi, bbox_inches="tight")


def _observacao(flags: dict, alvo: bool = False, pct_moda: float | None = None) -> str:
    msgs = []
    if alvo:
        msgs.append(f"candidato a alvo: classe majoritária {pct_moda:.2%} (ver desbalanceamento)")
    if flags.get("alto_nulo"):
        msgs.append("muitos ausentes")
    if flags.get("quase_constante") and not alvo:
        msgs.append("quase constante: pouco informativa")
    if flags.get("alta_cardinalidade"):
        msgs.append("alta cardinalidade: agrupar por hierarquia antes de usar")
    return "; ".join(msgs)


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def executar_univariada(df: pd.DataFrame, cfg: ConfigEDA, gerar_figuras: bool = True) -> dict:
    """Roda toda a análise univariada e grava tabelas e figuras."""
    tipos = classificar_colunas(df, cfg)
    n = len(df)
    dir_freq = cfg.saida_tabelas / "frequencias"
    dir_freq.mkdir(parents=True, exist_ok=True)
    cfg.saida_figuras.mkdir(parents=True, exist_ok=True)

    resumo, cobertura, estat = [], [], []

    for c in tipos["categoricas"]:
        s = df[c]
        freq = frequencias(s)
        nulos = int(s.isna().sum())
        pct_nulos = nulos / n if n else np.nan
        card = len(freq)
        pct_moda = float(freq["pct"].iloc[0]) if card else np.nan
        flags = {
            "alto_nulo": pct_nulos >= cfg.limiar_nulos,
            "quase_constante": card > 0 and pct_moda >= cfg.limiar_dominancia,
            "alta_cardinalidade": card > cfg.alta_cardinalidade,
        }
        resumo.append({
            "variavel": c, "tipo_analise": "categorica", "dtype": str(s.dtype), "n": n,
            "nulos": nulos, "pct_nulos": pct_nulos, "cardinalidade": card,
            "moda": freq["categoria"].iloc[0] if card else None, "pct_moda": pct_moda,
            **flags, "candidato_alvo": c in cfg.candidatos_alvo,
            "observacao": _observacao(flags, c in cfg.candidatos_alvo, pct_moda),
        })
        cobertura.append({
            "variavel": c, "n_categorias": card,
            "pct_top1": freq["pct"].head(1).sum(), "pct_top3": freq["pct"].head(3).sum(),
            "pct_top5": freq["pct"].head(5).sum(), "pct_top10": freq["pct"].head(10).sum(),
            f"n_cat_para_{cfg.limiar_pareto:.0%}": n_categorias_para_limiar(freq, cfg.limiar_pareto),
        })
        freq.head(cfg.max_linhas_csv).to_csv(dir_freq / f"{_nome_arquivo(c)}.csv",
                                             index=False, encoding="utf-8-sig")
        if gerar_figuras:
            fig = plotar_pareto(freq, f"Pareto — {c}", cfg.top_n_pareto, cfg.limiar_pareto, pct_nulos)
            if fig is not None:
                _salvar(fig, cfg.saida_figuras / f"pareto_{_nome_arquivo(c)}", cfg.formatos, cfg.dpi)
        log.info("categórica %-35s card=%d nulos=%.1f%%", c, card, 100 * pct_nulos)

    for c in tipos["numericas"]:
        e = estatisticas_numericas(df[c])
        estat.append({"variavel": c, **e})
        flags = {"alto_nulo": e["pct_nulos"] >= cfg.limiar_nulos, "quase_constante": False,
                 "alta_cardinalidade": False}
        resumo.append({
            "variavel": c, "tipo_analise": "numerica", "dtype": str(df[c].dtype), "n": n,
            "nulos": e["nulos"], "pct_nulos": e["pct_nulos"], **flags,
            "observacao": _observacao(flags),
        })
        if gerar_figuras:
            fig = plotar_histograma(df[c], f"Distribuição — {c}", cfg.recortar_percentil)
            if fig is not None:
                _salvar(fig, cfg.saida_figuras / f"hist_{_nome_arquivo(c)}", cfg.formatos, cfg.dpi)
        log.info("numérica   %-35s média=%s", c, f"{e.get('media', float('nan')):.2f}")

    for c in tipos["datas"]:
        s = df[c]
        nulos = int(s.isna().sum())
        obs = f"{s.min():%Y-%m-%d} → {s.max():%Y-%m-%d} (análise na etapa temporal)" if nulos < n else ""
        resumo.append({"variavel": c, "tipo_analise": "data", "dtype": str(s.dtype), "n": n,
                       "nulos": nulos, "pct_nulos": nulos / n if n else np.nan, "observacao": obs})

    for c in tipos["excluidas"]:
        resumo.append({"variavel": c, "tipo_analise": "excluida", "dtype": str(df[c].dtype), "n": n,
                       "nulos": int(df[c].isna().sum()), "pct_nulos": df[c].isna().mean(),
                       "observacao": "excluída da EDA (identificador / config)"})

    t_resumo = pd.DataFrame(resumo)
    t_cob = pd.DataFrame(cobertura).round(6)
    t_est = pd.DataFrame(estat)
    t_resumo.to_csv(cfg.saida_tabelas / "resumo_variaveis.csv", index=False, encoding="utf-8-sig")
    t_cob.to_csv(cfg.saida_tabelas / "pareto_cobertura.csv", index=False, encoding="utf-8-sig")
    if not t_est.empty:
        t_est.to_csv(cfg.saida_tabelas / "estatisticas_numericas.csv", index=False, encoding="utf-8-sig")

    return {"tipos": tipos, "resumo": t_resumo, "cobertura": t_cob, "estatisticas": t_est}
