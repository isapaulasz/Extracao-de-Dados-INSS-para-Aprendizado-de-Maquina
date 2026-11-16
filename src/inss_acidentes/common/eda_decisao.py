"""EDA (Fase 5), fechamento: desbalanceamento dos alvos e seleção de variáveis por técnica.

Este módulo **não lê o parquet**: consolida tabelas que a EDA já gravou em ``reports/<base>/tables/eda/``
(``desbalanceamento_alvos.csv``, ``mi_todos_alvos.csv``, ``associacoes_pares.csv``, ``resumo_variaveis.csv``,
taxas por arquivo/UF). Assim a decisão é reproduzível em segundos e auditável (cada linha traz o motivo).

Decisões metodológicas (documentar em ``docs/decisoes_metodologicas.md``):

* **O desbalanceamento decide a métrica, não o modelo.** Para alvo binário muito raro (< 1% da classe
  positiva) a acurácia do classificador trivial já é > 99%; a métrica primária passa a ser a PR-AUC, cuja
  linha de base é a própria prevalência (e não 0,5, como na ROC-AUC).
* **Tamanho de amostra por partição.** Para cada alvo calculam-se positivos no treino, no teste e por dobra
  da validação cruzada, e a meia-largura (pior caso) do IC95% do recall. O que importa é o número de
  positivos, não o de linhas.
* **Estabilidade da prevalência.** Teste de homogeneidade (qui-quadrado) e razão máx/mín das taxas por
  arquivo e por território. Com milhões de linhas o p-valor sempre rejeita; por isso a leitura usa a razão
  máx/mín (limiar configurável) e o V de Cramér como tamanho de efeito. Taxa estável no tempo sustenta o
  split estratificado aleatório; taxa instável exige validação temporal.
* **Variáveis por técnica** seguem regras em ordem fixa: identificador → vazamento → exclusão por alvo →
  exclusão própria → redundância (um nível por hierarquia e por técnica) → baixa informação →
  cardinalidade. A primeira regra que casa define o status.
* **Baixa informação marginal não é descarte automático** em RF e rede neural: a MI univariada não vê
  interações. Nesses modelos a variável fica como ``entra_opcional`` (testar por ablação); na árvore única,
  que precisa ser interpretável, sai.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from matplotlib.figure import Figure

try:  # scipy é opcional (como na bivariada): sem ele os p-valores saem NaN
    from scipy.stats import chi2 as _chi2
    from scipy.stats import norm as _norm
except ImportError:  # pragma: no cover
    _chi2 = _norm = None

log = logging.getLogger(__name__)

STATUS_ENTRA = ("entra", "entra_com_cuidado", "entra_opcional", "entra_numerica")


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #
def carregar_cfg(caminho: str | Path) -> dict:
    return yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}


def ler_csv(caminho: str | Path) -> pd.DataFrame:
    return pd.read_csv(caminho, encoding="utf-8-sig")


def _lookup_v(pares: pd.DataFrame | None, a: str, b: str) -> float:
    """V de Cramér corrigido do par (a, b) em qualquer ordem; NaN se o par não foi avaliado."""
    if pares is None or pares.empty:
        return float("nan")
    m = ((pares["var_a"] == a) & (pares["var_b"] == b)) | ((pares["var_a"] == b) & (pares["var_b"] == a))
    return float(pares.loc[m, "v_corrigido"].iloc[0]) if m.any() else float("nan")


def _fmt_pct(x: float, casas: int = 2) -> str:
    return f"{100 * x:.{casas}f}%".replace(".", ",")


# --------------------------------------------------------------------------- #
# 1. Plano de desbalanceamento
# --------------------------------------------------------------------------- #
def nivel_desbalanceamento(pct_min: float, n_classes: int, lim: dict) -> str:
    if n_classes > 2:
        return "multiclasse"
    if pct_min < lim["muito"]:
        return "muito desbalanceado"
    if pct_min < lim["forte"]:
        return "desbalanceado"
    return "moderado/balanceado"


def _grupo_metricas(nivel: str) -> str:
    return {"multiclasse": "multiclasse", "muito desbalanceado": "muito_desbalanceado",
            "desbalanceado": "muito_desbalanceado"}.get(nivel, "balanceado")


def plano_desbalanceamento(alvos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Uma linha por (alvo, cenário) com baselines, positivos por partição, métricas e estratégia por técnica."""
    sp = cfg.get("split", {})
    teste, k = float(sp.get("teste_pct", 0.2)), int(sp.get("k_dobras", 5))
    lim = cfg["limiares_desbalanceamento"]
    cfg_alvos = cfg.get("alvos", {})
    tecnicas = (cfg.get("variaveis") or {}).get("tecnicas", [])
    linhas = []
    for _, r in alvos.iterrows():
        a = cfg_alvos.get(r["alvo"], {})
        n, k_cl = int(r["n_validos"]), int(r["n_classes"])
        nivel = nivel_desbalanceamento(float(r["pct_minoritaria"]), k_cl, lim)
        grupo = _grupo_metricas(nivel)
        linha = {
            "alvo": r["alvo"], "cenario": r["cenario"], "papel": a.get("papel", "nao_classificado"),
            "cenario_adotado": r["cenario"] == a.get("cenario"),
            "n_validos": n, "n_classes": k_cl, "nivel": nivel,
            "pct_majoritaria": r["pct_majoritaria"], "pct_minoritaria": r["pct_minoritaria"],
            "n_minoritaria": int(r["n_minoritaria"]), "razao_maj_min": r["razao_maj_min"],
            "n_classes_lt_1pct": int(r["n_classes_lt_1pct"]),
            "acuracia_classificador_trivial": r["pct_majoritaria"],
        }
        pos = str(a["classe_positiva"]) if a.get("classe_positiva") is not None else None
        if k_cl == 2 and pos is not None:
            prev = r["pct_minoritaria"] if str(r["classe_minoritaria"]) == pos else r["pct_majoritaria"]
            n_pos = int(round(prev * n))
            n_pos_teste = int(round(n_pos * teste))
            n_pos_treino = n_pos - n_pos_teste
            linha.update({
                "classe_positiva": pos, "prevalencia_positiva": prev, "n_positivos": n_pos,
                "pr_auc_baseline": prev, "roc_auc_baseline": 0.5,
                "n_pos_treino": n_pos_treino, "n_pos_teste": n_pos_teste,
                "n_pos_por_dobra_validacao": int(round(n_pos_treino / k)),
                "meia_largura_ic95_recall_pior_caso": 1.96 * math.sqrt(0.25 / max(n_pos_teste, 1)),
                "peso_balanced_positiva": n / (2 * max(n_pos, 1)),
                "peso_balanced_negativa": n / (2 * max(n - n_pos, 1)),
            })
        m = (cfg.get("metricas") or {}).get(grupo, {})
        est = cfg.get("estrategia_muito_desbalanceado" if grupo == "muito_desbalanceado" else "estrategia_balanceado", {})
        linha.update({
            "metrica_primaria": m.get("primaria", ""),
            "metricas_secundarias": "; ".join(m.get("secundarias", [])),
            "metricas_complementares": "; ".join(m.get("complementar", [])),
            "metricas_proibidas": "; ".join(m.get("proibidas", [])),
            "estrategia_split": ("estratificado pelo alvo (e por classe, se multiclasse)" if cfg.get("metricas") else ""),
        })
        for t in tecnicas:
            linha[f"estrategia_{t}"] = est.get(t, "")
        linha["motivo_papel"] = a.get("motivo", "")
        linhas.append(linha)
    out = pd.DataFrame(linhas)
    ordem = {"primario": 0, "secundario": 1, "consequente_interesse": 1, "reserva": 2, "descartado": 3, "nao_classificado": 4}
    out["_o"] = out["papel"].map(ordem).fillna(9)
    out = out.sort_values(["_o", "alvo", "cenario_adotado"], ascending=[True, True, False]).drop(columns="_o")
    vazias = [c for c in out.columns if pd.api.types.is_string_dtype(out[c]) and (out[c].fillna("") == "").all()]
    return out.drop(columns=vazias).reset_index(drop=True)


def plotar_desbalanceamento(plano: pd.DataFrame) -> Figure:
    """Barras horizontais (escala log) da prevalência da classe minoritária por alvo e cenário."""
    d = plano.sort_values("pct_minoritaria").reset_index(drop=True)
    cores = {"muito desbalanceado": "#c0392b", "desbalanceado": "#e67e22",
             "moderado/balanceado": "#2e86c1", "multiclasse": "#7f8c8d"}
    fig = Figure(figsize=(9, 0.55 * len(d) + 1.6))
    ax = fig.subplots()
    rotulos = [f"{a} [{c}]" for a, c in zip(d["alvo"], d["cenario"])]
    ax.barh(rotulos, d["pct_minoritaria"] * 100, color=[cores.get(n, "#7f8c8d") for n in d["nivel"]])
    ax.set_xscale("log")
    for y, (p, n, rz) in enumerate(zip(d["pct_minoritaria"], d["n_minoritaria"], d["razao_maj_min"])):
        ax.text(p * 100 * 1.15, y, f"{_fmt_pct(p, 3)} · n={n:,} · 1:{rz:,.0f}".replace(",", "."), va="center", fontsize=8,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0}, zorder=3)
    ax.axvline(1, color="k", lw=0.8, ls="--")
    ax.set_xlabel("% da classe minoritária (escala log); linha tracejada = 1%")
    ax.set_title("Desbalanceamento dos candidatos a alvo")
    ax.set_xlim(right=max(d["pct_minoritaria"].max() * 100 * 12, 5))
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- #
# 2. Estabilidade da prevalência (arquivo, território)
# --------------------------------------------------------------------------- #
def heterogeneidade(tab: pd.DataFrame, ordenado: bool = False, limiar_razao: float = 1.25) -> dict:
    """Teste de homogeneidade das taxas entre grupos (k×2) + tendência de Cochran-Armitage se ``ordenado``.

    ``tab`` precisa de ``n`` e ``n_pos`` e de uma coluna de rótulo (``grupo`` ou ``categoria``). Se existirem
    ``informada`` e ``n_suficiente`` (tabelas territoriais), só as linhas verdadeiras entram.
    """
    t = tab.copy()
    for c in ("informada", "n_suficiente"):
        if c in t.columns:
            t = t[t[c].astype(str).str.lower() == "true"]
    rot = "grupo" if "grupo" in t.columns else "categoria"
    t = t[t["n"] > 0].reset_index(drop=True)
    k = len(t)
    n = t["n"].to_numpy(float)
    r = t["n_pos"].to_numpy(float)
    N, R = n.sum(), r.sum()
    p = R / N if N else float("nan")
    taxas = r / n
    out = {"n_grupos": k, "n_total": int(N), "n_pos_total": int(R), "taxa_global": p,
           "taxa_min": float(taxas.min()), "grupo_min": str(t[rot].iloc[int(taxas.argmin())]),
           "taxa_max": float(taxas.max()), "grupo_max": str(t[rot].iloc[int(taxas.argmax())])}
    out["razao_max_min"] = out["taxa_max"] / out["taxa_min"] if out["taxa_min"] > 0 else float("inf")
    if k < 2 or p in (0.0, 1.0) or np.isnan(p):
        out.update({"chi2": np.nan, "gl": k - 1, "p_valor": np.nan, "v_cramer": np.nan})
    else:
        chi2 = float(np.sum((r - n * p) ** 2 / (n * p * (1 - p))))
        out.update({"chi2": chi2, "gl": k - 1,
                    "p_valor": float(_chi2.sf(chi2, k - 1)) if _chi2 is not None else np.nan,
                    "v_cramer": math.sqrt(chi2 / N)})
    out.update({"tendencia_z": np.nan, "tendencia_p": np.nan, "tendencia_sentido": ""})
    if ordenado and k >= 3 and 0 < p < 1:
        s = np.arange(1, k + 1, dtype=float)
        num = float(np.sum(s * (r - n * p)))
        var = p * (1 - p) * (np.sum(n * s**2) - np.sum(n * s) ** 2 / N)
        if var > 0:
            z = num / math.sqrt(var)
            out.update({"tendencia_z": z, "tendencia_sentido": "crescente" if z > 0 else "decrescente",
                        "tendencia_p": float(2 * _norm.sf(abs(z))) if _norm is not None else np.nan})
    out["leitura"] = "estavel" if out["razao_max_min"] <= limiar_razao else "heterogenea"
    return out


def tabela_heterogeneidade(raiz_tabelas: str | Path, cfg: dict) -> pd.DataFrame:
    h = cfg.get("heterogeneidade") or {}
    lim = float(h.get("limiar_razao_estavel", 1.25))
    linhas = []
    for item in h.get("tabelas", []):
        caminho = Path(raiz_tabelas) / item["arquivo"]
        if not caminho.exists():
            log.warning("Tabela de taxa ausente, pulando: %s", caminho)
            continue
        r = heterogeneidade(ler_csv(caminho), bool(item.get("ordenado")), lim)
        linhas.append({"alvo": item["alvo"], "eixo": item["eixo"], **r})
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------- #
# 3. Variáveis por técnica — CAT (predição)
# --------------------------------------------------------------------------- #
def _grupo_de(var: str, grupos: list[dict]) -> dict | None:
    return next((g for g in grupos if var in g["membros"]), None)


def _codificacao(var: str, k: int | float, vcfg: dict, max_niveis: int) -> str:
    cod = vcfg.get("codificacao", {})
    if var in cod.get("ordinal", []):
        return "ordinal"
    if pd.notna(k) and k > max_niveis:
        return cod.get("alta_cardinalidade", "")
    if pd.notna(k) and k <= 12:
        return "one-hot"
    return "one-hot (muitos níveis) ou codificação por frequência"


def decidir_variaveis(mi: pd.DataFrame, resumo: pd.DataFrame | None, pares: pd.DataFrame | None,
                      cfg: dict) -> pd.DataFrame:
    """Status de cada variável para cada (alvo primário/secundário × técnica de predição)."""
    v = cfg["variaveis"]
    tecnicas = v["tecnicas"]
    max_niv = int(v.get("max_niveis_arvore", 50))
    lim_info = float(v.get("limiar_baixa_informacao", 0.001))
    ident = set(v.get("identificadores", []))
    grupos = v.get("grupos_redundancia", [])
    quase_const = set()
    if resumo is not None and "quase_constante" in resumo.columns:
        quase_const = set(resumo.loc[resumo["quase_constante"].astype(str) == "True", "variavel"])
    linhas = []
    for alvo, a in cfg["alvos"].items():
        if a.get("papel") not in ("primario", "secundario"):
            continue
        cen = a["cenario"]
        m_cen = mi[(mi["alvo"] == alvo) & (mi["cenario"] == cen)].set_index("variavel")
        m_comp = mi[(mi["alvo"] == alvo) & (mi["cenario"] == "completo")].set_index("variavel")
        universo = list(dict.fromkeys(list(m_cen.index) + [x for x in m_comp.index if x not in m_cen.index] + sorted(ident)))
        liberadas = (v.get("liberar_no_recorte") or {}).get(cen, {})
        extra = (v.get("excluir_extra") or {}).get(alvo, {})
        for var in universo:
            for tec in tecnicas:
                st, motivo, grupo = _decidir_uma(var, tec, alvo, cen, m_cen, m_comp, ident, liberadas, extra,
                                                 v.get("excluir_sempre", {}), grupos, quase_const, pares, max_niv, lim_info)
                row = m_cen.loc[var] if var in m_cen.index else None
                k = row["k_x"] if row is not None else np.nan
                linhas.append({
                    "base": cfg["base"], "alvo": alvo, "cenario": cen, "papel_alvo": a["papel"], "tecnica": tec,
                    "variavel": var, "status": st, "motivo": motivo, "grupo_redundancia": grupo or "",
                    "k_niveis": k,
                    "u_alvo": row["u_alvo"] if row is not None else np.nan,
                    "v_corrigido_alvo": row["v_corrigido"] if row is not None else np.nan,
                    "posicao_mi": row["posicao_mi"] if row is not None else np.nan,
                    "codificacao_sugerida": _codificacao(var, k, v, max_niv) if st in STATUS_ENTRA else "",
                })
    return pd.DataFrame(linhas)


def _decidir_uma(var, tec, alvo, cen, m_cen, m_comp, ident, liberadas, extra, excluir_sempre, grupos,
                 quase_const, pares, max_niv, lim_info=0.001):
    if var in ident:
        return "excluir_identificador", "Identificador, proveniência do arquivo ou texto livre.", ""
    if var not in m_cen.index:
        return "excluir_constante_no_recorte", f"Constante (ou ausente) no recorte {cen}.", ""
    row = m_cen.loc[var]
    liberada = var in liberadas
    if not liberada and (bool(row["no_mapa_vazamento"]) or bool(row["quase_determina_alvo"])):
        if bool(row["quase_determina_alvo"]):
            return "excluir_vazamento", f"Quase determina o alvo (U={row['u_alvo']:.2f}); no mapa de vazamento.", ""
        return "excluir_vazamento", f"No mapa de vazamento (U={row['u_alvo']:.3f}).", ""
    if var in extra:
        return "excluir_vazamento", extra[var], ""
    if var in excluir_sempre:
        return "excluir_outro", excluir_sempre[var], ""
    g = _grupo_de(var, grupos)
    nome_g = g["nome"] if g else ""
    if g and g["preferida"][tec] != var:
        pref = g["preferida"][tec]
        vv = _lookup_v(pares, var, pref)
        sufixo = f" (V={vv:.2f} com {pref})" if not np.isnan(vv) else ""
        return "excluir_redundante", f"{g['motivo']}; nesta técnica usar {pref}{sufixo}.", nome_g
    obs_lib = f" Liberada no recorte: {liberadas[var]}" if liberada else ""
    # variável liberada do mapa herda a recomendação "excluir"; a fraqueza é então medida pelo u_alvo
    fraca = (str(row["recomendacao"]).startswith("pouco informativa") or var in quase_const
             or (liberada and row["u_alvo"] < lim_info))
    if fraca:
        causa = "quase constante" if var in quase_const else "MI/V ~ 0 com o alvo"
        if tec == "arvore":
            return "excluir_baixa_informacao", f"{causa}; fora da árvore única (parcimônia/interpretação).{obs_lib}", nome_g
        return "entra_opcional", f"{causa}; manter só se a ablação mostrar ganho (a MI univariada não vê interações).{obs_lib}", nome_g
    if pd.notna(row["k_x"]) and row["k_x"] > max_niv:
        return "entra_com_cuidado", f"{int(row['k_x'])} níveis: exige codificação ajustada só no treino.{obs_lib}", nome_g
    return "entra", f"Candidata (U={row['u_alvo']:.4f}, V={row['v_corrigido']:.2f}).{obs_lib}", nome_g


def matriz_variaveis(decisao: pd.DataFrame) -> pd.DataFrame:
    """Largura: uma linha por variável, colunas ``<alvo>|<técnica>`` com o status. Boa para tabela do artigo."""
    d = decisao.copy()
    d["coluna"] = d["alvo"] + "|" + d["tecnica"]
    m = d.pivot(index="variavel", columns="coluna", values="status")
    n_entra = m.apply(lambda c: c.isin(STATUS_ENTRA)).sum(axis=1)
    m["n_combinacoes_que_usam"] = n_entra
    return m.sort_values("n_combinacoes_que_usam", ascending=False).reset_index()


def resumo_decisao(decisao: pd.DataFrame) -> pd.DataFrame:
    """Contagem de variáveis por (alvo, técnica, status)."""
    return (decisao.groupby(["alvo", "tecnica", "status"]).size().rename("n_variaveis").reset_index())


# --------------------------------------------------------------------------- #
# 4. Variáveis por técnica — BC (Apriori e clusterização)
# --------------------------------------------------------------------------- #
def decidir_variaveis_mineracao(resumo: pd.DataFrame, pares: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    v = cfg["variaveis"]
    mc = cfg.get("mineracao", {})
    dominancia = float(mc.get("limiar_dominancia", 0.95))
    cons = set(mc.get("consequentes_interesse", []))
    ident = set(v.get("identificadores", []))
    grupos = v.get("grupos_redundancia", [])
    condicionais = v.get("condicionais", {})
    excl = v.get("excluir_sempre", {})
    r = resumo.set_index("variavel")
    linhas = []
    for var, row in r.iterrows():
        for tec in v["tecnicas"]:
            g = _grupo_de(var, grupos)
            nome_g = g["nome"] if g else ""
            if var in ident:
                st, mot = "excluir_identificador", "Identificador, proveniência ou geografia de 5,5 mil níveis."
            elif var in excl:
                st, mot = "excluir_outro", excl[var]
            elif var in condicionais:
                st, mot = "condicional", condicionais[var]
            elif row["tipo_analise"] == "data":
                st, mot = "excluir_data_bruta", "Data bruta: usar as derivadas (ano, mês, faixas)."
            elif g and g["preferida"][tec] != var:
                pref = g["preferida"][tec]
                vv = _lookup_v(pares, var, pref)
                suf = f" (V={vv:.2f} com {pref})" if not np.isnan(vv) else ""
                st, mot = "excluir_redundante", f"{g['motivo']}; nesta técnica usar {pref}{suf}."
            elif pd.notna(row["pct_moda"]) and row["pct_moda"] >= dominancia:
                st, mot = ("excluir_quase_constante",
                           f"Categoria modal com {_fmt_pct(row['pct_moda'], 1)}: regras triviais e cluster sem poder de separação.")
            elif row["tipo_analise"] == "numerica":
                st, mot = "entra_numerica", "Numérica (padronizar); K-Prototypes aceita, Apriori exige faixas."
            elif row.get("alta_cardinalidade") in (True, "True"):
                st, mot = "entra_com_cuidado", f"{int(row['cardinalidade'])} níveis: itens raros; suporte mínimo baixo (Apriori) ou custo alto (K-Modes)."
            else:
                st, mot = "entra", "Categórica de baixa cardinalidade."
            if tec == "apriori" and st == "entra_numerica":
                st, mot = "excluir_redundante", "Numérica sem faixa nesta técnica (Apriori exige itens discretos)."
            linhas.append({"base": cfg["base"], "tecnica": tec, "variavel": var, "status": st,
                           "papel": "consequente_interesse" if var in cons and st in STATUS_ENTRA + ("condicional",) else
                                    ("antecedente" if st in STATUS_ENTRA else ""),
                           "motivo": mot, "grupo_redundancia": nome_g,
                           "cardinalidade": row.get("cardinalidade", np.nan), "pct_moda": row.get("pct_moda", np.nan),
                           "pct_nulos": row.get("pct_nulos", np.nan), "tipo_analise": row["tipo_analise"]})
    return pd.DataFrame(linhas)


def suporte_itens(decisao: pd.DataFrame, dir_frequencias: str | Path, tecnica: str = "apriori") -> pd.DataFrame:
    """Itens (variável=categoria) das variáveis que entram na técnica, com o suporte de cada um."""
    sel = decisao[(decisao["tecnica"] == tecnica)
                  & (decisao["status"].isin(STATUS_ENTRA) | ((decisao["status"] == "condicional")
                                                              & (decisao["tipo_analise"] == "categorica")))]
    partes = []
    for var in sel["variavel"]:
        f = Path(dir_frequencias) / f"{var}.csv"
        if not f.exists():
            continue
        t = ler_csv(f)[["categoria", "n", "pct"]].rename(columns={"categoria": "item", "pct": "suporte"})
        t.insert(0, "variavel", var)
        partes.append(t)
    if not partes:
        return pd.DataFrame(columns=["variavel", "item", "n", "suporte", "eh_nao_informado"])
    out = pd.concat(partes, ignore_index=True)
    out["eh_nao_informado"] = out["item"].astype(str).isin(["NAO_INFORMADO", "NAO_APLICAVEL"])
    return out


def cobertura_suporte(itens: pd.DataFrame, limiares: list[float], dominancia: float = 0.95) -> pd.DataFrame:
    """Quantos itens sobrevivem a cada suporte mínimo (ignora NAO_INFORMADO) e quais são dominantes."""
    d = itens[~itens["eh_nao_informado"]]
    linhas = []
    for var, g in d.groupby("variavel", sort=False):
        linha = {"variavel": var, "n_itens": len(g), "suporte_item_mais_raro": g["suporte"].min(),
                 "n_itens_dominantes": int((g["suporte"] >= dominancia).sum())}
        for lim in limiares:
            linha[f"itens_suporte>={lim:g}"] = int((g["suporte"] >= lim).sum())
        linhas.append(linha)
    tot = {"variavel": "(todas)", "n_itens": len(d), "suporte_item_mais_raro": d["suporte"].min() if len(d) else np.nan,
           "n_itens_dominantes": int((d["suporte"] >= dominancia).sum())}
    for lim in limiares:
        tot[f"itens_suporte>={lim:g}"] = int((d["suporte"] >= lim).sum())
    return pd.DataFrame(linhas + [tot])
