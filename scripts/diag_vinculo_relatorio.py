"""
Câmara Aberta — DIAGNÓSTICO: consolida tudo em cache/diag_vinculo.json, cache/amostra_validacao.csv e o relatório em texto.

Não coleta nada: lê os resumos e as arestas que os diag_vinculo_e*.py gravaram.
Também registra as vias bloqueadas que não são testadas por script (motivos medidos nesta execução).
"""

import csv
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict

import diag_vinculo_comum as D


def jl(caminho):
    if not caminho.exists():
        return []
    return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]


def lj(nome):
    p = D.DIAG / nome
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def registrar_bloqueios():
    D.bloqueio("E1/E4", "www.in.gov.br", "robots.txt: User-agent * / Disallow: / (coleta automatizada vedada); páginas do DOU e dados abertos do portal não acessados")
    D.bloqueio("E1/E4", "pesquisa.in.gov.br", "WAF: 'Request Rejected' ao cliente que não é navegador; endereço legado dos 848 links do DOU")
    D.bloqueio("E4", "inlabs.in.gov.br", "XML do DOU em lote: o FAQ da Imprensa Nacional (2018) diz que a entrega diária exige cadastro; NÃO me cadastrei — avisar o usuário",
               verificado="só por texto de FAQ, não pela página")
    D.bloqueio("E4", "portaldatransparencia.gov.br", "verificação humana ('Human Verification') até no robots.txt; a base de servidores (função, nível, data de nomeação) não pôde ser documentada nem acessada")
    D.bloqueio("E6", "api.transferegov.gestao.gov.br", "dados: HTTP 403 (página de bloqueio do Cloudflare) ao cliente Python; só a documentação (swagger) foi lida; não contornado")
    D.bloqueio("E7", "sites.tcu.gov.br", "robots.txt bloqueia ClaudeBot e anthropic-ai (Disallow: /); o CSV de jurisprudência (acórdãos) existe, mas não foi coletado; alternativa: o usuário baixa à mão")
    D.bloqueio("E7", "dados-abertos.apps.tcu.gov.br", "a documentação oficial lida (portal.tcu.gov.br) não cita API de acórdãos nem Fiscobras; endpoint não documentado não foi usado")
    D.bloqueio("E3", "pncp.gov.br /api/pncp/v1/…/arquivos/{n}", "download pelo caminho do manual (/api/pncp/v1/…/arquivos/{n}) deu timeout num momento em que /pncp-api/v1/… (a `url` que a própria listagem devolve) respondeu; NÃO ficou provado que o caminho do manual falha (o PNCP também recusou conexões de forma intermitente: ~40% dos pedidos, em ciclos)",
               tipo="intermitente")


def resumo_tempos():
    t = jl(D.TEMPOS)
    por = defaultdict(list)
    erros = Counter()
    for x in t:
        por[(x["via"], x["host"])].append(x["s"])
        if not (isinstance(x.get("status"), int) and x["status"] in (200, 204, 404)):
            erros[(x["via"], x["host"], str(x.get("status")))] += 1
    out = {}
    for (via, host), v in sorted(por.items()):
        v2 = sorted(v)
        out[f"{via} | {host}"] = {"requisicoes": len(v), "media_s": round(sum(v) / len(v), 2),
                                  "mediana_s": round(statistics.median(v), 2), "p95_s": round(v2[int(len(v2) * .95) - 1], 2),
                                  "max_s": round(max(v), 1)}
    return out, {f"{a}|{b}|{c}": n for (a, b, c), n in erros.most_common(12)}


def reconciliar(arestas):
    """Nome/cargo com letra ilegível (ligadura "ti" sem texto no PDF) → a ÚNICA versão legível igual, no mesmo órgão, com a
    letra ilegível valendo 1 ou 2 letras. `nome` e `cargo` ficam literais; a versão vai em `nome_reconciliado`/`cargo_reconciliado`."""
    ilegivel = lambda s: s and (chr(0xFFFD) in s or chr(0) in s)
    for campo, chave in (("nome", "nome_reconciliado"), ("cargo", "cargo_reconciliado")):
        legiveis = defaultdict(set)
        for a in arestas:
            v = a.get(campo)
            if v and not ilegivel(v):
                legiveis[a["orgao_uasg"]].add(v)
        n_ok = n_total = 0
        for a in arestas:
            v = a.get(campo)
            if not ilegivel(v):
                continue
            n_total += 1
            rx = re.compile("^" + "".join("[A-Za-zÀ-ÿ]{1,2}" if ch in (chr(0xFFFD), chr(0)) else re.escape(ch) for ch in v) + "$", re.I)
            cand = {c for c in legiveis[a["orgao_uasg"]] if rx.match(c)}
            if len(cand) == 1:
                a[chave] = next(iter(cand))
                n_ok += 1
        yield campo, n_ok, n_total


def funil_e2_amostra(am):
    """Funil do E2 só para os contratos da amostra, a partir dos registros por documento (sem nomes)."""
    docs = lj("e2_documentos_todos_pypdf.json") or []
    import coleta, obras
    por_c = defaultdict(list)
    for d in docs:
        if d.get("contrato_id") in am and d["tipo"] in ("Contrato", "Termo Aditivo", "Termo Apostilamento", "Termo de Apostilamento", "Termo Rescisão", "Termo de Rescisão", "Termo de Reconhecimento de Dívida"):
            por_c[d["contrato_id"]].append(d)
    f = {"contratos_tentados": len(am)}
    f["contratos_com_algum_arquivo"] = sum(1 for c in am if (coleta.ler_cache(obras.CACHE_O / "arquivos" / f"{c}.json") or []))
    f["contratos_com_pdf_contrato_ou_termo"] = len(por_c)
    f["contratos_com_pdf_baixado"] = sum(1 for v in por_c.values() if any("erro" not in d for d in v))
    f["contratos_com_texto_extraivel"] = sum(1 for v in por_c.values() if any(d.get("camada_texto") in ("sim", "parcial") for d in v))
    f["contratos_com_assinatura_de_agente_do_orgao"] = sum(1 for v in por_c.values() if any(d.get("n_assinaturas_orgao") for d in v))
    f["contratos_com_representante_confirmado"] = sum(1 for v in por_c.values() if any(d.get("tem_representante") for d in v))
    return f


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    registrar_bloqueios()
    arestas = D.ler_arestas()
    recon = list(reconciliar(arestas))
    cont, obras_de, _ = D.universo()
    am = set(D.amostra())

    # --- confirmação independente: mesma pessoa + contrato + categoria vindos de FONTES diferentes
    fonte_de = lambda a: ("pdf" if a["via"] in ("E1", "E2") else "compras_api" if "compras_gov" in a["metodo"] else "pncp_doc" if a["metodo"].startswith("pncp_") else a["via"])
    grupos = defaultdict(list)
    for a in arestas:
        grupos[(a["contrato_id"], a["categoria"], D.norm(a["nome"]))].append(a)
    for g in grupos.values():
        for a in g:
            a["confirmacao_independente"] = sorted({b["aresta_id"] for b in g if fonte_de(b) != fonte_de(a)})
    D.reescrever_arestas(arestas)

    # --- os três conceitos, separados
    def tres(lista):
        return {"arestas": len(lista),
                "vinculo_documental_true": sum(1 for a in lista if a["vinculo_documental"]),
                "com_confirmacao_independente": sum(1 for a in lista if a["confirmacao_independente"]),
                "vinculo_temporal": dict(Counter(a["vinculo_temporal"] for a in lista))}
    por_via = {v: tres([a for a in arestas if a["via"] == v]) for v in sorted({a["via"] for a in arestas})}
    por_cat = {c: tres([a for a in arestas if a["categoria"] == c]) for c in D.CATEGORIAS}
    por_cat_am = {c: tres([a for a in arestas if a["categoria"] == c and a.get("em_amostra")]) for c in D.CATEGORIAS}

    # --- cobertura por contrato (amostra e todos): contratos com ao menos uma aresta, por categoria
    def cobertura(ids):
        out = {}
        for c in D.CATEGORIAS:
            ct = {a["contrato_id"] for a in arestas if a["categoria"] == c}
            out[c] = {"contratos_com_aresta": len(ct & set(ids)), "de": len(ids), "pct": round(100 * len(ct & set(ids)) / len(ids), 1)}
        qq = {a["contrato_id"] for a in arestas}
        out["qualquer_categoria"] = {"contratos_com_aresta": len(qq & set(ids)), "de": len(ids), "pct": round(100 * len(qq & set(ids)) / len(ids), 1)}
        return out

    # --- cargos
    cargos = Counter(a.get("cargo") for a in arestas if a.get("cargo"))
    ilegiveis = sum(1 for a in arestas if chr(0xFFFD) in (a.get("nome") or "") or "\x00" in (a.get("nome") or ""))
    tempos, erros_rede = resumo_tempos()
    falhas = Counter(f"{x['via']}|{x['motivo']}" for x in jl(D.FALHAS))

    # --- checagens internas (NÃO são a precisão: a precisão é a validação manual do CSV)
    e2 = [a for a in arestas if a["script"] == "e2"]
    internas = {
        "E2_aresta_com_numero_do_contrato_no_texto_do_pdf_pct": round(100 * sum(1 for a in e2 if a["identificador_exato"]["tipo"] == "numero_contrato_no_documento") / max(1, len(e2)), 1),
        "E2_nome_com_2_ou_mais_palavras_pct": round(100 * sum(1 for a in e2 if len((a["nome"] or "").split()) >= 2) / max(1, len(e2)), 1),
        "E2_arestas_de_representante_confirmado_no_preambulo": sum(1 for a in e2 if a.get("papel_no_documento", "").startswith("representante")),
        "nomes_com_caractere_ilegivel": ilegiveis,
    }

    res = {
        "universo": {"obras": len({o for v in obras_de.values() for o in v}), "contratos": len(cont), "amostra_contratos": len(am)},
        "arestas_total": len(arestas),
        "tres_conceitos_por_via": por_via,
        "tres_conceitos_por_categoria_todas": por_cat,
        "tres_conceitos_por_categoria_na_amostra60": por_cat_am,
        "cobertura_por_contrato_amostra60": cobertura(am),
        "cobertura_por_contrato_todos303": cobertura(set(cont)),
        "resumos": {k: lj(v) for k, v in {"E1": "e1_resumo.json", "E2_todos": "e2_resumo_todos_pypdf.json", "E2_amostra": "e2_resumo_amostra60_pypdf.json",
                                          "E2_amostra_pymupdf": "e2_resumo_amostra60_pymupdf.json",
                                          "E3_amostra": "e3_resumo_amostra60.json", "E3_todos": "e3_resumo_todos.json",
                                          "E4": "e4_resumo.json", "E5_E6": "e5e6_resumo.json", "E8": "e8_resumo.json"}.items()},
        "top20_cargos_literais": cargos.most_common(20),
        "cargos_distintos_literais": len(cargos),
        "reconciliacao_de_letra_ilegivel": {c: {"reconciliados": a, "com_letra_ilegivel": b} for c, a, b in recon},
        "funil_E2_amostra60_recalculado": funil_e2_amostra(am),
        "tempos_por_requisicao": tempos,
        "erros_de_rede_ou_http": erros_rede,
        "falhas_registradas": dict(falhas.most_common(15)),
        "bloqueios": lj("bloqueios.json"),
        "checagens_internas": internas,
    }
    for k, v in list(res["resumos"].items()):
        if k == "E5_E6" and v:
            v["e5"].pop("detalhe", None)
    (D.CACHE / "diag_vinculo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- amostra de validação: 40 linhas, estratificadas por via (>= 8 por via com resultado), da amostra de 60 contratos
    rng = random.Random(20261016)
    base = [a for a in arestas if a.get("em_amostra") and a["categoria"] != "autor de emenda parlamentar vinculada documentalmente"]
    emenda = [a for a in arestas if a["categoria"] == "autor de emenda parlamentar vinculada documentalmente"]
    base_por_via = defaultdict(list)
    for a in base + emenda:
        base_por_via[a["via"]].append(a)
    vias = [v for v, l in base_por_via.items() if l]
    escolha = {v: min(8, len(base_por_via[v])) for v in vias}
    resto = 40 - sum(escolha.values())
    while resto > 0:
        livres = [v for v in vias if escolha[v] < len(base_por_via[v]) and len(base_por_via[v]) >= 16]    # o resto vai para as vias com volume
        if not livres:
            break
        for v in sorted(livres, key=lambda v: -len(base_por_via[v])):
            if resto > 0 and escolha[v] < len(base_por_via[v]):
                escolha[v] += 1
                resto -= 1
    linhas = []
    for v in vias:
        linhas += rng.sample(base_por_via[v], escolha[v])
    rng.shuffle(linhas)
    destino = D.CACHE / "amostra_validacao.csv"
    ja_validada = False
    if destino.exists():       # NÃO sobrescreve uma validação já feita à mão (coluna `correto` preenchida)
        ja_validada = any((l.split(";")[8:9] or [""])[0].strip() for l in destino.read_text(encoding="utf-8-sig").splitlines()[1:])
    if not ja_validada:
        with open(D.CACHE / "amostra_validacao.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["aresta_id", "via", "contrato", "url_documento", "pagina", "nome", "cargo", "trecho_literal", "correto"])
            for a in linhas:
                w.writerow([a["aresta_id"], a["via"], a["contrato_id"], a["url_documento"], a.get("pagina") or "", a["nome"], a.get("cargo") or "",
                            (a["trecho_literal"] or "").replace("\n", " ")[:300], ""])
    res["amostra_validacao"] = {"linhas": len(linhas), "por_via": dict(Counter(a["via"] for a in linhas)), "validada_a_mao": ja_validada}
    if ja_validada:
        res["amostra_validacao"]["resultado"] = {v: dict(Counter(r.get("correto") for r in csv.DictReader(open(destino, encoding="utf-8-sig"), delimiter=";") if r["via"] == v))
                                                 for v in ("E2", "E3", "E5")}
    (D.CACHE / "diag_vinculo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("arestas_total", "tres_conceitos_por_via", "cobertura_por_contrato_amostra60", "cobertura_por_contrato_todos303",
                                          "amostra_validacao", "checagens_internas")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
