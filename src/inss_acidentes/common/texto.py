"""Normalização de texto, em Python e em SQL (DuckDB), com a mesma regra nos dois lados."""
import unicodedata

NAO_INFORMADO = "NAO_INFORMADO"
ESPACOS = r"'\s+'"


def normalizar_py(s: str) -> str:
    """Trim, colapso de espaços, remoção de acentos e maiúsculas."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.split()).upper()


def lit(s) -> str:
    """Literal SQL entre aspas simples."""
    return "'" + str(s).replace("'", "''") + "'"


def norm_sql(col: str) -> str:
    """Expressão SQL equivalente a normalizar_py (sem alias)."""
    return f"upper(strip_accents(trim(regexp_replace(\"{col}\", {ESPACOS}, ' ', 'g'))))"


def tokens_normalizados(tokens: list[str]) -> list[str]:
    """Tokens de nulo disfarçado na mesma forma normalizada das colunas."""
    return sorted({normalizar_py(t) for t in tokens})
