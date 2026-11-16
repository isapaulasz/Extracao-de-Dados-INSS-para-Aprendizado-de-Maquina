import numpy as np
import pandas as pd
import pytest

from inss_acidentes.common.eda_univariada import (
    ConfigEDA,
    classificar_colunas,
    estatisticas_numericas,
    executar_univariada,
    frequencias,
    n_categorias_para_limiar,
    plotar_histograma,
    plotar_pareto,
    preparar_dataframe,
)


@pytest.fixture
def cfg(tmp_path):
    return ConfigEDA(base="teste", entrada=tmp_path / "x.parquet",
                     saida_figuras=tmp_path / "fig", saida_tabelas=tmp_path / "tab")


@pytest.fixture
def df():
    rng = np.random.default_rng(0)
    n = 1000
    return pd.DataFrame({
        "id_cat": np.arange(n),
        "sexo": rng.choice(["M", "F"], n, p=[0.8, 0.2]),
        "obito": rng.choice([0, 1], n, p=[0.99, 0.01]),
        "cid": rng.choice([f"S{i:02d}" for i in range(80)], n),
        "idade": rng.integers(16, 70, n),
        "data": pd.date_range("2020-01-01", periods=n, freq="D"),
        "uf": pd.Series(rng.choice(["PA", "SP", None], n, p=[0.4, 0.4, 0.2])),
    })


def test_frequencias_soma_100_e_ordenada():
    f = frequencias(pd.Series(["a"] * 6 + ["b"] * 3 + ["c"] + [None] * 5))
    assert list(f["categoria"]) == ["a", "b", "c"]
    assert f["n"].sum() == 10                       # ausentes fora da base
    assert f["pct_acum"].iloc[-1] == pytest.approx(1.0)


def test_frequencias_ignora_categorias_sem_ocorrencia():
    s = pd.Series(["a", "b"], dtype="category").cat.add_categories("z")
    assert "z" not in frequencias(s)["categoria"].tolist()


def test_n_categorias_para_limiar():
    f = frequencias(pd.Series(["a"] * 50 + ["b"] * 30 + ["c"] * 20))
    assert n_categorias_para_limiar(f, 0.80) == 2
    assert n_categorias_para_limiar(f, 0.50) == 1


def test_classificar_colunas(df, cfg):
    t = classificar_colunas(preparar_dataframe(df), cfg)
    assert "id_cat" in t["excluidas"]
    assert "obito" in t["categoricas"]              # numérica com 2 valores
    assert "idade" in t["numericas"]
    assert "data" in t["datas"]
    assert {"sexo", "cid", "uf"} <= set(t["categoricas"])


def test_override_do_yaml(df, cfg):
    cfg.categoricas = ["idade"]
    t = classificar_colunas(preparar_dataframe(df), cfg)
    assert "idade" in t["categoricas"] and "idade" not in t["numericas"]


def test_estatisticas_numericas():
    e = estatisticas_numericas(pd.Series([1, 2, 3, 4, 100, None]))
    assert e["n"] == 6 and e["nulos"] == 1
    assert e["mediana"] == 3 and e["n_outliers_iqr"] == 1


def test_graficos_retornam_figura(df):
    f = frequencias(df["cid"])
    assert plotar_pareto(f, "t", top_n=10) is not None
    assert plotar_histograma(df["idade"], "t") is not None
    assert plotar_pareto(frequencias(pd.Series([], dtype="object")), "t") is None


def test_pipeline_gera_arquivos(df, cfg):
    res = executar_univariada(preparar_dataframe(df), cfg)
    assert (cfg.saida_tabelas / "resumo_variaveis.csv").exists()
    assert (cfg.saida_tabelas / "pareto_cobertura.csv").exists()
    assert (cfg.saida_figuras / "pareto_cid.png").exists()
    assert (cfg.saida_figuras / "hist_idade.png").exists()
    r = res["resumo"].set_index("variavel")
    assert bool(r.loc["cid", "alta_cardinalidade"])
    assert r.loc["uf", "pct_nulos"] == pytest.approx(0.2, abs=0.05)


def test_histograma_usa_media_dos_dados_completos():
    fig = plotar_histograma(pd.Series([1] * 99 + [1000]), "t", recortar_percentil=99)
    rotulos = [txt.get_text() for txt in fig.axes[0].get_legend().get_texts()]
    assert any("11.0" in r for r in rotulos)        # média = 10,99 (não 1,0 dos dados recortados)
