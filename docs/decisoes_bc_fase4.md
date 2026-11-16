# BC — Fase 4: engenharia de atributos

Referência: `cat/features.py` e a seção Fase 4 de `docs/decisoes_metodologicas.md`. Entrada `bc_limpo.parquet`, saída `bc_features.parquet` (separado; a Fase 3 não é sobrescrita; a etapa é idempotente, grava em `.tmp` + `os.replace` e confere o número de linhas). Comando: `make bc-features`.

**Status: rascunho.** Faixas e mapa de vazamento marcados [CONFIRMAR] foram definidos sem ver a distribuição das features. Rode `make bc-features` e leia `docs/qualidade/bc_features_*.csv` antes de congelar.

## Adaptação da lista da Fase 4 ao BC

O BC não tem data do acidente, data de emissão da CAT, CNAE, CBO, emitente, óbito nem afastamento. Cada item foi adaptado ou descartado:

| Pedido (CAT) | No BC | Decisão |
|---|---|---|
| Idade no acidente | `idade` (já na Fase 3, na DIB) + `faixa_etaria` | faixa 0-13 incluída (pensionistas) |
| Ano, mês, dia da semana do acidente | `ano_dib`, `mes_dib`, `dia_semana_dib`, `mes_competencia` | a DIB é a data mais próxima do evento, mas é administrativa (limitação a citar no artigo) |
| Atraso de comunicação da CAT | `atraso_concessao_dias` (DIB→DDB) + faixa | análogo, não igual: mede demora de concessão |
| Região a partir da UF | `regiao` | mesmo mapa do CAT |
| Porte/setor via CNAE; grupo via CBO | não aplicável | `ramo_atividade` já é o setor; sem porte nem ocupação |
| Binários (óbito, afastamento, emitente) | `tem_data_cessacao`, `rmi_abaixo_piso` | óbito/afastamento só existem implicitamente na espécie (pensão por morte, auxílio-doença) |
| (novo) | `grupo_especie`, `faixa_rmi`, `duracao_beneficio_dias` + faixa | candidatos a alvo |

## Mapa de vazamento (propostas, a validar)

| Alvo candidato | Proibidas como preditoras (além de `sempre`) | Motivo |
|---|---|---|
| `grupo_especie` | `especie`, `data_dcb`, `tem_data_cessacao`, duração e faixa, `qt_sm_rmi`, `faixa_rmi`, `rmi_abaixo_piso` | redefinem o alvo ou dependem da regra de cálculo da espécie |
| `duracao_beneficio_dias` | `data_dcb`, `tem_data_cessacao`, `faixa_duracao_beneficio` | são a própria duração |
| `faixa_rmi` | `qt_sm_rmi`, `rmi_abaixo_piso` | são o próprio alvo |

`sempre`: `arquivo_origem`, `ano_arquivo` (redundante com `ano_competencia`), `vinculo_dependentes` (significado desconhecido) e `municipio_residencia_cod` (5,5 mil níveis).

Equivalente ao caso do CAT: assim como "indica óbito" redefine `houve_obito`, aqui `especie` redefine `grupo_especie`.

## Como validar

`bc_features_crosstabs.csv` cruza `grupo_especie` com as variáveis suspeitas. Se uma coluna quase determina a espécie (ex.: `tem_data_cessacao`, `faixa_rmi`), ela fica na lista proibida; se uma coluna que não listei se mostrar determinística, deve ser acrescentada.

## Pendências

- Rodar `make bc-features` e ajustar faixas (`bc_features_distribuicao.csv`, `bc_features_numericas.csv`).
- Confirmar semântica da DCB (real ou programada) antes de usar duração como alvo.
- Fase 3 fechada e uso do BC decidido (Apriori + clusterização); ver `docs/decisoes_bc_fases1_3.md`.

## Ajuste após a decisão de uso (BC = Apriori + clusterização)

Como o BC não será usado para predição, o mapa de vazamento acima fica como **registro e reserva**, não como restrição ativa. O equivalente para mineração é a **redundância**: itens que são função um do outro geram regras triviais (confiança 100%) e distorcem clusters. Ao montar a base de itens/clusters, usar só um de cada grupo:

| Grupo redundante | Usar | Evitar junto |
|---|---|---|
| espécie | `grupo_especie` | `especie` |
| datas | `ano_competencia` | `ano_dib` (e `ano_arquivo`) |
| renda | `faixa_rmi` | `rmi_abaixo_piso`, `qt_sm_rmi` |
| duração | `faixa_duracao_beneficio` | `tem_data_cessacao`, `duracao_beneficio_dias`, `data_dcb` |
| CID | `capitulo_cid` ou `cid_categoria_agrup` | `cid_categoria`, `cid_10` |
| idade | `faixa_etaria` | `idade`, `data_nascimento` |
| região | `regiao` (ou `uf`) | `municipio_residencia_cod` |

Apriori exige variáveis discretas: numéricas entram só por faixa. Para clusterização mista (K-Prototypes), `idade`, `qt_sm_rmi` e `atraso_concessao_dias` podem entrar numéricas; considerar amostragem pelo volume (1,9 milhão).
