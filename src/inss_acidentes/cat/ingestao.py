"""Fase 1: auditoria de schema por arquivo e consolidação dos parquets brutos."""
import re
import unicodedata

import pandas as pd

from inss_acidentes.common.io import RAIZ, carregar_yaml, conectar

PADRAO_ARQUIVO = re.compile(r"^(?P<seq>\d+)\.(?P<ano>\d{4})_(?P<mi>\d{2})-(?P<mf>\d{2})_")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _mapa_inverso(schema: dict) -> dict:
    mapa: dict[str, str] = {}
    for padrao, variantes in schema["colunas"].items():
        for v in [*variantes, padrao]:
            chave = _norm(v)
            if mapa.get(chave, padrao) != padrao:
                raise ValueError(f"Alias ambíguo '{v}': {mapa[chave]} e {padrao}")
            mapa[chave] = padrao
    return mapa


def _meta_arquivo(nome: str) -> tuple[int, int]:
    """'14.2018_07-12_CATS.parquet' -> (2018, 2)."""
    m = PADRAO_ARQUIVO.match(nome)
    if not m:
        raise ValueError(f"Nome fora do padrão NN.AAAA_MM-MM_*: {nome}")
    return int(m["ano"]), (1 if m["mi"] == "01" else 2)


def auditar_schemas() -> pd.DataFrame:
    """Passos 1-2 da Fase 1: schema de cada parquet bruto, comparado entre arquivos."""
    paths = carregar_yaml("configs/paths.yaml")
    schema = carregar_yaml("configs/cat/harmonizacao_schema.yaml")
    mapa = _mapa_inverso(schema)
    raw = RAIZ / paths["cat"]["raw"]
    arquivos = sorted(raw.glob("*.parquet"))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum .parquet em {raw}")

    con = conectar()
    linhas = []
    for arq in arquivos:
        p = arq.as_posix()
        n = con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
        desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()
        for nome, tipo, *_ in desc:
            padrao = mapa.get(_norm(nome))
            linhas.append({
                "arquivo": arq.name, "n_linhas": n,
                "coluna_original": nome, "tipo": tipo,
                "nome_padronizado": padrao,
                "chave": padrao or f"?? {nome}",
            })
    df = pd.DataFrame(linhas)

    dup = df[df.duplicated(["arquivo", "chave"], keep=False)]
    if not dup.empty:
        raise ValueError(f"Colunas mapeadas para o mesmo nome no mesmo arquivo:\n{dup}")

    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)
    tipos = df.pivot(index="chave", columns="arquivo", values="tipo")
    nomes = df.pivot(index="chave", columns="arquivo", values="coluna_original")
    df.to_csv(pasta / "cat_schema_por_arquivo.csv", index=False, encoding="utf-8-sig")
    tipos.to_csv(pasta / "cat_matriz_tipos.csv", encoding="utf-8-sig")
    nomes.to_csv(pasta / "cat_matriz_nomes.csv", encoding="utf-8-sig")

    curto = lambda m: m.rename(columns=lambda c: c[:2])  # 01, 02, ... para caber na tela
    print("\n== Linhas por arquivo ==")
    print(df.groupby("arquivo")["n_linhas"].first().to_string())
    print(f"TOTAL: {df.groupby('arquivo')['n_linhas'].first().sum():,}")

    print("\n== Colunas NÃO mapeadas no YAML ==")
    nm = df[df["nome_padronizado"].isna()]
    print(nm[["arquivo", "coluna_original", "tipo"]].to_string(index=False) if len(nm) else "nenhuma")

    ausentes_todos = set(schema["colunas"]) - set(df["nome_padronizado"].dropna())
    print("\n== Colunas do YAML que não existem em nenhum arquivo ==")
    print(sorted(ausentes_todos) or "nenhuma")

    print("\n== Colunas ausentes em algum arquivo (tipo mostrado onde existe) ==")
    aus = tipos[tipos.isna().any(axis=1)]
    print(curto(aus).fillna("--").to_string() if len(aus) else "nenhuma")

    print("\n== Colunas com TIPO divergente entre arquivos ==")
    div = tipos[tipos.nunique(axis=1) > 1]
    print(curto(div).fillna("--").to_string() if len(div) else "nenhuma")

    print("\n== Colunas com NOME ORIGINAL diferente entre arquivos ==")
    nd = nomes[nomes.nunique(axis=1) > 1]
    print(curto(nd).fillna("--").to_string() if len(nd) else "nenhuma")
    return df


def consolidar() -> None:
    """Passos 3-5 da Fase 1: harmoniza, concatena e valida a contagem por arquivo."""
    paths = carregar_yaml("configs/paths.yaml")
    schema = carregar_yaml("configs/cat/harmonizacao_schema.yaml")
    mapa = _mapa_inverso(schema)

    raw = RAIZ / paths["cat"]["raw"]
    saida = RAIZ / paths["cat"]["interim"] / "cat_consolidado.parquet"
    saida.parent.mkdir(parents=True, exist_ok=True)

    con = conectar()
    selects, esperado = [], {}

    for arq in sorted(raw.glob("*.parquet")):
        ano, semestre = _meta_arquivo(arq.name)
        p = arq.as_posix()
        cols = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
        esperado[arq.name] = con.execute(
            f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]

        partes, ignoradas, vistos = [], [], set()
        for c in cols:
            padrao = mapa.get(_norm(c))
            if padrao is None:
                ignoradas.append(c)
                continue
            if padrao in vistos:
                raise ValueError(f"{arq.name}: duas colunas mapeiam para '{padrao}'")
            vistos.add(padrao)
            # tudo texto aqui; a tipagem fica para a Fase 3
            partes.append(f'CAST("{c}" AS VARCHAR) AS "{padrao}"')
        partes += [
            f"'{arq.name}' AS arquivo_origem",
            f"{ano} AS ano_arquivo",
            f"{semestre} AS semestre_arquivo",
        ]
        aviso = f" | NÃO MAPEADAS (descartadas): {ignoradas}" if ignoradas else ""
        print(f"{arq.name}: {esperado[arq.name]:,} linhas{aviso}")
        selects.append(f"SELECT {', '.join(partes)} FROM read_parquet('{p}')")

    sql = " UNION ALL BY NAME ".join(selects)
    con.execute(f"COPY ({sql}) TO '{saida.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    # validação: por arquivo e no total
    obtido = dict(con.execute(
        f"SELECT arquivo_origem, count(*) FROM read_parquet('{saida.as_posix()}') GROUP BY 1"
    ).fetchall())
    if obtido != esperado:
        raise RuntimeError(f"Contagem diverge por arquivo:\nesperado={esperado}\nobtido={obtido}")

    pasta = RAIZ / "docs" / "qualidade"
    pasta.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"arquivo": list(esperado), "linhas_raw": list(esperado.values()),
                  "linhas_consolidado": [obtido[a] for a in esperado]}
                 ).to_csv(pasta / "cat_linhas_por_arquivo.csv", index=False)
    print(f"OK: {sum(obtido.values()):,} linhas em {saida.name} (validado por arquivo)")