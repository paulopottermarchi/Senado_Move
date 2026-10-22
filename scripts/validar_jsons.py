#!/usr/bin/env python3
"""Valida os principais arquivos JSON gerados antes da publicação.

O validador usa os schemas em `schemas/` para confirmar que os artefatos públicos
gerados pela rotina diária têm o contrato esperado pelo frontend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.arquivos_criticos import CRITICOS

BASE = Path(__file__).resolve().parent.parent
SCHEMA_DIR = BASE / "schemas"


def carregar_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"arquivo ausente: {path.relative_to(BASE)}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"arquivo não é UTF-8: {path.relative_to(BASE)}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON inválido em {path.relative_to(BASE)}: {exc.msg} "
            f"(linha {exc.lineno}, coluna {exc.colno})"
        ) from exc


def validar_arquivo(relpath: str, schema_name: str) -> None:
    arquivo = BASE / relpath
    schema_path = SCHEMA_DIR / schema_name

    if not schema_path.exists():
        raise FileNotFoundError(f"schema ausente: {schema_path.relative_to(BASE)}")

    dados = carregar_json(arquivo)
    schema = carregar_json(schema_path)

    validator = Draft202012Validator(schema)
    erros = sorted(validator.iter_errors(dados), key=lambda erro: str(erro.json_path))
    if not erros:
        return

    linhas = []
    for erro in erros:
        caminho = ".".join(str(p) for p in erro.absolute_path) or "<raiz>"
        linhas.append(f"- {relpath}: {caminho}: {erro.message}")

    raise ValueError("schema inválido\n" + "\n".join(linhas))


def main() -> int:
    print("Validando JSONs públicos com schemas...")
    try:
        for relpath, schema_name in CRITICOS:
            validar_arquivo(relpath, schema_name)
    except Exception as exc:  # noqa: BLE001
        print(f"VALIDAÇÃO FALHOU: {exc}", file=sys.stderr)
        return 1

    print(f"Validação OK: {len(CRITICOS)} arquivos JSON conferidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
