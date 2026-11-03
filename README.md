# Extração de Dados do INSS para Aprendizado de Máquina
Técnicas de Aprendizado de Máquina no Processo de Extração da Informação Aplicadas à Dados de Benefícios do INSS Concedidos em Função de Acidentes de Trabalho.

Estratégia: construir um único dataset limpo e auditado (base CAT) e derivar dele duas visões, uma para Apriori e clusterização e outra para predição. Decisões e justificativas: [`docs/decisoes_metodologicas.md`](docs/decisoes_metodologicas.md).

## Status
| Fase | Etapa | Situação |
|---|---|---|
| 0-1 | Ambiente, ingestão e consolidação | concluída |
| 2 | Auditoria de qualidade | concluída |
| 3 | Limpeza e tabela de atrito | concluída |
| 4-6 | Features, visões A e B | pendente |
| 7+ | Apriori, clusterização, predição e XAI | pendente |

A base de Benefícios Concedidos seguirá a mesma estrutura em etapa posterior.

## Requisitos
Python 3.10 ou mais recente. A consolidação usa DuckDB com limite de 8 GB de memória (parâmetro de `conectar()` em `src/inss_acidentes/common/io.py`).

## Dados
Os dados **não estão neste repositório**. São 14 arquivos Parquet da base de Comunicações de Acidente de Trabalho (CAT, 2012-2018). Fonte e instruções: [`data/README.md`](data/README.md).

## Como reproduzir
1. Crie o ambiente e instale as dependências:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```
2. Coloque os 14 arquivos `.parquet` da CAT em `data/cat/raw/`.
3. Execute:
```bash
make test               # testes unitários e de ponta a ponta (dados sintéticos)
make cat-ingestao       # Fase 1: consolida os 14 parquets
make cat-dicionario     # esqueleto do dicionário
make cat-fase2-3        # Fase 2 (auditoria), Fase 3 (limpeza) e verificação
python scripts/cat_02_dicionario.py --montar   # dicionário completo (exige a auditoria já rodada)
```

## Ordem de execução (CAT)
| Script | Fase | Saída |
|---|---|---|
| `cat_01_ingestao.py` | 1 | `data/cat/interim/cat_consolidado.parquet` |
| `cat_02_dicionario.py` | 1-2 | `docs/dicionario_dados/cat_esqueleto.csv`; com `--montar`, `cat.csv` |
| `cat_03_formatos.py`, `cat_04_diagnostico.py` | 2 | padrões de formato e diagnóstico (exploratório) |
| `cat_05_auditoria.py` | 2 | `docs/qualidade/cat_auditoria.md` e tabelas |
| `cat_06_limpeza.py` | 3 | `data/cat/processed/cat_limpo.parquet`, `cat_atrito.csv` |
| `cat_07_verificacao.py` | 3 | `docs/qualidade/cat_verificacao_limpo.csv` |

## Estrutura do repositório
- `configs/`: parâmetros e regras em YAML (limpeza, schema, dicionário).
- `src/inss_acidentes/`: código reutilizável (`common/` e `cat/`).
- `scripts/`: pontos de entrada, um por etapa.
- `notebooks/`: exploração e figuras do artigo.
- `docs/`: dicionário de dados, decisões metodológicas e relatórios de qualidade.
- `reports/`: figuras e tabelas geradas para o artigo.
- `tests/`: testes das regras de limpeza, do atrito e do pipeline.
- `data/`, `models/`, `logs/`: não versionados.

## Reprodutibilidade
Dependências: `requirements.txt` e `pyproject.toml`. Semente global: 42 (`configs/paths.yaml`). A ingestão e a limpeza são determinísticas.

## Licença
Código sob a licença do arquivo [`LICENSE`](LICENSE). Os dados têm licença própria da fonte: `MIT`.