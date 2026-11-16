"""Fase 4 (Benefícios Concedidos): engenharia de atributos. Lê bc_limpo.parquet e grava bc_features.parquet.

Mesmo desenho de cat/features.py: não remove linhas nem altera colunas existentes, só acrescenta
colunas; o arquivo da Fase 3 nunca é sobrescrito, o que torna a etapa idempotente. Os helpers
genéricos (_faixas, regiao_sql) são os do CAT.
"""
import os

import pandas as pd

from inss_acidentes.cat.features import DESCONHECIDA, NAO_APLICAVEL, _faixas, regiao_sql
from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar
from inss_acidentes.common.logging import configurar_logs
from inss_acidentes.common.texto import NAO_INFORMADO, lit, normalizar_py

CFG = "configs/beneficios/features.yaml"
NOVAS = ["ano_dib", "mes_dib", "dia_semana_dib", "mes_competencia",
         "atraso_concessao_dias", "faixa_atraso_concessao",
         "duracao_beneficio_dias", "faixa_duracao_beneficio", "tem_data_cessacao",
         "faixa_etaria", "regiao", "faixa_rmi", "rmi_abaixo_piso", "grupo_especie"]
NUMERICAS = ["atraso_concessao_dias", "duracao_beneficio_dias"]
CATEGORICAS_RESUMO = ["faixa_atraso_concessao", "faixa_duracao_beneficio", "faixa_etaria", "regiao",
                      "faixa_rmi", "grupo_especie", "tem_data_cessacao", "rmi_abaixo_piso",
                      "dia_semana_dib", "mes_dib", "ano_dib"]


def grupo_sql(col: str, grupos: dict) -> str:
    ramos = []
    for grupo, nomes in grupos.items():
        lista = ", ".join(lit(normalizar_py(n)) for n in nomes)
        ramos.append(f"WHEN {col} IN ({lista}) THEN {lit(grupo)}")
    return (f"CASE WHEN {col} = {lit(NAO_INFORMADO)} THEN {lit(NAO_INFORMADO)} "
            f"{' '.join(ramos)} ELSE 'OUTROS' END")


def expressoes(cfg: dict) -> dict[str, str]:
    """SQL de cada feature nova (nome -> expressão). Testável sem arquivos."""
    return {
        "ano_dib": "year(data_dib)",
        "mes_dib": "month(data_dib)",
        "dia_semana_dib": "isodow(data_dib)",
        "mes_competencia": "competencia_concessao % 100",
        "atraso_concessao_dias": ("CASE WHEN data_dib IS NOT NULL AND data_ddb IS NOT NULL "
                                  "AND data_ddb >= data_dib THEN date_diff('day', data_dib, data_ddb) END"),
        "faixa_atraso_concessao": _faixas("atraso_concessao_dias", cfg["atraso_concessao"]["faixas"], NAO_INFORMADO),
        "duracao_beneficio_dias": ("CASE WHEN data_dib IS NOT NULL AND data_dcb IS NOT NULL "
                                   "AND data_dcb >= data_dib THEN date_diff('day', data_dib, data_dcb) END"),
        "faixa_duracao_beneficio": (
            f"CASE WHEN data_dcb IS NULL THEN {lit(NAO_APLICAVEL)} "
            f"ELSE {_faixas('duracao_beneficio_dias', cfg['duracao_beneficio']['faixas'], NAO_INFORMADO)} END"),
        "tem_data_cessacao": "data_dcb IS NOT NULL",
        "faixa_etaria": _faixas("idade", cfg["faixas_etarias"], DESCONHECIDA),
        "regiao": regiao_sql(cfg["regiao_uf_coluna"], cfg["regioes"]),
        "faixa_rmi": _faixas("qt_sm_rmi", cfg["faixas_rmi"], DESCONHECIDA),
        "rmi_abaixo_piso": "qt_sm_rmi < 1",
        "grupo_especie": grupo_sql("especie", cfg["grupos_especie"]),
    }


def colunas_proibidas(alvo: str, cfg: dict | None = None) -> list[str]:
    """Colunas que não podem ser preditoras para o alvo ('especie', 'duracao', 'rmi')."""
    cfg = cfg or carregar_yaml(CFG)
    v = cfg["vazamento"]
    if alvo not in v or alvo == "sempre":
        raise KeyError(f"alvo desconhecido: {alvo}. Opções: {[k for k in v if k != 'sempre']}")
    return sorted(set(v["sempre"]) | set(v[alvo]["proibidas"]) | {v[alvo]["alvo"]})


def gerar_features() -> pd.DataFrame:
    log = configurar_logs("bc_features")
    paths = carregar_yaml("configs/paths.yaml")
    cfg = carregar_yaml(CFG)
    pasta_proc = RAIZ / paths["beneficios"]["processed"]
    entrada = (pasta_proc / cfg["arquivos"]["entrada"]).as_posix()
    saida = pasta_proc / cfg["arquivos"]["saida"]
    tmp = saida.with_name(saida.name + ".tmp")
    pasta_q = RAIZ / "docs" / "qualidade"
    pasta_q.mkdir(parents=True, exist_ok=True)

    con = conectar()
    um = lambda sql: con.execute(sql).fetchone()[0]
    existentes = {c for (c, *_) in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{entrada}')").fetchall()}
    ex = expressoes(cfg)
    necessarias = {"data_dib", "data_ddb", "data_dcb", "idade", "qt_sm_rmi", "especie",
                   "competencia_concessao", cfg["regiao_uf_coluna"]}
    faltando = necessarias - existentes
    if faltando:
        raise KeyError(f"colunas ausentes em {cfg['arquivos']['entrada']}: {sorted(faltando)}")
    nao_mapeadas = sorted(set(a for par in cfg["validacao"]["crosstabs"] for a in par)
                          - existentes - set(NOVAS))
    if nao_mapeadas:
        raise KeyError(f"validacao.crosstabs usa colunas inexistentes: {nao_mapeadas}")

    n_ent = um(f"SELECT count(*) FROM read_parquet('{entrada}')")
    # idempotência: se a entrada já tiver features (reuso), elas são recriadas
    excl = ", ".join(f'"{c}"' for c in existentes if c in NOVAS)
    origem = f"read_parquet('{entrada}')" if not excl else \
        f"(SELECT * EXCLUDE ({excl}) FROM read_parquet('{entrada}'))"
    # as faixas dependem de colunas numéricas criadas antes: duas camadas
    passo1 = ["ano_dib", "mes_dib", "dia_semana_dib", "mes_competencia", "atraso_concessao_dias",
              "duracao_beneficio_dias", "tem_data_cessacao", "faixa_etaria", "regiao", "faixa_rmi",
              "rmi_abaixo_piso", "grupo_especie"]
    con.execute(f"CREATE TEMP TABLE passo1 AS SELECT *, "
                f"{', '.join(f'{ex[c]} AS {c}' for c in passo1)} FROM {origem}")
    con.execute(f"""COPY (SELECT *, {ex['faixa_atraso_concessao']} AS faixa_atraso_concessao,
                                    {ex['faixa_duracao_beneficio']} AS faixa_duracao_beneficio FROM passo1)
                    TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n_sai = um(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')")
    assert n_sai == n_ent, f"linhas mudaram: {n_ent} -> {n_sai}"
    os.replace(tmp, saida)

    s = saida.as_posix()
    # 1) nulos e cardinalidade de cada feature nova
    linhas = []
    for c in NOVAS:
        nul = um(f'SELECT count(*) FILTER (WHERE "{c}" IS NULL) FROM read_parquet(\'{s}\')')
        dist = um(f'SELECT count(DISTINCT "{c}") FROM read_parquet(\'{s}\')')
        linhas.append({"feature": c, "n_nulos": nul, "pct_nulos": round(100 * nul / n_sai, 3),
                       "n_distintos": dist})
    resumo = pd.DataFrame(linhas)
    resumo.to_csv(pasta_q / "bc_features_resumo.csv", index=False, encoding="utf-8-sig")

    # 2) distribuição das categóricas novas (para ajustar as faixas do YAML)
    dist = []
    for c in CATEGORICAS_RESUMO:
        d = con.execute(f"SELECT CAST(\"{c}\" AS VARCHAR) AS valor, count(*) AS n "
                        f"FROM read_parquet('{s}') GROUP BY 1 ORDER BY 2 DESC").fetchdf()
        d.insert(0, "feature", c)
        d["pct"] = (100 * d.n / n_sai).round(3)
        dist.append(d)
    pd.concat(dist, ignore_index=True).to_csv(pasta_q / "bc_features_distribuicao.csv",
                                              index=False, encoding="utf-8-sig")

    # 3) quantis das numéricas (para ajustar as faixas de atraso e duração)
    num = []
    for c in NUMERICAS:
        r = con.execute(f"""SELECT count("{c}"), min("{c}"), quantile_cont("{c}", 0.25), median("{c}"),
                                   quantile_cont("{c}", 0.75), quantile_cont("{c}", 0.95), max("{c}")
                            FROM read_parquet('{s}')""").fetchone()
        num.append(dict(zip(["feature", "n", "min", "p25", "mediana", "p75", "p95", "max"], (c, *r))))
    pd.DataFrame(num).to_csv(pasta_q / "bc_features_numericas.csv", index=False, encoding="utf-8-sig")

    # 4) UFs sem região
    reg = con.execute(f"SELECT {cfg['regiao_uf_coluna']} AS uf, count(*) AS n FROM read_parquet('{s}') "
                      f"WHERE regiao = {lit(NAO_INFORMADO)} GROUP BY 1 ORDER BY 2 DESC").fetchdf()
    reg.to_csv(pasta_q / "bc_features_uf_sem_regiao.csv", index=False, encoding="utf-8-sig")

    # 5) crosstabs para validar o mapa de vazamento (como os crosstabs do CAT em decisoes_metodologicas.md)
    ct = []
    for a, b in cfg["validacao"]["crosstabs"]:
        d = con.execute(f"SELECT CAST(\"{a}\" AS VARCHAR) AS valor_a, CAST(\"{b}\" AS VARCHAR) AS valor_b, "
                        f"count(*) AS n FROM read_parquet('{s}') GROUP BY 1, 2 ORDER BY 1, 3 DESC").fetchdf()
        d["pct_da_linha"] = (100 * d.n / d.groupby("valor_a").n.transform("sum")).round(2)
        d.insert(0, "coluna_b", b)
        d.insert(0, "coluna_a", a)
        ct.append(d)
    pd.concat(ct, ignore_index=True).to_csv(pasta_q / "bc_features_crosstabs.csv",
                                            index=False, encoding="utf-8-sig")

    log.info("\n%s", resumo.to_string(index=False))
    log.info("Numéricas:\n%s", pd.DataFrame(num).to_string(index=False))
    log.info("UF sem região (valor -> linhas):\n%s", reg.to_string(index=False))
    log.info("OK: %s linhas em %s", f"{n_sai:,}", saida.name)
    return resumo
