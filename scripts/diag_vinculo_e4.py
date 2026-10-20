"""
Câmara Aberta — DIAGNÓSTICO E4: quadro de autoridades e verificação temporal (titular do cargo na data).

Estado das fontes (medido nesta execução; ver cache/diag_vinculo/bloqueios.json):
  - DOU (atos de nomeação, exoneração, designação): www.in.gov.br/robots.txt = "User-agent: * / Disallow: /";
    pesquisa.in.gov.br devolve "Request Rejected" (WAF) ao cliente que não é navegador. BLOQUEADO — não contornado.
  - INLABS (XML em lote): exige cadastro (login) — NÃO me cadastrei; avisar o usuário.
  - Wikidata (P39, mandatos com datas): consultado; tem reitores da USP, NENHUM do IFSP, da UFSCar ou da UNIFESP.
  - Base de servidores da CGU (função, nível e data de nomeação): a URL e o dicionário de dados não foram confirmados
    em documentação acessível (o Portal da Transparência exige verificação humana) — não adivinhei endpoint.

O que dá para fazer SEM essas fontes, e é o que este script faz:
  (1) quadro PARCIAL a partir do que os próprios contratos e termos citam no preâmbulo: "nomeada pela Portaria IFSP nº
      1.490, de 09 de abril de 2020", "portaria de delegação de competência nº 4.659, de 19 de dezembro de 2019" — número
      e data do ato, com o PDF e a página. É o documento afirmando; NÃO é confirmação no DOU.
  (2) verificação temporal por cruzamento ENTRE documentos: cargo único na unidade (reitor, pró-reitor…) com nomes que se
      alternam no tempo sem que um mandato tenha terminado ("A, B, A") = "conflito" a revisar à mão (pode ser substituição).
  (3) "cargo_fora_do_quadro": o cargo da assinatura é efetivo (técnico, assistente…), não função de confiança — não há ato
      de nomeação para verificar.
  Todo o resto fica "nao_verificado". Nenhuma data de fim de mandato é inventada (campo `fim` vazio).
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date

import diag_vinculo_comum as D

MESES = {m: i for i, m in enumerate(["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                                     "setembro", "outubro", "novembro", "dezembro"], 1)}
FUNCAO = re.compile(r"\b(CD|FG|FCC|FUC|FCPE|FCE|FUNC|CCE|DAS|NE)\s*-?\s*\d|reitor|pr[óo]-reitor|diretor|coordenador|chefe|superintendente|"
                    r"gerente|secret[áa]rio|subsecret|pregoeir|presidente|gestor|ordenador|decano|supervisor|comandante|agente de contrata|comiss[ãa]o|superintend", re.I)
UNICO = re.compile(r"^(reitor|vice-reitor|pr[óo]-reitor|diretor[- ]geral|superintendente|secret[áa]rio|subsecret)", re.I)


def norm_cargo(c):
    """Forma de comparação: sem '(a)', sem a unidade depois do nível, caixa alta, sem acento."""
    s = D.norm(re.sub(r"\((?:a|A)\)", "", c or ""))
    return s


def parse_ato(txt):
    """'Portaria IFSP nº 1.490, de 09 de abril de 2020' → {tipo, numero, data}. None se não casa."""
    if not txt:
        return None
    m = re.search(r"(Portaria|Decreto|Resolu[çc][ãa]o|Despacho)\s+([^,;]{0,40}?)n[º°o.]*\s*([\d./-]+)\s*(?:/\s*(\d{4}))?\s*,?\s*de\s+(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", txt, re.I)
    if m:
        mes = MESES.get(m[6].lower())
        data = f"{m[7]}-{mes:02d}-{int(m[5]):02d}" if mes else None
        return {"tipo": m[1].title(), "orgao_emissor": (m[2] or "").strip() or None, "numero": m[3].strip(".-/"), "data": data, "texto": txt[:140]}
    m = re.search(r"(Portaria|Decreto)\s+([^,;]{0,40}?)n[º°o.]*\s*([\d./-]+)", txt, re.I)
    if m:
        return {"tipo": m[1].title(), "orgao_emissor": (m[2] or "").strip() or None, "numero": m[3].strip(".-/"), "data": None, "texto": txt[:140]}
    return None


def iso(d):
    return d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    arestas = D.ler_arestas()
    quadro = {}
    por_cargo_unidade = defaultdict(list)          # (uasg, cargo_norm) → [(data, nome, aresta_id)]
    cargos = Counter()
    for a in arestas:
        c = a.get("cargo") or ""
        cargos[c] += 1
        if a["categoria"].startswith("autor de emenda"):
            continue
        k = (a["orgao_uasg"], norm_cargo(c))
        if a.get("data_do_vinculo") and c:
            por_cargo_unidade[k].append((a["data_do_vinculo"], a["nome"], a["aresta_id"]))
        # quadro parcial: nomeação e delegação citadas no preâmbulo
        for campo, tipo_ato in (("nomeacao_citada", "nomeação"), ("delegacao_citada", "delegação de competência")):
            ato = parse_ato(a.get(campo))
            if ato:
                chave = (a["orgao_uasg"], norm_cargo(c), D.pessoa_id(a["nome"], a["orgao_uasg"]), tipo_ato, ato["numero"], ato["data"])
                q = quadro.setdefault(chave, {
                    "orgao_uasg": a["orgao_uasg"], "cargo": c, "pessoa": a["nome"], "pessoa_id": D.pessoa_id(a["nome"], a["orgao_uasg"]),
                    "inicio": ato["data"] if tipo_ato == "nomeação" else None, "fim": None,
                    "tipo_do_ato": tipo_ato, "ato": ato,
                    "fonte": "citado no preâmbulo do documento; NÃO confirmado no DOU (bloqueado por robots.txt)",
                    "documentos": []})
                q["documentos"].append({"url": a["url_documento"], "pagina": a.get("pagina"), "hash": a["hash_documento"],
                                        "assinou_em": a.get("data_do_vinculo")})

    # (2) conflito: cargo único com nomes alternados no tempo
    conflitos = []
    for (uasg, cg), lst in por_cargo_unidade.items():
        if not UNICO.search(cg.lower()) and not re.search(r"\bREITOR\b", cg):
            continue
        lst.sort()
        nomes_em_ordem = []
        for d, nome, aid in lst:
            if not nomes_em_ordem or nomes_em_ordem[-1][0] != D.norm(nome):
                nomes_em_ordem.append((D.norm(nome), d, nome, aid))
        vistos = Counter(n for n, *_ in nomes_em_ordem)
        if any(v > 1 for v in vistos.values()):           # A … B … A
            conflitos.append({"orgao_uasg": uasg, "cargo": cg, "sequencia": [[n[2], n[1]] for n in nomes_em_ordem]})
    ids_conf = set()
    for cf in conflitos:
        for (uasg, cg), lst in por_cargo_unidade.items():
            if uasg == cf["orgao_uasg"] and cg == cf["cargo"]:
                ids_conf.update(aid for _, _, aid in lst)

    # (3) rótulo temporal por aresta
    cont = Counter()
    for a in arestas:
        if a["categoria"].startswith("autor de emenda"):
            a["vinculo_temporal"], a["ref_quadro"] = "nao_verificado", None
            cont["emenda: não se aplica (autor de emenda não tem cargo)"] += 1
            continue
        c = a.get("cargo") or ""
        if not c:
            a["vinculo_temporal"] = "nao_verificado"
            cont["sem cargo no documento"] += 1
            continue
        if not FUNCAO.search(c):
            a["vinculo_temporal"] = "cargo_fora_do_quadro"
            a["ref_quadro"] = "cargo efetivo (não é função de confiança): não há ato de nomeação a verificar"
            cont["cargo_fora_do_quadro"] += 1
        elif a["aresta_id"] in ids_conf:
            a["vinculo_temporal"] = "conflito"
            a["ref_quadro"] = "mais de um nome alternando no mesmo cargo único da unidade (pode ser substituição); revisar"
            cont["conflito"] += 1
        else:
            ato = parse_ato(a.get("nomeacao_citada"))
            a["vinculo_temporal"] = "nao_verificado"
            a["ref_quadro"] = (f"{ato['tipo']} {ato['orgao_emissor'] or ''} nº {ato['numero']}, de {ato['data']} — citada no preâmbulo; não confirmada no DOU"
                               if ato else None)
            cont["nao_verificado" + (" (com ato citado no preâmbulo)" if ato else "")] += 1
    D.reescrever_arestas(arestas)

    saida = {"gerado_em": date.today().isoformat(),
             "aviso": "Quadro PARCIAL. Fonte oficial (DOU) bloqueada para coleta automatizada; cada linha é um ato CITADO no preâmbulo de um "
                      "contrato/termo, com o PDF e a página. Não há data de fim. Não substitui a consulta ao DOU.",
             "linhas": sorted(quadro.values(), key=lambda q: (q["orgao_uasg"], q["cargo"], q["inicio"] or "")),
             "conflitos": conflitos}
    (D.CACHE / "quadro_autoridades.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    top = cargos.most_common(20)
    res = {"vinculo_temporal": dict(cont), "linhas_do_quadro": len(quadro), "conflitos": conflitos,
           "cargos_distintos_literais": len(cargos),
           "cargos_distintos_depois_de_normalizar": len({norm_cargo(c) for c in cargos}),
           "top20_cargos": top}
    (D.DIAG / "e4_resumo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
