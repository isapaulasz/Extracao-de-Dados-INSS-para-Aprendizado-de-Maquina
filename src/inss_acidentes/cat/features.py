"""Fase 4: engenharia de atributos. Lê cat_limpo.parquet e grava cat_features.parquet.

Não remove linhas nem altera colunas existentes: só acrescenta colunas. O arquivo da Fase 3
(cat_limpo.parquet) nunca é sobrescrito, o que torna a etapa idempotente.
"""
import os

import pandas as pd

from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar
from inss_acidentes.common.logging import configurar_logs
from inss_acidentes.common.texto import NAO_INFORMADO, lit, normalizar_py

NOVAS = ["ano_acidente", "mes_acidente", "dia_semana_acidente", "atraso_cat_dias",
         "faixa_atraso_cat", "faixa_etaria", "regiao", "emitente_empregador",
         "houve_obito", "houve_afastamento"]
NAO_APLICAVEL = "NAO_APLICAVEL"
DESCONHECIDA = "DESCONHECIDA"


def _faixas(col: str, faixas: list, nulo: str) -> str:
    """CASE por limites superiores inclusivos; o último limite null é 'acima de tudo'."""
    ramos = []
    for limite, rotulo in faixas:
        ramos.append(f"ELSE {lit(rotulo)}" if limite is None
                     else f"WHEN {col} <= {int(limite)} THEN {lit(rotulo)}")
    ramos = [r for r in ramos if not r.startswith("ELSE")] + [r for r in ramos if r.startswith("ELSE")]
    return f"CASE WHEN {col} IS NULL THEN {lit(nulo)} {' '.join(ramos)} END"


def regiao_sql(col: str, regioes: dict) -> str:
    ramos = []
    for regiao, ufs in regioes.items():
        lista = ", ".join(lit(normalizar_py(u)) for u in ufs)
        ramos.append(f"WHEN {col} IN ({lista}) THEN {lit(regiao)}")
    return f"CASE {' '.join(ramos)} ELSE {lit(NAO_INFORMADO)} END"


def expressoes(cfg: dict, tem_coluna=lambda c: True) -> dict[str, str]:
    """SQL de cada feature nova (nome -> expressão). Testável sem arquivos."""
    tc = int(cfg["atraso"]["tipo_cat_cod"])
    atraso = (f"CASE WHEN tipo_cat_cod = {tc} AND data_acidente IS NOT NULL "
              f"AND data_emissao_cat IS NOT NULL "
              f"THEN date_diff('day', data_acidente, data_emissao_cat) END")
    return {
        "ano_acidente": "year(data_acidente)",
        "mes_acidente": "month(data_acidente)",
        "dia_semana_acidente": "isodow(data_acidente)",
        "atraso_cat_dias": atraso,
        "faixa_atraso_cat": (
            f"CASE WHEN tipo_cat_cod IS DISTINCT FROM {tc} THEN {lit(NAO_APLICAVEL)} "
            f"ELSE {_faixas('atraso_cat_dias', cfg['atraso']['faixas'], NAO_INFORMADO)} END"),
        "faixa_etaria": _faixas("idade", cfg["faixas_etarias"], DESCONHECIDA),
        "regiao": regiao_sql(cfg["regiao_uf_coluna"], cfg["regioes"]),
        "emitente_empregador": (
            "CASE WHEN emitente_cat = 'NAO_INFORMADO' THEN NULL "
            "ELSE emitente_cat = 'EMPREGADOR' END"),
        # Desfechos derivados (candidatos a alvo). NUNCA entram como preditores.
        "houve_obito": ("coalesce(indica_obito_acidente, false) OR coalesce(indica_obito, false) "
                        "OR data_obito_segurado IS NOT NULL OR tipo_cat_cod = 3"),
        "houve_afastamento": "coalesce(indica_afastamento, false) OR data_afastamento IS NOT NULL",
    }


def colunas_proibidas(alvo: str, cfg: dict | None = None) -> list[str]:
    """Colunas que não podem ser preditoras para o alvo ('obito', 'afastamento', 'beneficio')."""
    cfg = cfg or carregar_yaml("configs/cat/features.yaml")
    v = cfg["vazamento"]
    if alvo not in v or alvo == "sempre":
        raise KeyError(f"alvo desconhecido: {alvo}. Opções: {[k for k in v if k != 'sempre']}")
    return sorted(set(v["sempre"]) | set(v[alvo]["proibidas"]) | {v[alvo]["alvo"]})


def gerar_features() -> pd.DataFrame:
    log = configurar_logs("cat_features")
    paths = carregar_yaml("configs/paths.yaml")
    cfg = carregar_yaml("configs/cat/features.yaml")
    pasta_proc = RAIZ / paths["cat"]["processed"]
    entrada = (pasta_proc / cfg["arquivos"]["entrada"]).as_posix()
    saida = pasta_proc / cfg["arquivos"]["saida"]
    tmp = saida.with_name(saida.name + ".tmp")
    pasta_q = RAIZ / "docs" / "qualidade"
    pasta_q.mkdir(parents=True, exist_ok=True)

    con = conectar()
    um = lambda sql: con.execute(sql).fetchone()[0]
    existentes = {c for (c, *_) in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{entrada}')").fetchall()}
    # idempotência: se a entrada já tiver features (reuso), elas são recriadas
    base = [c for c in existentes if c not in NOVAS]
    ex = expressoes(cfg)
    necessarias = {"data_acidente", "data_emissao_cat", "tipo_cat_cod", "idade", "emitente_cat",
                   "indica_obito_acidente", "indica_obito", "indica_afastamento",
                   "data_obito_segurado", "data_afastamento", cfg["regiao_uf_coluna"]}
    faltando = necessarias - existentes
    if faltando:
        raise KeyError(f"colunas ausentes em {cfg['arquivos']['entrada']}: {sorted(faltando)}")

    n_ent = um(f"SELECT count(*) FROM read_parquet('{entrada}')")
    excl = ", ".join(f'"{c}"' for c in existentes if c in NOVAS)
    origem = f"read_parquet('{entrada}')" if not excl else \
        f"(SELECT * EXCLUDE ({excl}) FROM read_parquet('{entrada}'))"
    # atraso_cat_dias precisa existir antes de faixa_atraso_cat: duas camadas
    con.execute(f"""CREATE TEMP TABLE passo1 AS SELECT *, {ex['atraso_cat_dias']} AS atraso_cat_dias,
        {ex['ano_acidente']} AS ano_acidente, {ex['mes_acidente']} AS mes_acidente,
        {ex['dia_semana_acidente']} AS dia_semana_acidente, {ex['faixa_etaria']} AS faixa_etaria,
        {ex['regiao']} AS regiao, {ex['emitente_empregador']} AS emitente_empregador,
        {ex['houve_obito']} AS houve_obito, {ex['houve_afastamento']} AS houve_afastamento
        FROM {origem}""")
    con.execute(f"""COPY (SELECT *, {ex['faixa_atraso_cat']} AS faixa_atraso_cat FROM passo1)
                    TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n_sai = um(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')")
    assert n_sai == n_ent, f"linhas mudaram: {n_ent} -> {n_sai}"
    os.replace(tmp, saida)

    # resumo: nulos e distribuição das features (para docs/qualidade e para o artigo)
    s = saida.as_posix()
    linhas = []
    for c in NOVAS:
        nul = um(f'SELECT count(*) FILTER (WHERE "{c}" IS NULL) FROM read_parquet(\'{s}\')')
        dist = um(f'SELECT count(DISTINCT "{c}") FROM read_parquet(\'{s}\')')
        linhas.append({"feature": c, "n_nulos": nul, "pct_nulos": round(100 * nul / n_sai, 3),
                       "n_distintos": dist})
    resumo = pd.DataFrame(linhas)
    resumo.to_csv(pasta_q / "cat_features_resumo.csv", index=False, encoding="utf-8-sig")
    reg = con.execute(f"SELECT {cfg['regiao_uf_coluna']} AS uf, count(*) AS n FROM read_parquet('{s}') "
                      f"WHERE regiao = {lit(NAO_INFORMADO)} GROUP BY 1 ORDER BY 2 DESC").fetchdf()
    reg.to_csv(pasta_q / "cat_features_uf_sem_regiao.csv", index=False, encoding="utf-8-sig")
    log.info("\n%s", resumo.to_string(index=False))
    log.info("UF sem região (valor -> linhas):\n%s", reg.to_string(index=False))
    log.info("OK: %s linhas em %s", f"{n_sai:,}", saida.name)
    return resumo
