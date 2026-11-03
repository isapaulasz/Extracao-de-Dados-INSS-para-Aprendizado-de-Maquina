"""Fase 3: limpeza e tipagem. cat_consolidado.parquet (tudo VARCHAR) -> cat_limpo.parquet."""
import pandas as pd

from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar

RENOMEAR = {"municipio_empresa": "municipio_empresa_cod",
            "municipio_segurado": "municipio_segurado_cod"}
LIXO = "regexp_matches(data_acidente, '^[0-9]{4}-[0-9]{2}-[0-9]{2}')"
ESPACOS = r"'\s+'"
ANO_ARQ = r"CAST(regexp_extract(arquivo_origem, '^[0-9]+\.([0-9]{4})_', 1) AS INTEGER) AS ano_arquivo"
SEM_ARQ = (r"CASE WHEN regexp_extract(arquivo_origem, '_([0-9]{2})-', 1) = '01' "
           r"THEN 1 ELSE 2 END AS semestre_arquivo")


def _lit(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _norm(col: str, tokens: list[str]) -> str:
    """trim + colapso de espaços; tokens de nulo disfarçado viram NULL."""
    base = f"trim(regexp_replace(\"{col}\", {ESPACOS}, ' ', 'g'))"
    lista = ", ".join(_lit(t) for t in tokens)
    return f'CASE WHEN {base} IN ({lista}) THEN NULL ELSE {base} END AS "{col}"'


def _data(c: str) -> str:
    return (f"CASE WHEN regexp_matches({c}, '^[0-9]{{4}}-') THEN TRY_CAST(left({c}, 10) AS DATE) "
            f"ELSE CAST(TRY_STRPTIME({c}, '%d/%m/%Y') AS DATE) END AS {c}")


def _bool(c: str) -> str:
    return f"CASE WHEN {c} = 'Sim' THEN TRUE WHEN {c} IN ('Não', 'Nao') THEN FALSE END AS {c}"


def _cbo(c: str) -> str:
    return (f"CASE WHEN regexp_matches({c}, '^[0-9]{{5,6}}$') AND NOT regexp_matches({c}, '^0+$') "
            f"THEN lpad({c}, 6, '0') END")


def _cnae(c: str) -> str:
    return (f"CASE WHEN regexp_matches({c}, '^[0-9]{{3,4}}$') AND NOT regexp_matches({c}, '^0+$') "
            f"THEN lpad({c}, 4, '0') END")


def _cid(c: str) -> str:
    k = f"upper(replace({c}, '.', ''))"
    return f"CASE WHEN regexp_matches({k}, '^[A-Z][0-9]{{2,3}}$') THEN {k} END"


def _cnpj(c: str) -> str:
    d = f"regexp_replace({c}, '[^0-9]', '', 'g')"
    return f"CASE WHEN length({d}) = 14 THEN {d} END"


def _nulos(con, origem: str, colunas: list[str]) -> pd.Series:
    sel = ", ".join(f'count(*) FILTER (WHERE "{c}" IS NULL) AS "{c}"' for c in colunas)
    return con.execute(f"SELECT {sel} FROM {origem}").fetchdf().iloc[0]


def limpar() -> None:
    paths = carregar_yaml("configs/paths.yaml")
    regras = carregar_yaml("configs/cat/regras_limpeza.yaml")
    schema = carregar_yaml("configs/cat/harmonizacao_schema.yaml")["colunas"]
    tokens = regras["nulos_disfarcados"]
    cols = [c for c in schema if c not in set(regras["colunas_descartadas"])]
    datas, bools = regras["datas"], regras["booleanas_sim_nao"]

    bruto = (RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet").as_posix()
    saida = RAIZ / paths["cat"]["processed"] / "cat_limpo.parquet"
    saida.parent.mkdir(parents=True, exist_ok=True)
    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)

    con = conectar()
    um = lambda sql: con.execute(sql).fetchone()[0]

    # 1) normaliza o texto e remove as linhas-lixo da exportação
    n_ent = um(f"SELECT count(*) FROM read_parquet('{bruto}')")
    con.execute(f"""CREATE TEMP TABLE n AS
        SELECT {', '.join(_norm(c, tokens) for c in cols)}, arquivo_origem
        FROM read_parquet('{bruto}') WHERE {LIXO}""")
    n_1 = um("SELECT count(*) FROM n")

    # 2) tipagem e regras por coluna
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
        "CASE WHEN especie_beneficio ILIKE '%sem benef%' THEN 'Sem benefício' "
        "ELSE especie_beneficio END AS especie_beneficio",
        "TRY_CAST(NULLIF(regexp_extract(especie_beneficio, '^([0-9]+):', 1), '') AS INTEGER) "
        "AS especie_codigo",
        "NULLIF(regexp_extract(municipio_empresa, '^([0-9]+)', 1), '') AS municipio_empresa_cod",
        "NULLIF(regexp_extract(municipio_segurado, '^([0-9]+)', 1), '') AS municipio_segurado_cod",
        ANO_ARQ, SEM_ARQ, "arquivo_origem",
    ]
    con.execute(f"CREATE TEMP TABLE t AS SELECT {', '.join(partes)} FROM n")

    # 3) variáveis derivadas
    idade = "date_sub('year', t.data_nascimento, t.data_acidente)"
    imin, imax = regras["idade"]["minima"], regras["idade"]["maxima"]
    sm = " ".join(f"WHEN {a} THEN {s}" for a, s in regras["salario_minimo"].items())
    con.execute(f"""CREATE TEMP TABLE f AS
        SELECT s.*, s.valor_remuneracao / s.salario_minimo AS remuneracao_sm FROM (
            SELECT t.*,
                   t.competencia_cat // 100 AS ano_competencia,
                   CASE WHEN {idade} BETWEEN {imin} AND {imax} THEN {idade} END AS idade,
                   CASE (t.competencia_cat // 100) {sm} END AS salario_minimo
            FROM t) s""")

    # 4) duplicatas exatas (informativo: não removemos)
    lista = ", ".join(f'"{c}"' for (c, *_) in con.execute("DESCRIBE f").fetchall()
                      if c not in ("arquivo_origem", "ano_arquivo", "semestre_arquivo"))
    n_dup = um(f"SELECT count(*) - count(DISTINCT hash({lista})) FROM f")

    # 5) grava, removendo acidente posterior à emissão
    con.execute(f"""COPY (SELECT * FROM f
                          WHERE data_emissao_cat IS NULL OR data_acidente <= data_emissao_cat)
                    TO '{saida.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n_fim = um(f"SELECT count(*) FROM read_parquet('{saida.as_posix()}')")

    # 6) tabelas de qualidade
    atrito = pd.DataFrame([
        ("entrada (consolidado)", 0, n_ent),
        ("linhas-lixo da exportação (data_acidente fora de AAAA-MM-DD)", n_ent - n_1, n_1),
        ("data_acidente posterior à data_emissao_cat", n_1 - n_fim, n_fim),
    ], columns=["etapa", "linhas_removidas", "linhas_restantes"])
    atrito.to_csv(pasta / "cat_atrito.csv", index=False, encoding="utf-8-sig")

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

    print("\n== Tabela de atrito ==")
    print(atrito.to_string(index=False))
    print(f"\n(informativo) linhas idênticas a outra em todas as colunas: {n_dup:,} (não removidas)")
    print("\n== Valores anulados (só colunas afetadas) ==")
    print(anul[(anul.nulos_disfarcados > 0) | (anul.nulos_por_regra > 0)].to_string(index=False))
    print(f"\nOK: {n_fim:,} linhas em {saida.name}")