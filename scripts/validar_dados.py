#!/usr/bin/env python3
"""Compatibilidade com a invocação histórica do validador de dados.

O validador principal é `scripts/validar_jsons.py`; este wrapper preserva o nome
alternativo usado por ferramentas e comandos locais.
"""

from __future__ import annotations

if __package__:
    from .validar_jsons import main
else:
    from validar_jsons import main


if __name__ == "__main__":
    raise SystemExit(main())
