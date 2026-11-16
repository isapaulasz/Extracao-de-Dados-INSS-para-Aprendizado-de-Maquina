import numpy as np
import pandas as pd
import pytest

from inss_acidentes.common.eda_bivariada import (
    ParamsBivariada,
    adicionar_derivadas,
    codificar,
    contingencia,
    executar_bivariada,
    preparar_bivariada,
    qui2_cramer,
)
from inss_acidentes.common.eda_univariada import ConfigEDA, _para_datetime, preparar_dataframe


def _v(a, b):
    ca, cb = codificar(pd.Series(a, dtype="category")), codificar(pd.Series(b, dtype="category"))
    return qui2_cramer(contingencia(ca, cb))


def test_independentes_v_corrigido_proximo_de_zero():
    rng = np.random.default_rng(0)
    r = _v(rng.choice(list("abcde"), 50_000), rng.choice(list("xyz"), 50_000))
    assert r["v_corrigido"] < 0.02


def test_variaveis_identicas_v_um():
    x = np.random.default_rng(1).choice(list("abcd"), 5_000)
    assert _v(x, x)["v_corrigido"] == pytest.approx(1.0, abs=0.01)


def test_hierarquia_detectada():
    fino = np.random.default_rng(2).choice([f"c{i}" for i in range(12)], 20_000)
    grosso = np.where(pd.Series(fino).str[1:].astype(int) < 6, "g1", "g2")   # derivada do fino
    assert _v(fino, grosso)["v_corrigido"] > 0.99


def test_correcao_reduz_v_em_amostra_pequena():
    rng = np.random.default_rng(3)
    r = _v(rng.choice(list("abcdefghij"), 200), rng.choice(list("klmnopqrst"), 200))
    assert r["v_corrigido"] < r["v_bruto"]


def test_nulos_pairwise():
    a = pd.Series(["x", "y", None, "x", "y", "x"] * 100, dtype="category")
    b = pd.Series(["u", "v", "u", None, "v", "u"] * 100, dtype="category")
    tab = contingencia(codificar(a), codificar(b))
    assert tab.sum() == 400          # só linhas com ambos preenchidos


def test_codificar_agrupa_raros_e_barra_cardinalidade():
    s = pd.Series(["a"] * 5000 + ["b"] * 4990 + ["r1", "r2", "r3"] * 3, dtype="category")
    c = codificar(s, min_pct=0.01)
    assert c.k == 3 and c.n_agrupados == 3 and c.rotulos[-1].startswith("Outras")
    assert codificar(pd.Series(list(range(500)), dtype="category"), min_pct=0, max_niveis=100).codigos is None


def test_derivadas_faixas_e_presenca():
    df = pd.DataFrame({"idade": [15, 25, 35, 65, None], "d": pd.to_datetime(["2020-01-01", None, None, "2020-03-01", None])})
    p = ParamsBivariada(discretizar={"idade": {"bordas": [18, 30, 60], "rotulos": ["<18", "18-29", "30-59", "60+"]}},
                        indicadores_presenca={"tem_d": "d"})
    out = adicionar_derivadas(df, p)
    assert out["idade_faixa"].astype(str).tolist()[:4] == ["<18", "18-29", "30-59", "60+"]
    assert out["tem_d"].astype(str).tolist() == ["sim", "não", "não", "sim", "não"]
    assert "idade_faixa" not in df.columns          # original intacto
    with pytest.raises(ValueError):
        adicionar_derivadas(df, ParamsBivariada(discretizar={"idade": {"bordas": [18, 30], "rotulos": ["a"]}}))


def test_conversao_datas_e_periodos():
    iso = pd.Series(["2018-05-03", None, "2019-01-31"] * 2, dtype="category")
    br = pd.Series(["03/05/2018", "31/01/2019", None] * 2, dtype="category")
    o1, f1 = _para_datetime(iso)
    o2, f2 = _para_datetime(br)
    assert f1 == f2 == 1.0 and o1.iloc[0] == pd.Timestamp("2018-05-03") and o2.iloc[0] == o1.iloc[0]
    per, f3 = _para_datetime(pd.Series([201801, 201812], dtype="int32"), periodo=True)
    assert per.iloc[1] == pd.Timestamp("2018-12-01") and f3 == 1.0


def test_pipeline_completo(tmp_path):
    rng = np.random.default_rng(4)
    n = 20_000
    cid = rng.choice([f"S{i}" for i in range(30)], n)
    df = pd.DataFrame({
        "id_x": np.arange(n),
        "cid": cid,
        "cap": np.where(pd.Series(cid).str[1:].astype(int) < 15, "I", "II"),   # derivada de cid
        "sexo": rng.choice(["M", "F"], n),
        "uf": rng.choice(["PA", "SP", "MG"], n),
        "obito": rng.choice(["sim", "nao"], n, p=[0.02, 0.98]),
        "idade": rng.integers(14, 80, n).astype(float),
        "data_acidente": pd.date_range("2020-01-01", periods=n, freq="h"),
        "data_obito": pd.Series([pd.NaT] * n),
        "competencia_x": rng.choice([202001, 202002, 202003], n),
        "texto_livre": [f"t{i}" for i in range(n)],
    })
    df.loc[df["obito"] == "sim", "data_obito"] = pd.Timestamp("2020-06-01")
    cfg = ConfigEDA(base="t", entrada=tmp_path / "x", saida_figuras=tmp_path / "f" / "eda" / "univariada",
                    saida_tabelas=tmp_path / "t" / "eda" / "univariada", candidatos_alvo=["obito"],
                    excluir=["texto_livre"],
                    bivariada={"excluir": ["cid"], "discretizar": {"idade": {"bordas": [18, 40, 60]}},
                               "indicadores_presenca": {"tem_data_obito": "data_obito"}})
    df = preparar_dataframe(df, cfg)
    assert str(df["competencia_x"].dtype).startswith("datetime")
    res = executar_bivariada(df, cfg)
    nao = res["nao_avaliadas"].set_index("variavel")["motivo"]
    assert "texto_livre" in nao.index and "cid" in nao.index and nao["idade"] == "substituida_por_faixa"
    rank = res["rankings"]["obito"].set_index("variavel")
    assert rank.loc["tem_data_obito", "possivel_vazamento_ou_derivada"]       # vazamento detectado
    assert rank.loc["sexo", "v_corrigido"] < 0.05
    assert (res["dir_tabelas"] / "cramer_v_matriz.csv").exists()
    assert (res["dir_figuras"] / "heatmap_cramer_v.png").exists()
    assert (res["dir_figuras"] / "associacao_alvo_obito.png").exists()


def test_teto_de_niveis_vale_para_cardinalidade_original():
    # 400 níveis, todos raros: antes de agrupar já passa do teto -> barrada (não vira "nível único")
    s = pd.Series(np.random.default_rng(5).integers(0, 400, 100_000), dtype="category")
    c = codificar(s, min_pct=0.01, max_niveis=250)
    assert c.codigos is None and c.k == 400
