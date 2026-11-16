import numpy as np
import pandas as pd
import pytest

from inss_acidentes.common.eda_bivariada import codificar
from inss_acidentes.common.eda_informacao_mutua import (
    ParamsMI,
    executar_mi,
    informacao_mutua,
    proibidas_do_mapa,
    ranking_mi,
    resumo_alvo,
)
from inss_acidentes.common.eda_univariada import ConfigEDA, preparar_dataframe


def _tab(x, y):
    cx, cy = codificar(pd.Series(x, dtype="category")), codificar(pd.Series(y, dtype="category"))
    from inss_acidentes.common.eda_bivariada import contingencia
    return contingencia(cx, cy)


def test_mi_conhecida_2x2_perfeita():
    r = informacao_mutua(np.array([[50, 0], [0, 50]]))
    assert r["mi_bits"] == pytest.approx(1.0)
    assert r["h_alvo_bits"] == pytest.approx(1.0)
    assert r["u_alvo"] == pytest.approx(1.0, abs=0.01)


def test_mi_independentes_proxima_de_zero_apos_correcao():
    rng = np.random.default_rng(0)
    r = informacao_mutua(_tab(rng.choice(list("abcdefgh"), 5_000), rng.choice(list("xyz"), 5_000)))
    assert r["u_alvo"] < 0.005
    assert r["mi_bits"] > r["mi_corrigida_bits"]      # a correção tira o viés positivo da MI bruta


def test_mi_assimetria_derivada():
    # y é função de x (hierarquia): conhecer x elimina toda a incerteza de y, mas não o contrário
    x = np.random.default_rng(1).choice([f"c{i}" for i in range(12)], 30_000)
    y = np.where(pd.Series(x).str[1:].astype(int) < 6, "g1", "g2")
    r_y = informacao_mutua(_tab(x, y))      # alvo = y
    r_x = informacao_mutua(_tab(y, x))      # alvo = x
    assert r_y["u_alvo"] > 0.99
    assert r_x["u_alvo"] < 0.35


def test_mi_tabela_degenerada():
    assert np.isnan(informacao_mutua(np.array([[10, 20]]))["u_alvo"])


def test_alvo_raro_u_comparavel_apesar_de_mi_pequena():
    rng = np.random.default_rng(2)
    n = 200_000
    x = rng.choice(["a", "b"], n)
    y = np.where((x == "a") & (rng.random(n) < 0.02), "sim", "nao")      # alvo raro, ligado a x
    r = informacao_mutua(_tab(x, y))
    assert r["mi_bits"] < 0.01 and r["u_alvo"] > 0.05      # MI absoluta pequena, mas u_alvo enxerga o sinal


def test_resumo_alvo_raro_recomenda_metricas_certas():
    s = pd.Series(["nao"] * 9953 + ["sim"] * 47, dtype="category")
    r = resumo_alvo(s, "obito")
    assert r["nivel"] == "muito desbalanceado" and "PR-AUC" in r["metricas_sugeridas"]
    assert r["razao_maj_min"] == pytest.approx(9953 / 47) and r["n_minoritaria_no_teste_20pct"] == 9
    assert resumo_alvo(pd.Series(["a", "b"] * 50), "t")["nivel"] == "moderado/balanceado"
    assert resumo_alvo(pd.Series(["a"] * 10), "t")["nivel"] == "alvo constante"


def test_proibidas_do_mapa_junta_sempre_alvo_e_proibidas():
    cfg = {"vazamento": {"sempre": ["arq"], "obito": {"alvo": "houve_obito", "proibidas": ["tipo_cat"]}}}
    out = proibidas_do_mapa(cfg, {"houve_obito": "obito", "x": "inexistente"})
    assert out == {"houve_obito": ["arq", "houve_obito", "tipo_cat"]}


def _dados(n=40_000, seed=3):
    rng = np.random.default_rng(seed)
    tipo = rng.choice(["1-INICIAL", "2-REABERTURA", "3-COMUNICACAO DE OBITO"], n, p=[0.97, 0.02, 0.01])
    cap = rng.choice(["I", "II", "III"], n)
    obito = np.where(tipo == "3-COMUNICACAO DE OBITO", True,
                     (cap == "III") & (rng.random(n) < 0.03))             # óbito raro, ligado ao capítulo
    ramo = np.where(rng.random(n) < 0.15, "COMERCIARIO", "NAO_INFORMADO")  # preenchido só com benefício
    return pd.DataFrame({
        "tipo_cat": tipo, "capitulo": cap, "sexo": rng.choice(["M", "F"], n),
        "ramo": ramo, "houve_obito": obito,
        "data_despacho": pd.Series(np.where(ramo == "NAO_INFORMADO", pd.NaT, pd.Timestamp("2015-01-01")),
                                   dtype="datetime64[ns]"),
    })


def _cfg(tmp_path):
    return ConfigEDA(base="t", entrada=tmp_path / "x", saida_figuras=tmp_path / "f" / "eda" / "univariada",
                     saida_tabelas=tmp_path / "t" / "eda" / "univariada", candidatos_alvo=["houve_obito"],
                     bivariada={"indicadores_presenca": {"tem_data_despacho": "data_despacho"}})


def test_ranking_marca_vazamento_e_ordena_por_u(tmp_path):
    cfg = _cfg(tmp_path)
    df = preparar_dataframe(_dados(), cfg)
    p = ParamsMI.from_dict({"proibidas_extra": {"houve_obito": ["ramo"]}},
                           proibidas={"houve_obito": ["tipo_cat"]})
    res = executar_mi(df, cfg, p, gerar_figuras=False)
    r = res["rankings"][("completo", "houve_obito")].set_index("variavel")
    assert r.loc["tipo_cat", "no_mapa_vazamento"] and r.loc["ramo", "no_mapa_vazamento"]
    assert r.loc["tipo_cat", "recomendacao"].startswith("excluir")
    assert r.loc["capitulo", "recomendacao"] == "candidata"
    assert r.loc["capitulo", "u_alvo"] > r.loc["sexo", "u_alvo"]
    assert r.loc["sexo", "recomendacao"] == "pouco informativa isoladamente"
    assert list(r["posicao_mi"]) == sorted(r["posicao_mi"])
    d = res["desbalanceamento"].iloc[0]
    assert d["pct_minoritaria"] < 0.05 and "PR-AUC" in d["metricas_sugeridas"]


def test_recorte_tipo1_remove_efeito_do_tipo_cat(tmp_path):
    cfg = _cfg(tmp_path)
    df = preparar_dataframe(_dados(), cfg)
    p = ParamsMI.from_dict({"recortes": [{"nome": "tipo1", "alvo": "houve_obito",
                                          "query": "tipo_cat == '1-INICIAL'"}]})
    res = executar_mi(df, cfg, p, gerar_figuras=True)
    cheio = res["rankings"][("completo", "houve_obito")].set_index("variavel")
    corte = res["rankings"][("tipo1", "houve_obito")].set_index("variavel")
    assert cheio.loc["tipo_cat", "u_alvo"] > 0.3              # no conjunto todo, tipo_cat quase explica o alvo
    assert "tipo_cat" not in corte.index                       # no recorte vira nível único e sai
    assert corte.loc["capitulo", "u_alvo"] > 0
    t = res["dir_tabelas"]
    for f in ("mi_alvo_houve_obito.csv", "mi_alvo_houve_obito__tipo1.csv", "desbalanceamento_alvos.csv",
              "mi_todos_alvos.csv", "mi_matriz_u_alvo.csv"):
        assert (t / f).exists(), f
    assert (res["dir_figuras"] / "mi_alvo_houve_obito.png").exists()
    assert set(res["desbalanceamento"]["cenario"]) == {"completo", "tipo1"}


def test_ranking_exclui_o_proprio_alvo():
    df = _dados()
    cod = {c: codificar(df[c].astype("category")) for c in ["tipo_cat", "capitulo", "houve_obito"]}
    r = ranking_mi(cod, "houve_obito", ParamsMI())
    assert "houve_obito" not in set(r["variavel"])
