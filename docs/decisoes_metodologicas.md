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
| 3 | Nulos disfarçados | Lista em `nulos_disfarcados` (inclui IGNORADO, NAO INFORMADO, NAO CLASSIFICADO, 9999, 000000, `{ñ class}`, `00/00/0000`, `ZERADO`) vira NULL. `ZERADO` aparece só em `uf_segurado` (70.049 linhas, nos 14 arquivos, sem concentração em nenhum) e é o 28º nível dessa coluna. | "Ignorado" e "não informado" equivalem a ausência de informação. |
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

## Fase 4: Engenharia de atributos (CAT)
**Situação:** concluída. `houve_obito` e `houve_afastamento` foram validados por crosstab nos dados reais (seções abaixo).

Executada por `scripts/cat_08_features.py` (regras em `configs/cat/features.yaml`). Lê `cat_limpo.parquet` e grava **`cat_features.parquet`** em `data/cat/processed/`; o arquivo da Fase 3 nunca é sobrescrito. A etapa só acrescenta colunas (nenhuma linha é removida; o número de linhas é conferido em código) e é idempotente. Resumo de nulos por feature: `docs/qualidade/cat_features_resumo.csv`; UFs sem região: `cat_features_uf_sem_regiao.csv`.

| # | Feature | Decisão | Justificativa |
|---|---|---|---|
| 1 | `ano_acidente`, `mes_acidente`, `dia_semana_acidente` | Derivadas de `data_acidente`. Dia da semana em ISO 8601 (1 = segunda ... 7 = domingo). | `ano_competencia` é o ano da emissão, não do acidente. Em `tipo_acidente = DOENCA` a data é a do diagnóstico, então mês e dia da semana têm outro significado (limitação a citar no artigo). |
| 2 | `atraso_cat_dias`, `faixa_atraso_cat` | Dias entre `data_acidente` e `data_emissao_cat`, **apenas para `tipo_cat = 1`**; NULL (e `NAO_APLICAVEL` na faixa) para reabertura e comunicação de óbito. Faixas: 0, 1, 2-7, 8-30, 31+. | Em reabertura a data do acidente é a original e a emissão é posterior por natureza; em comunicação de óbito a emissão decorre do óbito. O atraso mediria outra coisa. A limpeza já garante atraso >= 0. |
| 3 | `faixa_etaria` | 14-17, 18-24, 25-34, 35-44, 45-54, 55-64, 65+; `DESCONHECIDA` se a idade é NULL. | Decisão #10 da Fase 3 (idade nunca imputada na limpeza). |
| 4 | `regiao` | Região IBGE a partir de **`uf_segurado`** (decisão do autor). UF fora das 27 (incl. `NAO_INFORMADO`) vira `NAO_INFORMADO`. | `uf_segurado` não tem vazios no bruto, ao contrário de `uf_acidente` (29,4%). Depois de tratar o `ZERADO` na limpeza, as 70.049 linhas sem UF são as únicas com região `NAO_INFORMADO`: as outras 27 UFs foram todas mapeadas. Atenção: é a UF de residência, não a do acidente; o artigo deve dizer isso. |
| 5 | Setor e ocupação | Já derivados na Fase 3: `cnae_secao`, `cnae_divisao`, `cbo_grande_grupo`, `cbo_subgrupo`. | Nada a refazer. |
| 6 | Porte da empresa | **Não derivado.** | A CNAE informa atividade, não tamanho, e a base não traz número de empregados. Contar CATs por `cnpj_raiz` mediria volume de CATs, não porte, e teria de ser calculado só no treino; fica fora. |
| 7 | `emitente_empregador` | TRUE se `emitente_cat = EMPREGADOR` (98,3% dos casos); NULL se `NAO_INFORMADO`. | Binário útil, mesmo com baixa variância. |
| 8 | `houve_obito`, `houve_afastamento` | Desfechos derivados: óbito = `indica_obito_acidente` OU `indica_obito` OU `data_obito_segurado` OU `tipo_cat = 3`; afastamento = `indica_afastamento` OU `data_afastamento`. **Candidatos a alvo, nunca preditores.** | Unem os indicadores redundantes. Ambos validados por crosstab nos dados reais (seções abaixo). As descrições das `indica_*` ainda não foram conferidas contra a documentação do INSS (`validado = False`). |

### Validação de `houve_obito` (crosstab em `cat_limpo.parquet`)
| indica_obito | indica_obito_acidente | indica_acidente | tipo_cat | data de óbito | linhas |
|---|---|---|---|---|---|
| não | não | não | 1 | não | 3.394.460 |
| não | não | não | 2 | não | 18.913 |
| não | sim | sim | 1 | sim | 13.995 |
| sim | sim | sim | 3 | sim | 2.197 |
| sim | sim | sim | 3 | não | 25 |
| não | não | sim | 1 | sim | 15 |

- `indica_obito` coincide exatamente com `tipo_cat = 3` (2.222 linhas): são a mesma informação, e ambas ficam proibidas como preditoras do óbito.
- `indica_obito_acidente` e `indica_acidente` diferem em apenas 15 linhas.
- A data de óbito acompanha os indicadores, com 40 exceções (25 do tipo 3 sem data; 15 com data mas sem `indica_obito_acidente`), 0,25% dos positivos. A união dos sinais em `houve_obito` cobre os dois casos.
- `houve_obito` tem **16.232 positivos (0,47%)**. O desbalanceamento exige split estratificado, pesos de classe ou reamostragem só no treino, e métricas como PR-AUC, recall e F1 da classe positiva.
- O `tipo_cat` define o óbito quase por completo: o tipo 3 é 100% positivo, o tipo 2 tem 0 óbitos e só o tipo 1 varia (14.010 óbitos em cerca de 3,41 milhões). Como `atraso_cat_dias` e `faixa_atraso_cat` são NULL / `NAO_APLICAVEL` nos tipos 2 e 3, também denunciam o tipo e estão no mapa de vazamento do óbito.
- **Recomendação para a Fase 6 (decisão ainda aberta):** restringir a modelagem do óbito a `tipo_cat = 1`, para que o alvo meça o desfecho do acidente e não o tipo de comunicação. O recorte deve ser citado no artigo.

### Validação de `houve_afastamento` (crosstab em `cat_limpo.parquet`)
Total de 3.429.605 linhas, consistente com o crosstab do óbito.

| indica_afastamento | data de afastamento | linhas |
|---|---|---|
| sim | sim | 2.735.594 |
| não | não | 693.967 |
| não | sim | 40 |
| sim | não | 4 |

- Os dois sinais coincidem em 99,998% das linhas; só 44 divergem. A união em `houve_afastamento` cobre os dois casos e dá **2.735.638 positivos (79,77%)**, sem desbalanceamento forte.
- **Todas as 16.232 linhas de óbito têm afastamento.** Os indicadores de óbito (`indica_obito`, `indica_obito_acidente`, `indica_acidente`) e `data_obito_segurado` são, portanto, consequência do mesmo evento e foram acrescentados ao mapa de vazamento do afastamento.
- **`tipo_cat` também denuncia o afastamento:** o tipo 3 é 100% positivo, o tipo 2 tem 99,93% (18.899 de 18.913) e o tipo 1 tem 79,6% (2.714.517 de 3.408.470). Como no óbito, `tipo_cat`, `atraso_cat_dias` e `faixa_atraso_cat` entram na lista de proibidas. Restringir a `tipo_cat = 1` também vale para este alvo (recomendação para a Fase 6, decisão aberta).

### Mapa de vazamento (data leakage)
Fica em `configs/cat/features.yaml` (`vazamento`) e é lido por `colunas_proibidas(alvo)` em `cat/features.py`; a Fase 6 (`visoes.py`) deve usá-lo para descartar colunas.

| Alvo | Colunas proibidas como preditor (além das de `sempre`) |
|---|---|
| `obito` | `indica_obito`, `indica_obito_acidente`, `indica_acidente`, `tipo_cat`, `tipo_cat_cod`, `data_obito_segurado`, `especie_beneficio`, `especie_codigo`, `data_despacho`, `atraso_cat_dias`, `faixa_atraso_cat` |
| `afastamento` | `indica_afastamento`, `data_afastamento`, `especie_beneficio`, `especie_codigo`, `data_despacho`, `indica_obito`, `indica_obito_acidente`, `indica_acidente`, `data_obito_segurado`, `tipo_cat`, `tipo_cat_cod`, `atraso_cat_dias`, `faixa_atraso_cat` |
| `beneficio` | `especie_codigo`, `data_despacho`, `indica_afastamento`, `data_afastamento`, `ano_competencia`, `ano_acidente` |

`sempre`: `arquivo_origem`, `ano_arquivo`, `semestre_arquivo` (o % de CATs com benefício cai de 23% para 2,4% entre arquivos, provavelmente por data de extração), `numero_cat`, `razao_social`, `local_acidente`, `cnpj_empregador`, `cnpj_raiz` (identificadores ou alta cardinalidade) e os desfechos `houve_*`. A lista é uma proposta inicial: revise-a quando o alvo for escolhido (Fase 6).