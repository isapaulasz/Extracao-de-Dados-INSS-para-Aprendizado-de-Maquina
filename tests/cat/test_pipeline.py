"""Teste de ponta a ponta com dados sintéticos pequenos, num diretório temporário."""
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from inss_acidentes.cat import auditoria as A
from inss_acidentes.cat import limpeza as L
from inss_acidentes.common import io as IO
from inss_acidentes.common import logging as LG

REPO = Path(__file__).resolve().parents[2]
ARQ = [f"{i:02d}.{2012 + (i - 1) // 2}_{'01-06' if i % 2 else '07-12'}_CATS.parquet" for i in range(1, 5)]


def _linha(i, arq, **kw):
    y = int(arq[3:7])
    base = {
        "competencia_cat": f"{y}03", "agente_causador": "Motocicleta", "data_acidente": f"{y}-03-10 00:00:00",
        "cbo": "784205", "cbo_2": "x", "cid_10": "S52.5", "cid_10_2": "x", "cnae_empregador": "113",
        "cnae_empregador_2": "x", "emitente_cat": "Empregador", "especie_beneficio": "Cats Emitidas sem Benefício",
        "filiacao_segurado": "Empregado", "indica_obito_acidente": "Não", "indica_afastamento": "Sim",
        "indica_acidente": "Sim", "indica_obito": "Nao", "municipio_empresa": "355030 - SP",
        "municipio_segurado": "355030 - SP", "cnpj_empregador": f"{i % 7:02d}.028.316/0031-29",
        "area_atividade": "Urbano", "estado_civil": "Ignorado", "local_acidente": " Rua  A ",
        "natureza_lesao": "Fratura", "origem_cadastramento": "Internet", "parte_corpo_atingida": "Dedo",
        "sexo": "Masculino", "ramo_atividade": "Ignorado", "situacao_geradora": "Queda",
        "tipo_cat": "1-Inicial   ", "tipo_acidente": "Típico", "tipo_local_acidente": "Empregadora",
        "uf_acidente": "{ñ class}", "uf_empregador": "São Paulo", "uf_segurado": "São Paulo",
        "data_afastamento": "00/00/0000", "data_despacho": "00/00/0000", "data_nascimento": f"{1960 + i % 30}-05-05 00:00:00",
        "data_emissao_cat": f"{y}-03-12 00:00:00", "data_obito_segurado": "00/00/0000",
        "valor_remuneracao": str(1000 + i), "razao_social": None, "numero_cat": None,
        "arquivo_origem": arq, "ano_arquivo": y, "semestre_arquivo": 1 if int(arq[:2]) % 2 else 2,
    }
    base.update(kw)
    return base


@pytest.fixture()
def projeto(tmp_path, monkeypatch):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    cfg = yaml.safe_load((tmp_path / "configs/cat/regras_limpeza.yaml").read_text(encoding="utf-8"))
    cfg["categorias_raras"]["limiar"] = 0.05          # amostra pequena: 5% para ver o agrupamento
    (tmp_path / "configs/cat/regras_limpeza.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    for mod in (IO, L, A, LG):
        monkeypatch.setattr(mod, "RAIZ", tmp_path)
    linhas = [_linha(i, ARQ[i % 4], agente_causador="Faca" if i == 3 else "Motocicleta") for i in range(120)]
    linhas += [dict(linhas[0]), dict(linhas[1])]                                    # 2 duplicatas exatas
    linhas.append(_linha(200, ARQ[0], data_emissao_cat="2011-01-01 00:00:00"))      # acidente > emissão
    linhas.append(_linha(201, ARQ[0], data_acidente="2020-01-01 00:00:00", data_emissao_cat="2020-01-02 00:00:00"))
    linhas.append(_linha(202, ARQ[1], data_afastamento="2000-01-01 00:00:00"))      # afastamento < acidente
    lixo = {k: None for k in linhas[0]}
    lixo.update(data_acidente="Total", arquivo_origem=ARQ[2], ano_arquivo=2013, semestre_arquivo=1)
    linhas.append(lixo)
    df = pd.DataFrame(linhas)
    destino = tmp_path / "data/cat/interim"
    destino.mkdir(parents=True)
    duckdb.connect().execute(f"COPY (SELECT * FROM df) TO '{destino / 'cat_consolidado.parquet'}' (FORMAT PARQUET)")
    return tmp_path, len(df)


def test_pipeline_completo(projeto):
    raiz, n_entrada = projeto
    A.auditar_qualidade()
    assert (raiz / "docs/qualidade/cat_auditoria.md").exists()
    tab = L.limpar()

    # atrito fecha: entrada - removidas = saída
    assert tab.iloc[0].linhas_restantes == n_entrada
    assert tab.linhas_removidas.sum() == n_entrada - tab.iloc[-1].linhas_restantes
    rem = dict(zip(tab.etapa, tab.linhas_removidas))
    assert rem["linhas-lixo da exportação (data_acidente fora de AAAA-MM-DD)"] == 1
    assert rem["data_acidente posterior à data_emissao_cat"] == 1
    assert rem["data de acidente ou de emissão posterior a 2018-12-31"] == 1
    assert rem["duplicatas exatas (mantida a primeira ocorrência)"] == 2

    limpo = duckdb.connect().execute(f"SELECT * FROM '{raiz / 'data/cat/processed/cat_limpo.parquet'}'").fetchdf()
    assert len(limpo) == tab.iloc[-1].linhas_restantes
    assert set(limpo.estado_civil) == {"NAO_INFORMADO"} and set(limpo.ramo_atividade) == {"NAO_INFORMADO"}
    assert set(limpo.tipo_cat) == {"1-INICIAL"} and set(limpo.tipo_cat_cod) == {1}
    assert limpo.cnae_empregador.iloc[0] == "0113" and limpo.cnae_secao.iloc[0] == "A"
    assert limpo.capitulo_cid.iloc[0] == "XIX"
    assert "FACA" in set(limpo.agente_causador) and "OUTROS" in set(limpo.agente_causador_agrup)
    assert limpo.data_afastamento.isna().all()                                       # 00/00/0000 e anulada
    assert A.verificar_limpo().status.eq("OK").all()


def test_escopo_apenas_cat_inicial(projeto):
    raiz, _ = projeto
    cfg = yaml.safe_load((raiz / "configs/cat/regras_limpeza.yaml").read_text(encoding="utf-8"))
    cfg["escopo"]["tipo_cat_manter"] = [1]
    (raiz / "configs/cat/regras_limpeza.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    con = duckdb.connect()
    p = raiz / "data/cat/interim/cat_consolidado.parquet"
    df = con.execute(f"SELECT * FROM '{p}'").fetchdf()
    df.loc[0, "tipo_cat"] = "2-Reabertura"
    con.execute(f"COPY (SELECT * FROM df) TO '{p}' (FORMAT PARQUET)")
    tab = L.limpar()
    assert dict(zip(tab.etapa, tab.linhas_removidas))["escopo: apenas tipo_cat in [1]"] >= 1
