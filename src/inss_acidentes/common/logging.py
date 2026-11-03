"""Log em arquivo e tabela de atrito (linhas removidas e valores alterados por regra)."""
import logging as _logging
from dataclasses import dataclass, field

import pandas as pd

from inss_acidentes.common.io import RAIZ


def configurar_logs(nome: str) -> _logging.Logger:
    pasta = RAIZ / "logs"
    pasta.mkdir(exist_ok=True)
    log = _logging.getLogger(nome)
    if not log.handlers:
        log.setLevel(_logging.INFO)
        fmt = _logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        for h in (_logging.FileHandler(pasta / f"{nome}.log", encoding="utf-8"),
                  _logging.StreamHandler()):
            h.setFormatter(fmt)
            log.addHandler(h)
    return log


@dataclass
class Atrito:
    """Cada etapa registra o que removeu (linhas) ou alterou (valores/células)."""
    linhas: list[dict] = field(default_factory=list)
    atual: int = 0

    def entrada(self, n: int, etapa: str = "entrada (consolidado)") -> None:
        self.atual = n
        self.linhas.append(dict(etapa=etapa, tipo="entrada", linhas_removidas=0,
                                valores_alterados=0, linhas_restantes=n))

    def remocao(self, etapa: str, restantes: int) -> None:
        self.linhas.append(dict(etapa=etapa, tipo="remocao",
                                linhas_removidas=self.atual - restantes,
                                valores_alterados=0, linhas_restantes=restantes))
        self.atual = restantes

    def alteracao(self, etapa: str, valores: int) -> None:
        self.linhas.append(dict(etapa=etapa, tipo="alteracao", linhas_removidas=0,
                                valores_alterados=int(valores), linhas_restantes=self.atual))

    def tabela(self) -> pd.DataFrame:
        return pd.DataFrame(self.linhas)

    def conferir(self) -> None:
        """Entrada menos tudo o que foi removido tem de ser igual à saída."""
        t = self.tabela()
        esperado = t.iloc[0].linhas_restantes - t.linhas_removidas.sum()
        assert esperado == t.iloc[-1].linhas_restantes, "atrito não fecha com a saída"

    def salvar(self, caminho) -> pd.DataFrame:
        t = self.tabela()
        t.to_csv(caminho, index=False, encoding="utf-8-sig")
        return t
