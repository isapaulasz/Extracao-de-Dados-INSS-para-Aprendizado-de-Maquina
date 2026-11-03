import datetime as dt

import duckdb

from inss_acidentes.cat import limpeza as L

TOKENS = ["", "{ñ class}", "-", "None"]


def q(expr: str, valor):
    sql = f"SELECT {expr} FROM (SELECT ?::VARCHAR AS x)"
    return duckdb.connect().execute(sql, [valor]).fetchone()[0]


def test_norm():
    assert q(L._norm("x", TOKENS), "  VIA   PUBLICA  ") == "VIA PUBLICA"
    for nulo in ["{ñ class}", "-", "None", "   ", None]:
        assert q(L._norm("x", TOKENS), nulo) is None


def test_cbo():
    assert q(L._cbo("x"), "784205") == "784205"
    assert q(L._cbo("x"), "31210") == "031210"
    assert q(L._cbo("x"), "0") is None
    assert q(L._cbo("x"), None) is None


def test_cnae():
    assert q(L._cnae("x"), "8610") == "8610"
    assert q(L._cnae("x"), "113") == "0113"
    assert q(L._cnae("x"), "15") is None
    assert q(L._cnae("x"), "0") is None


def test_cid():
    assert q(L._cid("x"), "S61") == "S61"
    assert q(L._cid("x"), "S52.5") == "S525"
    assert q(L._cid("x"), "H8333") is None
    assert q(L._cid("x"), "7") is None


def test_data():
    assert q(L._data("x"), "2013-09-09 00:00:00") == dt.date(2013, 9, 9)
    assert q(L._data("x"), "15/03/2014") == dt.date(2014, 3, 15)
    assert q(L._data("x"), "00/00/0000") is None


def test_cnpj():
    assert q(L._cnpj("x"), "34.028.316/0031-29") == "34028316003129"
    assert q(L._cnpj("x"), "123") is None