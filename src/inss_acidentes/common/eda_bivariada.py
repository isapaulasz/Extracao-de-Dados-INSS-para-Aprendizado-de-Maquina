"""EDA bivariada entre categóricas (Fase 5): qui-quadrado + V de Cramér.

Decisões metodológicas (documentar em docs/decisoes_metodologicas.md):

* **Medida principal: V de Cramér com correção de viés (Bergsma, 2013).** O V "bruto" é inflado
  quando há muitas categorias; a correção corrige isso e permite comparar pares com tamanhos
  de tabela diferentes.
* **O p-valor do qui-quadrado é só informativo.** Com milhões de linhas qualquer associação,
  por menor que seja, tem p ~ 0; o que importa é o *tamanho do efeito* (V).
* **Pares em que uma variável é derivada da outra** (ex.: CID -> capítulo) dão V ~ 1 por
  construção; ficam listados em ``pares_redundantes.csv`` e não devem entrar juntos nos modelos.
* **Níveis raros** (< ``min_pct_nivel`` das linhas válidas) são agrupados em "Outras" para não
  gerar células com frequência esperada muito baixa.
* **Ausentes:** cada par usa só as linhas em que ambas as variáveis estão preenchidas.
* Numéricas entram discretizadas (``discretizar``) e colunas de data entram como indicador de
  presença (``indicadores_presenca``), o que também ajuda a detectar vazamento de informação.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from .eda_univariada import (
    COR_ACUM,
    COR_BARRA,
    ConfigEDA,
    _nome_arquivo,
    _salvar,
    _truncar,
    classificar_colunas,
)

try:  # scipy é opcional: sem ele não há p-valor nem ordenação do heatmap
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform
    from scipy.stats import chi2 as _chi2
except ImportError:  # pragma: no cover
    _chi2 = linkage = leaves_list = squareform = None

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Parâmetros
# --------------------------------------------------------------------------- #
@dataclass
class ParamsBivariada:
    excluir: list[str] = field(default_factory=list)          # fora só da bivariada
    max_niveis: int = 250                                      # acima disso a variável não entra
    min_pct_nivel: float = 0.0005                              # níveis < 0,05% viram "Outras"
    limiar_redundancia: float = 0.90                           # V >= 0,90: derivada/vazamento
    top_n_alvo: int = 15
    anotar_heatmap_ate: int = 30                               # anota os valores se <= N variáveis
    discretizar: dict = field(default_factory=dict)            # {col: {bordas: [...], rotulos: [...]}}
    indicadores_presenca: dict = field(default_factory=dict)   # {nome_novo: coluna_origem}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ParamsBivariada":
        d = d or {}
        validos = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and v is not None}
        return cls(**validos)


@dataclass
class Codificada:
    codigos: np.ndarray | None   # int16, -1 = ausente (None se a variável foi barrada)
    k: int
    rotulos: list[str]
    n_agrupados: int             # quantos níveis raros foram para "Outras"
    n_validos: int
    n_total: int

    @property
    def tem_nulos(self) -> bool:
        return self.n_validos < self.n_total


# --------------------------------------------------------------------------- #
# Derivação de variáveis
# --------------------------------------------------------------------------- #
def _faixas(serie: pd.Series, bordas: list[float], rotulos: list[str] | None = None) -> pd.Series:
    """Faixas [a, b) com bordas fixas; rótulos automáticos se não informados."""
    n = len(bordas) + 1
    if rotulos is None:
        rotulos = ([f"<{bordas[0]:g}"]
                   + [f"{bordas[i]:g}–<{bordas[i + 1]:g}" for i in range(n - 2)]
                   + [f"≥{bordas[-1]:g}"])
    if len(rotulos) != n:
        raise ValueError(f"{n} faixas exigem {n} rótulos (recebi {len(rotulos)}).")
    return pd.cut(serie, bins=[-np.inf, *bordas, np.inf], labels=rotulos, right=False, ordered=True)


def adicionar_derivadas(df: pd.DataFrame, p: ParamsBivariada) -> pd.DataFrame:
    """Cria ``<col>_faixa`` e indicadores de presença. Não altera o dataframe original."""
    df = df.copy(deep=False)
    for col, conf in p.discretizar.items():
        if col not in df.columns:
            log.warning("discretizar: coluna '%s' não existe — ignorada.", col)
            continue
        df[f"{col}_faixa"] = _faixas(pd.to_numeric(df[col], errors="coerce"),
                                     conf["bordas"], conf.get("rotulos"))
    for novo, origem in p.indicadores_presenca.items():
        if origem not in df.columns:
            log.warning("indicadores_presenca: coluna '%s' não existe — ignorada.", origem)
            continue
        df[novo] = pd.Categorical.from_codes(df[origem].notna().to_numpy().astype(np.int8), ["não", "sim"])
    return df


# --------------------------------------------------------------------------- #
# Codificação e estatística
# --------------------------------------------------------------------------- #
def codificar(serie: pd.Series, min_pct: float = 0.0005, max_niveis: int = 250) -> Codificada | None:
    """Converte em códigos inteiros, agrupando níveis raros.

    Devolve ``codigos=None`` (e ``k`` = nº de níveis originais) se a variável tem mais de ``max_niveis``
    níveis; devolve ``None`` se não há nenhum valor preenchido.
    """
    cat = serie if isinstance(serie.dtype, pd.CategoricalDtype) else serie.astype("category")
    codes = cat.cat.codes.to_numpy()
    k0 = len(cat.cat.categories)
    cont = np.bincount(codes[codes >= 0], minlength=k0)
    total = int(cont.sum())
    if total == 0:
        return None
    usados = cont > 0
    n_usados = int(usados.sum())
    if n_usados > max_niveis:            # teto vale para a cardinalidade original, antes de agrupar raros
        return Codificada(None, n_usados, [], 0, total, len(codes))
    raros = usados & (cont < max(1, min_pct * total))
    agrupar = int(raros.sum()) >= 2
    manter = np.flatnonzero(usados & (~raros if agrupar else usados))
    rotulos = [str(cat.cat.categories[i]) for i in manter]
    k = len(manter) + (1 if agrupar else 0)
    novo = np.full(k0, -1, dtype=np.int32)
    novo[manter] = np.arange(len(manter))
    if agrupar:
        novo[raros] = len(manter)
        rotulos.append(f"Outras ({int(raros.sum())})")
    out = np.full(len(codes), -1, dtype=np.int16)
    m = codes >= 0
    out[m] = novo[codes[m]]
    return Codificada(out, k, rotulos, int(raros.sum()) if agrupar else 0, total, len(codes))


def contingencia(a: Codificada, b: Codificada) -> np.ndarray:
    ca, cb = a.codigos, b.codigos
    if a.tem_nulos or b.tem_nulos:
        m = (ca >= 0) & (cb >= 0)
        ca, cb = ca[m], cb[m]
    idx = ca.astype(np.int32) * b.k + cb
    return np.bincount(idx, minlength=a.k * b.k).reshape(a.k, b.k)


def qui2_cramer(tab: np.ndarray) -> dict:
    """Qui-quadrado de independência + V de Cramér bruto e corrigido (Bergsma)."""
    vazio = {"n": int(tab.sum()), "chi2": np.nan, "dof": np.nan, "p_valor": np.nan,
             "v_bruto": np.nan, "v_corrigido": np.nan, "pct_celulas_esp_lt5": np.nan}
    tab = tab[tab.sum(axis=1) > 0][:, tab.sum(axis=0) > 0]
    r, k = tab.shape
    n = int(tab.sum())
    if r < 2 or k < 2 or n < 2:
        return vazio
    esp = np.outer(tab.sum(axis=1), tab.sum(axis=0)) / n
    chi2 = float(((tab - esp) ** 2 / esp).sum())
    dof = (r - 1) * (k - 1)
    phi2 = chi2 / n
    v_bruto = float(np.sqrt(phi2 / min(r - 1, k - 1)))
    phi2c = max(0.0, phi2 - dof / (n - 1))
    rc, kc = r - (r - 1) ** 2 / (n - 1), k - (k - 1) ** 2 / (n - 1)
    v_corr = float(np.sqrt(phi2c / max(min(rc - 1, kc - 1), 1e-12)))
    return {"n": n, "chi2": chi2, "dof": dof,
            "p_valor": float(_chi2.sf(chi2, dof)) if _chi2 is not None else np.nan,
            "v_bruto": v_bruto, "v_corrigido": min(v_corr, 1.0),
            "pct_celulas_esp_lt5": float((esp < 5).mean())}


# --------------------------------------------------------------------------- #
# Seleção de variáveis e matriz de associação
# --------------------------------------------------------------------------- #
def preparar_bivariada(df: pd.DataFrame, cfg: ConfigEDA) -> tuple[pd.DataFrame, dict, pd.DataFrame, ParamsBivariada]:
    """Deriva faixas/indicadores, seleciona e codifica as variáveis. Ainda não calcula pares."""
    p = ParamsBivariada.from_dict(cfg.bivariada)
    df = adicionar_derivadas(df, p)
    tipos = classificar_colunas(df, cfg)
    nao: list[tuple[str, str]] = [(c, "excluida_eda (config/identificador)") for c in tipos["excluidas"]]
    nao += [(c, "data (ver séries temporais)") for c in tipos["datas"]]
    nao += [(c, "substituida_por_faixa" if c in p.discretizar else "numerica_sem_discretizacao")
            for c in tipos["numericas"]]
    codif: dict[str, Codificada] = {}
    for c in tipos["categoricas"]:
        if c in p.excluir:
            nao.append((c, "excluida_bivariada (config)"))
            continue
        cod = codificar(df[c], p.min_pct_nivel, p.max_niveis)
        if cod is None:
            nao.append((c, "vazia (só ausentes)"))
        elif cod.codigos is None:
            nao.append((c, f"cardinalidade {cod.k} > max_niveis ({p.max_niveis})"))
        elif cod.k < 2:
            nao.append((c, "nível único (ou todos os níveis raros)"))
        else:
            codif[c] = cod
    for a in cfg.candidatos_alvo:
        if a not in codif:
            log.warning("candidato a alvo '%s' não está entre as variáveis avaliadas.", a)
    return df, codif, pd.DataFrame(nao, columns=["variavel", "motivo"]), p


def calcular_pares(codif: dict[str, Codificada]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calcula todos os pares. Devolve (tabela longa de pares, matriz de V corrigido)."""
    nomes = list(codif)
    linhas = []
    total = len(nomes) * (len(nomes) - 1) // 2
    for i, a in enumerate(nomes):
        for b in nomes[i + 1:]:
            r = qui2_cramer(contingencia(codif[a], codif[b]))
            linhas.append({"var_a": a, "var_b": b, "k_a": codif[a].k, "k_b": codif[b].k, **r})
        if (i + 1) % 10 == 0:
            log.info("bivariada: %d/%d pares", len(linhas), total)
    pares = pd.DataFrame(linhas).sort_values("v_corrigido", ascending=False, ignore_index=True)
    m = pd.DataFrame(np.eye(len(nomes)), index=nomes, columns=nomes)
    for r in pares.itertuples():
        m.loc[r.var_a, r.var_b] = m.loc[r.var_b, r.var_a] = r.v_corrigido
    return pares, m


def ranking_alvo(pares: pd.DataFrame, alvo: str, limiar: float) -> pd.DataFrame:
    m = pares[(pares["var_a"] == alvo) | (pares["var_b"] == alvo)].copy()
    m["variavel"] = np.where(m["var_a"] == alvo, m["var_b"], m["var_a"])
    m["possivel_vazamento_ou_derivada"] = m["v_corrigido"] >= limiar
    cols = ["variavel", "v_corrigido", "v_bruto", "chi2", "dof", "p_valor", "n",
            "pct_celulas_esp_lt5", "possivel_vazamento_ou_derivada"]
    return m[cols].sort_values("v_corrigido", ascending=False, ignore_index=True)


# --------------------------------------------------------------------------- #
# Gráficos
# --------------------------------------------------------------------------- #
def plotar_heatmap_v(matriz: pd.DataFrame, anotar_ate: int = 30, ordenar: bool = True) -> Figure:
    m = matriz.copy()
    if ordenar and linkage is not None and len(m) > 2:
        d = 1 - m.fillna(0).to_numpy()
        d = (d + d.T) / 2
        np.fill_diagonal(d, 0)
        ordem = leaves_list(linkage(squareform(d, checks=False), "average"))
        m = m.iloc[ordem, ordem]
    n = len(m)
    lado = min(16.0, max(6.0, 0.3 * n + 3))
    fig = Figure(figsize=(lado, lado * 0.9))
    ax = fig.subplots()
    arr = m.to_numpy().copy()
    np.fill_diagonal(arr, np.nan)
    im = ax.imshow(arr, cmap="YlOrRd", vmin=0, vmax=1)
    rot = [_truncar(c, 26) for c in m.index]
    ax.set_xticks(range(n), rot, rotation=90, fontsize=7)
    ax.set_yticks(range(n), rot, fontsize=7)
    if n <= anotar_ate:
        for i in range(n):
            for j in range(n):
                if i != j and not np.isnan(arr[i, j]):
                    ax.text(j, i, f"{arr[i, j]:.2f}", ha="center", va="center", fontsize=6,
                            color="white" if arr[i, j] > 0.6 else "black")
    fig.colorbar(im, ax=ax, shrink=0.8, label="V de Cramér (corrigido)")
    ax.set_title("Associação entre variáveis categóricas (V de Cramér corrigido)", fontsize=11)
    fig.tight_layout()
    return fig


def plotar_associacao_alvo(rank: pd.DataFrame, alvo: str, top_n: int = 15, limiar: float = 0.9) -> Figure:
    t = rank.head(top_n).iloc[::-1]
    fig = Figure(figsize=(7.5, 0.34 * len(t) + 1.8))
    ax = fig.subplots()
    cores = [COR_ACUM if v else COR_BARRA for v in t["possivel_vazamento_ou_derivada"]]
    ax.barh([_truncar(v, 34) for v in t["variavel"]], t["v_corrigido"], color=cores)
    for y, v in enumerate(t["v_corrigido"]):
        ax.text(v, y, f" {v:.3f}", va="center", fontsize=7)
    ax.set_xlim(0, min(1.0, max(0.1, float(t["v_corrigido"].max()) * 1.2)))
    ax.set_xlabel("V de Cramér (corrigido)")
    ax.set_title(f"Associação com «{alvo}»\n(vermelho: V ≥ {limiar:.2f}, possível derivada/vazamento)", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def executar_bivariada(df: pd.DataFrame, cfg: ConfigEDA, gerar_figuras: bool = True) -> dict:
    df, codif, nao, p = preparar_bivariada(df, cfg)
    if len(codif) < 2:
        raise ValueError("Menos de 2 variáveis categóricas elegíveis para a bivariada.")
    dir_t = cfg.saida_tabelas.parent / "bivariada"
    dir_f = cfg.saida_figuras.parent / "bivariada"
    dir_t.mkdir(parents=True, exist_ok=True)
    dir_f.mkdir(parents=True, exist_ok=True)
    csv = dict(index=False, encoding="utf-8-sig")

    aval = pd.DataFrame([{"variavel": c, "k_niveis": o.k, "n_validos": o.n_validos,
                          "pct_nulos": 1 - o.n_validos / o.n_total, "niveis_agrupados_em_outras": o.n_agrupados}
                         for c, o in codif.items()])
    aval.to_csv(dir_t / "variaveis_avaliadas.csv", **csv)
    nao.to_csv(dir_t / "variaveis_nao_avaliadas.csv", **csv)

    log.info("Calculando %d pares entre %d variáveis...", len(codif) * (len(codif) - 1) // 2, len(codif))
    pares, matriz = calcular_pares(codif)
    pares.to_csv(dir_t / "associacoes_pares.csv", **csv)
    matriz.to_csv(dir_t / "cramer_v_matriz.csv", encoding="utf-8-sig")
    redund = pares[pares["v_corrigido"] >= p.limiar_redundancia]
    redund.to_csv(dir_t / "pares_redundantes.csv", **csv)

    rankings = {}
    for alvo in cfg.candidatos_alvo:
        if alvo not in codif:
            continue
        rank = ranking_alvo(pares, alvo, p.limiar_redundancia)
        rankings[alvo] = rank
        rank.to_csv(dir_t / f"associacao_alvo_{_nome_arquivo(alvo)}.csv", **csv)
        if gerar_figuras:
            _salvar(plotar_associacao_alvo(rank, alvo, p.top_n_alvo, p.limiar_redundancia),
                    dir_f / f"associacao_alvo_{_nome_arquivo(alvo)}", cfg.formatos, cfg.dpi)
    if gerar_figuras:
        _salvar(plotar_heatmap_v(matriz, p.anotar_heatmap_ate), dir_f / "heatmap_cramer_v", cfg.formatos, cfg.dpi)

    return {"avaliadas": aval, "nao_avaliadas": nao, "pares": pares, "matriz": matriz,
            "redundantes": redund, "rankings": rankings, "dir_tabelas": dir_t, "dir_figuras": dir_f}
