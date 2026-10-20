"""
Câmara Aberta — DIAGNÓSTICO E8: responsáveis do contrato (cobertura; nada novo é coletado).

Fonte: GET /api/contrato/{id}/responsaveis (Contratos.gov.br; documentado em /docs/api-docs.json: usuario, funcao_id,
portaria, situacao, data_inicio, data_fim), já no cache de obras.py. Este script só CONTA: contratos com gestor
nomeado, com portaria identificada (número e data) e sem nenhum responsável. Nenhum nome de fiscal é lido ou gravado;
o obras.py descarta de propósito os nomes de fiscais e só publica o do gestor.
"""

import json
import re
import sys
from collections import Counter

import diag_vinculo_comum as D

MESES = "janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"


def forma_da_portaria(p):
    """'completa' = número + dia/mês/ano; 'numero_e_ano' = número com ano (1163/2025); 'so_numero'; 'sem_numero'."""
    p = (p or "").strip()
    if not p or not re.search(r"\d", p):
        return "sem_numero"
    if re.search(rf"\d{{1,2}}\s+de\s+(?:{MESES})\s+de\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}", p, re.I):
        return "completa"
    if re.search(r"\d+\s*[-/]\s*\d{4}|\d+/\d{2}\b", p):
        return "numero_e_ano"
    return "so_numero"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cont, _, _ = D.universo()
    n = len(cont)
    r = Counter()
    por_orgao = {}
    funcoes = Counter()
    for cid, c in cont.items():
        rs = D.cache_contrato(cid).get("responsaveis") or []
        o = por_orgao.setdefault(c["orgao"], Counter())
        o["contratos"] += 1
        if not rs:
            r["sem nenhum responsável cadastrado"] += 1
            o["sem_responsavel"] += 1
            continue
        r["com algum responsável cadastrado"] += 1
        for x in rs:
            funcoes[x["funcao"]] += 1
        gestores = [x for x in rs if (x["funcao"] or "").startswith("Gestor")]
        if gestores:
            r["com gestor"] += 1
            o["gestor"] += 1
            if any(x["nome"] for x in gestores):
                r["com gestor nomeado"] += 1
            formas = {forma_da_portaria(x["portaria"]) for x in gestores}
            melhor = "completa" if "completa" in formas else "numero_e_ano" if "numero_e_ano" in formas else "so_numero" if "so_numero" in formas else "sem_numero"
            r[f"gestor com portaria: {melhor}"] += 1
            if any(x["situacao"] == "Ativo" for x in gestores):
                r["gestor em situação Ativo"] += 1
        else:
            r["com responsáveis mas SEM gestor (só fiscais/apoio)"] += 1
        if any((x["funcao"] or "").startswith("Fiscal") for x in rs):
            r["com fiscal"] += 1
    res = {"contratos": n, "contagens": dict(r), "funcoes_mais_comuns": funcoes.most_common(12),
           "por_orgao": {k: dict(v) for k, v in sorted(por_orgao.items(), key=lambda kv: -kv[1]["contratos"])[:8]},
           "nota": "contagem apenas; nomes de fiscais não são lidos"}
    (D.DIAG / "e8_resumo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
