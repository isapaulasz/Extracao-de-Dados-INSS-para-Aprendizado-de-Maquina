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

## Fase 5: Análise exploratória (fechamento: desbalanceamento e variáveis por técnica)

**Escopo desta seção:** os dois últimos itens do roadmap da Fase 5 (desbalanceamento dos candidatos a alvo; quais variáveis entram em cada técnica). As subseções de univariada, bivariada, informação mútua, séries temporais e território serão acrescentadas como 5.1 a 5.5. **Data:** 16/11/2026. **Status:** propostas; os pontos marcados [CONFIRMAR] dependem de decisão do autor.

Executada por `scripts/eda_05_decisao.py` (`make eda-cat-decisao`, `make eda-bc-decisao`), com regras em `configs/<base>/decisao.yaml` e código em `common/eda_decisao.py`. O script **não lê o parquet**: consolida as tabelas que a EDA já gravou em `reports/<base>/tables/eda/`, então roda em segundos e cada decisão sai com o motivo. Saídas em `reports/<base>/tables/eda/decisao/`:

| Arquivo | Conteúdo |
|---|---|
| `plano_desbalanceamento.csv` | baselines, positivos por partição, métricas e estratégia por técnica |
| `heterogeneidade_taxas.csv` | (CAT) estabilidade da prevalência por arquivo e por território |
| `variaveis_por_tecnica.csv` | status e motivo de cada variável por alvo × técnica |
| `matriz_variaveis_tecnica.csv` | mesma informação em formato largo (tabela do artigo) |
| `suporte_itens.csv`, `cobertura_suporte.csv` | (BC) itens e quantos sobrevivem a cada suporte mínimo |

### 5.6 Desbalanceamento dos candidatos a alvo (CAT)

| Alvo | Recorte | Classe positiva | Positivos | % | Razão maj:min | Acurácia do classificador trivial | Papel proposto |
|---|---|---|---:|---:|---:|---:|---|
| `houve_obito` | `tipo_cat = 1` | True | 14.010 | 0,41% | 1:242 | 99,59% | **primário** |
| `houve_obito` | completo | True | 16.232 | 0,47% | 1:210 | 99,53% | sensibilidade |
| `houve_afastamento` | `tipo_cat = 1` | True | 2.714.517 | 79,64% | 1:3,9 | 79,64% | secundário (controle) |
| `tem_data_despacho` | completo | sim | 463.125 | 13,50% | 1:6,4 | 86,50% | descartado |
| `tipo_acidente` | completo | (4 classes) | | NAO_INFORMADO 0,19% | 1:404 | | reserva |
| `natureza_lesao_agrup` | completo | (24 classes, 11 < 1%) | | | 1:489 | | reserva |
| `parte_corpo_atingida_agrup` | completo | (42 classes, 21 < 1%) | | | 1:233 | | reserva |

**Decisões:**

1. **Alvos.** Óbito (`tipo_cat = 1`) é o alvo principal; afastamento (`tipo_cat = 1`) entra como contraste com um alvo balanceado, para mostrar que a escolha da métrica muda a conclusão. `tem_data_despacho` sai: a taxa de benefício cai de 23,0% para 2,4% entre os arquivos (V = 0,20 entre arquivo e alvo), ou seja, o alvo mede a data de extração e não o fenômeno. Os multiclasse ficam de reserva: CID-10 (capítulos S e T) já codifica natureza da lesão e parte do corpo, o que cria vazamento por construção. [CONFIRMAR]
2. **Métricas do óbito.** Um classificador que sempre responde "não" acerta 99,59%, então a **acurácia é proibida**. Métrica primária: **PR-AUC** (average precision), sempre ao lado da linha de base, que é a prevalência (0,0041), e não 0,5. Secundárias: recall com precisão fixa, F2 e F1 da classe positiva, MCC, balanced accuracy e Brier com curva de calibração. ROC-AUC só como complemento (é otimista com classe rara). O limiar de decisão é escolhido na validação, nunca no teste. Para o afastamento: ROC-AUC primária, com PR-AUC, F1, balanced accuracy e MCC, e acurácia só comparada ao baseline de 79,64%.
3. **Partição.** Split estratificado pelo alvo (80/20, semente 42) e validação cruzada estratificada de 5 dobras. Com 14.010 positivos sobram **11.208 no treino, 2.802 no teste e cerca de 2.242 por dobra**; a meia-largura do IC95% do recall no pior caso é ±1,85 p.p. O problema do óbito, portanto, não é falta de positivos, e sim o viés do otimizador para a classe majoritária.
4. **Tratamento do desbalanceamento, só no treino.** Árvore: `class_weight='balanced'` e `min_samples_leaf` alto. Random Forest: `class_weight='balanced_subsample'`. Rede neural: subamostragem aleatória dos negativos (ou `sample_weight`, se a versão instalada do scikit-learn suportar no `MLPClassifier`). Pesos e subamostragem distorcem as probabilidades: recalibrar antes de interpretar com SHAP ou de escolher limiar.
5. **Estabilidade da prevalência** (`heterogeneidade_taxas.csv`). Com milhões de linhas o p-valor sempre rejeita; a leitura usa a razão máx/mín e o V de Cramér.

| Alvo | Eixo | Taxa mín. | Taxa máx. | Razão | V | Leitura |
|---|---|---:|---:|---:|---:|---|
| óbito | arquivo | 0,415% | 0,495% | 1,19 | 0,004 | estável (tendência decrescente, z = -5,3) |
| afastamento | arquivo | 76,3% | 81,0% | 1,06 | 0,026 | estável (2017-S1 é o ponto baixo) |
| despacho | arquivo | 2,4% | 23,0% | 9,5 | 0,199 | **artefato de extração** |
| óbito | região | 0,39% (Sudeste) | 0,73% (Norte) | 1,88 | 0,017 | heterogênea |
| óbito | UF do segurado | 0,32% (SP) | 1,53% (TO) | 4,70 | 0,026 | heterogênea |
| afastamento | UF do segurado | 72,2% (SE) | 88,6% (DF) | 1,23 | 0,079 | estável (no limite do limiar 1,25) |

   Prevalência estável entre arquivos sustenta o split estratificado aleatório. Como a taxa de óbito decresce de leve, a validação temporal (treino nos arquivos 2012-2016, teste em 2017-2018, cerca de 3,9 mil positivos com todos os tipos de CAT) entra como **análise de sensibilidade**. A UF é a UF de residência, não a do acidente (limitação já registrada na Fase 4).
6. **BC (sem alvo supervisionado).** O desbalanceamento aparece como itens raros: `grupo_especie` tem 88,65% em AUXILIO_DOENCA, PENSAO_POR_MORTE com 0,19% e AUXILIO_SUPLEMENTAR com 0,055% (1.071 linhas); `faixa_rmi = DESCONHECIDA` tem 2 linhas; `faixa_duracao_beneficio = 366+` tem 0,46%. Consequências para o Apriori/FP-Growth: (a) a confiança sozinha engana, porque a classe dominante já tem 88,65% de base; ranquear por **lift**, leverage e suporte absoluto; (b) para um consequente raro aparecer, o suporte mínimo precisa ficar abaixo da frequência dele (≈ 0,05% para AUXILIO_SUPLEMENTAR); com 179 itens, 168 sobrevivem a suporte ≥ 0,1%, 109 a ≥ 0,5%, 91 a ≥ 1% e 61 a ≥ 5% (`cobertura_suporte.csv`); ponto de partida sugerido: suporte mínimo de 0,05% e `max_len` 4, a calibrar na Fase 7 [propor]; (c) itens `NAO_INFORMADO` e `faixa_rmi = DESCONHECIDA` saem das transações. Para o K-Prototypes (1,9 milhão de linhas) uma amostra proporcional teria só ~55 linhas de AUXILIO_SUPLEMENTAR em 100 mil; verificar a cobertura dos grupos raros na amostra ou agrupar por espécie [propor].

### 5.7 Variáveis por técnica

**Ordem das regras** (a primeira que casa define o status): identificador → vazamento (mapa da Fase 4, `u_alvo` ≥ 0,90 ou marca da EDA) → exclusão extra por alvo → exclusão própria → redundância (um nível por hierarquia e por técnica) → baixa informação → cardinalidade. Status possíveis: `entra`, `entra_com_cuidado` (> 50 níveis), `entra_opcional`, `entra_numerica` e as exclusões (`excluir_identificador`, `excluir_vazamento`, `excluir_redundante`, `excluir_baixa_informacao`, `excluir_outro`, `excluir_constante_no_recorte`); no BC também `condicional` (depende de confirmação).

**CAT (predição + XAI).** Mesma lista base para Árvore, Random Forest e Rede Neural; a diferença é o nível da hierarquia e o tratamento das variáveis fracas.

| Alvo | Árvore | Random Forest / Rede neural |
|---|---:|---:|
| `houve_obito` (`tipo_cat = 1`) | 16 variáveis | 21 (5 delas opcionais) |
| `houve_afastamento` (`tipo_cat = 1`) | 15 | 21 (6 opcionais) |

- **Excluídas por vazamento (10 em cada alvo):** `especie_beneficio`, `especie_codigo`, `indica_acidente`, `indica_obito_acidente`, `tem_data_obito_segurado`, `ramo_atividade`, `tem_data_despacho`, `tem_data_afastamento`, `indica_afastamento` e `houve_afastamento` (no óbito); no afastamento, `houve_obito` ocupa o lugar de `houve_afastamento`.
- **Correção ao mapa da Fase 4.** `indica_afastamento` estava como "candidata" para o óbito, mas todos os 16.232 óbitos têm afastamento, e `tem_data_afastamento` e `houve_afastamento` já eram excluídas. Passa a ser excluída também. Migrar para `features.yaml` na Fase 6. [CONFIRMAR]
- **`faixa_atraso_cat` liberada dentro do recorte `tipo_cat = 1`.** Ela estava no mapa só porque denunciava o tipo da CAT (NAO_APLICAVEL nos tipos 2 e 3); com o recorte esse motivo some. No óbito ela tem U ≈ 0 (fica opcional); no afastamento entra. [CONFIRMAR]
- **Um nível por hierarquia:** CID (`capitulo_cid` na árvore, `cid_categoria_agrup` nos demais), CNAE (`cnae_secao` / `cnae_empregador_agrup`), CBO (`cbo_grande_grupo` / `cbo_agrup`).
- **Geografia:** `uf_segurado` em todas as técnicas. Carrega mais informação que `regiao` (afastamento: V = 0,078 contra 0,017) e as demais UFs e municípios são redundantes (V entre 0,65 e 1,0) ou têm MI ≈ 0. `regiao` fica para descrição e mapas.
- **Fora de todas as técnicas:** `ano_acidente` e `ano_competencia` (V = 1,0 entre si, MI ≈ 0, e a prevalência deriva entre arquivos; servem só para partição temporal), `emitente_empregador` (função de `emitente_cat`) e os identificadores.
- **Baixa informação marginal:** sai da árvore única, que precisa ser interpretável, mas fica `entra_opcional` em RF e rede neural, porque a MI univariada não enxerga interações. Testar por ablação na Fase 7.
- **Alta cardinalidade** (`agente_causador_agrup`, `cid_categoria_agrup`, `cnae_empregador_agrup`, `cbo_agrup`): agrupamento de raros já feito na Fase 3; codificação por frequência ou por alvo **ajustada só dentro do treino**.
- **Verificação pendente:** `natureza_lesao_agrup`, `parte_corpo_atingida_agrup` e `cid_categoria_agrup` são os preditores mais fortes do óbito (V entre 0,17 e 0,22 no recorte). Confirmar na documentação do INSS que são preenchidos no registro da CAT e não depois do desfecho. O XAI vai colocá-los no topo; o artigo precisa dizer por que isso não é vazamento. [CONFIRMAR]

**BC (Apriori e clusterização).** 12 variáveis em cada técnica.

- **Apriori** (itens discretos): `grupo_especie`, `faixa_rmi`, `faixa_etaria`, `faixa_atraso_concessao`, `cid_categoria_agrup` (`entra_com_cuidado`, 78 itens), `uf`, `sexo`, `forma_filiacao`, `ramo_atividade`, `despacho_agrup`, `ano_competencia`, `mes_dib`. Consequentes de interesse: `grupo_especie`, `faixa_rmi` e `faixa_duracao_beneficio`.
- **Clusterização** (K-Prototypes): as mesmas, com `capitulo_cid` e `regiao` no lugar das versões mais granulares, e `idade`, `qt_sm_rmi` e `atraso_concessao_dias` como numéricas.
- **Condicionais (DCB):** `faixa_duracao_beneficio`, `tem_data_cessacao`, `duracao_beneficio_dias` e `data_dcb` só entram depois de confirmar se a DCB é real ou programada (pendência aberta na Fase 4).
- **Fora:** `vinculo_dependentes` (significado desconhecido), `dia_semana_dib` (data administrativa), `classificador_pa` e `acordo_internacional` (> 99% em uma categoria), `clientela` (função de `forma_filiacao`, V = 0,998), `cid_10` e `cid_categoria` (cardinalidade), `ano_dib`, `mes_competencia`, `especie`, `despacho`, `rmi_abaixo_piso` e as datas brutas.

**Limitações.** A MI e o V são univariados e medem associação, não importância no modelo; esta tabela é o ponto de partida da Fase 6 e será revista pela ablação e pelo SHAP. Os limiares (`limiar_razao_estavel` = 1,25, `max_niveis_arvore` = 50, `limiar_baixa_informacao` = 0,001) são convenções do autor e ficam no YAML.
