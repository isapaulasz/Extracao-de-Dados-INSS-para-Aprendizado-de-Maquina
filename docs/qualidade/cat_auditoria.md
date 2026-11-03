# Auditoria da base CAT (Fase 2)

_Gerado por `scripts/cat_05_auditoria.py`. Todas as tabelas completas estão em `docs/qualidade/`._

## 0. Escopo e dimensões
- 14 arquivos, **3,429,666** linhas no consolidado; **20** linhas-lixo da exportação (cabeçalho e linha de total) removidas antes da auditoria; **3,429,646** linhas auditadas, 42 colunas.
- Todas as colunas chegam como VARCHAR (tipo final definido na limpeza). Tipos e cardinalidade: `cat_dimensoes_tipos.csv`.
- Fase 1: o arquivo 14 (2018-S2) ganha `numero_cat` e `razao_social` e perde `valor_remuneracao`. Detalhe em `cat_schema_por_arquivo.csv`, `cat_matriz_nomes.csv` e `cat_matriz_tipos.csv`.

## 1. Preenchimento por coluna e por ano
`pct_vazio` soma nulos reais e nulos disfarçados. Colunas com variação de preenchimento >= 20 p.p. entre anos:

| coluna | pct_vazio_min_ano | pct_vazio_max_ano | variacao_pp |
|---|---|---|---|
| valor_remuneracao | 0 | 56.64 | 56.64 |
| razao_social | 43.36 | 100 | 56.64 |
| numero_cat | 43.36 | 100 | 56.64 |


Colunas com mais vazios no total:

| coluna | tipo_bruto | n_distintos_sem_vazios | pct_vazio_total |
|---|---|---|---|
| data_obito_segurado | VARCHAR | 2483 | 99.53 |
| razao_social | VARCHAR | 80464 | 92.88 |
| numero_cat | VARCHAR | 244041 | 92.88 |
| ramo_atividade | VARCHAR | 8 | 86.5 |
| data_despacho | VARCHAR | 1951 | 86.5 |
| uf_acidente | VARCHAR | 15 | 29.43 |
| data_afastamento | VARCHAR | 2548 | 20.23 |
| valor_remuneracao | VARCHAR | 311184 | 7.12 |
| estado_civil | VARCHAR | 5 | 6.22 |
| cbo_2 | VARCHAR | 2262 | 5.58 |
| situacao_geradora | VARCHAR | 57 | 4.78 |
| cnae_empregador_2 | VARCHAR | 666 | 0.45 |

_(mostrando 12 de 42 linhas; tabela completa no CSV)_


Tabelas: `cat_dimensoes_tipos.csv`, `cat_nulos_por_ano.csv`, `cat_nulos_por_ano_pivot.csv`.

## 2. Nulos disfarçados
Valores suspeitos (parecem "não informado") que **ainda não estão** na lista `nulos_disfarcados` de `regras_limpeza.yaml`. Decida, caso a caso, se entram na lista ou se são categorias legítimas:

_Nenhum valor suspeito fora da lista atual._

Todos os valores por coluna (categorias com até 100 níveis) ou top 30: `cat_top_valores_categoricas.csv`. Problemas de encoding (mojibake):

_Nenhum padrão de mojibake encontrado._

## 3. Duplicados
| tipo | grupos | linhas_envolvidas | linhas_excedentes | pct_excedentes |
|---|---|---|---|---|
| exata (todas as colunas) | 39 | 78 | 39 | 0.001 |
| aproximada (nascimento, sexo, data do acidente, CID, CNPJ) | 10356 | 20869 | 10513 | 0.307 |
|   aproximada: grupos que cruzam arquivos | 1654 |  |  |  |
|   aproximada: grupos com tipo_cat diferentes (ex.: inicial + reabertura) | 9427 |  |  |  |

Chave exata: todas as colunas (exceto metadados de arquivo). Chave aproximada: mesmo trabalhador (nascimento + sexo), data do acidente, CID e empregador, com todos preenchidos. Linhas aproximadas que cruzam arquivos ou mudam de `tipo_cat` costumam ser reaberturas ou republicações, não erro.

## 4. Inconsistências de data
| regra | n | pct | acao_na_limpeza |
|---|---|---|---|
| data_acidente inválida ou ausente | 0 | 0 | linha-lixo já removida; resto: ver limpeza |
| data_emissao_cat inválida ou ausente | 0 | 0 | mantida (NULL) |
| data_nascimento inválida ou ausente | 146 | 0.0043 | mantida (idade fica NULL) |
| acidente posterior à emissão da CAT | 2 | 0.0001 | remover linha |
| acidente posterior a 2018-12-31 (futuro) | 2 | 0.0001 | remover linha |
| emissão posterior a 2018-12-31 (futuro) | 0 | 0 | remover linha |
| acidente anterior a 2012-01-01 | 0 | 0 | só auditoria (ver acidente_minimo) |
| emissão anterior a 2012-01-01 | 0 | 0 | só auditoria |
| nascimento no futuro | 0 | 0 | idade vira NULL |
| idade negativa (nascimento posterior ao acidente) | 0 | 0 | idade vira NULL |
| idade entre 0 e 13 | 226 | 0.0066 | idade vira NULL |
| idade acima de 90 | 58 | 0.0017 | idade vira NULL |
| afastamento anterior ao acidente | 1 | 0 | anular data_afastamento |
| óbito anterior ao acidente | 0 | 0 | anular data_obito_segurado |
| despacho anterior à emissão | 5759 | 0.1679 | só auditoria |
| competência diferente do mês de emissão | 0 | 0 | só auditoria |
| ano da competência fora do período esperado | 0 | 0 | só auditoria |


## 5. Cardinalidade das categóricas
`niveis_para_90pct` e `niveis_para_99pct` mostram quantos níveis cobrem 90% e 99% das linhas; `niveis_raros` usa o limiar de 0.1%.

| coluna | n_niveis | top1 | top1_pct | niveis_para_90pct | niveis_para_99pct | niveis_raros | pct_linhas_em_raros |
|---|---|---|---|---|---|---|---|
| local_acidente | 1132695 | VIA PUBLICA | 2.32 | 790419 | 1098468 | 1132636 | 76.192 |
| cnpj_empregador | 592366 | 34.028.316/0031-29 | 0.6 | 251583 | 558070 | 592357 | 97.802 |
| cid_10 | 8997 | S610 | 6.26 | 446 | 2401 | 8816 | 23.541 |
| cid_10_2 | 8989 | S61.0 FERIM DE DEDOS S/LESAO DA UNHA | 6.26 | 446 | 2400 | 8808 | 23.56 |
| municipio_segurado | 5537 | 355030-SAO PAULO | 8.34 | 957 | 3244 | 5354 | 35.933 |
| municipio_empresa | 5071 | 355030-SAO PAULO | 10.49 | 696 | 2332 | 4902 | 31.011 |
| cbo | 2342 | 784205 | 5.5 | 379 | 1201 | 2186 | 22.555 |
| cbo_2 | 2262 | 784205-ALIMENTADOR DE LINHA DE PROD. | 5.82 | 376 | 1172 | 2103 | 22.54 |
| cid_categoria | 1869 | S61 | 11.56 | 87 | 442 | 1774 | 9.07 |
| cnae_empregador | 1428 | 8610 | 10.08 | 265 | 523 | 1227 | 15.494 |
| cnae_empregador_2 | 666 | ATIVIDADES DE ATENDIMENTO HOSPITALAR | 10.13 | 260 | 499 | 463 | 14.915 |
| agente_causador | 301 | MOTOCICLETA, MOTONETA | 7.79 | 82 | 189 | 184 | 4.713 |
| situacao_geradora | 57 | IMPACTO DE PES. CONTRA OBJETO EM MOVIMENTO | 11.36 | 25 | 39 | 14 | 0.396 |
| parte_corpo_atingida | 44 | DEDO | 23.86 | 22 | 36 | 4 | 0.235 |
| uf_segurado | 28 | SAO PAULO | 36.23 | 14 | 23 | 0 | 0 |
| natureza_lesao | 28 | CORTE, LACERACAO, FERIDA CONTUSA, PUNCTURA | 20.98 | 10 | 19 | 6 | 0.198 |
| uf_empregador | 27 | SAO PAULO | 37.78 | 13 | 22 | 2 | 0.181 |
| uf_acidente | 15 | MARANHAO | 52.77 | 5 | 11 | 0 | 0 |
| especie_beneficio | 10 | CATS EMITIDAS SEM BENEFICIO | 79.52 | 2 | 3 | 7 | 0.108 |
| ramo_atividade | 8 | COMERCIARIO | 99.79 | 1 | 1 | 6 | 0.033 |
| tipo_local_acidente | 5 | EMPREGADORA | 56.28 | 3 | 5 | 0 | 0 |
| emitente_cat | 5 | EMPREGADOR | 98.3 | 1 | 2 | 0 | 0 |
| estado_civil | 5 | SOLTEIRO | 53.62 | 2 | 4 | 0 | 0 |
| sexo | 3 | MASCULINO | 68.68 | 2 | 2 | 1 | 0.001 |
| filiacao_segurado | 3 | EMPREGADO | 99.8 | 1 | 1 | 1 | 0 |
| tipo_acidente | 3 | TIPICO | 76.6 | 2 | 3 | 0 | 0 |
| tipo_cat | 3 | 1-INICIAL | 99.38 | 1 | 1 | 1 | 0.065 |
| area_atividade | 2 | URBANO | 92.68 | 1 | 2 | 0 | 0 |
| indica_acidente | 2 | NAO | 99.53 | 1 | 1 | 0 | 0 |
| indica_obito_acidente | 2 | NAO | 99.53 | 1 | 1 | 0 | 0 |
| indica_obito | 2 | NAO | 99.94 | 1 | 1 | 1 | 0.065 |
| indica_afastamento | 2 | SIM | 79.76 | 2 | 2 | 0 | 0 |
| origem_cadastramento | 2 | INTERNET | 100 | 1 | 1 | 1 | 0.001 |


## 6. Distribuição por ano e por tipo de CAT
Total por tipo:

| tipo_cat | n | pct |
|---|---|---|
| 1-INICIAL | 3408511 | 99.384 |
| 2-REABERTURA | 18913 | 0.551 |
| 3-COMUNICACAO DE OBITO | 2222 | 0.065 |


Por ano e tipo: `cat_distribuicao_ano_tipo.csv`. Por arquivo (inclui % de CATs com benefício, que varia entre arquivos):

| arquivo_origem | n_linhas | n_inicial | n_reabertura | n_comunicacao_obito | pct_com_beneficio | pct_com_despacho |
|---|---|---|---|---|---|---|
| 01.2012_01-06_CATS.parquet | 254932 | 253660 | 1145 | 127 | 23 | 23 |
| 02.2012_07-12_CATS.parquet | 238318 | 236428 | 1732 | 158 | 21.3 | 21.2 |
| 03.2013_01-06_CATS.parquet | 260977 | 259515 | 1299 | 163 | 20.3 | 20.3 |
| 04.2013_07-12_CATS.parquet | 290782 | 288479 | 2127 | 176 | 20.5 | 20.4 |
| 05.2014_01-06_CATS.parquet | 261589 | 260218 | 1200 | 171 | 20.1 | 20.1 |
| 06.2014_07-12_CATS.parquet | 287450 | 285287 | 1957 | 206 | 15 | 14.9 |
| 07.2015_01-06_CATS.parquet | 241267 | 240055 | 1045 | 167 | 9.9 | 9.8 |
| 08.2015_07-12_CATS.parquet | 250415 | 248739 | 1520 | 156 | 4.2 | 4.2 |
| 09.2016_01-06_CATS.parquet | 229023 | 227875 | 987 | 161 | 14.3 | 14.3 |
| 10.2016_07-12_CATS.parquet | 236189 | 234564 | 1438 | 187 | 10.4 | 10.4 |
| 11.2017_01-06_CATS.parquet | 213267 | 212281 | 881 | 105 | 11.4 | 11.4 |
| 12.2017_07-12_CATS.parquet | 234590 | 233064 | 1378 | 148 | 6.2 | 6.2 |
| 13.2018_01-06_CATS.parquet | 186806 | 185946 | 744 | 116 | 5.5 | 5.5 |
| 14.2018_07-12_CATS.parquet | 244041 | 242400 | 1460 | 181 | 2.4 | 2.4 |


## Decisões que esta auditoria alimenta
Ver `docs/decisoes_metodologicas.md` (seções "Fases 2-3").
