import pandas as pd
import re

from inss_acidentes.cat import limpeza as _L
from inss_acidentes.common.logging import configurar_logs
from inss_acidentes.common.texto import lit, norm_sql, tokens_normalizados
from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar

EXTRAS = ("arquivo_origem", "ano_arquivo", "semestre_arquivo")
NAO_CATEGORICAS = {"competencia_cat", "valor_remuneracao", "cnpj_empregador", "numero_cat",
                   "razao_social", "local_acidente", "data_acidente", "data_afastamento",
                   "data_despacho", "data_nascimento", "data_emissao_cat", "data_obito_segurado"}
SUSPEITO = re.compile(r"IGNORAD|NAO INFORM|NAO CLASS|NAO SE APLICA|DESCONHEC|SEM INFORMA|"
                      r"^N/?A$|^0+$|^9+$|^-+$|^NONE$|^NULL$|^NAN$|^\{")
LIMIAR_VARIACAO_PP = 20.0   # variação de % vazio entre anos que sinaliza mudança de preenchimento
MAX_VALORES_LISTADOS = 100  # colunas com até N níveis listam todos; as demais, o top 30

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


# ----------------------------------------------------------------

def _ctx():
    paths = carregar_yaml("configs/paths.yaml")
    regras = carregar_yaml("configs/cat/regras_limpeza.yaml")
    parquet = (RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet").as_posix()
    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)
    return regras, parquet, pasta


def _salvar(df: pd.DataFrame, pasta, nome: str) -> pd.DataFrame:
    df.to_csv(pasta / nome, index=False, encoding="utf-8-sig")
    return df


def _md(df: pd.DataFrame, max_linhas: int = 30) -> str:
    """Tabela Markdown sem depender de tabulate."""
    d = df.head(max_linhas)
    cab = "| " + " | ".join(map(str, d.columns)) + " |\n|" + "|".join("---" for _ in d.columns) + "|\n"
    fmt = lambda x: "" if pd.isna(x) else (str(int(x)) if isinstance(x, float) and float(x).is_integer() else str(x))
    corpo = "".join("| " + " | ".join(fmt(x) for x in r) + " |\n" for r in d.itertuples(index=False))
    resto = f"\n_(mostrando {max_linhas} de {len(df)} linhas; tabela completa no CSV)_\n" if len(df) > max_linhas else ""
    return cab + corpo + resto


def _preparar(con, parquet: str, regras: dict):
    """Cria n (texto normalizado, nulos disfarçados = NULL) e t (datas tipadas), sem linhas-lixo."""
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()
            if r[0] not in EXTRAS]
    toks = regras["nulos_disfarcados"]
    con.execute(f"""CREATE TEMP TABLE n AS
        SELECT {', '.join(_L._norm(c, toks) for c in cols)}, arquivo_origem
        FROM read_parquet('{parquet}') WHERE {_L.LIXO}""")
    datas = regras["datas"]
    resto = [f'"{c}"' for c in cols if c not in datas]
    con.execute(f"CREATE TEMP TABLE t AS SELECT {', '.join(resto)}, "
                f"{', '.join(_L._data(c) for c in datas)}, arquivo_origem FROM n")
    return cols


# ---- 1. dimensões, tipos e % de vazios por coluna e por ano
def tabela_vazios_por_ano(con, parquet, regras, pasta, cols) -> tuple[pd.DataFrame, pd.DataFrame]:
    toks = ", ".join(lit(x) for x in tokens_normalizados(regras["nulos_disfarcados"]))
    aggs = []
    for c in cols:
        aggs.append(f'count(*) FILTER (WHERE "{c}" IS NULL) AS "{c}__real"')
        aggs.append(f'count(*) FILTER (WHERE "{c}" IS NOT NULL AND {norm_sql(c)} IN ({toks})) AS "{c}__disf"')
    ano = ("CASE WHEN regexp_matches(competencia_cat, '^[0-9]{6}$') "
           "THEN left(competencia_cat, 4) ELSE 'INVALIDO' END")
    wide = con.execute(f"""SELECT {ano} AS ano, count(*) AS n_linhas, {', '.join(aggs)}
                           FROM read_parquet('{parquet}') WHERE {_L.LIXO} GROUP BY 1 ORDER BY 1""").fetchdf()
    linhas = []
    for _, r in wide.iterrows():
        for c in cols:
            real, disf = int(r[f"{c}__real"]), int(r[f"{c}__disf"])
            linhas.append({"ano": r["ano"], "coluna": c, "n_linhas": int(r["n_linhas"]),
                           "n_nulos_reais": real, "n_nulos_disfarcados": disf,
                           "pct_vazio": round(100 * (real + disf) / r["n_linhas"], 2)})
    longo = _salvar(pd.DataFrame(linhas), pasta, "cat_nulos_por_ano.csv")
    pivot = longo.pivot(index="coluna", columns="ano", values="pct_vazio").reindex(cols)
    _salvar(pivot.reset_index(), pasta, "cat_nulos_por_ano_pivot.csv")

    tipos = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()}
    distintos = con.execute("SELECT " + ", ".join(f'count(DISTINCT "{c}") AS "{c}"' for c in cols)
                            + " FROM n").fetchdf().iloc[0]
    tot = longo.groupby("coluna")[["n_nulos_reais", "n_nulos_disfarcados", "n_linhas"]].sum()
    dim = pd.DataFrame({
        "coluna": cols,
        "tipo_bruto": [tipos.get(c, "") for c in cols],
        "n_distintos_sem_vazios": [int(distintos[c]) for c in cols],
        "pct_vazio_total": [round(100 * (tot.loc[c, "n_nulos_reais"] + tot.loc[c, "n_nulos_disfarcados"])
                                  / tot.loc[c, "n_linhas"], 2) for c in cols],
        "pct_vazio_min_ano": [pivot.loc[c].min() for c in cols],
        "pct_vazio_max_ano": [pivot.loc[c].max() for c in cols],
    })
    dim["variacao_pp"] = (dim.pct_vazio_max_ano - dim.pct_vazio_min_ano).round(2)
    dim["mudanca_de_preenchimento"] = dim.variacao_pp >= LIMIAR_VARIACAO_PP
    return _salvar(dim, pasta, "cat_dimensoes_tipos.csv"), longo


# ---- 2. valores mais frequentes das categóricas (inclui nulos disfarçados)
def tabela_top_valores(con, regras, pasta, cols) -> pd.DataFrame:
    toks = set(tokens_normalizados(regras["nulos_disfarcados"]))
    saida = []
    for c in cols:
        if c in NAO_CATEGORICAS:
            continue
        df = con.execute(f"""SELECT coalesce({norm_sql(c)}, '<NULO>') AS valor, any_value(trim("{c}")) AS exemplo_bruto,
                                    count(*) AS n FROM (SELECT * FROM read_parquet('{_ctx()[1]}')
                                    WHERE {_L.LIXO}) GROUP BY 1 ORDER BY n DESC""").fetchdf()
        total = df.n.sum()
        df = df.head(MAX_VALORES_LISTADOS if len(df) <= MAX_VALORES_LISTADOS else 30).copy()
        df.insert(0, "coluna", c)
        df.insert(1, "rank", range(1, len(df) + 1))
        df["pct"] = (100 * df.n / total).round(3)
        df["suspeito"] = df.valor.map(lambda v: bool(SUSPEITO.search(v)))
        df["ja_tratado_como_nulo"] = df.valor.isin(toks | {"<NULO>"})
        saida.append(df)
    return _salvar(pd.concat(saida, ignore_index=True), pasta, "cat_top_valores_categoricas.csv")


# ---- 3. duplicados
def tabela_duplicados(con, pasta, cols) -> pd.DataFrame:
    chave_exata = ", ".join(f'"{c}"' for c in cols)
    total = con.execute("SELECT count(*) FROM n").fetchone()[0]
    ex = con.execute(f"""SELECT count(*) AS grupos, sum(k) AS linhas, sum(k - 1) AS excedentes
        FROM (SELECT count(*) k FROM n GROUP BY hash({chave_exata}) HAVING count(*) > 1)""").fetchone()
    chave_ap = ('data_nascimento, sexo, data_acidente, cid_10, cnpj_empregador')
    ap = con.execute(f"""SELECT count(*), sum(k), sum(k - 1),
            count(*) FILTER (WHERE n_arq > 1), count(*) FILTER (WHERE n_tipo > 1)
        FROM (SELECT count(*) k, count(DISTINCT arquivo_origem) n_arq, count(DISTINCT tipo_cat) n_tipo
              FROM n WHERE data_nascimento IS NOT NULL AND data_acidente IS NOT NULL
                       AND cid_10 IS NOT NULL AND cnpj_empregador IS NOT NULL
              GROUP BY {chave_ap} HAVING count(*) > 1)""").fetchone()
    z = lambda x: int(x or 0)
    linhas = [
        ("exata (todas as colunas)", z(ex[0]), z(ex[1]), z(ex[2])),
        ("aproximada (nascimento, sexo, data do acidente, CID, CNPJ)", z(ap[0]), z(ap[1]), z(ap[2])),
        ("  aproximada: grupos que cruzam arquivos", z(ap[3]), None, None),
        ("  aproximada: grupos com tipo_cat diferentes (ex.: inicial + reabertura)", z(ap[4]), None, None),
    ]
    df = pd.DataFrame(linhas, columns=["tipo", "grupos", "linhas_envolvidas", "linhas_excedentes"])
    df["pct_excedentes"] = (100 * df.linhas_excedentes / total).round(3)
    return _salvar(df, pasta, "cat_duplicados.csv")


# ---- 4. inconsistências de data
def tabela_datas(con, regras, pasta) -> pd.DataFrame:
    ini, fim = regras["datas_regras"]["periodo_esperado"].values()
    idade = "date_sub('year', data_nascimento, data_acidente)"
    imin, imax = regras["idade"]["minima"], regras["idade"]["maxima"]
    ano_c = "TRY_CAST(left(competencia_cat, 4) AS INTEGER)"
    regras_sql = [
        ("data_acidente inválida ou ausente", "data_acidente IS NULL", "linha-lixo já removida; resto: ver limpeza"),
        ("data_emissao_cat inválida ou ausente", "data_emissao_cat IS NULL", "mantida (NULL)"),
        ("data_nascimento inválida ou ausente", "data_nascimento IS NULL", "mantida (idade fica NULL)"),
        ("acidente posterior à emissão da CAT", "data_acidente > data_emissao_cat", "remover linha"),
        (f"acidente posterior a {fim} (futuro)", f"data_acidente > DATE '{fim}'", "remover linha"),
        (f"emissão posterior a {fim} (futuro)", f"data_emissao_cat > DATE '{fim}'", "remover linha"),
        (f"acidente anterior a {ini}", f"data_acidente < DATE '{ini}'", "só auditoria (ver acidente_minimo)"),
        (f"emissão anterior a {ini}", f"data_emissao_cat < DATE '{ini}'", "só auditoria"),
        ("nascimento no futuro", f"data_nascimento > DATE '{fim}'", "idade vira NULL"),
        ("idade negativa (nascimento posterior ao acidente)", f"{idade} < 0", "idade vira NULL"),
        (f"idade entre 0 e {imin - 1}", f"{idade} BETWEEN 0 AND {imin - 1}", "idade vira NULL"),
        (f"idade acima de {imax}", f"{idade} > {imax}", "idade vira NULL"),
        ("afastamento anterior ao acidente", "data_afastamento < data_acidente", "anular data_afastamento"),
        ("óbito anterior ao acidente", "data_obito_segurado < data_acidente", "anular data_obito_segurado"),
        ("despacho anterior à emissão", "data_despacho < data_emissao_cat", "só auditoria"),
        ("competência diferente do mês de emissão",
         "competencia_cat IS NOT NULL AND data_emissao_cat IS NOT NULL "
         "AND strftime(data_emissao_cat, '%Y%m') <> competencia_cat", "só auditoria"),
        ("ano da competência fora do período esperado",
         f"{ano_c} NOT BETWEEN year(DATE '{ini}') AND year(DATE '{fim}')", "só auditoria"),
    ]
    sel = ", ".join(f"count(*) FILTER (WHERE {c}) AS r{i}" for i, (_, c, _) in enumerate(regras_sql))
    r = con.execute(f"SELECT count(*) AS total, {sel} FROM t").fetchdf().iloc[0]
    df = pd.DataFrame([(nome, int(r[f"r{i}"]), round(100 * r[f"r{i}"] / r["total"], 4), acao)
                       for i, (nome, _, acao) in enumerate(regras_sql)],
                      columns=["regra", "n", "pct", "acao_na_limpeza"])
    # datas informadas mas impossíveis de converter (ex.: 31/02): comparam n (texto) com t (tipada)
    return _salvar(df, pasta, "cat_inconsistencias_datas.csv")


# ---- 5. cardinalidade
def tabela_cardinalidade(con, regras, pasta, cols) -> pd.DataFrame:
    limiar = regras["categorias_raras"]["limiar"]
    candidatos = [c for c in cols if c not in NAO_CATEGORICAS] + ["cnpj_empregador", "local_acidente"]
    exprs = {c: f'"{c}"' for c in candidatos}
    exprs["cid_categoria"] = "left(cid_10, 3)"
    saida = []
    for nome, e in exprs.items():
        f = con.execute(f"SELECT {e} AS v, count(*) AS n FROM n WHERE {e} IS NOT NULL GROUP BY 1 "
                        f"ORDER BY n DESC").fetchdf()
        if f.empty:
            continue
        tot = f.n.sum()
        acum = f.n.cumsum() / tot
        raros = f[f.n < limiar * tot]
        saida.append({"coluna": nome, "n_niveis": len(f), "top1": f.v.iloc[0],
                      "top1_pct": round(100 * f.n.iloc[0] / tot, 2),
                      "niveis_para_90pct": int((acum < 0.90).sum() + 1),
                      "niveis_para_99pct": int((acum < 0.99).sum() + 1),
                      "niveis_raros": len(raros),
                      "pct_linhas_em_raros": round(100 * raros.n.sum() / tot, 3)})
    df = pd.DataFrame(saida).sort_values("n_niveis", ascending=False)
    return _salvar(df, pasta, "cat_cardinalidade.csv")


# ---- 6. distribuição por ano e por tipo de CAT
def tabela_distribuicao(con, pasta) -> tuple[pd.DataFrame, pd.DataFrame]:
    ano_tipo = con.execute("""
        SELECT coalesce(left(competencia_cat, 4), 'INVALIDO') AS ano, coalesce(tipo_cat, '<NULO>') AS tipo_cat,
               count(*) AS n FROM n GROUP BY 1, 2 ORDER BY 1, 2""").fetchdf()
    ano_tipo["pct_do_ano"] = (100 * ano_tipo.n / ano_tipo.groupby("ano").n.transform("sum")).round(2)
    _salvar(ano_tipo, pasta, "cat_distribuicao_ano_tipo.csv")
    arq = con.execute("""
        SELECT arquivo_origem, count(*) AS n_linhas,
               count(*) FILTER (WHERE tipo_cat LIKE '1%') AS n_inicial,
               count(*) FILTER (WHERE tipo_cat LIKE '2%') AS n_reabertura,
               count(*) FILTER (WHERE tipo_cat LIKE '3%') AS n_comunicacao_obito,
               round(100.0 * count(*) FILTER (WHERE especie_beneficio NOT LIKE '%SEM BENEF%')
                     / count(*), 1) AS pct_com_beneficio,
               round(100.0 * count(*) FILTER (WHERE data_despacho IS NOT NULL) / count(*), 1) AS pct_com_despacho
        FROM (SELECT n.*, t.data_despacho FROM n POSITIONAL JOIN t) GROUP BY 1 ORDER BY 1""").fetchdf()
    return ano_tipo, _salvar(arq, pasta, "cat_distribuicao_arquivo.csv")


# ---- encoding (mojibake)
def tabela_encoding(con, parquet, cols, pasta) -> pd.DataFrame:
    padrao = r"'\x{00C3}[\x{0080}-\x{00BF}]|\x{FFFD}'"
    texto = [c for c in cols if c not in {"competencia_cat", *NAO_CATEGORICAS - {"local_acidente", "razao_social"}}]
    sel = ", ".join(f"count(*) FILTER (WHERE regexp_matches(\"{c}\", {padrao})) AS \"{c}\"" for c in texto)
    r = con.execute(f"SELECT {sel} FROM read_parquet('{parquet}') WHERE {_L.LIXO}").fetchdf().iloc[0]
    df = pd.DataFrame({"coluna": texto, "linhas_com_mojibake": [int(r[c]) for c in texto]})
    return _salvar(df[df.linhas_com_mojibake > 0].reset_index(drop=True), pasta, "cat_encoding.csv")


# ---- relatório consolidado
def auditar_qualidade() -> None:
    log = configurar_logs("cat_auditoria")
    regras, parquet, pasta = _ctx()
    con = conectar()
    cols = _preparar(con, parquet, regras)
    n_total = con.execute(f"SELECT count(*) FROM read_parquet('{parquet}')").fetchone()[0]
    n_ok = con.execute("SELECT count(*) FROM n").fetchone()[0]

    log.info("1/6 vazios por ano"); dim, _ = tabela_vazios_por_ano(con, parquet, regras, pasta, cols)
    log.info("2/6 top valores"); top = tabela_top_valores(con, regras, pasta, cols)
    log.info("3/6 duplicados"); dup = tabela_duplicados(con, pasta, cols)
    log.info("4/6 datas"); dat = tabela_datas(con, regras, pasta)
    log.info("5/6 cardinalidade"); card = tabela_cardinalidade(con, regras, pasta, cols)
    log.info("6/6 distribuição + encoding")
    ano_tipo, arq = tabela_distribuicao(con, pasta)
    enc = tabela_encoding(con, parquet, cols, pasta)

    susp = top[top.suspeito & ~top.ja_tratado_como_nulo][["coluna", "valor", "n", "pct"]]
    tipo_geral = ano_tipo.groupby("tipo_cat").n.sum().reset_index()
    tipo_geral["pct"] = (100 * tipo_geral.n / tipo_geral.n.sum()).round(3)

    md = f"""# Auditoria da base CAT (Fase 2)

_Gerado por `scripts/cat_05_auditoria.py`. Todas as tabelas completas estão em `docs/qualidade/`._

## 0. Escopo e dimensões
- {len(pd.read_csv(pasta / 'cat_schema_por_arquivo.csv', usecols=['arquivo']).arquivo.unique()) if (pasta / 'cat_schema_por_arquivo.csv').exists() else 14} arquivos, **{n_total:,}** linhas no consolidado; **{n_total - n_ok}** linhas-lixo da exportação (cabeçalho e linha de total) removidas antes da auditoria; **{n_ok:,}** linhas auditadas, {len(cols)} colunas.
- Todas as colunas chegam como VARCHAR (tipo final definido na limpeza). Tipos e cardinalidade: `cat_dimensoes_tipos.csv`.
- Fase 1: o arquivo 14 (2018-S2) ganha `numero_cat` e `razao_social` e perde `valor_remuneracao`. Detalhe em `cat_schema_por_arquivo.csv`, `cat_matriz_nomes.csv` e `cat_matriz_tipos.csv`.

## 1. Preenchimento por coluna e por ano
`pct_vazio` soma nulos reais e nulos disfarçados. Colunas com variação de preenchimento >= {LIMIAR_VARIACAO_PP:.0f} p.p. entre anos:

{_md(dim[dim.mudanca_de_preenchimento][['coluna','pct_vazio_min_ano','pct_vazio_max_ano','variacao_pp']].sort_values('variacao_pp', ascending=False)) if dim.mudanca_de_preenchimento.any() else '_Nenhuma coluna ultrapassou o limiar._'}

Colunas com mais vazios no total:

{_md(dim.sort_values('pct_vazio_total', ascending=False)[['coluna','tipo_bruto','n_distintos_sem_vazios','pct_vazio_total']], 12)}

Tabelas: `cat_dimensoes_tipos.csv`, `cat_nulos_por_ano.csv`, `cat_nulos_por_ano_pivot.csv`.

## 2. Nulos disfarçados
Valores suspeitos (parecem "não informado") que **ainda não estão** na lista `nulos_disfarcados` de `regras_limpeza.yaml`. Decida, caso a caso, se entram na lista ou se são categorias legítimas:

{_md(susp) if len(susp) else '_Nenhum valor suspeito fora da lista atual._'}

Todos os valores por coluna (categorias com até {MAX_VALORES_LISTADOS} níveis) ou top 30: `cat_top_valores_categoricas.csv`. Problemas de encoding (mojibake):

{_md(enc) if len(enc) else '_Nenhum padrão de mojibake encontrado._'}

## 3. Duplicados
{_md(dup)}
Chave exata: todas as colunas (exceto metadados de arquivo). Chave aproximada: mesmo trabalhador (nascimento + sexo), data do acidente, CID e empregador, com todos preenchidos. Linhas aproximadas que cruzam arquivos ou mudam de `tipo_cat` costumam ser reaberturas ou republicações, não erro.

## 4. Inconsistências de data
{_md(dat, 40)}

## 5. Cardinalidade das categóricas
`niveis_para_90pct` e `niveis_para_99pct` mostram quantos níveis cobrem 90% e 99% das linhas; `niveis_raros` usa o limiar de {regras['categorias_raras']['limiar']:.1%}.

{_md(card, 40)}

## 6. Distribuição por ano e por tipo de CAT
Total por tipo:

{_md(tipo_geral)}

Por ano e tipo: `cat_distribuicao_ano_tipo.csv`. Por arquivo (inclui % de CATs com benefício, que varia entre arquivos):

{_md(arq, 20)}

## Decisões que esta auditoria alimenta
Ver `docs/decisoes_metodologicas.md` (seções "Fases 2-3").
"""
    (pasta / "cat_auditoria.md").write_text(md, encoding="utf-8")
    log.info("OK: relatório em docs/qualidade/cat_auditoria.md")


# ---- verificação do arquivo limpo (rodar depois da limpeza)
def verificar_limpo() -> pd.DataFrame:
    regras = carregar_yaml("configs/cat/regras_limpeza.yaml")
    paths = carregar_yaml("configs/paths.yaml")
    limpo = (RAIZ / paths["cat"]["processed"] / "cat_limpo.parquet").as_posix()
    pasta = RAIZ / "docs" / "qualidade"
    con = conectar()
    q = lambda sql: con.execute(sql).fetchone()[0]
    toks = ", ".join(lit(x) for x in tokens_normalizados(regras["nulos_disfarcados"]) if x)
    cat = regras["categoricas_nao_informado"]
    cols_cat = [c for c in cat]
    dr = regras["datas_regras"]
    ch = ", ".join(f'"{c}"' for (c, *_) in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{limpo}')").fetchall()
                   if c not in EXTRAS and not c.endswith("_agrup"))
    checks = [
        ("nenhuma categórica com NULL", sum(q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE \"{c}\" IS NULL") for c in cols_cat), 0),
        ("nenhum token de nulo disfarçado nas categóricas", sum(q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE \"{c}\" IN ({toks})") for c in cols_cat), 0),
        ("sem duplicatas exatas", q(f"SELECT count(*) - count(DISTINCT hash({ch})) FROM read_parquet('{limpo}')") if regras["duplicatas"]["remover_exatas"] else None, 0),
        ("acidente <= emissão", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE data_acidente > data_emissao_cat"), 0),
        ("sem datas futuras", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE data_acidente > DATE '{dr['periodo_esperado']['fim']}' OR data_emissao_cat > DATE '{dr['periodo_esperado']['fim']}'"), 0),
        ("idade dentro de [min, max]", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE idade < {regras['idade']['minima']} OR idade > {regras['idade']['maxima']}"), 0),
        ("remuneração dentro da faixa", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE valor_remuneracao < {regras['remuneracao']['minima']} OR valor_remuneracao > {regras['remuneracao']['maxima']}"), 0),
        ("CBO com 6 dígitos ou NAO_INFORMADO", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE cbo <> 'NAO_INFORMADO' AND length(cbo) <> 6"), 0),
        ("CNAE com 4 dígitos ou NAO_INFORMADO", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE cnae_empregador <> 'NAO_INFORMADO' AND length(cnae_empregador) <> 4"), 0),
        ("_agrup sem níveis raros (só OUTROS e NAO_INFORMADO absorvem)", sum(q(f"SELECT count(*) FROM (SELECT \"{c}_agrup\" FROM read_parquet('{limpo}') GROUP BY 1 HAVING count(*) < {regras['categorias_raras']['limiar']} * (SELECT count(*) FROM read_parquet('{limpo}')) AND \"{c}_agrup\" NOT IN ('OUTROS','NAO_INFORMADO'))") for c in regras["categorias_raras"]["colunas"]), 0),
    ]
    df = pd.DataFrame([(n, v, e, "OK" if v == e or v is None else "FALHOU") for n, v, e in checks],
                      columns=["verificacao", "encontrado", "esperado", "status"])
    _salvar(df, pasta, "cat_verificacao_limpo.csv")
    print(df.to_string(index=False))
    return df


# ---- dicionário de dados completo (esqueleto automático + conteúdo manual + achados da auditoria)
def montar_dicionario() -> pd.DataFrame:
    """Junta cat_esqueleto.csv, configs/cat/dicionario_manual.yaml e a auditoria em cat.csv."""
    manual = carregar_yaml("configs/cat/dicionario_manual.yaml")
    regras = carregar_yaml("configs/cat/regras_limpeza.yaml")
    schema = carregar_yaml("configs/cat/harmonizacao_schema.yaml")["colunas"]
    pasta = RAIZ / "docs" / "qualidade"
    dic = RAIZ / "docs" / "dicionario_dados"
    esq = pd.read_csv(dic / "cat_esqueleto.csv").set_index("nome_padronizado") if (dic / "cat_esqueleto.csv").exists() else pd.DataFrame()
    top = pd.read_csv(pasta / "cat_top_valores_categoricas.csv", keep_default_na=False) if (pasta / "cat_top_valores_categoricas.csv").exists() else pd.DataFrame()
    dim = pd.read_csv(pasta / "cat_dimensoes_tipos.csv").set_index("coluna") if (pasta / "cat_dimensoes_tipos.csv").exists() else pd.DataFrame()
    cat_ni, raras = set(regras["categoricas_nao_informado"]), set(regras["categorias_raras"]["colunas"])
    descart = set(regras["colunas_descartadas"])
    linhas = []
    for col, m in manual.items():
        trat = []
        if col in descart: trat.append("descartada")
        if col in regras["datas"]: trat.append("00/00/0000 e inválidas -> NULL")
        if col in cat_ni: trat.append("nulo -> NAO_INFORMADO")
        if col in raras: trat.append(f"níveis < {regras['categorias_raras']['limiar']:.1%} -> OUTROS em {col}_agrup")
        if col == "idade": trat.append(f"fora de {regras['idade']['minima']}-{regras['idade']['maxima']} -> NULL")
        if col == "valor_remuneracao": trat.append(f"fora de R$ {regras['remuneracao']['minima']}-{regras['remuneracao']['maxima']} -> NULL")
        sub = top[top.coluna == col] if len(top) else top
        if len(sub): sub = sub.assign(ja_tratado_como_nulo=sub.ja_tratado_como_nulo.astype(str) == "True")
        disf = sorted(sub[sub.ja_tratado_como_nulo & ~sub.valor.isin(["<NULO>", ""])].valor.unique()) if len(sub) else []
        vals = ""
        if m.get("valores") == "auto" and len(sub) and len(sub) <= 30:
            vals = " | ".join(sub[~sub.ja_tratado_como_nulo].valor)
        linhas.append({
            "nome_padronizado": col,
            "origem": "derivada" if m.get("derivada") else "bruta",
            "nome_original": " | ".join(schema[col]) if col in schema else "",
            "tipo_final": m["tipo"], "descricao": m["descricao"],
            "valores_validos": vals, "nulos_disfarcados": " | ".join(disf),
            "tratamento_na_limpeza": "; ".join(trat),
            "pct_vazio_bruto": dim.pct_vazio_total.get(col, "") if len(dim) else "",
            "n_distintos_bruto": esq.n_distintos.get(col, "") if len(esq) else "",
            "validado": bool(m.get("validado", False)),
        })
    df = pd.DataFrame(linhas)
    df.to_csv(dic / "cat.csv", index=False, encoding="utf-8-sig")
    return df
