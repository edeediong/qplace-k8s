"""Read-only guarantee: the package has no Kubernetes client, network or process-spawning code."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "qplace"
FORBIDDEN_MODULES = {"kubernetes", "requests", "httpx", "urllib", "http", "socket", "subprocess", "aiohttp", "pykube"}


def test_no_cluster_or_network_capable_imports():
    offenders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in FORBIDDEN_MODULES]
    assert offenders == []
