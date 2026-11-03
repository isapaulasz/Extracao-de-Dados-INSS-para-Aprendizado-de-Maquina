import duckdb
import pytest

from inss_acidentes.common import hierarquias as H
from inss_acidentes.common.texto import normalizar_py, tokens_normalizados


def q(expr: str, valor):
    return duckdb.connect().execute(f"SELECT {expr} FROM (SELECT ?::VARCHAR AS x)", [valor]).fetchone()[0]


@pytest.mark.parametrize("cid,cap", [
    ("A00", "I"), ("B99", "I"), ("C50", "II"), ("D49", None), ("D50", "III"), ("H59", "VII"),
    ("H60", "VIII"), ("M54", "XIII"), ("S61", "XIX"), ("T14", "XIX"), ("V89", "XX"),
    ("Y98", "XX"), ("Z00", "XXI"), ("U07", "XXII"),
])
def test_capitulo_cid(cid, cap):
    assert q(H.capitulo_cid("x"), cid + "5") == cap  # 4 caracteres também funciona


def test_capitulo_cid_nulo():
    assert q(H.capitulo_cid("x"), None) is None


def test_cbo():
    assert q(H.cbo_grande_grupo("x"), "784205") == "7"
    assert q(H.cbo_grande_grupo("x"), "031210") == "0"
    assert q(H.cbo_subgrupo("x"), "784205") == "78"


@pytest.mark.parametrize("cnae,secao", [("0113", "A"), ("0510", "B"), ("1051", "C"), ("3511", "D"),
                                         ("4711", "G"), ("8610", "Q"), ("9700", "T"), ("9900", "U")])
def test_cnae_secao(cnae, secao):
    assert q(H.cnae_secao("x"), cnae) == secao
    assert q(H.cnae_divisao("x"), cnae) == cnae[:2]


def test_texto_py_e_sql_coincidem():
    from inss_acidentes.common.texto import norm_sql
    for s in ["  Não  Informado ", "Auxílio-Doença", "{ñ class}"]:
        assert q(norm_sql("x"), s) == normalizar_py(s)
    assert "NAO INFORMADO" in tokens_normalizados(["Não informado"])
