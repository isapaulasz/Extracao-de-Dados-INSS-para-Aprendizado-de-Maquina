import numpy as np
import pandas as pd
import pytest

from inss_acidentes.common.eda_territorial import (
    ParamsTerritorial,
    executar_territorial,
    ranking_volume,
    taxa_por_categoria,
)
from inss_acidentes.common.eda_univariada import ConfigEDA


def _df(n=50_000, seed=0):
    rng = np.random.default_rng(seed)
    uf = rng.choice(["SAO PAULO", "PARA", "BAHIA", "NAO_INFORMADO"], n, p=[0.5, 0.2, 0.2, 0.1])
    p_obito = np.select([uf == "PARA", uf == "BAHIA"], [0.03, 0.01], default=0.01)     # PA tem 3× a taxa
    return pd.DataFrame({
        "uf": pd.Series(uf, dtype="category"),
        "regiao": pd.Series(np.select([uf == "SAO PAULO", uf == "NAO_INFORMADO"], ["SUDESTE", "NAO_INFORMADO"],
                                      default="NORTE/NORDESTE"), dtype="category"),
        "houve_obito": rng.random(n) < p_obito,
    })


def test_ranking_volume_ordena_e_marca_nao_informada():
    r = ranking_volume(_df()["uf"])
    assert r["sigla"].iloc[0] == "SP" and r["categoria"].iloc[-1] == "NAO_INFORMADO"
    assert bool(r.iloc[-1]["informada"]) is False and np.isnan(r.iloc[-1]["posicao"])
    assert r["pct_total"].sum() == pytest.approx(1.0)
    inf = r[r["informada"]]
    assert inf["pct_informadas"].sum() == pytest.approx(1.0)
    assert inf["pct_acum_informadas"].iloc[-1] == pytest.approx(1.0)


def test_taxa_por_categoria_ranqueia_uf_de_maior_risco_e_ignora_nao_informada():
    t = taxa_por_categoria(_df(), "uf", "houve_obito", True, min_n=200)
    assert t["sigla"].iloc[0] == "PA" and "NAO_INFORMADO" not in set(t["categoria"])
    assert t["razao_taxa_nacional"].iloc[0] > 1.5
    assert t["pct_dos_positivos"].sum() == pytest.approx(1.0)
    assert (t["ic_inf"] <= t["taxa"]).all() and (t["taxa"] <= t["ic_sup"]).all()


def test_n_suficiente_respeita_minimo():
    t = taxa_por_categoria(_df(n=1_000), "uf", "houve_obito", True, min_n=10_000)
    assert not t["n_suficiente"].any()


def test_pipeline_gera_tabelas_e_figuras_para_uf_e_regiao(tmp_path):
    cfg = ConfigEDA(base="t", entrada=tmp_path / "x", saida_figuras=tmp_path / "f" / "eda" / "univariada",
                    saida_tabelas=tmp_path / "t" / "eda" / "univariada")
    p = ParamsTerritorial.from_dict({"coluna_uf": "uf", "coluna_regiao": "regiao", "min_n_taxa": 100,
                                     "taxas_alvo": [{"coluna": "houve_obito", "positiva": True}]})
    res = executar_territorial(_df(), cfg, p, gerar_figuras=True)
    t, f = res["dir_tabelas"], res["dir_figuras"]
    for nome in ["ranking_uf.csv", "ranking_regiao.csv", "taxa_uf_houve_obito_True.csv",
                 "taxa_regiao_houve_obito_True.csv"]:
        assert (t / nome).exists(), nome
    for nome in ["ranking_uf.png", "taxa_uf_houve_obito_True.png"]:
        assert (f / nome).exists(), nome


def test_coluna_inexistente_nao_quebra(tmp_path):
    cfg = ConfigEDA(base="t", entrada=tmp_path / "x", saida_figuras=tmp_path / "f" / "eda" / "univariada",
                    saida_tabelas=tmp_path / "t" / "eda" / "univariada")
    res = executar_territorial(_df(), cfg, ParamsTerritorial(coluna_uf="uf_x"), gerar_figuras=False)
    assert res["volumes"] == {}
