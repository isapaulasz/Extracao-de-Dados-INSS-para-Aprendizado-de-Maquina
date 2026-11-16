"""Fase 3 (Benefícios Concedidos): limpeza, regras de configs/beneficios/regras_limpeza.yaml.

Mesmo desenho de cat/limpeza.py: DuckDB, tabelas temporárias encadeadas e tabela de atrito
(linhas removidas e valores alterados por regra). Os helpers SQL genéricos (_norm, _data, _bool,
_cid, _municipio, _nulos) são os do CAT, já testados; aqui ficam só as regras próprias do BC.
"""
import pandas as pd

from inss_acidentes.cat.limpeza import _bool, _cid, _data, _municipio, _norm, _nulos
from inss_acidentes.common import hierarquias as H
from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar
from inss_acidentes.common.logging import Atrito, configurar_logs
from inss_acidentes.common.texto import NAO_INFORMADO, lit, norm_sql, normalizar_py

CFG = "configs/beneficios/regras_limpeza.yaml"
SCHEMA = "configs/beneficios/harmonizacao_schema.yaml"
RENOMEAR = {"municipio_residencia": "municipio_residencia_cod"}
COLUNAS_ORIGEM = ("arquivo_origem", "ano_arquivo")
MESES = ["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO",
         "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]       # já sem acento, como saem da normalização


# ---------------------------------------------------------------- expressões SQL (testadas)
def lixo_sql(regras: dict) -> str:
    """Condição SQL (sobre o texto bruto) que identifica as linhas VÁLIDAS."""
    lv = regras["linha_valida"]
    return f"regexp_matches(trim(\"{lv['coluna']}\"), {lit(lv['regex'])})"


def _competencia(c: str) -> str:
    """'JANEIRO/2013' (texto já normalizado) -> 201301; NULL se o mês ou o ano não forem reconhecidos."""
    ramos = " ".join(f"WHEN {lit(m)} THEN {i}" for i, m in enumerate(MESES, 1))
    mes = f"CASE split_part({c}, '/', 1) {ramos} END"
    ano = f"TRY_CAST(NULLIF(split_part({c}, '/', 2), '') AS INTEGER)"
    return f"({ano} * 100 + {mes})"


def _rmi(c: str) -> str:
    """Vírgula decimal -> número (DOUBLE); texto não numérico vira NULL."""
    return f"TRY_CAST(replace({c}, ',', '.') AS DOUBLE)"


def idade_sql(referencia: list[str], prefixo: str = "") -> str:
    ref = ", ".join(f"{prefixo}{c}" for c in referencia)
    return f"date_sub('year', {prefixo}data_nascimento, coalesce({ref}))"


def especies_normalizadas(regras: dict) -> list[str]:
    return sorted({normalizar_py(x) for x in (regras["escopo"].get("especies_manter") or [])})


# ---------------------------------------------------------------- pipeline
def limpar() -> pd.DataFrame:
    log = configurar_logs("bc_limpeza")
    paths = carregar_yaml("configs/paths.yaml")
    regras = carregar_yaml(CFG)
    schema = carregar_yaml(SCHEMA)["colunas"]
    tokens = regras["nulos_disfarcados"]
    cols = [c for c in schema if c not in set(regras["colunas_descartadas"])]
    datas, bools = regras["datas"], regras["booleanas_sim_nao"]
    drg = regras["datas_regras"]

    bruto = (RAIZ / paths["beneficios"]["interim"] / "bc_consolidado.parquet").as_posix()
    saida = RAIZ / paths["beneficios"]["processed"] / "bc_limpo.parquet"
    saida.parent.mkdir(parents=True, exist_ok=True)
    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)

    con = conectar()
    um = lambda sql: con.execute(sql).fetchone()[0]
    atrito = Atrito()
    valida = lixo_sql(regras)

    # 1) linhas-lixo
    n_ent = um(f"SELECT count(*) FROM read_parquet('{bruto}')")
    atrito.entrada(n_ent)
    n_valid = um(f"SELECT count(*) FROM read_parquet('{bruto}') WHERE {valida}")
    pct_lixo = 100 * (n_ent - n_valid) / max(n_ent, 1)
    if pct_lixo > regras["linha_valida"]["max_pct_removidas"]:
        raise ValueError(
            f"{n_ent - n_valid:,} linhas ({pct_lixo:.1f}%) não casam com linha_valida.regex "
            f"{regras['linha_valida']['regex']!r} em {regras['linha_valida']['coluna']}: "
            "o formato da competência é diferente do esperado. Ajuste o YAML antes de limpar.")
    atrito.remocao(f"linhas-lixo da exportação ({regras['linha_valida']['coluna']} fora do formato mês/AAAA)", n_valid)

    # 2) escopo por espécie, ANTES de qualquer contagem de valores alterados: a partir daqui todas as
    #    contagens da tabela de atrito descrevem a população do estudo, não a base inteira do INSS.
    #    A espécie é comparada normalizada (mesma regra de normalizar_py), direto no texto bruto.
    manter = especies_normalizadas(regras)
    onde = valida
    if manter:
        lista = ", ".join(lit(x) for x in manter)
        onde = f"({valida}) AND {norm_sql('especie')} IN ({lista})"
        n_esc = um(f"SELECT count(*) FROM read_parquet('{bruto}') WHERE {onde}")
        atrito.remocao(f"escopo: apenas {len(manter)} espécie(s) listada(s) em especies_manter", n_esc)
    else:
        log.warning("escopo: especies_manter está vazio, TODAS as espécies do INSS foram mantidas")
        atrito.remocao("escopo: todas as espécies mantidas (especies_manter vazio)", atrito.atual)

    # 3) normalização de texto + nulos disfarçados
    con.execute(f"""CREATE TEMP TABLE n AS
        SELECT {', '.join(_norm(c, tokens) for c in cols)}, arquivo_origem, ano_arquivo
        FROM read_parquet('{bruto}') WHERE {onde}""")
    assert um("SELECT count(*) FROM n") == atrito.atual, "n diverge do atrito após o escopo"

    # 4) tipagem e regras por coluna
    tratadas = {"competencia_concessao", "cid_10", "municipio_residencia", "qt_sm_rmi", *datas, *bools}
    rmi_min, rmi_max = regras["qt_sm_rmi"]["minima_exclusiva"], regras["qt_sm_rmi"]["maxima"]
    r = _rmi("qt_sm_rmi")
    partes = [f'"{c}"' for c in cols if c not in tratadas]
    partes += [_data(c) for c in datas] + [_bool(c) for c in bools]
    partes += [
        f"{_competencia('competencia_concessao')} AS competencia_concessao",
        f"{_cid('cid_10')} AS cid_10",
        f"left({_cid('cid_10')}, 3) AS cid_categoria",
        f"CASE WHEN {r} > {rmi_min} AND {r} <= {rmi_max} THEN {r} END AS qt_sm_rmi",
        f"{_municipio('municipio_residencia')} AS municipio_residencia_cod",
        "arquivo_origem", "ano_arquivo",
    ]
    con.execute(f"CREATE TEMP TABLE t AS SELECT {', '.join(partes)} FROM n")

    # tabela de valores anulados por coluna (disfarçados e por regra)
    b = _nulos(con, f"(SELECT * FROM read_parquet('{bruto}') WHERE {onde})", cols)
    nn = _nulos(con, "n", cols)
    lim = _nulos(con, "t", [RENOMEAR.get(c, c) for c in cols])
    anul = pd.DataFrame({
        "coluna": cols,
        "nulos_bruto": [int(b[c]) for c in cols],
        "nulos_disfarcados": [int(nn[c] - b[c]) for c in cols],
        "nulos_por_regra": [int(lim[RENOMEAR.get(c, c)] - nn[c]) for c in cols],
    })
    anul.to_csv(pasta / "bc_valores_anulados.csv", index=False, encoding="utf-8-sig")
    atrito.alteracao("nulos disfarçados -> NULL (valores)", anul.nulos_disfarcados.sum())
    atrito.alteracao("valores fora da regra -> NULL (competência, CID, qt_sm_rmi, datas)", anul.nulos_por_regra.sum())

    # 5) derivadas
    idade = idade_sql(regras["idade"]["referencia"], "t.")
    imin, imax = regras["idade"]["minima"], regras["idade"]["maxima"]
    con.execute(f"""CREATE TEMP TABLE f AS
        SELECT s.*, {H.capitulo_cid('s.cid_categoria')} AS capitulo_cid
        FROM (
            SELECT t.*,
                   t.competencia_concessao // 100 AS ano_competencia,
                   CASE WHEN {idade} BETWEEN {imin} AND {imax} THEN {idade} END AS idade
            FROM t) s""")
    atual = "f"

    def filtrar(origem: str, destino: str, remover: str, etapa: str) -> str:
        """Cria destino sem as linhas em que `remover` é verdadeiro (NULL não remove)."""
        con.execute(f"CREATE TEMP TABLE {destino} AS SELECT * FROM {origem} "
                    f"WHERE NOT coalesce(({remover}), false)")
        atrito.remocao(etapa, um(f"SELECT count(*) FROM {destino}"))
        return destino

    # 6) datas inválidas
    fim = drg["periodo_esperado"]["fim"]
    if drg["remover_se_data_futura"]:
        atual = filtrar(atual, "e2", f"data_dib > DATE '{fim}' OR data_ddb > DATE '{fim}'",
                        f"data_dib ou data_ddb posterior a {fim}")
    if drg["remover_se_dib_apos_ddb"]:
        atual = filtrar(atual, "e3", "data_dib > data_ddb", "data_dib posterior à data_ddb")
    if drg["anular_dcb_antes_dib"]:
        qtd = um(f"SELECT count(*) FILTER (WHERE data_dcb < data_dib) FROM {atual}")
        atrito.alteracao("data_dcb anterior à data_dib -> NULL", qtd)
        con.execute(f"CREATE TEMP TABLE a_dcb AS SELECT * REPLACE "
                    f"(CASE WHEN data_dcb < data_dib THEN NULL ELSE data_dcb END AS data_dcb) FROM {atual}")
        atual = "a_dcb"

    # 7) duplicatas exatas
    chave = ", ".join(f'"{c}"' for (c, *_) in con.execute(f"DESCRIBE {atual}").fetchall()
                      if c not in COLUNAS_ORIGEM)
    n_antes = um(f"SELECT count(*) FROM {atual}")
    n_dist = um(f"SELECT count(DISTINCT hash({chave})) FROM {atual}")
    if regras["duplicatas"]["remover_exatas"]:
        con.execute(f"""CREATE TEMP TABLE d AS SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY hash({chave})
                                         ORDER BY arquivo_origem, rowid) AS rn FROM {atual}) WHERE rn = 1""")
        atrito.remocao("duplicatas exatas (mantida a primeira ocorrência)", um("SELECT count(*) FROM d"))
        assert n_antes - atrito.atual == n_antes - n_dist, "dedup inconsistente com a contagem de hashes"
        atual = "d"
    else:
        log.info("duplicatas exatas apenas contadas: %s", n_antes - n_dist)

    # 8) NAO_INFORMADO nas categóricas
    existentes = {c for (c, *_) in con.execute(f"DESCRIBE {atual}").fetchall()}
    cat_cols = regras["categoricas_nao_informado"]
    faltando = [c for c in cat_cols if c not in existentes]
    if faltando:
        raise KeyError(f"categoricas_nao_informado inexistentes: {faltando}")
    nulos_cat = _nulos(con, atual, cat_cols)
    repl = ", ".join(f"coalesce(\"{c}\", {lit(NAO_INFORMADO)}) AS \"{c}\"" for c in cat_cols)
    con.execute(f"CREATE TEMP TABLE c AS SELECT * REPLACE ({repl}) FROM {atual}")
    n_total = um("SELECT count(*) FROM c")
    ni = pd.DataFrame({"coluna": cat_cols, "n_nao_informado": [int(nulos_cat[c]) for c in cat_cols]})
    ni["pct"] = (100 * ni.n_nao_informado / max(n_total, 1)).round(2)
    ni.to_csv(pasta / "bc_nao_informado.csv", index=False, encoding="utf-8-sig")
    atrito.alteracao("nulos em categóricas -> NAO_INFORMADO (valores)", ni.n_nao_informado.sum())

    # 9) categorias raras
    lim_raras = regras["categorias_raras"]["limiar"]
    minimo = lim_raras * n_total
    extras, resumo = [], []
    for col in regras["categorias_raras"]["colunas"]:
        freq = con.execute(f'SELECT "{col}" AS nivel, count(*) AS n FROM c GROUP BY 1').fetchdf()
        raros = freq[(freq.n < minimo) & (freq.nivel != NAO_INFORMADO)]
        resumo.append({"coluna": col, "n_niveis": len(freq), "n_niveis_raros": len(raros),
                       "linhas_agrupadas": int(raros.n.sum()),
                       "pct_linhas_agrupadas": round(100 * raros.n.sum() / max(n_total, 1), 3)})
        extras.append(
            f"CASE WHEN \"{col}\" = {lit(NAO_INFORMADO)} THEN \"{col}\" "
            f"WHEN \"{col}\" IN (SELECT \"{col}\" FROM c GROUP BY 1 HAVING count(*) < {minimo}) "
            f"THEN 'OUTROS' ELSE \"{col}\" END AS \"{col}_agrup\"")
    rr = pd.DataFrame(resumo)
    rr.to_csv(pasta / "bc_categorias_raras.csv", index=False, encoding="utf-8-sig")
    atrito.alteracao(f"categorias raras (< {lim_raras:.1%}) -> OUTROS (valores)", rr.linhas_agrupadas.sum())

    con.execute(f"""COPY (SELECT c.*, {', '.join(extras)} FROM c)
                    TO '{saida.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n_fim = um(f"SELECT count(*) FROM read_parquet('{saida.as_posix()}')")
    assert n_fim == atrito.atual, "linhas gravadas diferem do atrito"
    atrito.conferir()
    tab = atrito.salvar(pasta / "bc_atrito.csv")

    log.info("\n%s", tab.to_string(index=False))
    log.info("OK: %s linhas em %s", f"{n_fim:,}", saida.name)
    return tab
