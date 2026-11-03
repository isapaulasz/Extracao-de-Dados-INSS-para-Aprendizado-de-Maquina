import sys
from inss_acidentes.cat.auditoria import gerar_esqueleto_dicionario, montar_dicionario

if __name__ == "__main__":
    if "--montar" in sys.argv:          # depois da auditoria (cat_05): junta tudo em cat.csv
        df = montar_dicionario()
        print(f"OK: {len(df)} colunas em docs/dicionario_dados/cat.csv")
    else:                                # antes da auditoria: só o esqueleto automático
        df = gerar_esqueleto_dicionario()
        print(f"OK: {len(df)} colunas em docs/dicionario_dados/cat_esqueleto.csv")