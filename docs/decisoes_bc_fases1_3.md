# BC (Benefícios Concedidos) — decisões das Fases 1 a 3

Referência de formato: `docs/decisoes_metodologicas.md` (CAT).

## Papel do BC no estudo (decidido em 15/11/2026)

**BC: regras de associação (Apriori/FP-Growth) e clusterização. CAT: predição (Árvore de Decisão, Rede Neural, Random Forest) + XAI.**
Motivo: o CAT tem desfechos claros (óbito, afastamento) e a Fase 4 dele já mapeia vazamento por alvo; o BC não tem desfecho natural, mas tem 1,9 milhão de linhas e muitas categóricas, que é o perfil de mineração de padrões.

## Fase 1: ingestão
7 arquivos anuais (2012–2018), 19 colunas harmonizadas para snake_case (`configs/beneficios/harmonizacao_schema.yaml`), consolidados em `bc_consolidado.parquet` com `arquivo_origem` e `ano_arquivo`. Soma das linhas por arquivo = total consolidado (validado): **35.510.608**.

## Fase 2: auditoria
Achados que moveram decisões: `EM BRANCO` e `Zerados` como nulos disfarçados; competência = mês da DDB em 100% das linhas; 9,7% das DCB anteriores à DDB na base toda; `vinculo_dependentes` com 78% NAO_INFORMADO no escopo e FILHO em 22–25% do auxílio-doença acidentário (significado desconhecido).

## Fase 3: limpeza (tabela de atrito, execução de 15/11/2026)

| Etapa | Removidas | Valores alterados | Restantes |
|---|---:|---:|---:|
| Entrada (consolidado) | 0 | 0 | 35.510.608 |
| Linhas-lixo da exportação | 0 | 0 | 35.510.608 |
| Escopo: 6 espécies acidentárias | 33.574.903 | 0 | 1.935.705 |
| Nulos disfarçados → NULL | 0 | 1.912.952 | 1.935.705 |
| Valores fora da regra → NULL (competência, CID, `qt_sm_rmi`, datas) | 0 | 9.969 | 1.935.705 |
| DIB/DDB posterior a 2018-12-31 | 0 | 0 | 1.935.705 |
| DCB anterior à DIB → NULL | 0 | 100 | 1.935.705 |
| Duplicatas exatas | 19 | 0 | 1.935.686 |
| Nulos em categóricas → NAO_INFORMADO | 0 | 1.964.901 | 1.935.686 |
| Categorias raras (< 0,1%) → OUTROS | 0 | 146.796 | 1.935.686 |

Decisões:
- **Escopo** por lista de espécies (AUXILIO DOENCA POR ACIDENTE DO TRABALHO, AUXILIO ACIDENTE, APOSENT. INVALIDEZ ACIDENTE TRABALHO, PENSAO POR MORTE ACIDENTE DO TRABALHO, AUXILIO SUPLEMENTAR ACIDENTE TRABALHO, APOSENT. INVALIDEZ ACIDENTARIA-TRAB.RUR.), aplicado logo após as linhas-lixo para que as contagens seguintes descrevam só o escopo. **[CONFIRMAR]** "AUXILIO ACIDENTE" foi incluído por inferência (existe "AUXILIO ACIDENTE PREVIDENCIARIO" à parte).
- **Duplicatas:** só 19 em 1,9 milhão; risco de remover linhas legítimas é desprezível.
- **Sem imputação** de numéricas; NAO_INFORMADO para categóricas, como no CAT.
- **Município:** fora de `categorias_raras` (5.562 níveis; 45% iriam a OUTROS). A geografia fica em `uf` e `regiao`.
- **Faixas aceitas:** idade [0, 110]; `qt_sm_rmi` em (0, 30].
- **Verificação pós-limpeza:** 10/10 OK.

## Pendências abertas
Significado de `vinculo_dependentes`; DCB real ou programada; queda de volume 2014→2015; confirmação de "AUXILIO ACIDENTE".
