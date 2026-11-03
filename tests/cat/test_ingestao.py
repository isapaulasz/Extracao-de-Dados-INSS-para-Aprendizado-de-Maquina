import pytest
from inss_acidentes.cat.ingestao import _norm, _mapa_inverso, _meta_arquivo


def test_norm():
    assert _norm("Agente  Causador  Acidente") == "agente_causador_acidente"
    assert _norm("Competência de Emissão da CAT") == "competencia_de_emissao_da_cat"
    assert _norm("CID-10_2") == "cid_10_2"


def test_alias_ambiguo():
    with pytest.raises(ValueError):
        _mapa_inverso({"colunas": {"a": ["X"], "b": ["x"]}})


def test_meta_arquivo():
    assert _meta_arquivo("03.2013_01-06_CATS.parquet") == (2013, 1)
    assert _meta_arquivo("14.2018_07-12_CATS.parquet") == (2018, 2)
    with pytest.raises(ValueError):
        _meta_arquivo("qualquer.parquet")