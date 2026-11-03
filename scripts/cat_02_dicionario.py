from inss_acidentes.cat.auditoria import gerar_esqueleto_dicionario

if __name__ == "__main__":
    df = gerar_esqueleto_dicionario()
    print(df.to_string())
    print(f"\nOK: {len(df)} colunas gravadas em docs/dicionario_dados/cat.csv")