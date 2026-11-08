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
    ("entradas/emendas_aliases.json", "emendas_aliases.schema.json"),
]

# Camadas que só existem depois da primeira rodada que as publica: cada padrão (glob, a partir da raiz) é validado quando há
# arquivo; nenhum arquivo não é erro (o passo que gera é opcional, e a página some sem ele).
OPCIONAIS = [
    ("site/dados/emendas/indice.json", "emendas_indice.schema.json"),
    ("site/dados/emendas/[0-9]*.json", "emendas_deputado.schema.json"),
    ("site/dados/presidencia.json", "presidencia.schema.json"),
]
