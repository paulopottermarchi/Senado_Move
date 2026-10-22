#!/usr/bin/env python3
"""Metadados dos arquivos JSON críticos do projeto.

Este módulo centraliza os arquivos que devem ser validados antes da publicação.
"""

from __future__ import annotations

CRITICOS = [
    ("site/dados/deputados.json", "deputados.schema.json"),
    ("site/dados/senadores.json", "senadores.schema.json"),
    ("site/dados/blocos.json", "blocos.schema.json"),
    ("site/dados/blocos_senado.json", "blocos_senado.schema.json"),
    ("site/dados/fluxo.json", "fluxo.schema.json"),
    ("site/dados/inicio.json", "inicio.schema.json"),
    ("site/dados/semana.json", "semana.schema.json"),
    ("site/dados/origem_leis.json", "origem_leis.schema.json"),
    ("site/dados/legislaturas.json", "legislaturas.schema.json"),
    ("site/dados/ecidadania.json", "ecidadania.schema.json"),
]
