import argparse
from inss_acidentes.cat.ingestao import auditar_schemas, consolidar

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-schema", action="store_true", help="só audita os schemas, não consolida")
    args = ap.parse_args()
    auditar_schemas()
    if not args.so_schema:
        consolidar()