import pandas as pd
from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar

def gerar_esqueleto_dicionario() -> pd.DataFrame:
    paths = carregar_yaml("configs/paths.yaml")
    parquet = (RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet").as_posix()
    con = conectar()

    total = con.execute(f"SELECT count(*) FROM read_parquet('{parquet}')").fetchone()[0]
    nomes = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()]

    linhas = []
    for nome in nomes:
        n_nulos, n_dist = con.execute(
            f'SELECT count(*) FILTER (WHERE "{nome}" IS NULL), count(DISTINCT "{nome}") '
            f"FROM read_parquet('{parquet}')").fetchone()
        top = con.execute(
            f'SELECT "{nome}" FROM read_parquet(\'{parquet}\') '
            f'GROUP BY 1 ORDER BY count(*) DESC LIMIT 8').fetchall()
        linhas.append({
            "nome_padronizado": nome,
            "nome_original": "",
            "tipo": "",
            "descricao": "",
            "valores_validos": "",
            "nulos_disfarcados": "",   # ex.: "{ñ class}, 000000, IGNORADO"
            "pct_nulos": round(100 * n_nulos / total, 2),
            "n_distintos": n_dist,
            "observacoes": "top valores: " + " | ".join(str(t[0]) for t in top),
        })

    df = pd.DataFrame(linhas)
    saida = RAIZ / "docs" / "dicionario_dados" / "cat.csv"
    saida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(saida, index=False, encoding="utf-8-sig")  # utf-8-sig abre bem no Excel
    return df

def auditar_formatos(colunas: list[str] | None = None) -> pd.DataFrame:
    """Padrão de formato dos valores (dígito -> 9) por arquivo e coluna."""
    paths = carregar_yaml("configs/paths.yaml")
    parquet = (RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet").as_posix()
    colunas = colunas or [
        "data_acidente", "data_afastamento", "data_despacho", "data_nascimento",
        "data_emissao_cat", "data_obito_segurado", "competencia_cat",
        "valor_remuneracao", "cbo", "cnae_empregador", "cid_10", "cnpj_empregador",
    ]
    con = conectar()
    partes = [
        f"""SELECT arquivo_origem, '{c}' AS coluna,
                   regexp_replace(CAST("{c}" AS VARCHAR), '[0-9]', '9', 'g') AS padrao,
                   count(*) AS n
            FROM read_parquet('{parquet}') GROUP BY 1, 3"""
        for c in colunas
    ]
    df = con.execute(" UNION ALL ".join(partes) + " ORDER BY coluna, arquivo_origem, n DESC").fetchdf()
    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)
    df.to_csv(pasta / "cat_padroes_formato.csv", index=False, encoding="utf-8-sig")
    return df


def diagnostico() -> None:
    """Responde às dúvidas que o padrão de formato deixou em aberto."""
    paths = carregar_yaml("configs/paths.yaml")
    p = (RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet").as_posix()
    con = conectar()

    def q(titulo: str, sql: str) -> None:
        print(f"\n== {titulo} ==")
        print(con.execute(sql).fetchdf().to_string(index=False))

    # A: linhas-lixo (impressas uma a uma, só com campos preenchidos)
    print("\n== A. Linhas-lixo (data_acidente fora do formato timestamp) ==")
    lixo = con.execute(f"""SELECT * FROM read_parquet('{p}')
        WHERE data_acidente IS NULL
           OR NOT regexp_matches(data_acidente, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}} ')
        ORDER BY arquivo_origem""").fetchdf()
    print(f"{len(lixo)} linhas")
    for _, r in lixo.iterrows():
        print({k: v for k, v in r.items() if v is not None and v == v})

    con.execute(f"""CREATE TEMP TABLE t AS
        SELECT arquivo_origem, competencia_cat, data_acidente, data_nascimento,
               data_emissao_cat, data_afastamento, data_despacho, data_obito_segurado,
               cbo, cnae_empregador, cid_10, valor_remuneracao, especie_beneficio
        FROM read_parquet('{p}')
        WHERE data_acidente ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}} '""")

    # B: o formato DD/MM/AAAA é sempre 00/00/0000?
    q("B. Datas no formato BR: quantas são 00/00/0000", " UNION ALL ".join(
        f"SELECT '{c}' AS coluna, count(*) FILTER (WHERE {c} LIKE '__/__/____') AS formato_br, "
        f"count(*) FILTER (WHERE {c} = '00/00/0000') AS zeradas FROM t"
        for c in ["data_afastamento", "data_despacho", "data_obito_segurado"]))

    # C: CBO/CNAE/CID fora do padrão
    q("C1. CBO com menos de 6 caracteres",
      "SELECT '[' || cbo || ']' AS cbo, count(*) n FROM t WHERE length(trim(cbo)) < 6 "
      "GROUP BY 1 ORDER BY n DESC LIMIT 12")
    q("C2. CNAE com 1-2 caracteres",
      "SELECT '[' || cnae_empregador || ']' AS cnae, count(*) n FROM t "
      "WHERE length(trim(cnae_empregador)) <= 2 GROUP BY 1 ORDER BY n DESC LIMIT 12")
    q("C3. CNAE com 3 caracteres (top 8)",
      "SELECT '[' || cnae_empregador || ']' AS cnae, count(*) n FROM t "
      "WHERE length(trim(cnae_empregador)) = 3 GROUP BY 1 ORDER BY n DESC LIMIT 8")
    q("C4. CID fora de ^[A-Z][0-9]{2,3}$ (após trim)",
      "SELECT coalesce('[' || cid_10 || ']', 'NULL') AS cid_10, count(*) n FROM t "
      "WHERE cid_10 IS NULL OR NOT regexp_matches(trim(cid_10), '^[A-Z][0-9]{2,3}$') "
      "GROUP BY 1 ORDER BY n DESC LIMIT 15")

    # D: remuneração (exclui o arquivo 14, que não tem a coluna)
    q("D1. valor_remuneracao: distribuição geral",
      """SELECT count(*) FILTER (WHERE v IS NULL) AS nao_numerico,
                count(*) FILTER (WHERE v <= 0) AS zero_ou_neg,
                count(*) FILTER (WHERE v > 0 AND v < 100) AS ate_99,
                count(*) FILTER (WHERE v >= 100000) AS acima_100mil,
                quantile_cont(v, [0.01, 0.5, 0.99, 0.999]) AS q01_50_99_999
         FROM (SELECT TRY_CAST(valor_remuneracao AS DOUBLE) v FROM t
               WHERE arquivo_origem NOT LIKE '14.%')""")
    q("D2. mediana nominal por arquivo",
      "SELECT left(arquivo_origem, 2) AS arq, median(TRY_CAST(valor_remuneracao AS DOUBLE)) AS mediana "
      "FROM t WHERE arquivo_origem NOT LIKE '14.%' GROUP BY 1 ORDER BY 1")

    # E: limites e consistência de datas
    q("E. Datas: limites e inconsistências", """
        SELECT min(a) AS min_acid, max(a) AS max_acid, min(e) AS min_emissao, max(e) AS max_emissao,
               min(n) AS min_nasc, max(n) AS max_nasc,
               count(*) FILTER (WHERE a IS NULL) AS acid_invalida,
               count(*) FILTER (WHERE a > e) AS acid_apos_emissao,
               count(*) FILTER (WHERE strftime(e, '%Y%m') <> competencia_cat) AS competencia_ne_emissao,
               count(*) FILTER (WHERE n IS NULL) AS nasc_invalida,
               count(*) FILTER (WHERE date_diff('year', n, a) < 14) AS idade_menor_14,
               count(*) FILTER (WHERE date_diff('year', n, a) > 90) AS idade_maior_90
        FROM (SELECT TRY_CAST(left(data_acidente, 10) AS DATE) a,
                     TRY_CAST(left(data_emissao_cat, 10) AS DATE) e,
                     TRY_CAST(left(data_nascimento, 10) AS DATE) n, competencia_cat FROM t)""")

    # F: a queda de benefícios por ano é real?
    q("F. % de CATs sem benefício por arquivo",
      "SELECT left(arquivo_origem, 2) AS arq, "
      "round(100.0 * count(*) FILTER (WHERE especie_beneficio ILIKE '%sem benef%') / count(*), 1) AS pct_sem_beneficio, "
      "count(*) AS n FROM t GROUP BY 1 ORDER BY 1")