# Auditoria da base CAT
- 14 arquivos, 3.429.666 linhas, mesmo schema e mesmos nomes de coluna em todos (todos VARCHAR).
- Exceção: o arquivo 14 (2018-S2) ganha `numero_cat` e `razao_social` e perde `valor_remuneracao`.
- Arquivos 07 e 12: 10 linhas-lixo cada (cabeçalho de exportação e linha de total).
- Datas de acidente, emissão e nascimento: formato único `AAAA-MM-DD HH:MM:SS` em todos os arquivos.
- Afastamento, despacho e óbito: `00/00/0000` = sem data (20,2%, 86,5% e 99,5% das linhas).
- CBO e CNAE sem zero à esquerda; CID com 3 e 4 caracteres e 1.537 em branco.
- Tabela de atrito: `cat_atrito.csv`. Valores anulados por coluna: `cat_valores_anulados.csv`.