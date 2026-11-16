# Auditoria da base BC (Fase 2)

_Gerado por `scripts/bc_02_auditoria.py`. Todas as tabelas completas estão em `docs/qualidade/` (prefixo `bc_`)._

## 0. Escopo e dimensões
- 7 arquivos, **35,510,608** linhas no consolidado; **0** linhas-lixo (competência fora do formato mês/AAAA) removidas antes da auditoria; **35,510,608** linhas auditadas, 19 colunas.
- Todas as colunas chegam como VARCHAR (tipo final definido na limpeza). Tipos e cardinalidade: `bc_dimensoes_tipos.csv`.
- O BC não tem data do acidente, CAT, emitente nem empregador. O "ano" das tabelas é o ano do arquivo (`ano_arquivo`).

## 1. Preenchimento por coluna e por ano
`pct_vazio` soma nulos reais e nulos disfarçados. Colunas com variação de preenchimento >= 20 p.p. entre anos:

_Nenhuma coluna com mudança de preenchimento._

Colunas com mais vazios no total:

| coluna | tipo_bruto | n_distintos_sem_vazios | pct_vazio_total |
|---|---|---|---|
| vinculo_dependentes | VARCHAR | 9 | 72.16 |
| cid_10 | VARCHAR | 12528 | 49.05 |
| cid_10_2 | VARCHAR | 10647 | 47.99 |
| data_dcb | VARCHAR | 7147 | 42.1 |
| municipio_residencia | VARCHAR | 5566 | 0.01 |
| especie | VARCHAR | 53 | 0 |
| competencia_concessao | VARCHAR | 84 | 0 |
| sexo | VARCHAR | 2 | 0 |
| data_nascimento | VARCHAR | 38486 | 0 |
| clientela | VARCHAR | 2 | 0 |
| despacho | VARCHAR | 17 | 0 |
| forma_filiacao | VARCHAR | 10 | 0 |

_(mostrando 12 de 19 linhas; tabela completa no CSV)_


Tabelas: `bc_dimensoes_tipos.csv`, `bc_nulos_por_ano.csv`, `bc_nulos_por_ano_pivot.csv`.

## 2. Nulos disfarçados
Valores suspeitos (parecem "não informado") que **ainda não estão** na lista `nulos_disfarcados` de `regras_limpeza.yaml`. Decida, caso a caso, se entram na lista ou se são categorias legítimas:

_Nenhum valor suspeito fora da lista atual._

Todos os valores por coluna (categorias com até 100 níveis) ou top 30: `bc_top_valores_categoricas.csv`. Problemas de encoding (mojibake):

_Nenhum padrão de mojibake encontrado._

## 3. Duplicados
| tipo | grupos | linhas_envolvidas | linhas_excedentes | pct_excedentes |
|---|---|---|---|---|
| exata (todas as colunas) | 5931 | 11995 | 6064 | 0.017 |
| aproximada (nascimento, sexo, espécie, DIB, CID, município) | 920 | 1844 | 924 | 0.003 |
|   aproximada: grupos que cruzam arquivos | 127 |  |  |  |
|   aproximada: grupos com DDB diferentes (possível reconcessão) | 842 |  |  |  |

Chave exata: todas as colunas (exceto metadados de arquivo). Chave aproximada: o BC não tem identificador, então "mesmo beneficiário e mesmo benefício" = nascimento + sexo + espécie + DIB + CID + município, todos preenchidos. Linhas aproximadas com DDB diferente costumam ser reconcessões, não erro.

**Risco da remoção de duplicatas exatas:** sem identificador, linhas legitimamente idênticas (ex.: pensão por morte entre irmãos com a mesma data de nascimento) também seriam removidas. Onde caem as duplicatas exatas (`bc_duplicados_por_especie.csv`):

| especie | n_linhas | duplicatas_exatas | pct_da_especie |
|---|---|---|---|
| APOSENTADORIA POR IDADE | 4602180 | 5038 | 0.109 |
| AMPARO SOCIAL AO IDOSO | 1103485 | 470 | 0.043 |
| PENSAO POR MORTE PREVIDENCIARIA | 2956006 | 213 | 0.007 |
| AMP. SOCIAL PESSOA PORTADORA DEFICIENCIA | 1211098 | 82 | 0.007 |
| APOSENTADORIA POR TEMPO DE CONTRIBUICAO | 2366743 | 65 | 0.003 |
| APOSENTADORIA INVALIDEZ PREVIDENCIARIA | 1389486 | 54 | 0.004 |
| AUXILIO SALARIO MATERNIDADE | 4322726 | 52 | 0.001 |
| AUXILIO DOENCA PREVIDENCIARIO | 15149446 | 45 | 0 |
| AUXILIO RECLUSAO | 173894 | 14 | 0.008 |
| APOSENT. INVALIDEZ ACIDENTE TRABALHO | 76484 | 8 | 0.01 |

_(mostrando 10 de 18 linhas; tabela completa no CSV)_


## 4. Inconsistências de data e idade
| regra | n | pct | acao_na_limpeza |
|---|---|---|---|
| competência ausente ou fora do formato mês/AAAA | 0 | 0 | mantida (NULL) |
| data_nascimento inválida ou ausente | 57 | 0.0002 | mantida (idade fica NULL) |
| data_dib inválida ou ausente | 0 | 0 | mantida (NULL) |
| data_ddb inválida ou ausente | 0 | 0 | mantida (NULL) |
| data_dcb ausente (00/00/0000: sem cessação) | 14951186 | 42.1034 | esperado; mantida (NULL) |
| data_dib posterior à data_ddb | 13 | 0 | só auditoria (ver remover_se_dib_apos_ddb) |
| data_dib posterior a 2018-12-31 (futuro) | 0 | 0 | remover linha |
| data_ddb posterior a 2018-12-31 (futuro) | 0 | 0 | remover linha |
| data_dib anterior a 2012-01-01 | 1905960 | 5.3673 | só auditoria (DIB retroativa é possível) |
| data_ddb anterior a 2012-01-01 | 0 | 0 | só auditoria |
| data_dcb anterior à data_dib | 1820 | 0.0051 | anular data_dcb |
| data_dcb anterior à data_ddb | 3429141 | 9.6567 | só auditoria |
| data_dcb posterior a 2018-12-31 | 416304 | 1.1723 | só auditoria (cessação estimada) |
| nascimento no futuro | 0 | 0 | idade vira NULL |
| idade negativa (nascimento posterior ao início do benefício) | 23966 | 0.0675 | idade vira NULL |
| idade abaixo de 0 | 23966 | 0.0675 | idade vira NULL |
| idade entre 0 e 13 (comum em pensão por morte) | 823972 | 2.3204 | mantida; ver bc_idade_por_especie.csv |
| idade acima de 90 | 24343 | 0.0686 | só auditoria |
| idade acima de 110 | 11 | 0 | idade vira NULL |
| competência diferente do mês da data_ddb | 0 | 0 | só auditoria (testa a hipótese competência = mês do despacho) |
| ano da competência diferente do ano do arquivo | 0 | 0 | só auditoria |
| ano da competência fora do período esperado | 0 | 0 | só auditoria |

A coluna `acao_na_limpeza` mostra o que a Fase 3 faz com cada regra. Distribuição de `qt_sm_rmi` e da idade (para confirmar as faixas do YAML): `bc_numericas.csv`; idade por espécie: `bc_idade_por_especie.csv`.

| variavel | metrica | valor |
|---|---|---|
| qt_sm_rmi | n_preenchidos | 35510608 |
| qt_sm_rmi | n_nao_numerico | 0 |
| qt_sm_rmi | n_zero_ou_negativo | 0 |
| qt_sm_rmi | n_abaixo_de_1_sm | 178191 |
| qt_sm_rmi | n_acima_de_30 | 61 |
| qt_sm_rmi | minimo | 0.001 |
| qt_sm_rmi | maximo | 831.836 |
| qt_sm_rmi | p0.1 | 0.5 |
| qt_sm_rmi | p1 | 1 |
| qt_sm_rmi | mediana | 1 |
| qt_sm_rmi | p99 | 5.23 |
| qt_sm_rmi | p99.9 | 6.134 |
| idade | n_calculada | 35510551 |
| idade | minimo | -98 |
| idade | maximo | 114 |
| idade | n_negativa | 23966 |
| idade | n_0_a_13 | 823972 |
| idade | n_acima_de_90 | 24343 |
| idade | p0.1 | 0 |
| idade | p1 | 4 |
| idade | mediana | 46 |
| idade | p99 | 79 |
| idade | p99.9 | 89 |


## 5. Cardinalidade das categóricas
| coluna | n_niveis | top1 | top1_pct | niveis_para_90pct | niveis_para_99pct | niveis_raros | pct_linhas_em_raros |
|---|---|---|---|---|---|---|---|
| cid_10 | 12528 | D25 | 1.72 | 1110 | 4366 | 12347 | 36.786 |
| cid_10_2 | 10647 | EM BRANCO | 2.21 | 1079 | 4250 | 10469 | 36.264 |
| municipio_residencia | 5566 | 21504-SP-SAO PAULO | 5.32 | 2546 | 4873 | 5420 | 56.357 |
| cid_categoria | 2180 | M54 | 4.73 | 279 | 892 | 2034 | 18.67 |
| especie | 53 | AUXILIO DOENCA PREVIDENCIARIO | 42.66 | 7 | 12 | 38 | 0.032 |
| uf | 27 | SAO PAULO | 23.27 | 16 | 24 | 0 | 0 |
| despacho | 17 | CONCESSAO NORMAL | 73.81 | 3 | 7 | 8 | 0.108 |
| forma_filiacao | 10 | EMPREGADO | 37.21 | 4 | 6 | 2 | 0.083 |
| vinculo_dependentes | 9 | FILHO | 74.04 | 2 | 3 | 3 | 0.13 |
| ramo_atividade | 9 | COMERCIARIO | 75.84 | 2 | 4 | 3 | 0.154 |
| classificador_pa | 3 | SEM PENSAO ALIMENTICIA | 99.64 | 1 | 1 | 1 | 0.012 |
| sexo | 2 | FEMININO | 56.45 | 2 | 2 | 0 | 0 |
| clientela | 2 | URBANO | 81.4 | 2 | 2 | 0 | 0 |
| acordo_internacional | 2 | NAO | 99.97 | 1 | 1 | 1 | 0.035 |


## 6. Distribuição por ano e por espécie
O BC traz **todos** os auxílios do INSS; a limpeza só restringe a benefícios acidentários se `escopo.especies_manter` estiver preenchido. Lista completa de espécies: `bc_especies.csv`. A sugestão abaixo é automática (nome contém "ACIDENT" e não contém "PREVIDENCI") e **precisa ser conferida por você**:

| especie | n | anos_presentes | primeiro_ano | ultimo_ano | pct | sugestao_acidentaria | no_yaml_atual |
|---|---|---|---|---|---|---|---|
| AUXILIO DOENCA PREVIDENCIARIO | 15149446 | 7 | 2012 | 2018 | 42.662 | False | False |
| APOSENTADORIA POR IDADE | 4602180 | 7 | 2012 | 2018 | 12.96 | False | False |
| AUXILIO SALARIO MATERNIDADE | 4322726 | 7 | 2012 | 2018 | 12.173 | False | False |
| PENSAO POR MORTE PREVIDENCIARIA | 2956006 | 7 | 2012 | 2018 | 8.324 | False | False |
| APOSENTADORIA POR TEMPO DE CONTRIBUICAO | 2366743 | 7 | 2012 | 2018 | 6.665 | False | False |
| AUXILIO DOENCA POR ACIDENTE DO TRABALHO | 1715928 | 7 | 2012 | 2018 | 4.832 | True | False |
| APOSENTADORIA INVALIDEZ PREVIDENCIARIA | 1389486 | 7 | 2012 | 2018 | 3.913 | False | False |
| AMP. SOCIAL PESSOA PORTADORA DEFICIENCIA | 1211098 | 7 | 2012 | 2018 | 3.411 | False | False |
| AMPARO SOCIAL AO IDOSO | 1103485 | 7 | 2012 | 2018 | 3.107 | False | False |
| AUXILIO RECLUSAO | 173894 | 7 | 2012 | 2018 | 0.49 | False | False |
| AUXILIO ACIDENTE | 138538 | 7 | 2012 | 2018 | 0.39 | True | False |
| APOSENTADORIA ESPECIAL | 127210 | 7 | 2012 | 2018 | 0.358 | False | False |
| AUXILIO ACIDENTE PREVIDENCIARIO | 86454 | 7 | 2012 | 2018 | 0.243 | False | False |
| APOSENT. TEMPO DE SERVICO DE PROFESSOR | 79436 | 7 | 2012 | 2018 | 0.224 | False | False |
| APOSENT. INVALIDEZ ACIDENTE TRABALHO | 76484 | 7 | 2012 | 2018 | 0.215 | True | False |
| PENSAO POR MORTE ACIDENTE DO TRABALHO | 3663 | 7 | 2012 | 2018 | 0.01 | True | False |
| PENSAO VITALICIA DEPENDENTES SERINGUEIRO | 2034 | 7 | 2012 | 2018 | 0.006 | False | False |
| PENSAO ESPECIAL HANSENIASE LEI 11520/07 | 1993 | 7 | 2012 | 2018 | 0.006 | False | False |
| PENSAO POR MORTE DE EX-COMBATENTE | 1094 | 7 | 2012 | 2018 | 0.003 | False | False |
| AUXILIO SUPLEMENTAR ACIDENTE TRABALHO | 1071 | 7 | 2012 | 2018 | 0.003 | True | False |
| PENSAO VITALICIA SINDROME TALIDOMIDA | 437 | 7 | 2012 | 2018 | 0.001 | False | False |
| PENSAO VITALICIA SERINGUEIROS | 401 | 7 | 2012 | 2018 | 0.001 | False | False |
| APOSENTADORIA POR INVALIDEZ-TRAB. RURAL | 136 | 7 | 2012 | 2018 | 0 | False | False |
| PENSAO POR MORTE DE TRABALHADOR RURAL | 133 | 7 | 2012 | 2018 | 0 | False | False |
| PENSAO POR MORTE DE ANISTIADOS | 124 | 7 | 2012 | 2018 | 0 | False | False |
| PENSAO POR MORTE EX-SASSE | 96 | 7 | 2012 | 2018 | 0 | False | False |
| RENDA MENSAL VITALICIA POR INCAPACIDADE | 61 | 7 | 2012 | 2018 | 0 | False | False |
| APOSENTADORIA POR VELHICE - TRAB. RURAL | 59 | 6 | 2012 | 2018 | 0 | False | False |
| AMPARO PREVIDENC. INVALIDEZ- TRAB. RURAL | 37 | 7 | 2012 | 2018 | 0 | False | False |
| APOSENT. INVALIDEZ ACIDENTARIA-TRAB.RUR. | 21 | 7 | 2012 | 2018 | 0 | True | False |
| BENEFICIO INDENIZATORIO A CARGO DA UNIAO | 21 | 5 | 2013 | 2018 | 0 | False | False |
| APOSENT. POR TEMPO SERVICO EX-COMBATENTE | 17 | 5 | 2012 | 2018 | 0 | False | False |
| PENSAO POR MORTE EX-COMBATENTE MARITIMO | 15 | 5 | 2013 | 2018 | 0 | False | False |
| PENSAO MORTE SERVIDOR PUBLICO FEDERAL | 10 | 5 | 2012 | 2016 | 0 | False | False |
| APOSENTADORIA DE ANISTIADOS | 8 | 6 | 2012 | 2017 | 0 | False | False |
| APOSENT. TEMPO SERVICO JORNALISTA | 7 | 3 | 2014 | 2018 | 0 | False | False |
| PENSAO ESPECIAL VITALICIA - LEI 9793/99 | 7 | 3 | 2012 | 2014 | 0 | False | False |
| APOSENT. POR IDADE - EMPREGADOR RURAL | 7 | 3 | 2012 | 2014 | 0 | False | False |
| APOSENTADORIA ESPECIAL DE AERONAUTA | 7 | 5 | 2012 | 2017 | 0 | False | False |
| ABONO PERMANENCIA EM SERVICO - 30 ANOS | 6 | 4 | 2012 | 2018 | 0 | False | False |

_(mostrando 40 de 53 linhas; tabela completa no CSV)_


Sugestão para colar em `configs/beneficios/regras_limpeza.yaml` (revise antes):

```yaml
escopo:
  especies_manter:
    - AUXILIO DOENCA POR ACIDENTE DO TRABALHO
    - AUXILIO ACIDENTE
    - APOSENT. INVALIDEZ ACIDENTE TRABALHO
    - PENSAO POR MORTE ACIDENTE DO TRABALHO
    - AUXILIO SUPLEMENTAR ACIDENTE TRABALHO
    - APOSENT. INVALIDEZ ACIDENTARIA-TRAB.RUR.
```

Por ano e espécie: `bc_distribuicao_ano_especie.csv`. Por arquivo:

| arquivo_origem | n_linhas | pct_com_cid | pct_com_dcb | n_competencia_fora_do_ano_arquivo |
|---|---|---|---|---|
| 01.2012_01-12_BC.parquet | 5004105 | 53.3 | 59.2 | 0 |
| 02.2013_01-12_BC.parquet | 5255162 | 52.8 | 58.9 | 0 |
| 03.2014_01-12_BC.parquet | 5257897 | 53.1 | 59.5 | 0 |
| 04.2015_01-12_BC.parquet | 4384448 | 48.8 | 56.7 | 0 |
| 05.2016_01-12_BC.parquet | 5293918 | 50.1 | 57.1 | 0 |
| 06.2017_01-12_BC.parquet | 5150319 | 46.8 | 54.8 | 0 |
| 07.2018_01-12_BC.parquet | 5164759 | 51.4 | 58.8 | 0 |

