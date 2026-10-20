"""
Câmara Aberta — DIAGNÓSTICO E5 (emenda → empenho → contrato) e E6 (instrumento de repasse).

Fontes (todas com documentação oficial baixada em cache/diag_vinculo/doc_*.json ou conferida no arquivo):
  - CGU, EmendasParlamentares.zip (dadosabertos-download.cgu.gov.br, o mesmo link de obras.py): três CSV —
    EmendasParlamentares (emenda, autor, tipo, valores), _Convenios (emenda → nº do convênio) e
    _PorFavorecido (emenda → CNPJ/CPF do favorecido, mês e valor). NENHUM liga emenda a número de empenho.
  - ObrasGov, /empenho (cache de obras.py): ug_emitente, nr_empenho, codigo_autor_emenda.
  - Contratos.gov.br, /contrato/{id}/empenhos (cache de obras.py): unidade_gestora, numero.
  - ObrasGov, /projeto-investimento (OpenAPI em /obras/openapi.json): repassadores (organização + CNPJ),
    tomadores, executores, investimentos_previstos, pins, ppas, textos livres.

E5 monta: emenda (código de 12 dígitos no empenho do ObrasGov) → autor (CGU) → empenho (ug + número) →
empenho do contrato no Contratos.gov.br (ug + número iguais) → contrato. Só IGUALDADE EXATA de ug e número.
"""

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict

import coleta
import obras
import diag_vinculo_comum as D

OG = obras.OG


def projetos_sp():
    """/projeto-investimento?uf_principal=SP, todas as páginas (cache do diagnóstico)."""
    cam = D.DIAG / "obrasgov_projetos_SP.json"
    if cam.exists():
        return json.loads(cam.read_text(encoding="utf-8"))
    itens, pag = [], 1
    while True:
        d, st = D.pedir(OG + "/projeto-investimento", params=dict(uf_principal="SP", pagina=pag, tamanho_da_pagina=200),
                        via="E6", intervalo=1.0)
        if not isinstance(d, dict) or "data" not in d:
            raise RuntimeError(f"projeto-investimento p.{pag}: {st}")
        itens += d["data"]
        if pag >= d["total_pages"]:
            break
        pag += 1
    cam.write_text(json.dumps(itens, ensure_ascii=False), encoding="utf-8")
    return itens


def cgu_zip():
    b, st = D.pedir(obras.EMENDAS_ZIP, via="E5", bruto=True, cache_nome="emendas_zip", timeout=600)
    return zipfile.ZipFile(io.BytesIO(b))


def colunas_zip(z):
    out = {}
    for i in z.infolist():
        with z.open(i.filename) as f:
            cab = f.readline().decode("latin-1").strip()
        out[i.filename] = {"bytes": i.file_size, "colunas": [c.strip('"') for c in cab.split(";")]}
    return out


def tipo_emenda(t):
    t = (t or "").lower()
    if "individual" in t:
        return "individual"
    if "bancada" in t:
        return "bancada"
    if "comiss" in t:
        return "comissão"
    if "relator" in t:
        return "relator"
    return "outro"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cont, obras_de, base = D.universo()
    arestas = D.Arestas()
    D.reescrever_arestas([a for a in D.ler_arestas() if a.get("script") != "e5"])
    arestas = D.Arestas()
    z = cgu_zip()
    res = {"cgu_zip": colunas_zip(z)}
    tem_empenho = [n for n, v in res["cgu_zip"].items() if any("mpenho" in c for c in v["colunas"])]
    res["cgu_arquivo_com_numero_de_empenho"] = tem_empenho

    # --- emendas da CGU: código → [autor, nome, tipo, ano]
    emendas = obras.emendas_cgu()
    autor_dep, fora_dep = obras.autores_deputados(emendas)
    senadores = {}
    for arq in ("senadores.json", "senadores_antigos.json"):
        sj = coleta.DADOS / arq
        if sj.exists():
            lista = json.loads(sj.read_text(encoding="utf-8"))
            lista = lista if isinstance(lista, list) else lista.get("senadores", [])
            for s in lista:
                for n in (s.get("nome"), s.get("nomeCompleto")):
                    if n:
                        senadores.setdefault(coleta.nome_chave(n), []).append(s)
    por_obra = {o["id"]: o for o in base["obras"]}
    obs_cache = {}
    for oid in por_obra:
        obs_cache[oid] = coleta.ler_cache(obras.CACHE_O / "obra" / f"{oid.replace('/', '_')}.json")

    # --- E5: empenhos do ObrasGov com código de emenda válido
    cont_emp = Counter()
    resolvidas = []
    obras_com = defaultdict(set)
    for oid, o in por_obra.items():
        d = obs_cache.get(oid)
        if not d:
            continue
        # empenhos dos contratos da obra (Contratos.gov.br): (unidade_gestora, numero) → contrato
        emp_contrato = {}
        for c in o["contratos"]:
            cc = D.cache_contrato(c["id"])
            for e in cc.get("empenhos", []):
                emp_contrato.setdefault((D.so_digitos(e.get("unidade_gestora")), (e.get("numero") or "").strip().upper()),
                                        []).append(c)
        for e in d["empenho"]:
            bruto = e.get("codigo_autor_emenda")
            if bruto in (None, "", "0", 0, "0.0"):
                cont_emp["empenho sem código de emenda"] += 1
                continue
            k = obras.codigo_emenda(bruto)
            if k is None:
                cont_emp["código inválido (@, s/e, 8 dígitos…)"] += 1
                continue
            if k not in emendas:
                cont_emp["código válido sem par na CGU"] += 1
                continue
            autor_cod, autor_nome, tipo, ano = emendas[k]
            tp = tipo_emenda(tipo)
            chave = (D.so_digitos(e.get("ug_emitente")), (e.get("nr_empenho") or "").strip().upper())
            cts = emp_contrato.get(chave, [])
            dep = autor_dep.get(autor_cod)
            sen = bool(senadores.get(coleta.nome_chave(autor_nome))) and tp == "individual" and not dep
            cont_emp["emenda ligada ao empenho"] += 1
            obras_com[oid].add((k, tp))
            resolvidas.append({"obra": oid, "emenda": k, "tipo": tp, "tipo_cgu": tipo, "autor": autor_nome, "autor_cod": autor_cod,
                               "ano": ano, "empenho": chave, "contratos_pelo_empenho": [c["id"] for c in cts],
                               "deputado": dep, "senador": sen})
            for c in (cts or [None]):
                if tp == "individual":
                    nome_a = autor_nome
                else:
                    nome_a = autor_nome        # bancada/comissão/relator: nome da instância, não de pessoa
                # bancada, comissão e relator não são pessoa: entram como aresta (para contar por tipo), marcadas
                # `autor_e_pessoa=False`; só a individual aponta para uma pessoa (deputado ou senador)
                arestas.nova(
                    obra_id=oid, contrato_id=str(c["id"]) if c else None, nome=nome_a, cargo=("autor da emenda individual (CGU)" if tp == "individual" else f"emenda de {tp} (CGU): instância, não pessoa"),
                    categoria="autor de emenda parlamentar vinculada documentalmente", via="E5", script="e5",
                    identificador_exato={"tipo": "codigo_emenda+empenho", "valor": f"{k} | {chave[0]}/{chave[1]}"},
                    metodo="obrasgov_empenho_codigo_emenda+cgu" + ("+empenho_contrato_ug_numero" if c else ""),
                    orgao_uasg=chave[0], url_documento=f"{OG}/empenho?id_projeto_investimento={oid}",
                    hash_documento=D.sha256(json.dumps(e, sort_keys=True, ensure_ascii=False)),
                    trecho_literal=f"codigo_autor_emenda={k}; ug_emitente={chave[0]}; nr_empenho={chave[1]}; CGU autor {autor_cod} {autor_nome} ({tipo}, {ano})",
                    data_do_vinculo=None, data_publicacao=None, vinculo_documental=True,
                    confirmacao_independente=[], deputado_resolvido=dep, senador_nome_confere=sen,
                    empenho_casa_com_contrato=bool(c), ano_emenda=ano, tipo_emenda=tp, autor_e_pessoa=(tp == "individual"))

    por_tipo = Counter()
    for oid, s in obras_com.items():
        for tp in {t for _, t in s}:
            por_tipo[tp] += 1
    res["e5"] = {
        "contagem_empenhos": dict(cont_emp),
        "obras_com_emenda_ligada": len(obras_com),
        "obras_por_tipo": dict(por_tipo),
        "emendas_distintas": len({r["emenda"] for r in resolvidas}),
        "empenhos_que_casam_com_empenho_de_contrato_ug_numero": sum(1 for r in resolvidas if r["contratos_pelo_empenho"]),
        "empenhos_ligados_a_emenda": len(resolvidas),
        "individuais_resolvidas_para_deputado": sum(1 for r in resolvidas if r["deputado"]),
        "individuais_cujo_nome_confere_com_senador": sum(1 for r in resolvidas if r["senador"]),
        "autores_deputados_regra_atual": {"codigos_ligados_a_deputado": len(autor_dep), "fora_da_regra": dict(fora_dep)},
        "detalhe": resolvidas,
    }

    # --- E6: instrumento de repasse
    proj = {p["id_projeto_investimento"]: p for p in projetos_sp()}
    rx = {
        "convenio": re.compile(r"conv[êe]nio\s*(?:n[º°o.]*\s*)?(\d{4,7}\s*/\s*\d{4})", re.I),
        "ted": re.compile(r"(?:TED|termo de execu[çc][ãa]o descentralizada)\s*(?:n[º°o.]*\s*)?(\d{1,6}\s*/\s*\d{4})", re.I),
        "contrato_de_repasse": re.compile(r"contrato de repasse\s*(?:n[º°o.]*\s*)?([\d./-]{5,25})", re.I),
        "termo_de_compromisso": re.compile(r"termo de compromisso\s*(?:n[º°o.]*\s*)?([\d./-]{3,20})", re.I),
        "plano_de_acao_ou_transf_especial": re.compile(r"(plano de a[çc][ãa]o\s*(?:n[º°o.]*\s*)?\d{3,10}|transfer[êe]ncias? especia[li]s?|emenda pix)", re.I),
        "siconv_transferegov_id": re.compile(r"(?:siconv|transferegov|plataforma \+ ?brasil)\s*[:n º°.]*\s*(\d{5,8})", re.I),
    }

    def textos(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, dict):
            for v in x.values():
                yield from textos(v)
        elif isinstance(x, list):
            for v in x:
                yield from textos(v)

    campos_proj = Counter()
    achados = defaultdict(list)
    n_obras = 0
    for oid in por_obra:
        p = proj.get(oid)
        if not p:
            continue
        n_obras += 1
        for k, v in p.items():
            if v not in (None, "", [], {}):
                campos_proj[k] += 1
        alvo = [t for t in textos({k: v for k, v in p.items() if k not in ("fotos", "geometria")})]
        d = obs_cache.get(oid) or {}
        alvo += [e.get("descricao_empenho") or "" for e in d.get("empenho", [])]
        for c in por_obra[oid]["contratos"]:
            cc = D.cache_contrato(c["id"])
            alvo += [cc["registro"].get("informacao_complementar") or "", cc["registro"].get("objeto") or ""]
            alvo += [h.get("observacao") or "" for h in cc.get("historico", [])]
        for nome, r in rx.items():
            for t in alvo:
                m = r.search(t)
                if m:
                    achados[nome].append((oid, m.group(0)[:80]))
                    break
    repass = Counter(r.get("organizacao_repassador") for p in proj.values() if p["id_projeto_investimento"] in por_obra
                     for r in p.get("repassadores") or [])
    res["e6"] = {
        "obras_com_projeto_no_obrasgov": n_obras,
        "campos_do_projeto_preenchidos": dict(campos_proj),
        "obras_com_instrumento_por_tipo": {k: len({o for o, _ in v}) for k, v in achados.items()},
        "obras_com_algum_instrumento": len({o for v in achados.values() for o, _ in v}),
        "exemplos": {k: [t for _, t in v[:5]] for k, v in achados.items()},
        "repassadores_mais_frequentes": repass.most_common(8),
        "obras_com_repassador_cadastrado": sum(1 for p in proj.values() if p["id_projeto_investimento"] in por_obra and p.get("repassadores")),
        "campo_de_instrumento_estruturado_no_openapi": False,
    }
    # convênios da CGU (emenda → número do convênio): quantos dos códigos de emenda das obras têm convênio
    cods = {r["emenda"] for r in resolvidas}
    conv_hits = []
    with z.open("EmendasParlamentares_Convenios.csv") as f:
        rd = csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
        for x in rd:
            if x["Código da Emenda"] in cods:
                conv_hits.append({k: x[k] for k in ("Código da Emenda", "Número Convênio", "Convenente", "Data Publicação Convênio")})
    res["e6"]["cgu_convenios_das_emendas_das_obras"] = conv_hits[:20]
    res["e6"]["cgu_convenios_n"] = len(conv_hits)
    # favorecido: os CNPJ dos contratados das obras como favorecido de emenda (candidatos, NÃO vínculo)
    cnpjs = {D.so_digitos(c["fornecedor"][1]): c["id"] for o in base["obras"] for c in o["contratos"]}
    fav = Counter(); fav_emendas = defaultdict(set)
    with z.open("EmendasParlamentares_PorFavorecido.csv") as f:
        rd = csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
        for x in rd:
            cod = D.so_digitos(x["Código do Favorecido"])
            if cod in cnpjs:
                fav[cod] += 1
                fav_emendas[cod].add(x["Código da Emenda"])
    res["e5"]["candidatos_por_favorecido_nao_sao_vinculo"] = {
        "contratados_que_aparecem_como_favorecido_de_alguma_emenda": len(fav),
        "de": len(cnpjs), "emendas_distintas": len({e for v in fav_emendas.values() for e in v})}
    (D.DIAG / "e5e6_resumo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "e5"}, ensure_ascii=False, indent=1)[:6000])
    print(json.dumps({k: v for k, v in res["e5"].items() if k != "detalhe"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
