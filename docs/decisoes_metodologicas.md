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

- **Fase 1:** na consolidação, todas as colunas são lidas como texto (`VARCHAR`) e apenas renomeadas conforme `harmonizacao_schema.yaml`; a conversão de tipos ocorre na Fase 3. A contagem de linhas do consolidado é conferida contra a soma das linhas dos 14 arquivos originais, e esse total é a primeira linha da tabela de atrito. Total de registros: 3,429,666.
- **Fase 6:** atribuição das visões A e B às técnicas de mineração/clusterização e de predição.

## Fase 1: Ingestão
- Motor: DuckDB (consolidação fora da memória do pandas).
- Todas as colunas entram como VARCHAR; a tipagem é feita na Fase 3, porque o tipo das datas diverge entre arquivos.
- Colunas adicionadas: `arquivo_origem`, `ano_arquivo`, `semestre_arquivo` (extraídos do nome do arquivo).
- Validação: contagem de linhas conferida por arquivo e no total (`docs/qualidade/cat_linhas_por_arquivo.csv`).
- Comparação de schema entre os 14 arquivos: `docs/qualidade/cat_schema_por_arquivo.csv` e matrizes de tipos e nomes.

## Fase 2: Auditoria de qualidade (CAT)
Executada por `scripts/cat_05_auditoria.py` sobre o consolidado, antes de qualquer limpeza. Relatório: `docs/qualidade/cat_auditoria.md`; tabelas completas em `docs/qualidade/`.

| Verificação | Tabela |
|---|---|
| Dimensões, tipos, % de vazios por coluna e por ano | `cat_dimensoes_tipos.csv`, `cat_nulos_por_ano.csv`, `cat_nulos_por_ano_pivot.csv` |
| Nulos disfarçados (valores mais frequentes por categórica) | `cat_top_valores_categoricas.csv`, `cat_encoding.csv` |
| Duplicados exatos e por chave aproximada | `cat_duplicados.csv` |
| Inconsistências de data e idade | `cat_inconsistencias_datas.csv` |
| Cardinalidade das categóricas | `cat_cardinalidade.csv` |
| Distribuição por ano, arquivo e tipo de CAT | `cat_distribuicao_ano_tipo.csv`, `cat_distribuicao_arquivo.csv` |

Definições usadas:
- **Vazio** = NULL real ou valor que, normalizado (trim, maiúsculas, sem acento), está na lista `nulos_disfarcados`.
- **Mudança de preenchimento** = variação de 20 p.p. ou mais no % de vazios entre anos (por ano da competência).
- **Chave aproximada de duplicata** = data de nascimento + sexo + data do acidente + CID-10 + CNPJ do empregador, todos preenchidos. É só diagnóstico: não remove linhas.
- **Valor suspeito** = valor que casa com padrões como IGNORADO, NAO INFORMADO, NAO CLASSIFICADO, 0, 9999, `{...}`.

Achados já conhecidos antes da Fase 3 (da ingestão e do diagnóstico):
- 3.429.666 linhas em 14 arquivos; 20 linhas-lixo da exportação (arquivos 07 e 12), que coincidem com as 20 linhas de `tipo_cat` nulo ou `-`.
- Distribuição de `tipo_cat` (sem as linhas-lixo): 1-Inicial 3.408.511 (99,38%), 2-Reabertura 18.913 (0,55%), 3-Comunicação de óbito 2.222 (0,06%).
- Arquivo 14 (2018-S2) ganha `numero_cat` e `razao_social` e perde `valor_remuneracao`.
- `% de CATs com benefício` varia de 23% a 2,4% entre arquivos e coincide com `data_despacho` preenchida. Hipótese (a verificar): datas de extração diferentes por arquivo. Qualquer alvo ligado a benefício deve tratar `arquivo_origem`.

## Fase 3: Limpeza (CAT)
Executada por `scripts/cat_06_limpeza.py` (regras em `configs/cat/regras_limpeza.yaml`); conferida por `scripts/cat_07_verificacao.py`. Os parquets foram convertidos de csv/txt/xlsx pelo autor, com tudo como texto; não são os arquivos originais do gov.br.

| # | Etapa | Decisão | Justificativa |
|---|---|---|---|
| 1 | Linhas-lixo | Remover as linhas de cabeçalho/total da exportação (`data_acidente` fora de AAAA-MM-DD). | Não são registros. |
| 2 | Texto | trim, colapso de espaços, remoção de acentos, maiúsculas (em todas as colunas). | Evita níveis duplicados ("Não"/"Nao", "Motocicleta"/"MOTOCICLETA"). |
| 3 | Nulos disfarçados | Lista em `nulos_disfarcados` (inclui IGNORADO, NAO INFORMADO, NAO CLASSIFICADO, 9999, 000000, `{ñ class}`, `00/00/0000`) vira NULL. | "Ignorado" e "não informado" equivalem a ausência de informação. |
| 4 | Valores fora da regra | CBO `0`, CNAE com 1-2 dígitos, CID fora de `^[A-Z][0-9]{2,3}$`, idade fora de 14-90, remuneração fora de R$ 100-100.000 viram NULL, sem remover a linha. | Preserva a linha e evita valores impossíveis. |
| 5 | Zeros à esquerda | CBO 5 -> 6 dígitos; CNAE 3 -> 4 dígitos. | Perda de zero na conversão da origem. |
| 6 | Escopo por tipo de CAT | **Manter os três tipos** (1, 2 e 3) e usar `tipo_cat` como variável. Reversível: `escopo.tipo_cat_manter: [1]` faz "um acidente = uma linha". | Reaberturas e comunicações de óbito somam 0,6% das linhas; a decisão final fica para a Fase 6 e é um parâmetro, não código. |
| 7 | Datas inválidas | Remover linha: acidente posterior à emissão; acidente ou emissão depois de 2018-12-31. Anular (NULL): afastamento ou óbito anterior ao acidente. Acidente antes de 2012 não é removido (CAT pode ser emitida com atraso). | Período dos dados: 2012-2018. Cada regra tem sua contagem no atrito. |
| 8 | Duplicatas exatas | **Remover**, mantendo a primeira ocorrência (ordem do arquivo). Chave: todas as colunas, exceto `arquivo_origem`, `ano_arquivo`, `semestre_arquivo`. | Decisão do autor. Limitação: antes do arquivo 14 não há ID da CAT, então um caso legítimo idêntico (mesmo trabalhador, empregador, CID e dia) também seria removido. A auditoria (`cat_duplicados.csv`) mede esse risco. |
| 9 | Nulos em categóricas | NULL vira a categoria `NAO_INFORMADO`, sem imputação. Datas e numéricas ficam NULL. | Preserva a informação de "não preenchido". |
| 10 | Nulos em numéricas | Nenhuma imputação na limpeza. A idade (única numérica relevante) fica NULL fora de 14-90; a mediana é imputada só dentro do pipeline de modelagem, ajustada no treino. Para Apriori e clusterização, a faixa etária terá nível "desconhecida". | Evita vazamento de dados do teste para o treino. |
| 11 | Categorias raras | Colunas `<col>_agrup` com níveis de frequência < 0,1% trocados por `OUTROS`; a coluna original é mantida; `NAO_INFORMADO` nunca é agrupado. | O mapeamento usa só a frequência da própria coluna (sem o alvo). Se um revisor exigir, recalcular dentro do treino. |
| 12 | Hierarquias | `cid_categoria` (3 caracteres), `capitulo_cid` (I-XXII), `cbo_grande_grupo` (1 dígito), `cbo_subgrupo` (2), `cnae_divisao` (2), `cnae_secao` (A-U). | Reduz a cardinalidade. Mapeamentos em `common/hierarquias.py`. |
| 13 | Remuneração | `valor_remuneracao` é nominal. `remuneracao_sm` divide pelo salário mínimo do ano da emissão da CAT. Não existe no arquivo 14. | Torna os anos comparáveis. Valores do salário mínimo conferidos com o decreto: `R$ 622 em 2012 a R$ 954 em 2018`. |
| 14 | Colunas só do arquivo 14 | `numero_cat` e `razao_social` ficam no limpo, mas fora das visões A e B. | Existem em 1 de 14 arquivos. |

Rastreabilidade: `docs/qualidade/cat_atrito.csv` registra, por etapa, as linhas removidas e os valores alterados, e fecha com a saída (`entrada - removidas = linhas finais`, verificado em código). Detalhes por coluna: `cat_valores_anulados.csv`, `cat_nao_informado.csv`, `cat_categorias_raras.csv`. Verificação do arquivo final: `cat_verificacao_limpo.csv`.
