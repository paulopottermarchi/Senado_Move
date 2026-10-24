"""
Câmara Aberta — DIAGNÓSTICO E9 (emenda no empenho, em TODAS as obras de SP) e E10 (instrumento de repasse nas obras de SP).
Não publica nada: grava cache/diag_vinculo/e9e10_resumo.json e cache/diag_vinculo/e9e10_relatorio.txt.

Por que existe: o E5 e o E6 (diag_vinculo_e5e6.py) olharam só as 260 obras que têm contrato no Contratos.gov.br. Emenda
individual vai quase sempre por transferência a estado ou município (convênio, contrato de repasse, termo de compromisso,
transferência especial), e essas obras não têm contrato federal: o universo certo é o das 20.637 obras de SP do ObrasGov
(cache/diag_vinculo/obrasgov_projetos_SP.json, do E6).

E9 — emenda → autor, sem passar pelo contrato:
  /empenho do ObrasGov inteiro (o país todo cabe em ~450 páginas de 200; cada página fica em cache, a coleta retoma) → só os
  empenhos de obras de SP → `codigo_autor_emenda`:
    - 12 dígitos = código da emenda (ano + autor + número) → CGU, EmendasParlamentares (autor, tipo, ano, UF de aplicação) →
      deputado pela MESMA regra do site (obras.autores_deputados: nome exato de um só deputado + todos os anos no mandato);
    - 8 dígitos (sem o ano) ficam à parte. A reconstrução "ano do empenho + 8 dígitos" é testada na CGU só como MEDIDA: não é
      identificador exato e não vira vínculo.
  Conferências: ano da emenda × ano do empenho; UF de aplicação da emenda × SP.
E10 — o número do instrumento:
  /execucao-fisica inteiro (~372 páginas) → `tipo_instrumento` das obras de SP (o ObrasGov não tem campo com o NÚMERO do
  instrumento: só o tipo e a data). O número só aparece no texto livre do cadastro; regex conservadora (tipo + nº NNNNNN/AAAA)
  → CGU, EmendasParlamentares_Convenios (Número Convênio → Código da Emenda) → autor.

Regras do diagnóstico (as do diag_vinculo_comum): só identificador exato; nome isolado não prova; sem CPF; fato documentado,
nunca "suspeito"; via bloqueada é registrada, sem contorno.
"""

import csv
import io
import json
import re
import sys
from collections import Counter, defaultdict

import coleta
import obras
import diag_vinculo_comum as D
import diag_vinculo_e5e6 as E56

OG = obras.OG
RESUMO = D.DIAG / "e9e10_resumo.json"
RELATORIO = D.DIAG / "e9e10_relatorio.txt"
TEXTOS = ("desc_nome", "desc_projeto", "desc_funcao_social", "desc_meta_global", "obs_pertinentes_intervencao",
          "desc_populacao_beneficiada")
# tipo do instrumento + número no formato do Transferegov (6 dígitos / ano de 4); "nº" opcional. Nada mais solto que isso.
NUM_INSTRUMENTO = re.compile(r"(conv[eê]nio|contrato\s+de\s+repasse|termo\s+de\s+compromisso|\bTED\b|transfer[eê]ncia\s+especial)"
                             r"\s*(?:n\.?\s*[º°o]?\.?\s*|n[úu]mero\s*)?[:\-]?\s*(\d{6})\s*/\s*((?:19|20)\d{2})", re.I)


def todas_paginas(caminho, via, tam=200):
    """Todas as páginas de um endpoint do ObrasGov (sem filtro: o país inteiro). Cada página fica no cache do diagnóstico."""
    itens, pag, total = [], 1, None
    while True:
        d, st = D.pedir(OG + caminho, params=dict(pagina=pag, tamanho_da_pagina=tam), via=via, intervalo=1.0, tentativas=6, timeout=180)
        if not isinstance(d, dict) or "data" not in d or "total_pages" not in d:
            raise RuntimeError(f"ObrasGov {caminho} p.{pag}: {st}")
        total = total if total is not None else d.get("total_items")
        itens += d["data"]
        if pag % 50 == 0:
            print(f"   {caminho}: página {pag}/{d['total_pages']} · {len(itens)} itens", flush=True)
        if pag >= d["total_pages"]:
            return itens, total
        pag += 1


def ler_csv(z, nome):
    with z.open(nome) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")


def classifica_codigo(v):
    s = re.sub(r"\.0$", "", str(v if v is not None else "").strip())
    if not s:
        return "sem código", None
    if re.fullmatch(r"0+", s):
        return "zero", None
    if re.fullmatch(r"\d{12}", s):
        return "12 dígitos", s
    if re.fullmatch(r"\d{8}", s):
        return "8 dígitos", s
    return "outro formato", s


def uf_categoria(uf):
    u = D.norm(uf)
    if u in ("SAO PAULO", "SP"):
        return "SP"
    if u.startswith("MULTIPLO"):
        return "múltiplo"
    if not u or u.startswith("SEM INFORMA"):
        return "sem informação"
    return "outra UF"


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "—"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    projetos = E56.projetos_sp()
    sp = {p["id_projeto_investimento"]: p for p in projetos}
    base = json.loads(obras.SAIDA.read_text(encoding="utf-8"))
    com_contrato = {o["id"] for o in base["obras"]}
    print(f"{len(sp)} obras de SP no ObrasGov · {len(com_contrato)} com contrato no Contratos.gov.br (o universo do E5/E6)", flush=True)

    # ---------------------------------------------------------------- CGU
    emendas = obras.emendas_cgu()                       # código → [autor, nome, tipo, ano]
    autor_dep, fora_dep = obras.autores_deputados(emendas)
    deps = {d["id"]: d for d in json.loads(coleta.SAIDA.read_text(encoding="utf-8"))}
    senadores = set()
    for arq in ("senadores.json", "senadores_antigos.json"):
        sj = coleta.DADOS / arq
        if sj.exists():
            lista = json.loads(sj.read_text(encoding="utf-8"))
            for s in (lista if isinstance(lista, list) else lista.get("senadores", [])):
                for n in (s.get("nome"), s.get("nomeCompleto")):
                    if n:
                        senadores.add(coleta.nome_chave(n))
    z = E56.cgu_zip()
    uf_emenda, convenios = {}, defaultdict(set)
    for x in ler_csv(z, "EmendasParlamentares.csv"):
        c = x.get("Código da Emenda") or ""
        if re.fullmatch(r"\d{12}", c):
            uf_emenda.setdefault(c, (x.get("UF") or "").strip())
    for x in ler_csv(z, "EmendasParlamentares_Convenios.csv"):
        c, n = (x.get("Código da Emenda") or "").strip(), D.so_digitos(x.get("Número Convênio") or "")
        if re.fullmatch(r"\d{12}", c) and n:
            convenios[n].add(c)
    print(f"CGU: {len(emendas) - 1} emendas · {len(convenios)} números de convênio ligados a emenda", flush=True)

    # ---------------------------------------------------------------- E9: empenhos
    print("E9: /empenho do ObrasGov (país inteiro)…", flush=True)
    empenhos, total_emp = todas_paginas("/empenho", "E9")
    emp_sp = [e for e in empenhos if e.get("id_projeto_investimento") in sp]
    cont = Counter()
    por_obra = defaultdict(lambda: {"tipos": set(), "deputados": set(), "emendas": set(), "valido": False})
    obras_com_empenho = set()
    detalhe, oito, ano_diverge, uf_fora, sem_cgu, uf_cat = [], Counter(), [], [], Counter(), Counter()
    individuais_sem_dep = Counter()
    for e in emp_sp:
        oid = e["id_projeto_investimento"]
        obras_com_empenho.add(oid)
        cat, cod = classifica_codigo(e.get("codigo_autor_emenda"))
        cont[cat] += 1
        ano_emp = (e.get("nr_empenho") or "")[:4]
        if cat == "8 dígitos":
            rec = ano_emp + cod if re.fullmatch(r"(19|20)\d{2}", ano_emp) else None
            oito["reconstruído existe na CGU" if rec in emendas else "reconstruído não existe na CGU"] += 1
            continue
        if cat != "12 dígitos":
            continue
        em = emendas.get(cod)
        if not em:
            sem_cgu["código não consta da CGU"] += 1
            continue
        tipo = E56.tipo_emenda(em[2])
        b = por_obra[oid]
        b["valido"] = True
        b["tipos"].add(tipo)
        b["emendas"].add(cod)
        if em[3] != ano_emp:
            ano_diverge.append((oid, cod, em[3], e.get("nr_empenho")))
        uf = uf_emenda.get(cod, "")              # a CGU grava a UF por extenso: "SÃO PAULO", "Múltiplo", "Sem informação"
        uf_cat[uf_categoria(uf)] += 1
        if uf_categoria(uf) == "outra UF":
            uf_fora.append((oid, cod, uf))
        dep = autor_dep.get(em[0]) if tipo == "individual" else None
        if dep:
            b["deputados"].add(dep)
        elif tipo == "individual":
            individuais_sem_dep["nome confere com senador" if coleta.nome_chave(em[1]) in senadores else
                                "autor não é deputado do site (ex-deputado ou outro)"] += 1
        detalhe.append({"obra": oid, "sistema": sp[oid].get("sistema_resp"), "com_contrato": oid in com_contrato,
                        "empenho": [e.get("ug_emitente"), e.get("nr_empenho")], "valor": e.get("valor_empenho"),
                        "emenda": cod, "tipo": tipo, "autor_cgu": em[0], "autor": em[1], "ano": em[3], "uf_aplicacao": uf,
                        "deputado": dep, "deputado_nome": deps[dep]["nome"] if dep else None})

    validas = {o for o, b in por_obra.items() if b["valido"]}
    tipos_obra = Counter(t for o in validas for t in por_obra[o]["tipos"])
    com_dep = {o for o in validas if por_obra[o]["deputados"]}
    deputados = Counter(d for o in com_dep for d in por_obra[o]["deputados"])
    por_sistema = defaultdict(Counter)
    for oid, p in sp.items():
        s = p.get("sistema_resp") or "—"
        por_sistema[s]["obras"] += 1
        por_sistema[s]["com empenho"] += oid in obras_com_empenho
        por_sistema[s]["com emenda"] += oid in validas
        por_sistema[s]["com deputado"] += oid in com_dep
    e9 = {
        "empenhos_no_pais": len(empenhos), "total_informado_pela_api": total_emp, "empenhos_de_obras_SP": len(emp_sp),
        "obras_SP_com_empenho": len(obras_com_empenho), "codigos": dict(cont), "oito_digitos": dict(oito), "sem_cgu": dict(sem_cgu),
        "obras_com_emenda": len(validas), "obras_com_emenda_por_tipo": dict(tipos_obra),
        "obras_com_emenda_e_contrato": len(validas & com_contrato), "obras_com_deputado": len(com_dep),
        "deputados_distintos": len(deputados), "emendas_distintas": len({d["emenda"] for d in detalhe}),
        "individuais_sem_deputado": dict(individuais_sem_dep), "autores_fora_da_regra": dict(fora_dep),
        "ano_da_emenda_diferente_do_empenho": len(ano_diverge), "uf_de_aplicacao": dict(uf_cat),
        "deputados_por_uf": dict(Counter(deps[d]["uf"] for d in deputados)),
        "por_sistema": {k: dict(v) for k, v in sorted(por_sistema.items(), key=lambda kv: -kv[1]["obras"])},
        "deputados_top": [[deps[d]["nome"], deps[d]["partido"], n] for d, n in deputados.most_common(15)],
        "exemplos_ano_diverge": ano_diverge[:10], "exemplos_uf_fora": uf_fora[:10],
    }

    # ---------------------------------------------------------------- E10: instrumento
    print("E10: /execucao-fisica do ObrasGov (país inteiro)…", flush=True)
    execs, total_ex = todas_paginas("/execucao-fisica", "E10")
    ex_sp = defaultdict(list)
    for x in execs:
        if x.get("id_projeto_investimento") in sp:
            ex_sp[x["id_projeto_investimento"]].append(x)
    tipo_inst = Counter()
    for oid, lst in ex_sp.items():
        for t in {x.get("tipo_instrumento") or "(vazio)" for x in lst}:
            tipo_inst[t] += 1
    numeros, achados = Counter(), []
    for oid, p in sp.items():
        for campo in TEXTOS:
            m = NUM_INSTRUMENTO.search(p.get(campo) or "")
            if not m:
                continue
            n = m.group(2).lstrip("0")
            ems = sorted(convenios.get(n, set()) | convenios.get(m.group(2), set()))
            info = [{"emenda": c, "tipo": E56.tipo_emenda(emendas[c][2]) if c in emendas else None,
                     "autor": emendas[c][1] if c in emendas else None,
                     "deputado": autor_dep.get(emendas[c][0]) if c in emendas and E56.tipo_emenda(emendas[c][2]) == "individual" else None}
                    for c in ems]
            numeros["obras com nº de instrumento no texto"] += 1
            numeros["nº que a CGU liga a emenda"] += bool(ems)
            numeros["… com deputado pela regra do site"] += any(i["deputado"] for i in info)
            achados.append({"obra": oid, "campo": campo, "trecho": m.group(0), "numero": m.group(2) + "/" + m.group(3),
                            "sistema": p.get("sistema_resp"), "emendas": info})
            break
    e10 = {"execucoes_no_pais": len(execs), "total_informado_pela_api": total_ex, "obras_SP_com_execucao": len(ex_sp),
           "tipo_instrumento_obras_SP": dict(tipo_inst.most_common()), "numero_no_texto": dict(numeros), "achados": achados}

    res = {"e9": e9, "e10": e10, "detalhe_e9": detalhe}
    RESUMO.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- relatório
    n = len(sp)
    L = [f"DIAGNÓSTICO E9/E10 — {n} obras de SP no ObrasGov (o E5/E6 olhou {len(com_contrato)})", ""]
    L += ["E9  Emenda no empenho → autor (sem passar pelo contrato)",
          f"    empenhos no país: {len(empenhos)} (a API informa {total_emp}) · de obras de SP: {len(emp_sp)} · obras de SP com empenho: "
          f"{len(obras_com_empenho)} ({pct(len(obras_com_empenho), n)})",
          "    código no empenho: " + " · ".join(f"{k} {v}" for k, v in cont.most_common()),
          "    8 dígitos (sem o ano), só medida: " +(" · ".join(f"{k} {v}" for k, v in oito.items()) or "nenhum"),
          f"    12 dígitos fora da CGU: {sem_cgu.get('código não consta da CGU', 0)}",
          f"    obras com emenda (código exato na CGU): {len(validas)} ({pct(len(validas), n)}) · por tipo: "
          + " · ".join(f"{k} {v}" for k, v in tipos_obra.most_common()),
          f"    dessas, com contrato no Contratos.gov.br: {len(validas & com_contrato)}",
          f"    obras com deputado (emenda individual, regra do site): {len(com_dep)} ({pct(len(com_dep), n)}) · deputados distintos: {len(deputados)}",
          "    emendas individuais sem deputado: " + (" · ".join(f"{k} {v}" for k, v in individuais_sem_dep.items()) or "nenhuma"),
          f"    conferências: ano da emenda ≠ ano do empenho em {len(ano_diverge)} empenhos; UF de aplicação da emenda (CGU): "
          + " · ".join(f"{k} {v}" for k, v in uf_cat.most_common())
          + "; deputados por UF: " +" · ".join(f"{k} {v}" for k, v in Counter(deps[d]["uf"] for d in deputados).most_common()),
          "    por sistema de origem da obra (obras · com empenho · com emenda · com deputado):"]
    for s, v in e9["por_sistema"].items():
        L.append(f"      {s:18} {v['obras']:6} {v['com empenho']:7} {v['com emenda']:7} {v['com deputado']:7}")
    if deputados:
        L.append("    deputados com mais obras: " + "; ".join(f"{a} ({p}) {k}" for a, p, k in e9["deputados_top"][:8]))
    L += ["", "E10 Instrumento de repasse",
          f"    execuções físicas no país: {len(execs)} (a API informa {total_ex}) · obras de SP com execução: {len(ex_sp)}",
          "    tipo_instrumento nas obras de SP: " + " · ".join(f"{k} {v}" for k, v in tipo_inst.most_common(8)),
          "    o ObrasGov não tem campo com o NÚMERO do instrumento (só tipo e data de criação); no texto livre do cadastro: "
          + " · ".join(f"{k} {v}" for k, v in numeros.items())]
    RELATORIO.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n" + "\n".join(L))


if __name__ == "__main__":
    main()
