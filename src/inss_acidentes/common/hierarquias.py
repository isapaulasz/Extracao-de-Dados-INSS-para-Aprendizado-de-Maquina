"""Hierarquias de códigos (CID-10, CBO-2002, CNAE 2.0) como expressões SQL.

Todas recebem uma coluna já limpa (CID 3-4 caracteres, CBO 6 dígitos, CNAE 4 dígitos)
e devolvem NULL quando o código é NULL. O preenchimento com NAO_INFORMADO é feito depois.
"""

# (capítulo, letra+número inicial, letra+número final) da CID-10, comparados nos 3 primeiros caracteres
CAPITULOS_CID = [
    ("I", "A00", "B99"), ("II", "C00", "D48"), ("III", "D50", "D89"), ("IV", "E00", "E90"),
    ("V", "F00", "F99"), ("VI", "G00", "G99"), ("VII", "H00", "H59"), ("VIII", "H60", "H95"),
    ("IX", "I00", "I99"), ("X", "J00", "J99"), ("XI", "K00", "K93"), ("XII", "L00", "L99"),
    ("XIII", "M00", "M99"), ("XIV", "N00", "N99"), ("XV", "O00", "O99"), ("XVI", "P00", "P96"),
    ("XVII", "Q00", "Q99"), ("XVIII", "R00", "R99"), ("XIX", "S00", "T98"),
    ("XX", "V01", "Y98"), ("XXI", "Z00", "Z99"), ("XXII", "U00", "U99"),
]

# (seção, divisão inicial, divisão final) da CNAE 2.0
SECOES_CNAE = [
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39), ("F", 41, 43),
    ("G", 45, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 63), ("K", 64, 66), ("L", 68, 68),
    ("M", 69, 75), ("N", 77, 82), ("O", 84, 84), ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93),
    ("S", 94, 96), ("T", 97, 97), ("U", 99, 99),
]


def capitulo_cid(col: str) -> str:
    c3 = f"left({col}, 3)"
    ramos = " ".join(f"WHEN {c3} BETWEEN '{a}' AND '{b}' THEN '{cap}'" for cap, a, b in CAPITULOS_CID)
    return f"CASE WHEN {col} IS NULL THEN NULL {ramos} END"


def cbo_grande_grupo(col: str) -> str:
    return f"left({col}, 1)"


def cbo_subgrupo(col: str) -> str:
    """Subgrupo principal (2 dígitos)."""
    return f"left({col}, 2)"


def cnae_divisao(col: str) -> str:
    return f"left({col}, 2)"


def cnae_secao(col: str) -> str:
    d = f"TRY_CAST(left({col}, 2) AS INTEGER)"
    ramos = " ".join(f"WHEN {d} BETWEEN {a} AND {b} THEN '{s}'" for s, a, b in SECOES_CNAE)
    return f"CASE WHEN {col} IS NULL THEN NULL {ramos} END"
