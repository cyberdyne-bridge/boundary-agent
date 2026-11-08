"""共享路径：全部可用环境变量覆盖，默认落在仓库内 data/。

公开仓库里不写死任何绝对路径；实验室数据（对话库、RAG 库、实验现场）
默认放在 data/ 下，而 data/ 已在 .gitignore 里。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BOUNDARY_DATA", REPO / "data"))
LAB = Path(os.environ.get("BOUNDARY_LAB", DATA / "lab"))
VM_AN = Path(os.environ.get("BOUNDARY_VM_AN", DATA / "vm-an"))
OUT = Path(os.environ.get("BOUNDARY_OUT", DATA / "capture"))


def lab(name: str) -> Path:
    """单个实验现场目录，例如 lab('a') → data/lab/a。"""
    return Path(os.environ.get(f"BOUNDARY_LAB_{name.upper()}", LAB / name))


LAB_A, LAB_B, LAB_C = lab("a"), lab("b"), lab("c")
