import datetime as dt
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from inss_acidentes.cat import features as F
from inss_acidentes.common import io as IO
from inss_acidentes.common import logging as LG

REPO = Path(__file__).resolve().parents[2]
CFG = yaml.safe_load((REPO / "configs/cat/features.yaml").read_text(encoding="utf-8"))


def q(expr: str, valor):
    return duckdb.connect().execute(f"SELECT {expr} FROM (SELECT ?::INTEGER AS x)", [valor]).fetchone()[0]


def test_faixa_etaria():
    sql = F._faixas("x", CFG["faixas_etarias"], F.DESCONHECIDA)
    for idade, esperado in [(14, "14-17"), (17, "14-17"), (18, "18-24"), (34, "25-34"),
                            (64, "55-64"), (65, "65+"), (90, "65+"), (None, "DESCONHECIDA")]:
        assert q(sql, idade) == esperado


def test_faixa_atraso():
    sql = F._faixas("x", CFG["atraso"]["faixas"], "NAO_INFORMADO")
    for d, esperado in [(0, "0"), (1, "1"), (2, "2-7"), (7, "2-7"), (8, "8-30"), (30, "8-30"),
                        (31, "31+"), (400, "31+"), (None, "NAO_INFORMADO")]:
        assert q(sql, d) == esperado


@pytest.mark.parametrize("uf,reg", [("SAO PAULO", "SUDESTE"), ("PARA", "NORTE"), ("MARANHAO", "NORDESTE"),
                                    ("DISTRITO FEDERAL", "CENTRO-OESTE"), ("RIO GRANDE DO SUL", "SUL"),
                                    ("RN", "NORDESTE"), ("NAO_INFORMADO", "NAO_INFORMADO"),
                                    ("EXTERIOR", "NAO_INFORMADO")])
def test_regiao(uf, reg):
    sql = F.regiao_sql("x", CFG["regioes"])
    assert duckdb.connect().execute(f"SELECT {sql} FROM (SELECT ?::VARCHAR AS x)", [uf]).fetchone()[0] == reg


def test_regioes_cobrem_27_ufs_sem_repeticao():
    nomes = [u for ufs in CFG["regioes"].values() for u in ufs if len(u) > 2]
    assert len(nomes) == 27 and len(set(nomes)) == 27
    siglas = [u for ufs in CFG["regioes"].values() for u in ufs if len(u) == 2]
    assert len(siglas) == 27 and len(set(siglas)) == 27


def test_vazamento():
    ob = F.colunas_proibidas("obito", CFG)
    for c in ["indica_obito", "tipo_cat_cod", "data_obito_segurado", "especie_beneficio",
              "houve_obito", "arquivo_origem"]:
        assert c in ob
    assert "idade" not in ob and "regiao" not in ob
    assert "data_afastamento" in F.colunas_proibidas("afastamento", CFG)
    with pytest.raises(KeyError):
        F.colunas_proibidas("inexistente", CFG)


def test_todas_as_features_novas_tem_expressao():
    assert set(F.expressoes(CFG)) == set(F.NOVAS)


def _linha(**kw):
    base = dict(data_acidente=dt.date(2015, 3, 9), data_emissao_cat=dt.date(2015, 3, 12), tipo_cat_cod=1,
                idade=30, emitente_cat="EMPREGADOR", indica_obito_acidente=False, indica_obito=False,
                indica_afastamento=True, data_obito_segurado=None, data_afastamento=dt.date(2015, 3, 9),
                uf_segurado="SAO PAULO")
    base.update(kw)
    return base


@pytest.fixture()
def projeto(tmp_path, monkeypatch):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    for mod in (IO, F, LG):
        monkeypatch.setattr(mod, "RAIZ", tmp_path)
    linhas = [
        _linha(),                                                                    # normal
        _linha(tipo_cat_cod=2, data_acidente=dt.date(2014, 1, 1)),                   # reabertura
        _linha(tipo_cat_cod=3, indica_obito=True, data_obito_segurado=dt.date(2015, 3, 9)),
        _linha(idade=None, emitente_cat="NAO_INFORMADO", uf_segurado="NAO_INFORMADO",
               indica_afastamento=False, data_afastamento=None),
        _linha(data_emissao_cat=dt.date(2015, 5, 1), uf_segurado="PARA"),            # atraso 53 dias
    ]
    destino = tmp_path / "data/cat/processed"
    destino.mkdir(parents=True)
    df = pd.DataFrame(linhas)
    duckdb.connect().execute(f"COPY (SELECT * FROM df) TO '{destino / 'cat_limpo.parquet'}' (FORMAT PARQUET)")
    return tmp_path


def test_gerar_features(projeto):
    entrada = projeto / "data/cat/processed/cat_limpo.parquet"
    antes = entrada.read_bytes()
    F.gerar_features()
    assert entrada.read_bytes() == antes                          # cat_limpo.parquet intacto
    saida = projeto / "data/cat/processed/cat_features.parquet"
    d = duckdb.connect().execute(f"SELECT * FROM '{saida}'").fetchdf()
    assert len(d) == 5 and not list(saida.parent.glob("*.tmp"))
    assert d.ano_acidente[0] == 2015 and d.mes_acidente[0] == 3
    assert d.dia_semana_acidente[0] == 1                           # 2015-03-09 foi segunda-feira
    assert d.atraso_cat_dias[0] == 3 and d.faixa_atraso_cat[0] == "2-7"
    assert pd.isna(d.atraso_cat_dias[1]) and d.faixa_atraso_cat[1] == "NAO_APLICAVEL"   # reabertura
    assert pd.isna(d.atraso_cat_dias[2]) and d.faixa_atraso_cat[2] == "NAO_APLICAVEL"
    assert d.atraso_cat_dias[4] == 53 and d.faixa_atraso_cat[4] == "31+"
    assert d.faixa_etaria[0] == "25-34" and d.faixa_etaria[3] == "DESCONHECIDA"
    assert list(d.regiao) == ["SUDESTE"] * 3 + ["NAO_INFORMADO", "NORTE"]
    assert d.emitente_empregador[0] and pd.isna(d.emitente_empregador[3])
    assert list(d.houve_obito) == [False, False, True, False, False]
    assert list(d.houve_afastamento) == [True, True, True, False, True]
    assert (projeto / "docs/qualidade/cat_features_resumo.csv").exists()


def test_idempotente(projeto):
    F.gerar_features()
    primeira = duckdb.connect().execute(
        f"SELECT * FROM '{projeto / 'data/cat/processed/cat_features.parquet'}'").fetchdf()
    F.gerar_features()
    segunda = duckdb.connect().execute(
        f"SELECT * FROM '{projeto / 'data/cat/processed/cat_features.parquet'}'").fetchdf()
    pd.testing.assert_frame_equal(primeira, segunda)
