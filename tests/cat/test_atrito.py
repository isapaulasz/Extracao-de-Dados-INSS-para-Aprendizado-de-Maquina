import pytest

from inss_acidentes.common.logging import Atrito


def test_atrito_fecha():
    a = Atrito()
    a.entrada(100)
    a.remocao("lixo", 98)
    a.alteracao("nulos", 500)
    a.remocao("duplicatas", 90)
    a.conferir()
    t = a.tabela()
    assert list(t.linhas_removidas) == [0, 2, 0, 8]
    assert t.iloc[-1].linhas_restantes == 90


def test_atrito_detecta_erro():
    a = Atrito()
    a.entrada(100)
    a.remocao("x", 90)
    a.atual = 90
    a.linhas[-1]["linhas_restantes"] = 95    # saída inconsistente
    with pytest.raises(AssertionError):
        a.conferir()
