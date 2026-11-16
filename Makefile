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
cat-features:
	python scripts/cat_08_features.py
cat-fase3-4: cat-limpeza cat-verificacao cat-features
test:
	pytest -q
cat-relatorio:
	mkdir -p reports/cat/tables && cp docs/qualidade/cat_atrito.csv reports/cat/tables/

bc-schema:
	python scripts/bc_01_ingestao.py --so-schema
bc-ingestao:
	python scripts/bc_01_ingestao.py
bc-auditoria:
	python scripts/bc_02_auditoria.py
bc-limpeza:
	python scripts/bc_03_limpeza.py
bc-verificacao:
	python scripts/bc_04_verificacao.py
bc-fase2-3: bc-auditoria bc-limpeza bc-verificacao
bc-features:
	python scripts/bc_05_features.py
bc-fase3-4: bc-limpeza bc-verificacao bc-features

.PHONY: eda-cat eda-bc eda-cat-univariada eda-cat-bivariada eda-bc-univariada eda-bc-bivariada \
        eda-cat-mi eda-cat-temporal eda-cat-territorial eda-bc-mi eda-bc-temporal eda-bc-territorial \
        eda-cat-decisao eda-bc-decisao

eda-cat: eda-cat-univariada eda-cat-bivariada eda-cat-mi eda-cat-temporal eda-cat-territorial eda-cat-decisao
eda-bc: eda-bc-univariada eda-bc-bivariada eda-bc-mi eda-bc-temporal eda-bc-territorial eda-bc-decisao

eda-cat-univariada:
	python scripts/eda_05_univariada.py --base cat
eda-cat-bivariada:
	python scripts/eda_05_bivariada.py --base cat
eda-bc-univariada:
	python scripts/eda_05_univariada.py --base beneficios
eda-bc-bivariada:
	python scripts/eda_05_bivariada.py --base beneficios
eda-cat-mi:
	python scripts/eda_05_informacao_mutua.py --base cat
eda-cat-temporal:
	python scripts/eda_05_temporal.py --base cat
eda-cat-territorial:
	python scripts/eda_05_territorial.py --base cat
eda-bc-mi:
	python scripts/eda_05_informacao_mutua.py --base beneficios
eda-bc-temporal:
	python scripts/eda_05_temporal.py --base beneficios
eda-bc-territorial:
	python scripts/eda_05_territorial.py --base beneficios
eda-cat-decisao:
	python scripts/eda_05_decisao.py --base cat
eda-bc-decisao:
	python scripts/eda_05_decisao.py --base beneficios
