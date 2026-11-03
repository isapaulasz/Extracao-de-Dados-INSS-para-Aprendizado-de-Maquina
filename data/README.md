# Dados (não versionados)
- `cat/raw/`: os 14 arquivos `NN.AAAA_MM-MM_CATS.parquet` (CAT 2012-2018), convertidos de csv/txt/xlsx pelo autor com todas as colunas como texto. **Fonte original: [Dados Abertos Gov.br - INSS - Comunicação de Acidente de Trabalho - CAT](https://dados.gov.br/dados/conjuntos-dados/inss-comunicacao-de-acidente-de-trabalho-cat1).**
- `cat/interim/`: `cat_consolidado.parquet` (gerado pela Fase 1).
- `cat/processed/`: `cat_limpo.parquet` (Fase 3) e, depois, as visões A e B.
- `external/`: tabelas de apoio (CID-10, CBO, CNAE, IBGE), se necessárias.
Para recriar: coloque os parquets em `cat/raw/` e rode `make cat-ingestao`.
