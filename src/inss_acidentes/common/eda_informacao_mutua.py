"""EDA (Fase 5): informação mútua entre variáveis categóricas e candidatos a alvo.

Decisões metodológicas (documentar em docs/decisoes_metodologicas.md):

* **Mesmo conjunto de variáveis e mesma codificação da bivariada** (``preparar_bivariada``): numéricas
  entram discretizadas, níveis raros viram "Outras", pares usam só linhas com ambas preenchidas.
  Assim a MI e o V de Cramér são comparáveis variável a variável.
* **Medida principal: ``u_alvo`` = MI corrigida / H(alvo)** (coeficiente de incerteza de Theil, U(alvo|x)):
  fração da incerteza do alvo que conhecer ``x`` elimina. Em alvos raros (óbito ~0,5%) a MI em bits é
  minúscula por construção; normalizar por H(alvo) torna os alvos comparáveis entre si.
* **Correção de viés de Miller-Madow.** A MI "plug-in" é inflada em variáveis com muitos níveis (mesmo
  problema do V de Cramér, resolvido por Bergsma na bivariada). Subtrai-se o viés esperado
  ``(células_não_vazias - linhas - colunas + 1) / (2n)`` (em nats) e trunca-se em zero.
* **MI captura associação não linear e não monotônica**, mas é simétrica quanto à direção causal e não
  distingue "preditor legítimo" de "consequência do alvo": por isso cada linha traz as marcas
  ``no_mapa_vazamento`` (lista do ``features.yaml``) e ``quase_determina_alvo`` (``u_alvo`` alto).
* **Recortes** (ex.: óbito só em ``tipo_cat = 1-INICIAL``): rodam a mesma análise num subconjunto, o que
  testa se a associação sobrevive quando se remove o efeito do tipo de comunicação.
* A tabela de **desbalanceamento** sai junto porque H(alvo) já é calculada aqui e porque ela decide a
  métrica de avaliação (PR-AUC/recall/F1 em vez de acurácia) e o desenho do split.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from .eda_bivariada import Codificada, contingencia, preparar_bivariada, qui2_cramer
from .eda_univariada import COR_ACUM, COR_BARRA, ConfigEDA, _nome_arquivo, _salvar, _truncar

log = logging.getLogger(__name__)
LN2 = float(np.log(2.0))


# --------------------------------------------------------------------------- #
# Parâmetros
# --------------------------------------------------------------------------- #
@dataclass
class ParamsMI:
    top_n: int = 15
    limiar_determinismo: float = 0.90     # u_alvo >= : x quase determina o alvo (derivada/vazamento)
    limiar_irrelevante: float = 0.001     # u_alvo < : explica < 0,1% da incerteza do alvo
    recortes: list = field(default_factory=list)          # [{nome, alvo (opcional), query}]
    proibidas_extra: dict = field(default_factory=dict)   # {alvo: [colunas]} propostas desta fase
    alvos_vazamento: dict = field(default_factory=dict)   # {alvo: chave em features.yaml -> vazamento}
    proibidas: dict = field(default_factory=dict)         # {alvo: [colunas]} resolvido por proibidas_do_mapa

    @classmethod
    def from_dict(cls, d: dict | None, proibidas: dict | None = None) -> "ParamsMI":
        d = d or {}
        validos = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and v is not None
                   and k != "proibidas"}
        p = cls(**validos)
        p.proibidas = proibidas or {}
        return p

    def proibidas_do_alvo(self, alvo: str) -> set[str]:
        return set(self.proibidas.get(alvo, [])) | set(self.proibidas_extra.get(alvo, []))


def proibidas_do_mapa(features_cfg: dict | None, alvo_para_chave: dict[str, str]) -> dict[str, list[str]]:
    """Resolve {alvo_da_eda: chave} em listas de colunas proibidas, lendo o ``vazamento`` do features.yaml.

    Mesma lógica de ``cat.features.colunas_proibidas``, sem importar o módulo (que depende do DuckDB).
    """
    out: dict[str, list[str]] = {}
    v = (features_cfg or {}).get("vazamento") or {}
    for alvo, chave in (alvo_para_chave or {}).items():
        if chave == "sempre" or chave not in v:
            log.warning("mapa de vazamento: chave '%s' (alvo '%s') não existe no features.yaml.", chave, alvo)
            continue
        out[alvo] = sorted(set(v.get("sempre", [])) | set(v[chave].get("proibidas", [])) | {v[chave]["alvo"]})
    return out


# --------------------------------------------------------------------------- #
# Estatística
# --------------------------------------------------------------------------- #
def _entropia_bits(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())


def informacao_mutua(tab: np.ndarray) -> dict:
    """MI (bits) entre linhas (x) e colunas (alvo) de uma tabela de contingência.

    Devolve MI bruta, MI corrigida (Miller-Madow), entropias, ``u_alvo`` = MI_corr / H(alvo) e
    ``nmi`` = 2 MI_corr / (H(x) + H(alvo)).
    """
    tab = np.asarray(tab, dtype=np.float64)
    tab = tab[tab.sum(axis=1) > 0][:, tab.sum(axis=0) > 0]
    n = float(tab.sum())
    vazio = {"n": int(n), "mi_bits": np.nan, "mi_corrigida_bits": np.nan, "h_x_bits": np.nan,
             "h_alvo_bits": np.nan, "u_alvo": np.nan, "nmi": np.nan, "vies_bits": np.nan}
    r, k = tab.shape
    if r < 2 or k < 2 or n < 2:
        return vazio
    pxy = tab / n
    px = pxy.sum(axis=1, keepdims=True)
    py = pxy.sum(axis=0, keepdims=True)
    nz = pxy > 0
    mi = float((pxy[nz] * np.log2(pxy[nz] / (px @ py)[nz])).sum())
    hx, hy = _entropia_bits(px.ravel()), _entropia_bits(py.ravel())
    celulas = int(nz.sum())
    vies = ((celulas - 1) - (r - 1) - (k - 1)) / (2.0 * n) / LN2
    mi_c = max(0.0, mi - vies)
    return {"n": int(n), "mi_bits": max(mi, 0.0), "mi_corrigida_bits": mi_c, "h_x_bits": hx, "h_alvo_bits": hy,
            "u_alvo": min(1.0, mi_c / hy) if hy > 0 else np.nan,
            "nmi": min(1.0, 2 * mi_c / (hx + hy)) if (hx + hy) > 0 else np.nan,
            "vies_bits": vies}


def ranking_mi(codif: dict[str, Codificada], alvo: str, p: ParamsMI) -> pd.DataFrame:
    """MI de cada variável contra ``alvo``, ordenada por ``u_alvo``, com marcas de vazamento."""
    y = codif[alvo]
    linhas = []
    for x, cx in codif.items():
        if x == alvo:
            continue
        tab = contingencia(cx, y)
        linhas.append({"variavel": x, "k_x": cx.k, **informacao_mutua(tab),
                       "v_corrigido": qui2_cramer(tab)["v_corrigido"]})
    r = pd.DataFrame(linhas)
    if r.empty:
        return r
    proib = p.proibidas_do_alvo(alvo)
    r["no_mapa_vazamento"] = r["variavel"].isin(proib)
    r["quase_determina_alvo"] = r["u_alvo"] >= p.limiar_determinismo
    r["possivel_vazamento"] = r["no_mapa_vazamento"] | r["quase_determina_alvo"]
    r["recomendacao"] = np.select(
        [r["no_mapa_vazamento"], r["quase_determina_alvo"], r["u_alvo"] < p.limiar_irrelevante],
        ["excluir: no mapa de vazamento", "verificar: quase determina o alvo",
         "pouco informativa isoladamente"],
        default="candidata")
    r = r.sort_values("u_alvo", ascending=False, ignore_index=True)
    r["posicao_mi"] = np.arange(1, len(r) + 1)
    r["posicao_v"] = r["v_corrigido"].rank(ascending=False, method="min").astype("Int64")
    cols = ["variavel", "k_x", "n", "u_alvo", "nmi", "mi_corrigida_bits", "mi_bits", "vies_bits",
            "h_x_bits", "h_alvo_bits", "v_corrigido", "posicao_mi", "posicao_v",
            "no_mapa_vazamento", "quase_determina_alvo", "possivel_vazamento", "recomendacao"]
    return r[cols]


# --------------------------------------------------------------------------- #
# Desbalanceamento
# --------------------------------------------------------------------------- #
def _metricas_sugeridas(k: int, pct_min: float) -> tuple[str, str]:
    if k < 2:
        return "alvo constante", "sem variação: não serve como alvo"
    if k == 2:
        if pct_min < 0.01:
            return ("muito desbalanceado",
                    "PR-AUC (average precision), recall/precisão/F1 da classe positiva, MCC; evitar acurácia; "
                    "split estratificado; class_weight ou reamostragem só no treino")
        if pct_min < 0.10:
            return ("desbalanceado",
                    "PR-AUC, F1 da classe positiva, balanced accuracy; split estratificado; class_weight")
        return "moderado/balanceado", "ROC-AUC, F1, acurácia; split estratificado por precaução"
    if pct_min < 0.01:
        return ("multiclasse com classes raras",
                "F1 macro e por classe, balanced accuracy, matriz de confusão; considerar agrupar classes raras; "
                "split estratificado")
    return "multiclasse", "F1 macro, balanced accuracy, matriz de confusão; split estratificado"


def resumo_alvo(serie: pd.Series, alvo: str, cenario: str = "completo") -> dict:
    """Distribuição de classes do alvo (sobre valores preenchidos) e métrica sugerida."""
    vc = serie.value_counts(dropna=True)
    vc = vc[vc > 0].sort_values(ascending=False)
    n, k = int(vc.sum()), len(vc)
    if k == 0:
        return {"alvo": alvo, "cenario": cenario, "n_validos": 0, "n_classes": 0}
    pct = vc / n
    pct_min = float(pct.iloc[-1])
    h = _entropia_bits(pct.to_numpy())
    nivel, metricas = _metricas_sugeridas(k, pct_min)
    return {
        "alvo": alvo, "cenario": cenario, "n_validos": n, "n_classes": k,
        "classe_majoritaria": str(vc.index[0]), "pct_majoritaria": float(pct.iloc[0]),
        "classe_minoritaria": str(vc.index[-1]), "n_minoritaria": int(vc.iloc[-1]), "pct_minoritaria": pct_min,
        "razao_maj_min": float(vc.iloc[0] / vc.iloc[-1]),
        "n_classes_lt_1pct": int((pct < 0.01).sum()),
        "n_minoritaria_no_teste_20pct": int(vc.iloc[-1] * 0.2),
        "entropia_bits": h, "entropia_normalizada": h / np.log2(k) if k > 1 else 0.0,
        "nivel": nivel, "metricas_sugeridas": metricas,
    }


# --------------------------------------------------------------------------- #
# Gráfico
# --------------------------------------------------------------------------- #
def plotar_mi_alvo(rank: pd.DataFrame, alvo: str, top_n: int = 15, cenario: str = "completo") -> Figure:
    t = rank.head(top_n).iloc[::-1]
    fig = Figure(figsize=(7.8, 0.34 * len(t) + 1.9))
    ax = fig.subplots()
    cores = [COR_ACUM if f else COR_BARRA for f in t["possivel_vazamento"]]
    ax.barh([_truncar(v, 34) for v in t["variavel"]], t["u_alvo"], color=cores)
    for y, v in enumerate(t["u_alvo"]):
        ax.text(v, y, f" {v:.3f}", va="center", fontsize=7)
    ax.set_xlim(0, min(1.0, max(0.02, float(np.nanmax(t["u_alvo"])) * 1.2)))
    ax.set_xlabel("Fração da incerteza do alvo explicada (MI corrigida / H(alvo))")
    sub = "" if cenario == "completo" else f" · recorte: {cenario}"
    ax.set_title(f"Informação mútua com «{alvo}»{sub}\n(vermelho: no mapa de vazamento ou quase determina o alvo)",
                 fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def executar_mi(df: pd.DataFrame, cfg: ConfigEDA, p: ParamsMI, gerar_figuras: bool = True) -> dict:
    dir_t: Path = cfg.saida_tabelas.parent / "informacao_mutua"
    dir_f: Path = cfg.saida_figuras.parent / "informacao_mutua"
    dir_t.mkdir(parents=True, exist_ok=True)
    dir_f.mkdir(parents=True, exist_ok=True)
    csv = dict(index=False, encoding="utf-8-sig")

    cenarios: list[tuple[str, str | None, str | None]] = [("completo", None, None)]
    cenarios += [(r["nome"], r.get("alvo"), r["query"]) for r in p.recortes]

    rankings: dict[tuple[str, str], pd.DataFrame] = {}
    desb: list[dict] = []
    for nome, alvo_recorte, query in cenarios:
        d = df if query is None else df.query(query)
        log.info("MI — cenário '%s': %s linhas", nome, f"{len(d):,}".replace(",", "."))
        if len(d) == 0:
            log.warning("cenário '%s' vazio (query: %s) — ignorado.", nome, query)
            continue
        d, codif, _, _ = preparar_bivariada(d, cfg)
        for alvo in ([alvo_recorte] if alvo_recorte else cfg.candidatos_alvo):
            if alvo in d.columns:
                desb.append(resumo_alvo(d[alvo], alvo, nome))
            if alvo not in codif:
                continue
            rank = ranking_mi(codif, alvo, p)
            rankings[(nome, alvo)] = rank
            suf = "" if nome == "completo" else f"__{_nome_arquivo(nome)}"
            rank.to_csv(dir_t / f"mi_alvo_{_nome_arquivo(alvo)}{suf}.csv", **csv)
            if gerar_figuras and len(rank):
                _salvar(plotar_mi_alvo(rank, alvo, p.top_n, nome),
                        dir_f / f"mi_alvo_{_nome_arquivo(alvo)}{suf}", cfg.formatos, cfg.dpi)
            log.info("  alvo %-30s %d variáveis | candidatas: %d", alvo, len(rank),
                     int((rank["recomendacao"] == "candidata").sum()))

    desbalanceamento = pd.DataFrame(desb)
    desbalanceamento.to_csv(dir_t / "desbalanceamento_alvos.csv", **csv)
    if rankings:
        todos = pd.concat([r.assign(cenario=c, alvo=a) for (c, a), r in rankings.items()], ignore_index=True)
        todos = todos[["cenario", "alvo", "variavel", "k_x", "u_alvo", "nmi", "v_corrigido", "posicao_mi",
                       "posicao_v", "no_mapa_vazamento", "quase_determina_alvo", "recomendacao"]]
        todos.to_csv(dir_t / "mi_todos_alvos.csv", **csv)
        base = todos[todos["cenario"] == "completo"]
        base.pivot(index="variavel", columns="alvo", values="u_alvo").sort_index().to_csv(
            dir_t / "mi_matriz_u_alvo.csv", encoding="utf-8-sig")
    return {"rankings": rankings, "desbalanceamento": desbalanceamento, "dir_tabelas": dir_t, "dir_figuras": dir_f}
