#!/usr/bin/env python3
"""Valida os principais arquivos JSON gerados antes da publicação.

Os arquivos críticos são verificadas em duas camadas:
1. existência do arquivo
2. parse JSON válido e estrutura compatível com a expectativa do frontend

Se algum arquivo falha, a rotina sai com erro e o site não avança para publicação.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

CRITICOS = [
    (BASE / "site" / "dados" / "deputados.json", "list"),
    (BASE / "site" / "dados" / "senadores.json", "list"),
    (BASE / "site" / "dados" / "blocos.json", "list"),
    (BASE / "site" / "dados" / "fluxo.json", "dict"),
    (BASE / "site" / "dados" / "inicio.json", "dict"),
    (BASE / "site" / "dados" / "semana.json", "dict"),
    (BASE / "site" / "dados" / "origem_leis.json", "dict"),
    (BASE / "site" / "dados" / "legislaturas.json", "list"),
]


def validar_arquivo(caminho: Path, tipo: str) -> None:
    if not caminho.exists():
        raise FileNotFoundError(f"arquivo ausente: {caminho}")

    try:
        texto = caminho.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"arquivo não é UTF-8: {caminho}") from exc

    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido em {caminho}: {exc.msg} (linha {exc.lineno}, coluna {exc.colno})") from exc

    if tipo == "list" and not isinstance(dados, list):
        raise TypeError(f"esperava lista em {caminho}, mas recebeu {type(dados).__name__}")
    if tipo == "dict" and not isinstance(dados, dict):
        raise TypeError(f"esperava dicionário em {caminho}, mas recebeu {type(dados).__name__}")

    if isinstance(dados, (list, dict)) and len(dados) == 0:
        raise ValueError(f"arquivo vazio: {caminho}")


def validar_publicacao() -> list[str]:
    problemas = []
    for caminho, tipo in CRITICOS:
        try:
            validar_arquivo(caminho, tipo)
        except Exception as exc:  # noqa: BLE001
            problemas.append(f"- {caminho.relative_to(BASE)}: {exc}")
    if problemas:
        raise RuntimeError("Erros de validação dos JSONs públicos:\n" + "\n".join(problemas))
    return [str(p.relative_to(BASE)) for p, _ in CRITICOS]


def main() -> int:
    print("Validando JSONs públicos...")
    try:
        validados = validar_publicacao()
    except Exception as exc:  # noqa: BLE001
        print(f"VALIDAÇÃO FALHOU: {exc}", file=sys.stderr)
        return 1

    print(f"Validação OK: {len(validados)} arquivos JSON conferidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
