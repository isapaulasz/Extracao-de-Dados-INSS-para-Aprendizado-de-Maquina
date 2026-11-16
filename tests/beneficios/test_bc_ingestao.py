import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from inss_acidentes.beneficios import ingestao as B
from inss_acidentes.common import io as IO

REPO = Path(__file__).resolve().parents[2]
CABECALHO = ["Competência concessão", "Espécie", "CID", "CID_2", "Despacho", "Dt Nascimento", "Sexo.",
             "Clientela", "Mun Resid", "Vínculo dependentes", "Forma Filiação", "UF", "Qt SM RMI",
             "Ramo Atividade", "Dt DCB", "Dt DDB", "Dt DIB", "Acordo Internacional", "Classificador PA"]
EXEMPLO = [
    ["janeiro/2013", "Auxílio Salario Maternidade", "000000", "Zerados", "Concessao Normal", "21/07/1983",
     "Feminino", "Rural", "02003-AL-Arapiraca", "Filho", "Segurado Especial", "Alagoas", "1,000", "Rural",
     "28/01/2013", "02/01/2013", "01/10/2012", "Não", "Sem Pensâo Alimentícia"],
    ["janeiro/2013", "Pensão por Morte Previdenciária", "000000", "Zerados", "Concessao Normal", "10/09/1938",
     "Masculino", "Rural", "02003-AL-Arapiraca", "Cônjuge", "Segurado Especial", "Alagoas", "1,000", "Rural",
     "00/00/0000", "04/01/2013", "24/12/2012", "Não", "Sem Pensâo Alimentícia"],
    ["janeiro/2013", "Auxílio Doenca Previdenciário", "S420", "S42.0 Frat da Clavicula",
     "Conc. Base Artigo 27 Inciso Ii do Rbps", "16/10/1989", "Masculino", "Urbano", "02003-AL-Arapiraca",
     "Não Informado", "Empregado", "Alagoas", "1,000", "Comerciario", "01/03/2013", "02/01/2013",
     "05/11/2012", "Não", "Sem Pensâo Alimentícia"],
]
ARQUIVOS = [f"{i:02d}.{2011 + i}_01-12_BC.parquet" for i in range(1, 8)]


def _grava(destino: Path, df: pd.DataFrame) -> None:
    duckdb.connect().execute(f"COPY (SELECT * FROM df) TO '{destino.as_posix()}' (FORMAT PARQUET)")


@pytest.fixture()
def projeto(tmp_path, monkeypatch):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    for mod in (IO, B):
        monkeypatch.setattr(mod, "RAIZ", tmp_path)
    raw = tmp_path / "data/beneficios/raw"
    raw.mkdir(parents=True)
    esperado = {}
    for i, nome in enumerate(ARQUIVOS):
        n = i + 2                                              # 2..8 linhas por arquivo
        df = pd.DataFrame([EXEMPLO[k % 3] for k in range(n)], columns=CABECALHO)
        if i == 3:                                             # 04: nome com variação e coluna nova
            df = df.rename(columns={"Sexo.": "Sexo"})
            df["Coluna Nova"] = "x"
        _grava(raw / nome, df)
        esperado[nome] = n
    return tmp_path, esperado


def test_meta_arquivo():
    assert B._meta_arquivo("01.2012_01-12_BC.parquet") == 2012
    assert B._meta_arquivo("07.2018_01-12_BC.parquet") == 2018
    with pytest.raises(ValueError):
        B._meta_arquivo("01.2012_01-12_CATS.parquet")


def test_schema_cobre_cabecalho_real():
    """Todo nome do cabeçalho do BC tem de cair em um nome padronizado, sem ambiguidade."""
    schema = yaml.safe_load((REPO / "configs/beneficios/harmonizacao_schema.yaml").read_text(encoding="utf-8"))
    mapa = B._mapa_inverso(schema)
    nao_mapeados = [c for c in CABECALHO if B._norm(c) not in mapa]
    assert not nao_mapeados
    assert mapa[B._norm("Sexo.")] == mapa[B._norm("Sexo")] == "sexo"
    assert mapa[B._norm("CID")] == "cid_10" and mapa[B._norm("CID_2")] == "cid_10_2"


def test_dicionario_cobre_o_schema():
    schema = yaml.safe_load((REPO / "configs/beneficios/harmonizacao_schema.yaml").read_text(encoding="utf-8"))
    dic = pd.read_csv(REPO / "docs/dicionario_dados/beneficios.csv")
    assert set(dic[dic.origem != "derivada"].nome_padronizado) == set(schema["colunas"]) | {"arquivo_origem", "ano_arquivo"}
    assert not dic.nome_padronizado.duplicated().any()


def test_consolidacao_e_validacao(projeto):
    raiz, esperado = projeto
    df = B.auditar_schemas()
    assert df.arquivo.nunique() == 7
    B.consolidar()
    out = raiz / "data/beneficios/interim/bc_consolidado.parquet"
    con = duckdb.connect()
    total = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
    assert total == sum(esperado.values())                       # soma dos 7 arquivos == consolidado
    por_arq = dict(con.execute(f"SELECT arquivo_origem, count(*) FROM '{out}' GROUP BY 1").fetchall())
    assert por_arq == esperado
    cols = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()}
    assert set(cols) == set(yaml.safe_load(
        (raiz / "configs/beneficios/harmonizacao_schema.yaml").read_text(encoding="utf-8"))["colunas"]) \
        | {"arquivo_origem", "ano_arquivo"}                      # "Coluna Nova" foi descartada
    assert all(t == "VARCHAR" for c, t in cols.items() if c not in ("ano_arquivo",))
    assert set(r[0] for r in con.execute(f"SELECT DISTINCT ano_arquivo FROM '{out}'").fetchall()) \
        == set(range(2012, 2019))
    assert (raiz / "docs/qualidade/bc_linhas_por_arquivo.csv").exists()


def test_colunas_duplicadas_no_mesmo_arquivo_falham(projeto):
    raiz, _ = projeto
    _grava(raiz / "data/beneficios/raw" / ARQUIVOS[0],
           pd.DataFrame([EXEMPLO[0] + ["x"]], columns=CABECALHO + ["Sexo"]))   # "Sexo." e "Sexo"
    with pytest.raises(ValueError):
        B.auditar_schemas()
