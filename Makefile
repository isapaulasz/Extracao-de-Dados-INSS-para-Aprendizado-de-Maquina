cat-schema:
	python scripts/cat_01_ingestao.py --so-schema
cat-ingestao:
	python scripts/cat_01_ingestao.py
cat-dicionario:
	python scripts/cat_02_dicionario.py
cat-limpeza:
	python scripts/cat_05_limpeza.py