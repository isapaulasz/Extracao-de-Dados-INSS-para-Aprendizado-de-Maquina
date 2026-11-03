import pandas as pd

from inss_acidentes.common import hierarquias as H
from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar
from inss_acidentes.common.logging import Atrito, configurar_logs
from inss_acidentes.common.texto import NAO_INFORMADO, lit, norm_sql, tokens_normalizados

RENOMEAR = {"municipio_empresa": "municipio_empresa_cod",
            "municipio_segurado": "municipio_segurado_cod"}
LIXO = "regexp_matches(data_acidente, '^[0-9]{4}-[0-9]{2}-[0-9]{2}')"
ANO_ARQ = r"CAST(regexp_extract(arquivo_origem, '^[0-9]+\.([0-9]{4})_', 1) AS INTEGER) AS ano_arquivo"
SEM_ARQ = (r"CASE WHEN regexp_extract(arquivo_origem, '_([0-9]{2})-', 1) = '01' "
           r"THEN 1 ELSE 2 END AS semestre_arquivo")
COLUNAS_ORIGEM = ("arquivo_origem", "ano_arquivo", "semestre_arquivo")


# ---------------------------------------------------------------- expressões SQL (testadas)
def _lit(s) -> str:
    return lit(s)


def _norm(col: str, tokens: list[str]) -> str:
    """Normaliza o texto; tokens de nulo disfarçado (comparados já normalizados) viram NULL."""
    base = norm_sql(col)
    lista = ", ".join(_lit(t) for t in tokens_normalizados(tokens))
    return f'CASE WHEN {base} IN ({lista}) THEN NULL ELSE {base} END AS "{col}"'


def _data(c: str) -> str:
    return (f"CASE WHEN regexp_matches({c}, '^[0-9]{{4}}-') THEN TRY_CAST(left({c}, 10) AS DATE) "
            f"ELSE CAST(TRY_STRPTIME({c}, '%d/%m/%Y') AS DATE) END AS {c}")


def _bool(c: str) -> str:
    k = f"upper(strip_accents({c}))"
    return f"CASE WHEN {k} = 'SIM' THEN TRUE WHEN {k} = 'NAO' THEN FALSE END AS {c}"


def _cbo(c: str) -> str:
    return (f"CASE WHEN regexp_matches({c}, '^[0-9]{{5,6}}$') AND NOT regexp_matches({c}, '^0+$') "
            f"THEN lpad({c}, 6, '0') END")


def _cnae(c: str) -> str:
    return (f"CASE WHEN regexp_matches({c}, '^[0-9]{{3,4}}$') AND NOT regexp_matches({c}, '^0+$') "
            f"THEN lpad({c}, 4, '0') END")


def _cid(c: str) -> str:
    k = f"upper(replace({c}, '.', ''))"
    return f"CASE WHEN regexp_matches({k}, '^[A-Z][0-9]{{2,3}}$') THEN {k} END"


def _municipio(c: str) -> str:
    """Código numérico no início do texto; vazio ou só zeros (ex.: 000000 = ignorado) vira NULL."""
    k = f"regexp_extract({c}, '^([0-9]+)', 1)"
    return f"CASE WHEN {k} = '' OR regexp_matches({k}, '^0+$') THEN NULL ELSE {k} END"


def _cnpj(c: str) -> str:
    d = f"regexp_replace({c}, '[^0-9]', '', 'g')"
    return f"CASE WHEN length({d}) = 14 THEN {d} END"


def _nulos(con, origem: str, colunas: list[str]) -> pd.Series:
    sel = ", ".join(f'count(*) FILTER (WHERE "{c}" IS NULL) AS "{c}"' for c in colunas)
    return con.execute(f"SELECT {sel} FROM {origem}").fetchdf().iloc[0]


# ---------------------------------------------------------------- pipeline
def limpar() -> pd.DataFrame:
    log = configurar_logs("cat_limpeza")
    paths = carregar_yaml("configs/paths.yaml")
    regras = carregar_yaml("configs/cat/regras_limpeza.yaml")
    schema = carregar_yaml("configs/cat/harmonizacao_schema.yaml")["colunas"]
    tokens = regras["nulos_disfarcados"]
    cols = [c for c in schema if c not in set(regras["colunas_descartadas"])]
    datas, bools = regras["datas"], regras["booleanas_sim_nao"]
    drg = regras["datas_regras"]

    bruto = (RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet").as_posix()
    saida = RAIZ / paths["cat"]["processed"] / "cat_limpo.parquet"
    saida.parent.mkdir(parents=True, exist_ok=True)
    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)

    con = conectar()
    um = lambda sql: con.execute(sql).fetchone()[0]
    atrito = Atrito()

    # 1) linhas-lixo
    n_ent = um(f"SELECT count(*) FROM read_parquet('{bruto}')")
    atrito.entrada(n_ent)
    # 2) normalização de texto + nulos disfarçados
    con.execute(f"""CREATE TEMP TABLE n AS
        SELECT {', '.join(_norm(c, tokens) for c in cols)}, arquivo_origem
        FROM read_parquet('{bruto}') WHERE {LIXO}""")
    atrito.remocao("linhas-lixo da exportação (data_acidente fora de AAAA-MM-DD)", um("SELECT count(*) FROM n"))

    # 3) tipagem e regras por coluna
    tratadas = {"competencia_cat", "cbo", "cnae_empregador", "cid_10", "especie_beneficio",
                "municipio_empresa", "municipio_segurado", "cnpj_empregador",
                "valor_remuneracao", *datas, *bools}
    rmin, rmax = regras["remuneracao"]["minima"], regras["remuneracao"]["maxima"]
    v = "TRY_CAST(valor_remuneracao AS DOUBLE)"
    partes = [f'"{c}"' for c in cols if c not in tratadas]
    partes += [_data(c) for c in datas] + [_bool(c) for c in bools]
    partes += [
        "CASE WHEN regexp_matches(competencia_cat, '^[0-9]{6}$') "
        "THEN CAST(competencia_cat AS INTEGER) END AS competencia_cat",
        f"{_cbo('cbo')} AS cbo",
        f"{_cnae('cnae_empregador')} AS cnae_empregador",
        f"{_cid('cid_10')} AS cid_10",
        f"left({_cid('cid_10')}, 3) AS cid_categoria",
        f"{_cnpj('cnpj_empregador')} AS cnpj_empregador",
        f"left({_cnpj('cnpj_empregador')}, 8) AS cnpj_raiz",
        f"CASE WHEN {v} BETWEEN {rmin} AND {rmax} THEN {v} END AS valor_remuneracao",
        "CASE WHEN especie_beneficio LIKE '%SEM BENEF%' THEN 'SEM BENEFICIO' "
        "ELSE especie_beneficio END AS especie_beneficio",
        "TRY_CAST(NULLIF(regexp_extract(especie_beneficio, '^([0-9]+):', 1), '') AS INTEGER) "
        "AS especie_codigo",
        "TRY_CAST(NULLIF(regexp_extract(tipo_cat, '^([0-9])', 1), '') AS INTEGER) AS tipo_cat_cod",
        f"{_municipio('municipio_empresa')} AS municipio_empresa_cod",
        f"{_municipio('municipio_segurado')} AS municipio_segurado_cod",
        ANO_ARQ, SEM_ARQ, "arquivo_origem",
    ]
    con.execute(f"CREATE TEMP TABLE t AS SELECT {', '.join(partes)} FROM n")

    # tabela de valores anulados por coluna (disfarçados e por regra)
    b = _nulos(con, f"(SELECT * FROM read_parquet('{bruto}') WHERE {LIXO})", cols)
    nn = _nulos(con, "n", cols)
    lim = _nulos(con, "t", [RENOMEAR.get(c, c) for c in cols])
    anul = pd.DataFrame({
        "coluna": cols,
        "nulos_bruto": [int(b[c]) for c in cols],
        "nulos_disfarcados": [int(nn[c] - b[c]) for c in cols],
        "nulos_por_regra": [int(lim[RENOMEAR.get(c, c)] - nn[c]) for c in cols],
    })
    anul.to_csv(pasta / "cat_valores_anulados.csv", index=False, encoding="utf-8-sig")
    atrito.alteracao("nulos disfarçados -> NULL (valores)", anul.nulos_disfarcados.sum())
    atrito.alteracao("valores fora da regra -> NULL (CBO, CNAE, CID, remuneração, datas)", anul.nulos_por_regra.sum())

    # 4) derivadas
    idade = "date_sub('year', t.data_nascimento, t.data_acidente)"
    imin, imax = regras["idade"]["minima"], regras["idade"]["maxima"]
    sm = " ".join(f"WHEN {a} THEN {s}" for a, s in regras["salario_minimo"].items())
    con.execute(f"""CREATE TEMP TABLE f AS
        SELECT s.*, s.valor_remuneracao / s.salario_minimo AS remuneracao_sm,
               {H.capitulo_cid('s.cid_categoria')} AS capitulo_cid,
               {H.cbo_grande_grupo('s.cbo')} AS cbo_grande_grupo,
               {H.cbo_subgrupo('s.cbo')} AS cbo_subgrupo,
               {H.cnae_divisao('s.cnae_empregador')} AS cnae_divisao,
               {H.cnae_secao('s.cnae_empregador')} AS cnae_secao
        FROM (
            SELECT t.*,
                   t.competencia_cat // 100 AS ano_competencia,
                   CASE WHEN {idade} BETWEEN {imin} AND {imax} THEN {idade} END AS idade,
                   CASE (t.competencia_cat // 100) {sm} END AS salario_minimo
            FROM t) s""")
    atual = "f"

    def filtrar(origem: str, destino: str, remover: str, etapa: str) -> str:
        """Cria destino sem as linhas em que `remover` é verdadeiro (NULL não remove)."""
        con.execute(f"CREATE TEMP TABLE {destino} AS SELECT * FROM {origem} "
                    f"WHERE NOT coalesce(({remover}), false)")
        atrito.remocao(etapa, um(f"SELECT count(*) FROM {destino}"))
        return destino

    # 5) escopo por tipo de CAT
    manter = regras["escopo"]["tipo_cat_manter"]
    if manter and set(manter) != {1, 2, 3}:
        lista = ", ".join(str(int(x)) for x in manter)
        atual = filtrar(atual, "e1", f"tipo_cat_cod IS NULL OR tipo_cat_cod NOT IN ({lista})",
                        f"escopo: apenas tipo_cat in {sorted(manter)}")
    else:
        atrito.remocao("escopo: todos os tipos de CAT mantidos (tipo_cat como variável)", atrito.atual)

    # 6) datas inválidas
    fim, ini = drg["periodo_esperado"]["fim"], drg["periodo_esperado"]["inicio"]
    if drg["remover_se_acidente_apos_emissao"]:
        atual = filtrar(atual, "e2", "data_acidente > data_emissao_cat",
                        "data_acidente posterior à data_emissao_cat")
    if drg["remover_se_data_futura"]:
        atual = filtrar(atual, "e3", f"data_acidente > DATE '{fim}' OR data_emissao_cat > DATE '{fim}'",
                        f"data de acidente ou de emissão posterior a {fim}")
    if drg.get("acidente_minimo"):
        atual = filtrar(atual, "e4", f"data_acidente < DATE '{drg['acidente_minimo']}'",
                        f"data_acidente anterior a {drg['acidente_minimo']}")
    anular = []
    if drg["anular_afastamento_antes_acidente"]:
        anular.append(("data_afastamento", "data_afastamento < data_acidente"))
    if drg["anular_obito_antes_acidente"]:
        anular.append(("data_obito_segurado", "data_obito_segurado < data_acidente"))
    for col, cond in anular:
        qtd = um(f"SELECT count(*) FILTER (WHERE {cond}) FROM {atual}")
        atrito.alteracao(f"{col} anterior ao acidente -> NULL", qtd)
        prox = f"a_{col}"
        con.execute(f"CREATE TEMP TABLE {prox} AS SELECT * REPLACE "
                    f"(CASE WHEN {cond} THEN NULL ELSE {col} END AS {col}) FROM {atual}")
        atual = prox

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
    ni["pct"] = (100 * ni.n_nao_informado / n_total).round(2)
    ni.to_csv(pasta / "cat_nao_informado.csv", index=False, encoding="utf-8-sig")
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
                       "pct_linhas_agrupadas": round(100 * raros.n.sum() / n_total, 3)})
        extras.append(
            f"CASE WHEN \"{col}\" = {lit(NAO_INFORMADO)} THEN \"{col}\" "
            f"WHEN \"{col}\" IN (SELECT \"{col}\" FROM c GROUP BY 1 HAVING count(*) < {minimo}) "
            f"THEN 'OUTROS' ELSE \"{col}\" END AS \"{col}_agrup\"")
    rr = pd.DataFrame(resumo)
    rr.to_csv(pasta / "cat_categorias_raras.csv", index=False, encoding="utf-8-sig")
    atrito.alteracao(f"categorias raras (< {lim_raras:.1%}) -> OUTROS (valores)", rr.linhas_agrupadas.sum())

    con.execute(f"""COPY (SELECT c.*, {', '.join(extras)} FROM c)
                    TO '{saida.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n_fim = um(f"SELECT count(*) FROM read_parquet('{saida.as_posix()}')")
    assert n_fim == atrito.atual, "linhas gravadas diferem do atrito"
    atrito.conferir()
    tab = atrito.salvar(pasta / "cat_atrito.csv")

    log.info("\n%s", tab.to_string(index=False))
    log.info("OK: %s linhas em %s", f"{n_fim:,}", saida.name)
    return tab