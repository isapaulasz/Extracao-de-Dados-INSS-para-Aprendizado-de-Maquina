from pathlib import Path
import yaml
import duckdb
import os, random
import numpy as np

RAIZ = Path(__file__).resolve().parents[3]  # raiz do projeto

def carregar_yaml(caminho_relativo: str) -> dict:
    with open(RAIZ / caminho_relativo, encoding="utf-8") as f:
        return yaml.safe_load(f)

def conectar(memoria: str = "8GB") -> duckdb.DuckDBPyConnection:
    tmp = RAIZ / "data" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memoria}'")
    con.execute(f"SET temp_directory='{tmp.as_posix()}'")
    return con

def fixar_semente(seed: int | None = None) -> int:
    seed = seed if seed is not None else carregar_yaml("configs/paths.yaml")["seed"]
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    # se usar PyTorch/TensorFlow: torch.manual_seed(seed) / tf.random.set_seed(seed)
    return seed