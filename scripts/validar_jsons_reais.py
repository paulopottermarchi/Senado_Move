#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

if __package__:
    from .arquivos_criticos import CRITICOS, OPCIONAIS
else:
    from arquivos_criticos import CRITICOS, OPCIONAIS

BASE = Path(__file__).resolve().parent.parent
SCHEMA_DIR = BASE / "schemas"


def carregar_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Erro ao ler {path}: {exc}") from exc


def validar_arquivo(relpath: str, schema_name: str):
    arquivo = BASE / relpath
    schema_path = SCHEMA_DIR / schema_name

    if not arquivo.exists():
        raise FileNotFoundError(f"Arquivo ausente: {relpath}")

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema ausente: {schema_name}")

    dados = carregar_json(arquivo)
    schema = carregar_json(schema_path)

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    erros = sorted(validator.iter_errors(dados), key=lambda erro: str(erro.json_path))

    if erros:
        linhas = []
        for erro in erros:
            caminho = ".".join(str(p) for p in erro.absolute_path) or "<raiz>"
            linhas.append(f"- {relpath}: {caminho}: {erro.message}")
        raise ValueError("Schema inválido:\n" + "\n".join(linhas))


def main() -> int:
    print("Validando arquivos reais contra os schemas...")
    # camadas opcionais: só os arquivos que existem (antes da primeira publicação, nenhum)
    opcionais = [(p.relative_to(BASE).as_posix(), s) for padrao, s in OPCIONAIS for p in sorted(BASE.glob(padrao))]
    for relpath, schema_name in CRITICOS + opcionais:
        try:
            validar_arquivo(relpath, schema_name)
            print(f"OK: {relpath} -> {schema_name}")
        except Exception as exc:
            print(f"FALHA: {relpath} -> {schema_name}", file=sys.stderr)
            print(exc, file=sys.stderr)
            return 1

    print(f"Validação OK: {len(CRITICOS)} arquivos conferidos e {len(opcionais)} de camadas opcionais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
