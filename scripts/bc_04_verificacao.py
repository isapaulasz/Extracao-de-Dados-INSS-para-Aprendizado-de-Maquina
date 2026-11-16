import sys
from inss_acidentes.beneficios.auditoria import verificar_limpo

if __name__ == "__main__":
    df = verificar_limpo()
    sys.exit(0 if (df.status != "FALHOU").all() else 1)
