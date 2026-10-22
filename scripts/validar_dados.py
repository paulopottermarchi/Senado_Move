#!/usr/bin/env python3
"""Compatibilidade para a etapa antiga do workflow.

O repositório usa `scripts/validar_jsons.py` como validador principal, mas o
workflow antigo ainda chama `scripts/validar_dados.py`. Mantemos um wrapper
simples para evitar quebra de CI e preservar a API esperada.
"""

from __future__ import annotations

from scripts.validar_jsons import main


if __name__ == "__main__":
    raise SystemExit(main())
