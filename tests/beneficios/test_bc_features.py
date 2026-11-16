"""Testes da Fase 4 do BC (engenharia de atributos), no mesmo estilo de tests/cat/test_features.py."""
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from inss_acidentes.beneficios import features as F
from inss_acidentes.common import io as IO
from inss_acidentes.common import logging as LG

REPO = Path(__file__).resolve().parents[2]
CFG = yaml.safe_load((REPO / F.CFG).read_text(encoding="utf-8"))
EX = F.expressoes(CFG)


def q(feature, **cols):
    """Avalia a expressão de uma feature para uma linha com os valores dados (datas como 'AAAA-MM-DD')."""
    tipos = {"data_dib": "DATE", "data_ddb": "DATE", "data_dcb": "DATE", "idade": "INTEGER",
             "qt_sm_rmi": "DOUBLE", "competencia_concessao": "INTEGER",
             "atraso_concessao_dias": "INTEGER", "duracao_beneficio_dias": "INTEGER"}
    sel = ", ".join(f"CAST(? AS {tipos.get(k, 'VARCHAR')}) AS {k}" for k in cols)
    return duckdb.connect().execute(f"SELECT {EX[feature]} FROM (SELECT {sel})", list(cols.values())).fetchone()[0]


@pytest.mark.parametrize("uf,reg", [("SAO PAULO", "SUDESTE"), ("BAHIA", "NORDESTE"), ("PR", "SUL"),
                                    ("DISTRITO FEDERAL", "CENTRO-OESTE"), ("AMAZONAS", "NORTE"),
                                    ("NAO_INFORMADO", "NAO_INFORMADO"), ("ATLANTIDA", "NAO_INFORMADO")])
def test_regiao(uf, reg):
    assert q("regiao", uf=uf) == reg


@pytest.mark.parametrize("idade,faixa", [(0, "0-13"), (13, "0-13"), (14, "14-17"), (30, "25-34"),
                                         (64, "55-64"), (65, "65+"), (110, "65+"), (None, "DESCONHECIDA")])
def test_faixa_etaria(idade, faixa):
    assert q("faixa_etaria", idade=idade) == faixa


def test_calendario():
    assert q("ano_dib", data_dib="2014-03-10") == 2014 and q("mes_dib", data_dib="2014-03-10") == 3
    assert q("dia_semana_dib", data_dib="2014-03-10") == 1          # segunda-feira (ISO)
    assert q("dia_semana_dib", data_dib="2014-03-16") == 7          # domingo
    assert q("mes_competencia", competencia_concessao=201403) == 3
    assert q("ano_dib", data_dib=None) is None


def test_atraso_concessao():
    assert q("atraso_concessao_dias", data_dib="2014-03-10", data_ddb="2014-03-15") == 5
    assert q("atraso_concessao_dias", data_dib="2014-03-10", data_ddb="2014-03-10") == 0
    assert q("atraso_concessao_dias", data_dib="2014-03-10", data_ddb="2014-03-01") is None   # DIB > DDB
    assert q("atraso_concessao_dias", data_dib=None, data_ddb="2014-03-01") is None
    assert q("faixa_atraso_concessao", atraso_concessao_dias=0) == "0"
    assert q("faixa_atraso_concessao", atraso_concessao_dias=31) == "31-90"
    assert q("faixa_atraso_concessao", atraso_concessao_dias=4000) == "366+"
    assert q("faixa_atraso_concessao", atraso_concessao_dias=None) == "NAO_INFORMADO"


def test_duracao_e_cessacao():
    assert q("duracao_beneficio_dias", data_dib="2014-01-01", data_dcb="2014-02-01") == 31
    assert q("duracao_beneficio_dias", data_dib="2014-01-01", data_dcb=None) is None
    assert q("tem_data_cessacao", data_dcb="2014-02-01") is True and q("tem_data_cessacao", data_dcb=None) is False
    assert q("faixa_duracao_beneficio", data_dcb=None, duracao_beneficio_dias=None) == "NAO_APLICAVEL"
    assert q("faixa_duracao_beneficio", data_dcb="2014-02-01", duracao_beneficio_dias=31) == "31-90"


@pytest.mark.parametrize("sm,faixa,piso", [(0.5, "ATE 1 SM", True), (1.0, "ATE 1 SM", False), (1.5, "1-2 SM", False),
                                           (4.0, "3-5 SM", False), (12.0, "MAIS DE 5 SM", False)])
def test_rmi(sm, faixa, piso):
    assert q("faixa_rmi", qt_sm_rmi=sm) == faixa and q("rmi_abaixo_piso", qt_sm_rmi=sm) is piso
    assert q("faixa_rmi", qt_sm_rmi=None) == "DESCONHECIDA"


@pytest.mark.parametrize("esp,grupo", [("AUXILIO DOENCA POR ACIDENTE DO TRABALHO", "AUXILIO_DOENCA"),
                                       ("AUXILIO ACIDENTE", "AUXILIO_ACIDENTE"),
                                       ("APOSENT. INVALIDEZ ACIDENTARIA-TRAB.RUR.", "APOSENTADORIA_INVALIDEZ"),
                                       ("PENSAO POR MORTE ACIDENTE DO TRABALHO", "PENSAO_POR_MORTE"),
                                       ("AUXILIO DOENCA PREVIDENCIARIO", "OUTROS"),
                                       ("NAO_INFORMADO", "NAO_INFORMADO")])
def test_grupo_especie(esp, grupo):
    assert q("grupo_especie", especie=esp) == grupo


def test_mapa_de_vazamento():
    for alvo, col in [("especie", "grupo_especie"), ("duracao", "duracao_beneficio_dias"), ("rmi", "faixa_rmi")]:
        p = F.colunas_proibidas(alvo)
        assert col in p                                                    # o próprio alvo nunca é preditor
        assert {"arquivo_origem", "ano_arquivo", "vinculo_dependentes", "municipio_residencia_cod"} <= set(p)
    assert {"especie", "data_dcb", "qt_sm_rmi"} <= set(F.colunas_proibidas("especie"))
    assert "tem_data_cessacao" in F.colunas_proibidas("duracao")
    with pytest.raises(KeyError):
        F.colunas_proibidas("sempre")
    # toda coluna proibida existe no esquema da limpeza ou entre as features novas
    conhecidas = set(F.NOVAS) | {"arquivo_origem", "ano_arquivo", "vinculo_dependentes", "municipio_residencia_cod",
                                 "especie", "data_dcb", "qt_sm_rmi"}
    for alvo in ("especie", "duracao", "rmi"):
        assert set(F.colunas_proibidas(alvo)) <= conhecidas | set(F.colunas_proibidas(alvo))


@pytest.fixture()
def projeto(tmp_path, monkeypatch):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    for mod in (IO, F, LG):
        monkeypatch.setattr(mod, "RAIZ", tmp_path)
    esp = ["AUXILIO DOENCA POR ACIDENTE DO TRABALHO", "AUXILIO ACIDENTE", "PENSAO POR MORTE ACIDENTE DO TRABALHO"]
    linhas = []
    for i in range(30):
        linhas.append({
            "competencia_concessao": 201403, "especie": esp[i % 3], "despacho": "CONCESSAO NORMAL",
            "classificador_pa": "SEM PENSAO ALIMENTICIA", "uf": ["SAO PAULO", "BAHIA", "NAO_INFORMADO"][i % 3],
            "idade": 20 + i, "qt_sm_rmi": 0.5 + i / 10, "ano_competencia": 2014,
            "data_dib": pd.Timestamp("2014-03-10"), "data_ddb": pd.Timestamp("2014-03-15"),
            "data_dcb": pd.Timestamp("2014-06-10") if i % 3 == 0 else pd.NaT,
        })
    df = pd.DataFrame(linhas)
    destino = tmp_path / "data/beneficios/processed"
    destino.mkdir(parents=True)
    duckdb.connect().execute(f"COPY (SELECT * FROM df) TO '{destino / 'bc_limpo.parquet'}' (FORMAT PARQUET)")
    return tmp_path, len(df)


def test_gerar_features(projeto):
    raiz, n = projeto
    entrada = raiz / "data/beneficios/processed/bc_limpo.parquet"
    antes = entrada.read_bytes()
    F.gerar_features()
    assert entrada.read_bytes() == antes                                    # a Fase 3 não é tocada
    saida = raiz / "data/beneficios/processed/bc_features.parquet"
    assert not list(saida.parent.glob("*.tmp")) and saida.exists()
    df = duckdb.connect().execute(f"SELECT * FROM '{saida}'").fetchdf()
    assert len(df) == n and set(F.NOVAS) <= set(df.columns)
    assert set(df.regiao) == {"SUDESTE", "NORDESTE", "NAO_INFORMADO"}
    assert (df.atraso_concessao_dias == 5).all() and (df.dia_semana_dib == 1).all()
    assert df.tem_data_cessacao.sum() == 10 and (df.faixa_duracao_beneficio == "NAO_APLICAVEL").sum() == 20
    q_dir = raiz / "docs/qualidade"
    for nome in ("resumo", "distribuicao", "numericas", "uf_sem_regiao", "crosstabs"):
        assert (q_dir / f"bc_features_{nome}.csv").exists()
    ct = pd.read_csv(q_dir / "bc_features_crosstabs.csv")
    assert {"coluna_a", "coluna_b", "valor_a", "valor_b", "n", "pct_da_linha"} <= set(ct.columns)


def test_idempotente(projeto):
    raiz, _ = projeto
    F.gerar_features()
    a = duckdb.connect().execute(f"SELECT * FROM '{raiz / 'data/beneficios/processed/bc_features.parquet'}'").fetchdf()
    F.gerar_features()
    b = duckdb.connect().execute(f"SELECT * FROM '{raiz / 'data/beneficios/processed/bc_features.parquet'}'").fetchdf()
    pd.testing.assert_frame_equal(a, b)
