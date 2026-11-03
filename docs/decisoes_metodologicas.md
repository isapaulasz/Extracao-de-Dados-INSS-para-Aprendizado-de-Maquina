# Decisõe Metodológicas

## Fase 0: Ambiente e organização

**Data:** [02/11/2026]
**Escopo:** base de Comunicações de Acidente de Trabalho (CAT). A base de Benefícios Concedidos seguirá a mesma estrutura em etapa posterior.

### 0.1 Estratégia geral

Optou-se por construir um único dataset limpo e auditado a partir da base CAT e derivar dele duas visões analíticas: uma para mineração de regras de associação (Apriori) e clusterização, e outra para predição (Árvore de Decisão, Random Forest e Rede Neural, com técnicas de XAI). A definição de qual visão atende a qual técnica fica adiada para a Fase 6, de modo que todas as etapas anteriores (ingestão, auditoria, limpeza e engenharia de atributos) sejam comuns e reaproveitadas.

**Justificativa:** evita retrabalho e garante que todas as técnicas partam da mesma base tratada, o que torna os resultados comparáveis e a tabela de atrito (registros descartados por etapa) única para o artigo.

### 0.2 Organização do repositório

O projeto adota a seguinte separação de responsabilidades:

- `data/`: dados em três camadas (`raw/` imutável, `interim/` para a consolidação, `processed/` para os datasets finais). Não é versionado no Git.
- `src/inss_acidentes/`: código reutilizável, dividido em `common/` (funções compartilhadas entre CAT e Benefícios) e `cat/` (etapas específicas, uma por fase).
- `scripts/`: pontos de entrada de linha de comando, um por etapa, que apenas chamam as funções de `src/`.
- `notebooks/`: exploração e visualização; a lógica final é migrada para `src/`.
- `configs/`: parâmetros e regras em arquivos YAML, fora do código.
- `docs/`: dicionário de dados, decisões metodológicas e relatórios de qualidade.
- `reports/`: figuras e tabelas geradas para o artigo.
- `models/` e `logs/`: não versionados.

O código foi organizado como pacote Python instalável em modo editável (`pip install -e .`, com `pyproject.toml`), permitindo importar os módulos da mesma forma em scripts e notebooks.

**Justificativa:** separar dados brutos, código, parâmetros e resultados facilita a reprodutibilidade e permite reaproveitar a estrutura para a base de Benefícios. Manter os parâmetros em YAML torna as regras de limpeza e harmonização auditáveis sem alterar o código.

### 0.3 Ferramentas

| Finalidade | Ferramenta | Motivo |
|---|---|---|
| Leitura e consolidação dos 14 arquivos Parquet | DuckDB | Consulta os arquivos diretamente do disco, sem carregar todos os registros na memória, o que evita estouro de memória com milhões de linhas. |
| Manipulação de dados no restante do fluxo | pandas, NumPy | Amplo suporte nas bibliotecas de modelagem. |
| Regras de associação | mlxtend | Implementações de Apriori e FP-Growth. |
| Clusterização de dados categóricos/mistos | kmodes | K-Modes e K-Prototypes. |
| Modelagem e avaliação | scikit-learn | Árvore de Decisão, Random Forest, rede neural (MLP), validação cruzada e métricas. |
| Explicabilidade | SHAP | Interpretação global e local dos modelos. |
| Leitura de configurações | PyYAML | Carregamento dos arquivos de `configs/`. |

Ambiente: Python [versão], sistema operacional Linux, ambiente virtual `.venv`, editor VS Code.

### 0.4 Reprodutibilidade

- **Semente aleatória:** fixada em `42`, armazenada em `configs/paths.yaml`. A função `fixar_semente()` (em `common/io.py`) define as sementes do Python, do NumPy e da variável `PYTHONHASHSEED`, e o valor é passado explicitamente via `random_state` ao split treino/teste, aos modelos e ao K-Modes. A semente não afeta a ingestão e a limpeza, que são determinísticas, mas é necessária para que as etapas de clusterização, divisão dos dados e treinamento produzam sempre os mesmos resultados.
- **Versões das bibliotecas:** registradas em `requirements.txt` (gerado com `pip freeze`) e/ou `pyproject.toml`, e citadas no `README.md`.
- **Dados brutos:** os 14 arquivos Parquet originais ficam em `data/cat/raw/` e não são alterados em nenhuma etapa. Toda transformação gera um novo arquivo em `interim/` ou `processed/`.

### 0.5 Dicionário de dados

Foi definido que o dicionário de dados será a fonte de verdade para as decisões de limpeza. Ele é construído em duas etapas:

1. **Esqueleto automático** (`cat/auditoria.py`, executado por `scripts/cat_02_dicionario.py`): gera, para cada coluna do dataset consolidado, o nome padronizado, o percentual de nulos, o número de valores distintos e os valores mais frequentes.
2. **Preenchimento manual:** `nome_original`, `tipo` (tipo final após a limpeza), `descricao`, `valores_validos` e `nulos_disfarcados`, com base na documentação oficial do INSS/Dataprev, nas tabelas de referência (CID-10, CBO, CNAE) e na inspeção dos valores mais frequentes. O conteúdo inferido dos dados, e não da documentação, é marcado como tal em `observacoes`.

Arquivo final: `docs/dicionario_dados/cat.csv`. Para evitar perda do trabalho manual, o esqueleto automático é gravado em arquivo separado (`cat_esqueleto.csv`) [ajuste se não adotou essa separação].

As informações de `valores_validos` e `nulos_disfarcados` serão convertidas em regras no arquivo `configs/cat/regras_limpeza.yaml`, que será lido pelo módulo de limpeza (Fase 3).

### 0.6 Decisões que ficam para as próximas fases

- **Fase 1:** na consolidação, todas as colunas são lidas como texto (`VARCHAR`) e apenas renomeadas conforme `harmonizacao_schema.yaml`; a conversão de tipos ocorre na Fase 3. A contagem de linhas do consolidado é conferida contra a soma das linhas dos 14 arquivos originais, e esse total é a primeira linha da tabela de atrito. Total de registros: [N].
- **Fase 6:** atribuição das visões A e B às técnicas de mineração/clusterização e de predição.

## Fase 1: Ingestão
- Motor: DuckDB (consolidação fora da memória do pandas).
- Todas as colunas entram como VARCHAR; a tipagem é feita na Fase 3, porque o tipo das datas diverge entre arquivos.
- Colunas adicionadas: `arquivo_origem`, `ano_arquivo`, `semestre_arquivo` (extraídos do nome do arquivo).
- Validação: contagem de linhas conferida por arquivo e no total (`docs/qualidade/cat_linhas_por_arquivo.csv`).
- Comparação de schema entre os 14 arquivos: `docs/qualidade/cat_schema_por_arquivo.csv` e matrizes de tipos e nomes.

## Fases 2-3: Auditoria e limpeza (CAT)
- Os parquets foram convertidos de csv/txt/xlsx pelo autor, com tudo como texto; não são os arquivos originais do gov.br.
- Removidas 22 linhas: 20 de cabeçalho/total da exportação (arquivos 07 e 12) e 2 com acidente posterior à emissão.
- Valores inválidos viram NULL sem remover a linha: `00/00/0000`, `{ñ class}`, `-`, `None`, CBO `0`, CNAE com 1-2 dígitos, CID fora de `^[A-Z][0-9]{2,3}$`, idade fora de 14-90, remuneração fora de R$100-100.000.
- Zeros à esquerda recuperados: CBO (5 -> 6 dígitos, grande grupo 0) e CNAE (3 -> 4 dígitos, agropecuária).
- `valor_remuneracao` é nominal. `remuneracao_sm` divide pelo salário mínimo do ano da emissão da CAT (a competência é sempre o mês da emissão). Não existe no arquivo 14 (2018-S2).
- `numero_cat` e `razao_social` só existem no arquivo 14: ficam no limpo, mas fora das visões A e B.
- Duplicatas exatas: apenas contadas. Sem ID da CAT antes do arquivo 14, não dá para separar duplicata de caso legítimo.
- `% de CATs com benefício` varia de 23% a 2,4% entre arquivos, sem ser monotônico, e coincide com `data_despacho` preenchida. Hipótese (a verificar): datas de extração diferentes por arquivo. Qualquer alvo ligado a benefício deve tratar `arquivo_origem`.