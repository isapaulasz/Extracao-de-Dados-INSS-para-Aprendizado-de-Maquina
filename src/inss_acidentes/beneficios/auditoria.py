"""Fase 2 (Benefícios Concedidos): auditoria de qualidade ANTES da limpeza, e verificação DEPOIS.

Mesmo desenho de cat/auditoria.py. O BC não tem data de acidente, CAT, emitente nem empregador,
então as verificações de data, duplicata aproximada e distribuição usam as colunas do BC
(DIB, DDB, DCB, espécie, competência).
"""
import re

import pandas as pd

from inss_acidentes.beneficios import limpeza as _L
from inss_acidentes.cat.auditoria import LIMIAR_VARIACAO_PP, MAX_VALORES_LISTADOS, SUSPEITO, _md, _salvar
from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar
from inss_acidentes.common.logging import configurar_logs
from inss_acidentes.common.texto import lit, norm_sql, normalizar_py, tokens_normalizados

EXTRAS = ("arquivo_origem", "ano_arquivo")
NAO_CATEGORICAS = {"competencia_concessao", "qt_sm_rmi", "data_nascimento", "data_dcb", "data_ddb", "data_dib"}
SUSPEITO_BC = re.compile(SUSPEITO.pattern + r"|ZERAD|EM BRANCO")


def _ctx():
    paths = carregar_yaml("configs/paths.yaml")
    regras = carregar_yaml(_L.CFG)
    parquet = (RAIZ / paths["beneficios"]["interim"] / "bc_consolidado.parquet").as_posix()
    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)
    return regras, parquet, pasta


def _preparar(con, parquet: str, regras: dict) -> list[str]:
    """Cria n (texto normalizado, nulos disfarçados = NULL) e t (datas tipadas), sem linhas-lixo."""
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()
            if r[0] not in EXTRAS]
    toks = regras["nulos_disfarcados"]
    con.execute(f"""CREATE TEMP TABLE n AS
        SELECT {', '.join(_L._norm(c, toks) for c in cols)}, arquivo_origem, ano_arquivo
        FROM read_parquet('{parquet}') WHERE {_L.lixo_sql(regras)}""")
    datas = regras["datas"]
    resto = [f'"{c}"' for c in cols if c not in datas]
    con.execute(f"CREATE TEMP TABLE t AS SELECT {', '.join(resto)}, "
                f"{', '.join(_L._data(c) for c in datas)}, arquivo_origem, ano_arquivo FROM n")
    return cols


# ---- 1. dimensões, tipos e % de vazios por coluna e por ano (ano = ano do arquivo)
def tabela_vazios_por_ano(con, parquet, regras, pasta, cols) -> tuple[pd.DataFrame, pd.DataFrame]:
    toks = ", ".join(lit(x) for x in tokens_normalizados(regras["nulos_disfarcados"]))
    aggs = []
    for c in cols:
        aggs.append(f'count(*) FILTER (WHERE "{c}" IS NULL) AS "{c}__real"')
        aggs.append(f'count(*) FILTER (WHERE "{c}" IS NOT NULL AND {norm_sql(c)} IN ({toks})) AS "{c}__disf"')
    wide = con.execute(f"""SELECT CAST(ano_arquivo AS VARCHAR) AS ano, count(*) AS n_linhas, {', '.join(aggs)}
                           FROM read_parquet('{parquet}') WHERE {_L.lixo_sql(regras)}
                           GROUP BY 1 ORDER BY 1""").fetchdf()
    linhas = []
    for _, r in wide.iterrows():
        for c in cols:
            real, disf = int(r[f"{c}__real"]), int(r[f"{c}__disf"])
            linhas.append({"ano": r["ano"], "coluna": c, "n_linhas": int(r["n_linhas"]),
                           "n_nulos_reais": real, "n_nulos_disfarcados": disf,
                           "pct_vazio": round(100 * (real + disf) / r["n_linhas"], 2)})
    longo = _salvar(pd.DataFrame(linhas), pasta, "bc_nulos_por_ano.csv")
    pivot = longo.pivot(index="coluna", columns="ano", values="pct_vazio").reindex(cols)
    _salvar(pivot.reset_index(), pasta, "bc_nulos_por_ano_pivot.csv")

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
    return _salvar(dim, pasta, "bc_dimensoes_tipos.csv"), longo


# ---- 2. valores mais frequentes das categóricas (inclui nulos disfarçados)
def tabela_top_valores(con, parquet, regras, pasta, cols) -> pd.DataFrame:
    toks = set(tokens_normalizados(regras["nulos_disfarcados"]))
    saida = []
    for c in cols:
        if c in NAO_CATEGORICAS:
            continue
        df = con.execute(f"""SELECT coalesce({norm_sql(c)}, '<NULO>') AS valor, any_value(trim("{c}")) AS exemplo_bruto,
                                    count(*) AS n FROM (SELECT * FROM read_parquet('{parquet}')
                                    WHERE {_L.lixo_sql(regras)}) GROUP BY 1 ORDER BY n DESC""").fetchdf()
        total = df.n.sum()
        df = df.head(MAX_VALORES_LISTADOS if len(df) <= MAX_VALORES_LISTADOS else 30).copy()
        df.insert(0, "coluna", c)
        df.insert(1, "rank", range(1, len(df) + 1))
        df["pct"] = (100 * df.n / total).round(3)
        df["suspeito"] = df.valor.map(lambda v: bool(SUSPEITO_BC.search(v)))
        df["ja_tratado_como_nulo"] = df.valor.isin(toks | {"<NULO>"})
        saida.append(df)
    return _salvar(pd.concat(saida, ignore_index=True), pasta, "bc_top_valores_categoricas.csv")


# ---- 3. duplicados
def tabela_duplicados(con, pasta, cols) -> tuple[pd.DataFrame, pd.DataFrame]:
    chave_exata = ", ".join(f'"{c}"' for c in cols)
    total = con.execute("SELECT count(*) FROM n").fetchone()[0]
    ex = con.execute(f"""SELECT count(*) AS grupos, sum(k) AS linhas, sum(k - 1) AS excedentes
        FROM (SELECT count(*) k FROM n GROUP BY hash({chave_exata}) HAVING count(*) > 1)""").fetchone()
    # BC não tem identificador do beneficiário: "mesma pessoa e mesmo benefício" = nascimento, sexo,
    # espécie, início (DIB), CID e município de residência, todos preenchidos.
    chave_ap = "data_nascimento, sexo, especie, data_dib, cid_10, municipio_residencia"
    ap = con.execute(f"""SELECT count(*), sum(k), sum(k - 1),
            count(*) FILTER (WHERE n_arq > 1), count(*) FILTER (WHERE n_ddb > 1)
        FROM (SELECT count(*) k, count(DISTINCT arquivo_origem) n_arq, count(DISTINCT data_ddb) n_ddb
              FROM t WHERE data_nascimento IS NOT NULL AND data_dib IS NOT NULL AND sexo IS NOT NULL
                       AND especie IS NOT NULL AND cid_10 IS NOT NULL AND municipio_residencia IS NOT NULL
              GROUP BY {chave_ap} HAVING count(*) > 1)""").fetchone()
    z = lambda x: int(x or 0)
    linhas = [
        ("exata (todas as colunas)", z(ex[0]), z(ex[1]), z(ex[2])),
        ("aproximada (nascimento, sexo, espécie, DIB, CID, município)", z(ap[0]), z(ap[1]), z(ap[2])),
        ("  aproximada: grupos que cruzam arquivos", z(ap[3]), None, None),
        ("  aproximada: grupos com DDB diferentes (possível reconcessão)", z(ap[4]), None, None),
    ]
    df = pd.DataFrame(linhas, columns=["tipo", "grupos", "linhas_envolvidas", "linhas_excedentes"])
    df["pct_excedentes"] = (100 * df.linhas_excedentes / total).round(3)
    _salvar(df, pasta, "bc_duplicados.csv")

    # onde caem as duplicatas exatas: mede o risco de remover casos legítimos (ex.: pensão entre irmãos)
    exc = con.execute(f"""SELECT coalesce(especie, '<NULO>') AS especie, sum(k - 1) AS duplicatas_exatas
        FROM (SELECT any_value(especie) AS especie, count(*) k FROM n
              GROUP BY hash({chave_exata}) HAVING count(*) > 1) GROUP BY 1""").fetchdf()
    tot = con.execute("SELECT coalesce(especie, '<NULO>') AS especie, count(*) AS n_linhas FROM n GROUP BY 1").fetchdf()
    por_esp = tot.merge(exc, on="especie", how="left").fillna({"duplicatas_exatas": 0})
    por_esp["duplicatas_exatas"] = por_esp.duplicatas_exatas.astype(int)
    por_esp["pct_da_especie"] = (100 * por_esp.duplicatas_exatas / por_esp.n_linhas).round(3)
    por_esp = por_esp.sort_values(["duplicatas_exatas", "n_linhas"], ascending=False).reset_index(drop=True)
    return df, _salvar(por_esp, pasta, "bc_duplicados_por_especie.csv")


# ---- 4. inconsistências de data e idade
def tabela_datas(con, regras, pasta) -> pd.DataFrame:
    per = regras["datas_regras"]["periodo_esperado"]
    ini, fim = per["inicio"], per["fim"]          # por nome: a ordem das chaves do YAML não importa
    idade = _L.idade_sql(regras["idade"]["referencia"])
    imin, imax = regras["idade"]["minima"], regras["idade"]["maxima"]
    comp = _L._competencia("competencia_concessao")
    regras_sql = [
        ("competência ausente ou fora do formato mês/AAAA", f"{comp} IS NULL", "mantida (NULL)"),
        ("data_nascimento inválida ou ausente", "data_nascimento IS NULL", "mantida (idade fica NULL)"),
        ("data_dib inválida ou ausente", "data_dib IS NULL", "mantida (NULL)"),
        ("data_ddb inválida ou ausente", "data_ddb IS NULL", "mantida (NULL)"),
        ("data_dcb ausente (00/00/0000: sem cessação)", "data_dcb IS NULL", "esperado; mantida (NULL)"),
        ("data_dib posterior à data_ddb", "data_dib > data_ddb", "só auditoria (ver remover_se_dib_apos_ddb)"),
        (f"data_dib posterior a {fim} (futuro)", f"data_dib > DATE '{fim}'", "remover linha"),
        (f"data_ddb posterior a {fim} (futuro)", f"data_ddb > DATE '{fim}'", "remover linha"),
        (f"data_dib anterior a {ini}", f"data_dib < DATE '{ini}'", "só auditoria (DIB retroativa é possível)"),
        (f"data_ddb anterior a {ini}", f"data_ddb < DATE '{ini}'", "só auditoria"),
        ("data_dcb anterior à data_dib", "data_dcb < data_dib", "anular data_dcb"),
        ("data_dcb anterior à data_ddb", "data_dcb < data_ddb", "só auditoria"),
        (f"data_dcb posterior a {fim}", f"data_dcb > DATE '{fim}'", "só auditoria (cessação estimada)"),
        ("nascimento no futuro", f"data_nascimento > DATE '{fim}'", "idade vira NULL"),
        ("idade negativa (nascimento posterior ao início do benefício)", f"{idade} < 0", "idade vira NULL"),
        (f"idade abaixo de {imin}", f"{idade} < {imin}", "idade vira NULL"),
        ("idade entre 0 e 13 (comum em pensão por morte)", f"{idade} BETWEEN 0 AND 13", "mantida; ver bc_idade_por_especie.csv"),
        ("idade acima de 90", f"{idade} > 90", "só auditoria"),
        (f"idade acima de {imax}", f"{idade} > {imax}", "idade vira NULL"),
        ("competência diferente do mês da data_ddb",
         f"{comp} IS NOT NULL AND data_ddb IS NOT NULL AND strftime(data_ddb, '%Y%m') <> CAST({comp} AS VARCHAR)",
         "só auditoria (testa a hipótese competência = mês do despacho)"),
        ("ano da competência diferente do ano do arquivo",
         f"{comp} IS NOT NULL AND {comp} // 100 <> ano_arquivo", "só auditoria"),
        ("ano da competência fora do período esperado",
         f"{comp} // 100 NOT BETWEEN year(DATE '{ini}') AND year(DATE '{fim}')", "só auditoria"),
    ]
    sel = ", ".join(f"count(*) FILTER (WHERE {c}) AS r{i}" for i, (_, c, _) in enumerate(regras_sql))
    r = con.execute(f"SELECT count(*) AS total, {sel} FROM t").fetchdf().iloc[0]
    df = pd.DataFrame([(nome, int(r[f"r{i}"]), round(100 * r[f"r{i}"] / r["total"], 4), acao)
                       for i, (nome, _, acao) in enumerate(regras_sql)],
                      columns=["regra", "n", "pct", "acao_na_limpeza"])
    return _salvar(df, pasta, "bc_inconsistencias_datas.csv")


def tabela_numericas(con, regras, pasta) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Distribuição de qt_sm_rmi e da idade (para confirmar as faixas do YAML) e idade por espécie."""
    rmi = _L._rmi("qt_sm_rmi")
    rmax = regras["qt_sm_rmi"]["maxima"]
    idade = _L.idade_sql(regras["idade"]["referencia"])
    q = lambda e: con.execute(f"SELECT quantile_cont({e}, [0.001, 0.01, 0.5, 0.99, 0.999]) FROM t").fetchone()[0]
    lin = []
    r = con.execute(f"""SELECT count(*) FILTER (WHERE qt_sm_rmi IS NOT NULL),
            count(*) FILTER (WHERE qt_sm_rmi IS NOT NULL AND {rmi} IS NULL),
            count(*) FILTER (WHERE {rmi} <= 0), count(*) FILTER (WHERE {rmi} < 1),
            count(*) FILTER (WHERE {rmi} > {rmax}), min({rmi}), max({rmi}) FROM t""").fetchone()
    for nome, v in zip(["n_preenchidos", "n_nao_numerico", "n_zero_ou_negativo", "n_abaixo_de_1_sm",
                        f"n_acima_de_{rmax}", "minimo", "maximo"], r):
        lin.append(("qt_sm_rmi", nome, v))
    for p, v in zip(["p0.1", "p1", "mediana", "p99", "p99.9"], q(rmi)):
        lin.append(("qt_sm_rmi", p, v))
    r = con.execute(f"""SELECT count(*) FILTER (WHERE {idade} IS NOT NULL), min({idade}), max({idade}),
            count(*) FILTER (WHERE {idade} < 0), count(*) FILTER (WHERE {idade} BETWEEN 0 AND 13),
            count(*) FILTER (WHERE {idade} > 90) FROM t""").fetchone()
    for nome, v in zip(["n_calculada", "minimo", "maximo", "n_negativa", "n_0_a_13", "n_acima_de_90"], r):
        lin.append(("idade", nome, v))
    for p, v in zip(["p0.1", "p1", "mediana", "p99", "p99.9"], q(idade)):
        lin.append(("idade", p, v))
    num = _salvar(pd.DataFrame(lin, columns=["variavel", "metrica", "valor"]), pasta, "bc_numericas.csv")
    por_esp = con.execute(f"""SELECT coalesce(especie, '<NULO>') AS especie, count(*) AS n,
            count(*) FILTER (WHERE {idade} < 14) AS n_idade_menor_14,
            count(*) FILTER (WHERE {idade} > 90) AS n_idade_maior_90,
            median({idade}) AS mediana_idade
        FROM t GROUP BY 1 ORDER BY n DESC LIMIT 30""").fetchdf()
    return num, _salvar(por_esp, pasta, "bc_idade_por_especie.csv")


# ---- 5. cardinalidade
def tabela_cardinalidade(con, regras, pasta, cols) -> pd.DataFrame:
    limiar = regras["categorias_raras"]["limiar"]
    candidatos = [c for c in cols if c not in NAO_CATEGORICAS]
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
    return _salvar(df, pasta, "bc_cardinalidade.csv")


# ---- 6. distribuição por ano e por espécie, e sugestão de escopo acidentário
def tabela_distribuicao(con, regras, pasta) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ano_esp = con.execute("""
        SELECT CAST(ano_arquivo AS VARCHAR) AS ano, coalesce(especie, '<NULO>') AS especie, count(*) AS n
        FROM n GROUP BY 1, 2 ORDER BY 1, 3 DESC""").fetchdf()
    ano_esp["pct_do_ano"] = (100 * ano_esp.n / ano_esp.groupby("ano").n.transform("sum")).round(2)
    _salvar(ano_esp, pasta, "bc_distribuicao_ano_especie.csv")

    comp = _L._competencia("competencia_concessao")
    arq = con.execute(f"""
        SELECT arquivo_origem, count(*) AS n_linhas,
               round(100.0 * count(*) FILTER (WHERE cid_10 IS NOT NULL) / count(*), 1) AS pct_com_cid,
               round(100.0 * count(*) FILTER (WHERE data_dcb IS NOT NULL) / count(*), 1) AS pct_com_dcb,
               count(*) FILTER (WHERE {comp} // 100 <> ano_arquivo) AS n_competencia_fora_do_ano_arquivo
        FROM t GROUP BY 1 ORDER BY 1""").fetchdf()
    _salvar(arq, pasta, "bc_distribuicao_arquivo.csv")

    esp = con.execute("""
        SELECT coalesce(especie, '<NULO>') AS especie, count(*) AS n,
               count(DISTINCT ano_arquivo) AS anos_presentes, min(ano_arquivo) AS primeiro_ano,
               max(ano_arquivo) AS ultimo_ano
        FROM n GROUP BY 1 ORDER BY n DESC""").fetchdf()
    esp["pct"] = (100 * esp.n / esp.n.sum()).round(3)
    inc = re.compile(regras["escopo"]["sugestao_regex_incluir"])
    exc = re.compile(regras["escopo"]["sugestao_regex_excluir"])
    esp["sugestao_acidentaria"] = esp.especie.map(lambda e: bool(inc.search(e) and not exc.search(e)))
    esp["no_yaml_atual"] = esp.especie.isin(_L.especies_normalizadas(regras))
    return ano_esp, arq, _salvar(esp, pasta, "bc_especies.csv")


# ---- encoding (mojibake)
def tabela_encoding(con, parquet, regras, cols, pasta) -> pd.DataFrame:
    padrao = r"'\x{00C3}[\x{0080}-\x{00BF}]|\x{FFFD}'"
    texto = [c for c in cols if c not in NAO_CATEGORICAS]
    sel = ", ".join(f"count(*) FILTER (WHERE regexp_matches(\"{c}\", {padrao})) AS \"{c}\"" for c in texto)
    r = con.execute(f"SELECT {sel} FROM read_parquet('{parquet}') WHERE {_L.lixo_sql(regras)}").fetchdf().iloc[0]
    df = pd.DataFrame({"coluna": texto, "linhas_com_mojibake": [int(r[c]) for c in texto]})
    return _salvar(df[df.linhas_com_mojibake > 0].reset_index(drop=True), pasta, "bc_encoding.csv")


# ---- relatório consolidado
def auditar_qualidade() -> None:
    log = configurar_logs("bc_auditoria")
    regras, parquet, pasta = _ctx()
    con = conectar()
    cols = _preparar(con, parquet, regras)
    n_total = con.execute(f"SELECT count(*) FROM read_parquet('{parquet}')").fetchone()[0]
    n_ok = con.execute("SELECT count(*) FROM n").fetchone()[0]

    log.info("1/6 vazios por ano"); dim, _ = tabela_vazios_por_ano(con, parquet, regras, pasta, cols)
    log.info("2/6 top valores"); top = tabela_top_valores(con, parquet, regras, pasta, cols)
    log.info("3/6 duplicados"); dup, dup_esp = tabela_duplicados(con, pasta, cols)
    log.info("4/6 datas e numéricas"); dat = tabela_datas(con, regras, pasta)
    num, idade_esp = tabela_numericas(con, regras, pasta)
    log.info("5/6 cardinalidade"); card = tabela_cardinalidade(con, regras, pasta, cols)
    log.info("6/6 distribuição + encoding")
    ano_esp, arq, esp = tabela_distribuicao(con, regras, pasta)
    enc = tabela_encoding(con, parquet, regras, cols, pasta)

    susp = top[top.suspeito & ~top.ja_tratado_como_nulo][["coluna", "valor", "n", "pct"]]
    sugeridas = esp[esp.sugestao_acidentaria]
    yaml_sugerido = "\n".join(f"    - {e}" for e in sugeridas.especie)
    mud = dim[dim.mudanca_de_preenchimento][["coluna", "pct_vazio_min_ano", "pct_vazio_max_ano", "variacao_pp"]]

    md = f"""# Auditoria da base BC (Fase 2)

_Gerado por `scripts/bc_02_auditoria.py`. Todas as tabelas completas estão em `docs/qualidade/` (prefixo `bc_`)._

## 0. Escopo e dimensões
- {arq.arquivo_origem.nunique()} arquivos, **{n_total:,}** linhas no consolidado; **{n_total - n_ok:,}** linhas-lixo (competência fora do formato mês/AAAA) removidas antes da auditoria; **{n_ok:,}** linhas auditadas, {len(cols)} colunas.
- Todas as colunas chegam como VARCHAR (tipo final definido na limpeza). Tipos e cardinalidade: `bc_dimensoes_tipos.csv`.
- O BC não tem data do acidente, CAT, emitente nem empregador. O "ano" das tabelas é o ano do arquivo (`ano_arquivo`).

## 1. Preenchimento por coluna e por ano
`pct_vazio` soma nulos reais e nulos disfarçados. Colunas com variação de preenchimento >= {LIMIAR_VARIACAO_PP:.0f} p.p. entre anos:

{_md(mud) if len(mud) else "_Nenhuma coluna com mudança de preenchimento._"}

Colunas com mais vazios no total:

{_md(dim.sort_values("pct_vazio_total", ascending=False)[["coluna", "tipo_bruto", "n_distintos_sem_vazios", "pct_vazio_total"]], 12)}

Tabelas: `bc_dimensoes_tipos.csv`, `bc_nulos_por_ano.csv`, `bc_nulos_por_ano_pivot.csv`.

## 2. Nulos disfarçados
Valores suspeitos (parecem "não informado") que **ainda não estão** na lista `nulos_disfarcados` de `regras_limpeza.yaml`. Decida, caso a caso, se entram na lista ou se são categorias legítimas:

{_md(susp) if len(susp) else "_Nenhum valor suspeito fora da lista atual._"}

Todos os valores por coluna (categorias com até {MAX_VALORES_LISTADOS} níveis) ou top 30: `bc_top_valores_categoricas.csv`. Problemas de encoding (mojibake):

{_md(enc) if len(enc) else "_Nenhum padrão de mojibake encontrado._"}

## 3. Duplicados
{_md(dup)}
Chave exata: todas as colunas (exceto metadados de arquivo). Chave aproximada: o BC não tem identificador, então "mesmo beneficiário e mesmo benefício" = nascimento + sexo + espécie + DIB + CID + município, todos preenchidos. Linhas aproximadas com DDB diferente costumam ser reconcessões, não erro.

**Risco da remoção de duplicatas exatas:** sem identificador, linhas legitimamente idênticas (ex.: pensão por morte entre irmãos com a mesma data de nascimento) também seriam removidas. Onde caem as duplicatas exatas (`bc_duplicados_por_especie.csv`):

{_md(dup_esp[dup_esp.duplicatas_exatas > 0], 10) if (dup_esp.duplicatas_exatas > 0).any() else "_Nenhuma duplicata exata._"}

## 4. Inconsistências de data e idade
{_md(dat, 30)}
A coluna `acao_na_limpeza` mostra o que a Fase 3 faz com cada regra. Distribuição de `qt_sm_rmi` e da idade (para confirmar as faixas do YAML): `bc_numericas.csv`; idade por espécie: `bc_idade_por_especie.csv`.

{_md(num, 40)}

## 5. Cardinalidade das categóricas
{_md(card, 15)}

## 6. Distribuição por ano e por espécie
O BC traz **todos** os auxílios do INSS; a limpeza só restringe a benefícios acidentários se `escopo.especies_manter` estiver preenchido. Lista completa de espécies: `bc_especies.csv`. A sugestão abaixo é automática (nome contém "{regras['escopo']['sugestao_regex_incluir']}" e não contém "{regras['escopo']['sugestao_regex_excluir']}") e **precisa ser conferida por você**:

{_md(esp, 40)}

Sugestão para colar em `configs/beneficios/regras_limpeza.yaml` (revise antes):

```yaml
escopo:
  especies_manter:
{yaml_sugerido if yaml_sugerido else "    []   # nenhuma espécie casou com a sugestão"}
```

Por ano e espécie: `bc_distribuicao_ano_especie.csv`. Por arquivo:

{_md(arq)}
"""
    (pasta / "bc_auditoria.md").write_text(md, encoding="utf-8")
    log.info("OK: relatório em docs/qualidade/bc_auditoria.md")


# ---- verificação do arquivo limpo (rodar depois da limpeza)
def verificar_limpo() -> pd.DataFrame:
    regras = carregar_yaml(_L.CFG)
    paths = carregar_yaml("configs/paths.yaml")
    limpo = (RAIZ / paths["beneficios"]["processed"] / "bc_limpo.parquet").as_posix()
    pasta = RAIZ / "docs" / "qualidade"
    con = conectar()
    q = lambda sql: con.execute(sql).fetchone()[0]
    toks = ", ".join(lit(x) for x in tokens_normalizados(regras["nulos_disfarcados"]) if x)
    cols_cat = regras["categoricas_nao_informado"]
    dr = regras["datas_regras"]
    fim = dr["periodo_esperado"]["fim"]
    ch = ", ".join(f'"{c}"' for (c, *_) in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{limpo}')").fetchall()
                   if c not in EXTRAS and not c.endswith("_agrup"))
    r = regras["qt_sm_rmi"]
    manter = _L.especies_normalizadas(regras)
    checks = [
        ("nenhuma categórica com NULL", sum(q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE \"{c}\" IS NULL") for c in cols_cat), 0),
        ("nenhum token de nulo disfarçado nas categóricas", sum(q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE \"{c}\" IN ({toks})") for c in cols_cat), 0),
        ("sem duplicatas exatas", q(f"SELECT count(*) - count(DISTINCT hash({ch})) FROM read_parquet('{limpo}')") if regras["duplicatas"]["remover_exatas"] else None, 0),
        ("sem datas futuras (DIB e DDB)", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE data_dib > DATE '{fim}' OR data_ddb > DATE '{fim}'"), 0),
        ("cessação (DCB) não anterior ao início (DIB)", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE data_dcb < data_dib") if dr["anular_dcb_antes_dib"] else None, 0),
        ("competência AAAAMM com mês 1-12", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE competencia_concessao IS NOT NULL AND competencia_concessao % 100 NOT BETWEEN 1 AND 12"), 0),
        ("idade dentro de [min, max]", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE idade < {regras['idade']['minima']} OR idade > {regras['idade']['maxima']}"), 0),
        ("qt_sm_rmi dentro da faixa", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE qt_sm_rmi <= {r['minima_exclusiva']} OR qt_sm_rmi > {r['maxima']}"), 0),
        ("só espécies de especies_manter (se definida)", q(f"SELECT count(*) FROM read_parquet('{limpo}') WHERE especie NOT IN ({', '.join(lit(x) for x in manter)})") if manter else None, 0),
        ("_agrup sem níveis raros (só OUTROS e NAO_INFORMADO absorvem)", sum(q(f"SELECT count(*) FROM (SELECT \"{c}_agrup\" FROM read_parquet('{limpo}') GROUP BY 1 HAVING count(*) < {regras['categorias_raras']['limiar']} * (SELECT count(*) FROM read_parquet('{limpo}')) AND \"{c}_agrup\" NOT IN ('OUTROS','NAO_INFORMADO'))") for c in regras["categorias_raras"]["colunas"]), 0),
    ]
    df = pd.DataFrame([(n, v, e, "OK" if v == e or v is None else "FALHOU") for n, v, e in checks],
                      columns=["verificacao", "encontrado", "esperado", "status"])
    _salvar(df, pasta, "bc_verificacao_limpo.csv")
    print(df.to_string(index=False))
    return df
