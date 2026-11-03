from inss_acidentes.cat.auditoria import auditar_formatos

if __name__ == "__main__":
    df = auditar_formatos()
    pd_opts = {"max_rows": None, "max_colwidth": 40}
    for col, g in df.groupby("coluna"):
        print(f"\n== {col} ==")
        print(g.pivot_table(index="padrao", columns=g["arquivo_origem"].str[:2],
                            values="n", aggfunc="sum").fillna(0).astype(int).to_string())