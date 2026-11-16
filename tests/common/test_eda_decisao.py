import numpy as np
import pandas as pd
import pytest

from inss_acidentes.common.eda_decisao import (
    cobertura_suporte,
    decidir_variaveis,
    decidir_variaveis_mineracao,
    heterogeneidade,
    matriz_variaveis,
    nivel_desbalanceamento,
    plano_desbalanceamento,
    suporte_itens,
)

LIM = {"muito": 0.01, "forte": 0.10}


def _alvos():
    base = dict(n_classes_lt_1pct=0, n_minoritaria_no_teste_20pct=0, entropia_bits=0, entropia_normalizada=0,
                nivel="", metricas_sugeridas="")
    return pd.DataFrame([
        dict(alvo="obito", cenario="completo", n_validos=1_000_000, n_classes=2, classe_majoritaria="False",
             pct_majoritaria=0.995, classe_minoritaria="True", n_minoritaria=5_000, pct_minoritaria=0.005,
             razao_maj_min=199.0, **base),
        dict(alvo="afast", cenario="completo", n_validos=1_000_000, n_classes=2, classe_majoritaria="True",
             pct_majoritaria=0.80, classe_minoritaria="False", n_minoritaria=200_000, pct_minoritaria=0.20,
             razao_maj_min=4.0, **base),
        dict(alvo="tipo", cenario="completo", n_validos=1_000_000, n_classes=4, classe_majoritaria="A",
             pct_majoritaria=0.7, classe_minoritaria="D", n_minoritaria=2_000, pct_minoritaria=0.002,
             razao_maj_min=350.0, **base),
    ])


def _cfg_plano():
    return {
        "base": "cat", "split": {"teste_pct": 0.2, "k_dobras": 5}, "limiares_desbalanceamento": LIM,
        "alvos": {"obito": {"papel": "primario", "cenario": "completo", "classe_positiva": "True"},
                  "afast": {"papel": "secundario", "cenario": "completo", "classe_positiva": "True"},
                  "tipo": {"papel": "reserva", "cenario": "completo"}},
        "estrategia_muito_desbalanceado": {"arvore": "pesos"}, "estrategia_balanceado": {"arvore": "sem pesos"},
        "metricas": {"muito_desbalanceado": {"primaria": "PR-AUC", "proibidas": ["acurácia"]},
                     "balanceado": {"primaria": "ROC-AUC"}, "multiclasse": {"primaria": "F1 macro"}},
        "variaveis": {"tecnicas": ["arvore"]},
    }


def test_nivel():
    assert nivel_desbalanceamento(0.004, 2, LIM) == "muito desbalanceado"
    assert nivel_desbalanceamento(0.05, 2, LIM) == "desbalanceado"
    assert nivel_desbalanceamento(0.2, 2, LIM) == "moderado/balanceado"
    assert nivel_desbalanceamento(0.001, 5, LIM) == "multiclasse"


def test_plano_alvo_raro_usa_pr_auc_e_baseline_e_a_prevalencia():
    p = plano_desbalanceamento(_alvos(), _cfg_plano()).set_index("alvo")
    o = p.loc["obito"]
    assert o["metrica_primaria"] == "PR-AUC" and o["pr_auc_baseline"] == pytest.approx(0.005)
    assert o["acuracia_classificador_trivial"] == pytest.approx(0.995)
    assert o["n_positivos"] == 5_000 and o["n_pos_teste"] == 1_000 and o["n_pos_treino"] == 4_000
    assert o["n_pos_por_dobra_validacao"] == 800
    assert o["peso_balanced_positiva"] == pytest.approx(100.0)
    assert o["estrategia_arvore"] == "pesos"


def test_plano_classe_positiva_e_a_majoritaria_no_afastamento():
    a = plano_desbalanceamento(_alvos(), _cfg_plano()).set_index("alvo").loc["afast"]
    assert a["prevalencia_positiva"] == pytest.approx(0.80)       # positiva = majoritária
    assert a["metrica_primaria"] == "ROC-AUC" and a["n_positivos"] == 800_000


def test_plano_multiclasse_sem_campos_binarios_e_papel_ordenado():
    p = plano_desbalanceamento(_alvos(), _cfg_plano())
    assert list(p["papel"]) == ["primario", "secundario", "reserva"]
    t = p.set_index("alvo").loc["tipo"]
    assert t["metrica_primaria"] == "F1 macro" and pd.isna(t["pr_auc_baseline"])


def _tab(taxas, n=100_000, rot="grupo"):
    return pd.DataFrame({rot: [f"g{i}" for i in range(len(taxas))], "n": n,
                         "n_pos": [int(round(t * n)) for t in taxas]})


def test_heterogeneidade_estavel_e_heterogenea():
    est = heterogeneidade(_tab([0.0050, 0.0049, 0.0051, 0.0050]), limiar_razao=1.25)
    assert est["leitura"] == "estavel" and est["razao_max_min"] < 1.1
    het = heterogeneidade(_tab([0.01, 0.002, 0.005]), limiar_razao=1.25)
    assert het["leitura"] == "heterogenea" and het["grupo_max"] == "g0" and het["grupo_min"] == "g1"
    assert het["v_cramer"] > est["v_cramer"]


def test_heterogeneidade_tendencia_de_cochran_armitage():
    r = heterogeneidade(_tab([0.0060, 0.0055, 0.0050, 0.0045, 0.0040], n=400_000), ordenado=True)
    assert r["tendencia_sentido"] == "decrescente" and r["tendencia_z"] < -5
    plana = heterogeneidade(_tab([0.005] * 5), ordenado=True)
    assert abs(plana["tendencia_z"]) < 1e-6


def test_heterogeneidade_filtra_grupos_pequenos_territoriais():
    t = _tab([0.005, 0.005, 0.05])
    t["informada"], t["n_suficiente"] = True, [True, True, False]
    assert heterogeneidade(t)["n_grupos"] == 2


# ---- variáveis por técnica (CAT) ------------------------------------------------------------------ #
def _mi():
    def linha(v, k, u, rec, leak=False, quase=False):
        return dict(cenario="rec", alvo="obito", variavel=v, k_x=k, u_alvo=u, nmi=u, v_corrigido=u,
                    posicao_mi=1, no_mapa_vazamento=leak, quase_determina_alvo=quase, recomendacao=rec)
    return pd.DataFrame([
        linha("indica_obito_acidente", 2, 1.0, "excluir: no mapa de vazamento", True, True),
        linha("cid_cat", 97, 0.01, "candidata"),
        linha("cid_cap", 16, 0.02, "candidata"),
        linha("sexo", 3, 0.003, "candidata"),
        linha("mes", 12, 0.0001, "pouco informativa isoladamente"),
        linha("atraso", 6, 0.0001, "excluir: no mapa de vazamento", True),
        linha("ano", 7, 0.0, "pouco informativa isoladamente"),
        linha("indica_afast", 2, 0.03, "candidata"),
    ])


def _cfg_var():
    return {
        "base": "cat",
        "alvos": {"obito": {"papel": "primario", "cenario": "rec"}},
        "variaveis": {
            "tecnicas": ["arvore", "random_forest"], "max_niveis_arvore": 50, "identificadores": ["arquivo_origem"],
            "excluir_sempre": {"ano": "deriva"},
            "excluir_extra": {"obito": {"indica_afast": "consequência do óbito"}},
            "liberar_no_recorte": {"rec": {"atraso": "só denunciava o tipo"}},
            "grupos_redundancia": [{"nome": "cid", "motivo": "hierarquia", "membros": ["cid_cap", "cid_cat"],
                                    "preferida": {"arvore": "cid_cap", "random_forest": "cid_cat"}}],
        },
    }


def _st(dec, var, tec):
    return dec[(dec["variavel"] == var) & (dec["tecnica"] == tec)].iloc[0]


def test_decisao_regras_em_ordem():
    d = decidir_variaveis(_mi(), None, None, _cfg_var())
    assert _st(d, "arquivo_origem", "arvore")["status"] == "excluir_identificador"
    assert _st(d, "indica_obito_acidente", "arvore")["status"] == "excluir_vazamento"
    assert _st(d, "indica_afast", "arvore")["status"] == "excluir_vazamento"        # exclusão extra por alvo
    assert _st(d, "ano", "arvore")["status"] == "excluir_outro"
    assert _st(d, "sexo", "arvore")["status"] == "entra"


def test_decisao_hierarquia_um_nivel_por_tecnica_e_cardinalidade():
    d = decidir_variaveis(_mi(), None, None, _cfg_var())
    assert _st(d, "cid_cap", "arvore")["status"] == "entra"
    assert _st(d, "cid_cat", "arvore")["status"] == "excluir_redundante"
    assert _st(d, "cid_cat", "random_forest")["status"] == "entra_com_cuidado"      # 97 níveis
    assert _st(d, "cid_cap", "random_forest")["status"] == "excluir_redundante"


def test_decisao_baixa_informacao_sai_da_arvore_mas_fica_opcional_em_rf():
    d = decidir_variaveis(_mi(), None, None, _cfg_var())
    assert _st(d, "mes", "arvore")["status"] == "excluir_baixa_informacao"
    assert _st(d, "mes", "random_forest")["status"] == "entra_opcional"


def test_decisao_variavel_liberada_no_recorte_e_avaliada_pelo_u_alvo():
    d = decidir_variaveis(_mi(), None, None, _cfg_var())
    # u_alvo = 0.0001 < limiar (0.001): sai do mapa, mas continua fraca
    assert _st(d, "atraso", "arvore")["status"] == "excluir_baixa_informacao"
    assert _st(d, "atraso", "random_forest")["status"] == "entra_opcional"


def test_matriz_conta_combinacoes():
    m = matriz_variaveis(decidir_variaveis(_mi(), None, None, _cfg_var())).set_index("variavel")
    assert m.loc["sexo", "n_combinacoes_que_usam"] == 2
    assert m.loc["indica_obito_acidente", "n_combinacoes_que_usam"] == 0


# ---- mineração (BC) -------------------------------------------------------------------------------- #
def _resumo_bc():
    def l(v, tipo="categorica", moda=0.5, card=3, alta=False):
        return dict(variavel=v, tipo_analise=tipo, pct_moda=moda, cardinalidade=card, pct_nulos=0.0,
                    alta_cardinalidade=alta)
    return pd.DataFrame([l("sexo"), l("constante", moda=0.999), l("idade", "numerica", np.nan, np.nan),
                         l("faixa_etaria"), l("data_dib", "data", np.nan, np.nan), l("cid", card=79, alta=True),
                         l("dcb_faixa", moda=0.4), l("arquivo_origem")])


def _cfg_bc():
    return {"base": "bc", "mineracao": {"limiar_dominancia": 0.95, "consequentes_interesse": ["sexo"]},
            "variaveis": {"tecnicas": ["apriori", "clusterizacao"], "identificadores": ["arquivo_origem"],
                          "excluir_sempre": {}, "condicionais": {"dcb_faixa": "DCB pendente"},
                          "grupos_redundancia": [{"nome": "idade", "motivo": "discretização",
                                                  "membros": ["faixa_etaria", "idade"],
                                                  "preferida": {"apriori": "faixa_etaria", "clusterizacao": "idade"}}]}}


def test_mineracao_numerica_vira_faixa_no_apriori_e_numerica_no_cluster():
    d = decidir_variaveis_mineracao(_resumo_bc(), None, _cfg_bc())
    s = lambda v, t: d[(d.variavel == v) & (d.tecnica == t)].iloc[0]["status"]       # noqa: E731
    assert s("faixa_etaria", "apriori") == "entra" and s("idade", "apriori") == "excluir_redundante"
    assert s("idade", "clusterizacao") == "entra_numerica" and s("faixa_etaria", "clusterizacao") == "excluir_redundante"
    assert s("constante", "apriori") == "excluir_quase_constante"
    assert s("data_dib", "apriori") == "excluir_data_bruta"
    assert s("dcb_faixa", "apriori") == "condicional"
    assert s("cid", "apriori") == "entra_com_cuidado"
    assert s("arquivo_origem", "apriori") == "excluir_identificador"
    assert d[(d.variavel == "sexo") & (d.tecnica == "apriori")].iloc[0]["papel"] == "consequente_interesse"


def test_suporte_itens_e_cobertura(tmp_path):
    for v, linhas in {"sexo": [("M", 700, 0.7), ("F", 300, 0.3)],
                      "cid": [("A", 600, 0.6), ("B", 3, 0.003), ("NAO_INFORMADO", 397, 0.397)]}.items():
        pd.DataFrame(linhas, columns=["categoria", "n", "pct"]).rename_axis("rank").reset_index().to_csv(tmp_path / f"{v}.csv", index=False)
    dec = pd.DataFrame({"tecnica": ["apriori"] * 3, "variavel": ["sexo", "cid", "idade"],
                        "status": ["entra", "entra_com_cuidado", "excluir_redundante"],
                        "tipo_analise": ["categorica"] * 3})
    itens = suporte_itens(dec, tmp_path)
    assert set(itens["variavel"]) == {"sexo", "cid"}
    cob = cobertura_suporte(itens, [0.01, 0.5]).set_index("variavel")
    assert cob.loc["cid", "n_itens"] == 2                       # NAO_INFORMADO não conta como item
    assert cob.loc["cid", "itens_suporte>=0.01"] == 1 and cob.loc["(todas)", "itens_suporte>=0.5"] == 2
