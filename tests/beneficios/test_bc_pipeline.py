"""Teste de ponta a ponta do BC (Fases 2 e 3) com dados sintéticos pequenos, num diretório temporário."""
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from inss_acidentes.beneficios import auditoria as A
from inss_acidentes.beneficios import limpeza as L
from inss_acidentes.common import io as IO
from inss_acidentes.common import logging as LG

REPO = Path(__file__).resolve().parents[2]
ARQ = [f"{i:02d}.{2011 + i}_01-12_BC.parquet" for i in range(1, 5)]
ACID = "Auxílio Doenca por Acidente do Trabalho"
PREV = "Auxílio Doenca Previdenciário"
CFG = "configs/beneficios/regras_limpeza.yaml"


def _linha(i, arq, **kw):
    y = int(arq[3:7])
    base = {
        "competencia_concessao": f"Março/{y}", "especie": ACID if i % 5 else PREV, "cid_10": "S420",
        "cid_10_2": "S42.0 Frat da Clavicula", "despacho": "Concessao Normal",
        "data_nascimento": f"05/05/{1960 + i % 30}", "sexo": "Masculino", "clientela": "Urbano",
        "municipio_residencia": f"0200{i % 7}-AL-Arapiraca", "vinculo_dependentes": "Não Informado",
        "forma_filiacao": "Empregado", "uf": "Alagoas", "qt_sm_rmi": "1,000", "ramo_atividade": "Comerciario",
        "data_dcb": "00/00/0000", "data_ddb": f"15/03/{y}", "data_dib": f"10/03/{y}",
        "acordo_internacional": "Não", "classificador_pa": "Sem Pensâo Alimentícia",
        "arquivo_origem": arq, "ano_arquivo": y,
    }
    base.update(kw)
    return base


def _ajusta(raiz, fn):
    p = raiz / CFG
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    fn(cfg)
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


@pytest.fixture()
def projeto(tmp_path, monkeypatch):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    _ajusta(tmp_path, lambda c: (c["categorias_raras"].update(limiar=0.05),   # amostra pequena
                                 c["escopo"].update(especies_manter=[])))     # o YAML real já tem escopo; aqui parte de vazio
    for mod in (IO, L, A, LG):
        monkeypatch.setattr(mod, "RAIZ", tmp_path)
    linhas = [_linha(i, ARQ[i % 4]) for i in range(120)]
    linhas += [dict(linhas[0]), dict(linhas[1])]                                           # 2 duplicatas exatas
    linhas.append(_linha(200, ARQ[0], data_dib="10/01/2020", data_ddb="15/01/2020"))        # data futura
    linhas.append(_linha(201, ARQ[1], data_dcb="01/01/2000"))                               # DCB < DIB
    linhas.append(_linha(202, ARQ[1], data_nascimento="05/05/2030"))                        # idade negativa
    linhas.append(_linha(203, ARQ[2], qt_sm_rmi="99999,0"))                                 # RMI fora da faixa
    linhas.append(_linha(204, ARQ[2], qt_sm_rmi="abc"))                                     # RMI não numérica
    linhas.append(_linha(205, ARQ[3], cid_10="000000", cid_10_2="Zerados"))                 # CID nulo disfarçado
    linhas.append(_linha(206, ARQ[3], data_nascimento="00/00/0000", sexo="Ignorado"))       # nulos disfarçados
    lixo = {k: None for k in linhas[0]}
    lixo.update(competencia_concessao="Total", arquivo_origem=ARQ[2], ano_arquivo=2014)
    linhas.append(lixo)
    df = pd.DataFrame(linhas)
    destino = tmp_path / "data/beneficios/interim"
    destino.mkdir(parents=True)
    duckdb.connect().execute(f"COPY (SELECT * FROM df) TO '{destino / 'bc_consolidado.parquet'}' (FORMAT PARQUET)")
    return tmp_path, len(df)


def test_competencia_sql():
    q = lambda v: duckdb.connect().execute(f"SELECT {L._competencia('x')} FROM (SELECT ?::VARCHAR AS x)", [v]).fetchone()[0]
    assert q("JANEIRO/2013") == 201301 and q("MARCO/2014") == 201403 and q("DEZEMBRO/2018") == 201812
    assert q("MARCOO/2014") is None and q("JANEIRO/") is None and q(None) is None


def test_rmi_sql():
    q = lambda v: duckdb.connect().execute(f"SELECT {L._rmi('x')} FROM (SELECT ?::VARCHAR AS x)", [v]).fetchone()[0]
    assert q("1,000") == 1.0 and q("2,5") == 2.5 and q("abc") is None and q(None) is None


def test_auditoria_gera_relatorio_e_sugere_escopo(projeto):
    raiz, _ = projeto
    A.auditar_qualidade()
    rel = (raiz / "docs/qualidade/bc_auditoria.md").read_text(encoding="utf-8")
    assert "Auditoria da base BC" in rel and "especies_manter" in rel
    esp = pd.read_csv(raiz / "docs/qualidade/bc_especies.csv", keep_default_na=False)
    sug = dict(zip(esp.especie, esp.sugestao_acidentaria))
    assert sug["AUXILIO DOENCA POR ACIDENTE DO TRABALHO"] is True or sug["AUXILIO DOENCA POR ACIDENTE DO TRABALHO"] == "True"
    assert str(sug["AUXILIO DOENCA PREVIDENCIARIO"]) == "False"
    dat = pd.read_csv(raiz / "docs/qualidade/bc_inconsistencias_datas.csv").set_index("regra").n
    assert dat["data_dcb anterior à data_dib"] == 1
    assert dat["idade negativa (nascimento posterior ao início do benefício)"] == 1
    assert dat["data_dib posterior a 2018-12-31 (futuro)"] == 1
    dup = pd.read_csv(raiz / "docs/qualidade/bc_duplicados.csv").set_index("tipo")
    assert dup.loc["exata (todas as colunas)", "linhas_excedentes"] == 2
    assert not pd.read_csv(raiz / "docs/qualidade/bc_dimensoes_tipos.csv").empty


def test_pipeline_completo(projeto):
    raiz, n_entrada = projeto
    tab = L.limpar()

    # atrito fecha: entrada - removidas = saída
    assert tab.iloc[0].linhas_restantes == n_entrada
    assert tab.linhas_removidas.sum() == n_entrada - tab.iloc[-1].linhas_restantes
    rem = dict(zip(tab.etapa, tab.linhas_removidas))
    assert rem["linhas-lixo da exportação (competencia_concessao fora do formato mês/AAAA)"] == 1
    assert rem["escopo: todas as espécies mantidas (especies_manter vazio)"] == 0
    assert rem["data_dib ou data_ddb posterior a 2018-12-31"] == 1
    assert rem["duplicatas exatas (mantida a primeira ocorrência)"] == 2
    alt = dict(zip(tab.etapa, tab.valores_alterados))
    assert alt["data_dcb anterior à data_dib -> NULL"] == 1

    limpo = duckdb.connect().execute(f"SELECT * FROM '{raiz / 'data/beneficios/processed/bc_limpo.parquet'}'").fetchdf()
    assert len(limpo) == tab.iloc[-1].linhas_restantes
    assert "cid_10_2" not in limpo.columns and "municipio_residencia" not in limpo.columns
    assert set(limpo.vinculo_dependentes) == {"NAO_INFORMADO"}                      # "Não Informado" é nulo disfarçado
    assert (limpo.cid_10 == "NAO_INFORMADO").sum() == 1 and (limpo.sexo == "NAO_INFORMADO").sum() == 1
    assert limpo.competencia_concessao.iloc[0] // 100 in (2012, 2013, 2014, 2015)   # "Março/AAAA" -> AAAAMM
    assert set(limpo.competencia_concessao % 100) == {3}
    assert limpo.capitulo_cid.iloc[0] == "XIX" and limpo.cid_categoria.iloc[0] == "S42"
    assert limpo.municipio_residencia_cod.iloc[0].startswith("0200")                # zero à esquerda preservado
    assert limpo.qt_sm_rmi.max() == 1.0 and limpo.qt_sm_rmi.isna().sum() == 2       # 99999,0 e "abc"
    assert limpo.idade.isna().sum() == 2                                            # nascimento futuro e 00/00/0000
    assert limpo.data_dcb.isna().all()                                              # 00/00/0000 e DCB < DIB
    assert limpo.acordo_internacional.eq(False).all()
    assert set(limpo.especie) == {"AUXILIO DOENCA POR ACIDENTE DO TRABALHO", "AUXILIO DOENCA PREVIDENCIARIO"}
    assert A.verificar_limpo().status.eq("OK").all()


def test_escopo_por_especie(projeto):
    raiz, _ = projeto
    _ajusta(raiz, lambda c: c["escopo"].update(especies_manter=[ACID]))             # nome com acento e caixa originais
    tab = L.limpar()
    rem = dict(zip(tab.etapa, tab.linhas_removidas))
    assert rem["escopo: apenas 1 espécie(s) listada(s) em especies_manter"] > 0
    limpo = duckdb.connect().execute(f"SELECT especie FROM '{raiz / 'data/beneficios/processed/bc_limpo.parquet'}'").fetchdf()
    assert set(limpo.especie) == {"AUXILIO DOENCA POR ACIDENTE DO TRABALHO"}
    v = A.verificar_limpo()
    assert v.status.eq("OK").all() and "só espécies de especies_manter (se definida)" in set(v.verificacao)

    # o escopo vem logo depois das linhas-lixo, antes de qualquer contagem de valores alterados
    etapas = list(tab.etapa)
    i_esc = next(i for i, e in enumerate(etapas) if e.startswith("escopo"))
    i_nul = etapas.index("nulos disfarçados -> NULL (valores)")
    assert i_esc == 2 and i_esc < i_nul
    # e, por isso, as contagens de valores alterados descrevem só a população do escopo
    alt_escopo = dict(zip(tab.etapa, tab.valores_alterados))["nulos disfarçados -> NULL (valores)"]
    _ajusta(raiz, lambda c: c["escopo"].update(especies_manter=[]))
    sem = L.limpar()
    alt_total = dict(zip(sem.etapa, sem.valores_alterados))["nulos disfarçados -> NULL (valores)"]
    assert 0 < alt_escopo < alt_total


def test_formato_de_competencia_inesperado_aborta(projeto):
    raiz, _ = projeto
    _ajusta(raiz, lambda c: c["linha_valida"].update(regex=r"^[0-9]{6}$"))           # formato que a base não tem
    with pytest.raises(ValueError, match="formato da competência"):
        L.limpar()


def test_em_branco_e_nulo_disfarcado():
    cfg = yaml.safe_load((REPO / CFG).read_text(encoding="utf-8"))
    from inss_acidentes.common.texto import tokens_normalizados
    assert "EM BRANCO" in tokens_normalizados(cfg["nulos_disfarcados"])
    assert A.SUSPEITO_BC.search("EM BRANCO") and A.SUSPEITO_BC.search("ZERADOS")
    assert len(cfg["escopo"]["especies_manter"]) == 6
