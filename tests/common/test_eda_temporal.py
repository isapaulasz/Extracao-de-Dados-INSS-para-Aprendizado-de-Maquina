import numpy as np
import pandas as pd
import pytest

from inss_acidentes.common.eda_temporal import (
    ParamsTemporal,
    executar_temporal,
    intervalo_wilson,
    mascara_positiva,
    sazonalidade,
    serie_anual,
    serie_categoria,
    serie_mensal,
    taxa_mensal,
    taxa_por_grupo,
)
from inss_acidentes.common.eda_univariada import ConfigEDA


def test_wilson_valor_conhecido_e_casos_extremos():
    lo, hi = intervalo_wilson([50], [100])
    assert lo[0] == pytest.approx(0.4038, abs=1e-3) and hi[0] == pytest.approx(0.5962, abs=1e-3)
    lo0, hi0 = intervalo_wilson([0], [50])
    assert lo0[0] == 0.0 and 0 < hi0[0] < 0.1                  # k = 0 não dá intervalo degenerado
    assert np.isnan(intervalo_wilson([0], [0])[0][0])


def test_mascara_positiva_categoria_bool_e_boolean_com_na():
    cat = pd.Series(["sim", "não", "Sim", None], dtype="category")
    # comparação sem diferenciar maiúsculas: "sim" e "Sim" são o mesmo nível; ausente não é positivo
    assert mascara_positiva(cat, "SIM").tolist() == [True, False, True, False]
    b = pd.Series([True, False, True])
    assert mascara_positiva(b, True).tolist() == [True, False, True]
    nb = pd.Series([True, None, False], dtype="boolean")
    assert mascara_positiva(nb, True).tolist() == [True, False, False]
    assert mascara_positiva(pd.Series(["PENSAO", "OUTRA"], dtype="category"), "pensao").tolist() == [True, False]


def test_serie_mensal_completa_zeros_e_fora_da_janela():
    s = pd.Series(pd.to_datetime(["2011-12-15", "2012-01-10", "2012-01-20", "2012-03-05", "2019-01-01", None]))
    m, info = serie_mensal(s, "2012-01", "2012-04")
    assert m.tolist() == [2, 0, 1, 0] and len(m) == 4
    assert info["n_antes_inicio"] == 1 and info["n_depois_fim"] == 1 and info["n_nulos"] == 1
    assert info["meses_sem_registro"] == 2 and info["n_na_janela"] == 3


def test_serie_anual_e_sazonalidade():
    idx = pd.period_range("2012-01", "2013-12", freq="M")
    m = pd.Series(np.r_[np.full(12, 10), np.full(12, 20)], index=idx)
    a = serie_anual(m)
    assert a["n"].tolist() == [120, 240] and a["var_pct_ano_anterior"].iloc[1] == pytest.approx(100.0)
    s = sazonalidade(m)
    assert len(s) == 12 and s["indice_sazonal"].round(6).eq(1.0).all()


def _df(n=30_000, seed=0):
    rng = np.random.default_rng(seed)
    datas = pd.Timestamp("2012-01-01") + pd.to_timedelta(rng.integers(0, 365 * 7, n), unit="D")
    arq = pd.Series(np.where(datas.year < 2015, "A.parquet", "B.parquet"), dtype="category")
    # taxa de benefício muito menor no arquivo B: artefato de extração
    ben = np.where(arq == "A.parquet", rng.random(n) < 0.20, rng.random(n) < 0.02)
    return pd.DataFrame({
        "data_acidente": datas, "data_emissao_cat": datas + pd.to_timedelta(rng.integers(0, 5, n), unit="D"),
        "tipo_acidente": pd.Series(rng.choice(list("abcdefgh"), n), dtype="category"),
        "houve_obito": rng.random(n) < 0.01, "tem_beneficio": pd.Series(np.where(ben, "sim", "não"), dtype="category"),
        "arquivo_origem": arq,
    })


def test_taxa_mensal_e_por_grupo_detectam_artefato_de_arquivo():
    df = _df()
    t = taxa_mensal(df, "data_emissao_cat", "tem_beneficio", "sim", "2012-01", "2018-12")
    assert len(t) == 84 and t["n"].sum() > 29_000
    assert t["ic_inf"].le(t["taxa"]).all() and t["taxa"].le(t["ic_sup"]).all()
    g = taxa_por_grupo(df, "arquivo_origem", "tem_beneficio", "sim").set_index("grupo")
    assert g.loc["A.parquet", "taxa"] > 5 * g.loc["B.parquet", "taxa"]


def test_serie_categoria_top_n_mais_outras_e_total_confere():
    df = _df()
    t = serie_categoria(df, "data_acidente", "tipo_acidente", "2012-01", "2018-12", top_n=3)
    assert list(t.columns).count("Outras") == 1 and t.shape[1] == 4
    assert int(t.to_numpy().sum()) == int(df["data_acidente"].between("2012-01-01", "2018-12-31").sum())


def test_pipeline_completo_gera_arquivos(tmp_path):
    cfg = ConfigEDA(base="t", entrada=tmp_path / "x", saida_figuras=tmp_path / "f" / "eda" / "univariada",
                    saida_tabelas=tmp_path / "t" / "eda" / "univariada")
    p = ParamsTemporal.from_dict({
        "colunas_data": ["data_acidente", "data_emissao_cat", "coluna_inexistente"],
        "series_categoria": ["tipo_acidente"], "top_n_categorias": 4,
        "taxas_alvo": [{"coluna": "houve_obito", "positiva": True},
                       {"coluna": "tem_beneficio", "positiva": "sim"}],
    })
    assert p.eixo_taxas == "data_acidente"                   # padrão: primeira coluna de data
    res = executar_temporal(_df(), cfg, p, gerar_figuras=True)
    t, f = res["dir_tabelas"], res["dir_figuras"]
    for nome in ["cobertura_temporal.csv", "serie_mensal_data_acidente.csv", "serie_anual_data_emissao_cat.csv",
                 "sazonalidade_data_acidente.csv", "serie_categoria_tipo_acidente_por_data_acidente.csv",
                 "taxa_mensal_houve_obito_True.csv", "taxa_por_arquivo_tem_beneficio_sim.csv"]:
        assert (t / nome).exists(), nome
    for nome in ["serie_mensal_data_acidente.png", "serie_anual_data_acidente.png",
                 "sazonalidade_data_acidente.png", "serie_categoria_tipo_acidente_por_data_acidente.png",
                 "taxa_mensal_houve_obito_True.png", "taxa_por_arquivo_tem_beneficio_sim.png"]:
        assert (f / nome).exists(), nome
    assert len(res["cobertura"]) == 2                         # a coluna inexistente foi ignorada
