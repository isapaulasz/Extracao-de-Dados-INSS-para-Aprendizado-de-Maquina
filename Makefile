cat-schema:
	python scripts/cat_01_ingestao.py --so-schema
cat-ingestao:
	python scripts/cat_01_ingestao.py
cat-dicionario:
	python scripts/cat_02_dicionario.py
cat-auditoria:
	python scripts/cat_05_auditoria.py
cat-limpeza:
	python scripts/cat_06_limpeza.py
cat-verificacao:
	python scripts/cat_07_verificacao.py
cat-fase2-3: cat-auditoria cat-limpeza cat-verificacao
test:
	pytest -q
cat-relatorio:
	mkdir -p reports/cat/tables && cp docs/qualidade/cat_atrito.csv reports/cat/tables/
