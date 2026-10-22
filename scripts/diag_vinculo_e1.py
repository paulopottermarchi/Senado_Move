"""
Câmara Aberta — DIAGNÓSTICO E1: signatário do extrato publicado no DOU.

Três fontes, medidas separadamente:
  (a) a página do jornal, pelos links já coletados (campo link_publicacao de /contrato/{id}/publicacoes). Os 848 links
      são do endereço antigo http://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?jornal=…&pagina=…&data=….
      NÃO ACESSADO: www.in.gov.br/robots.txt tem "User-agent: * / Disallow: /" e pesquisa.in.gov.br responde "Request
      Rejected" ao cliente que não é navegador. Registrado em bloqueios.json; sem contorno.
  (b) o texto do extrato que o próprio Contratos.gov.br entrega: `texto_dou` em /contrato/{id}/publicacoes
      (documentado em /docs/api-docs.json). É o texto ENVIADO à Imprensa Nacional — não é a página publicada.
  (c) os anexos "Publicações DOU" / "Outras Publicações" de /contrato/{id}/arquivos: PDF da página do jornal que o
      órgão anexou ao contrato, servido pelo storage do Contratos.gov.br (lido em diag_vinculo_e2.py).
Localiza o extrato pelo número do contrato E pelo processo; procura nome + cargo depois do corpo do texto.
"""

import json
import re
import sys
from collections import Counter

import diag_vinculo_comum as D
import diag_vinculo_e2 as E

CARGO = re.compile(r"(reitor|diretor|pr[óo]-reitor|coordenador|chefe|superintendente|secret|gestor|presidente|ordenador|pregoeir|gerente|comandante|delegad)", re.I)
MESES = "janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"


def assinatura_no_final(texto):
    """Depois do corpo, linha de NOME seguida de linha de CARGO. Devolve (nome, cargo, linhas) ou None."""
    linhas = [l.strip() for l in (texto or "").replace("\r", "").split("\n") if l.strip() and not l.startswith("##OFI")]
    for i in range(len(linhas) - 1):
        nome, cargo = linhas[i], linhas[i + 1]
        if (re.fullmatch(r"[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ'. -]{8,70}", nome) and len(nome.split()) >= 2 and not nome.startswith("##")
                and len(cargo) <= 90 and CARGO.search(cargo) and not re.search(r"\d", nome + cargo)):
            return nome, cargo, linhas[max(0, i - 1): i + 2]
    return None


def numero_confere(num, texto):
    return E.numero_no_texto(num, " ".join((texto or "").split()))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cont, obras_de, _ = D.universo()
    am = set(D.amostra())
    D.reescrever_arestas([a for a in D.ler_arestas() if a.get("script") != "e1"])
    arestas = D.Arestas()
    D.bloqueio("E1/E4", "www.in.gov.br", "robots.txt: User-agent * / Disallow: / — coleta automatizada vedada; páginas do DOU não acessadas",
               robots="User-agent: *\nDisallow: /")
    D.bloqueio("E1/E4", "pesquisa.in.gov.br", "responde 'Request Rejected' (WAF) a cliente que não é navegador; endereço legado dos 848 links",
               exemplo="http://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?jornal=530&pagina=110&data=30/08/2022")
    res = {}
    for escopo, ids in (("amostra60", sorted(am)), ("todos303", sorted(cont))):
        f = Counter()
        extratos_por_tipo = Counter()
        for cid in ids:
            c = cont[cid]
            pubs = D.cache_contrato(cid).get("publicacoes", [])
            f["contratos"] += 1
            f["contratos_com_alguma_publicacao_no_dou"] += bool(pubs)
            loc = ass = False
            for p in pubs:
                f["publicacoes"] += 1
                f["publicacoes_com_link_para_a_pagina_do_jornal"] += bool(p.get("link_publicacao"))
                if p.get("status") != "PUBLICADA":
                    f[f"publicacao_status_{p.get('status')}"] += 1
                    continue
                f["publicacoes_status_PUBLICADA"] += 1
                t = p.get("texto_dou") or ""
                f["publicadas_com_texto_dou_na_api"] += bool(t)
                m = re.search(r"##ATO\s+(.{0,70}?)(?:N[º°o]|\r|\n)", t)
                tipo = (m[1].strip() if m else "?")[:34]
                extratos_por_tipo[tipo] += 1
                ncf = numero_confere(c["numero"], t)
                proc = D.so_digitos(c.get("processo"))
                pcf = bool(proc) and proc in D.so_digitos(t)
                if ncf or pcf:
                    f["extrato_localizado_por_numero_ou_processo"] += 1
                    f["extrato_localizado_por_numero_E_processo"] += bool(ncf and pcf)
                    loc = True
                    sg = assinatura_no_final(t)
                    if sg:
                        f["assinatura_extraida_do_texto_dou"] += 1
                        ass = True
                        nome, cargo, ctx = sg
                        # data do ato: "Santos, 11 de abril de 2025." (cidade e data) — ato e publicação separados
                        dm = re.search(rf"(\d{{1,2}})\s+de\s+({MESES})\s+de\s+(\d{{4}})", t, re.I)
                        meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
                        d_ato = None
                        if dm:
                            mm = dm[2].lower().replace("marco", "março")
                            d_ato = f"{dm[3]}-{meses.index(mm) + 1:02d}-{int(dm[1]):02d}" if mm in meses else None
                        for oid in obras_de[cid]:
                            arestas.nova(obra_id=oid, contrato_id=cid, nome=nome.title() if nome.isupper() else nome, cargo=cargo,
                                         categoria="signatário do extrato (DOU)", via="E1", script="e1",
                                         identificador_exato={"tipo": "numero_contrato_no_texto" if ncf else "processo_no_texto",
                                                              "valor": c["numero"] if ncf else c.get("processo")},
                                         metodo="texto_dou_api_regex_nome_cargo", orgao_uasg=c["uasg"],
                                         url_documento=f"{D.obras.CT}/contrato/{cid}/publicacoes#{p['id']}",
                                         hash_documento=D.sha256(t), trecho_literal=" | ".join(ctx),
                                         data_do_vinculo=d_ato, data_publicacao=p.get("data_publicacao"),
                                         vinculo_documental=True, em_amostra=cid in am,
                                         observacao="texto enviado à Imprensa Nacional (API do Contratos.gov.br), não a página do jornal; "
                                                    "o extrato é aviso de penalidade, não extrato do contrato" if "penalidade" in t.lower() else
                                                    "texto enviado à Imprensa Nacional (API do Contratos.gov.br), não a página do jornal")
            f["contratos_com_extrato_localizado"] += loc
            f["contratos_com_assinatura_extraida"] += ass
        res[escopo] = {"funil": dict(f), "tipos_de_ato_mais_comuns": extratos_por_tipo.most_common(8)}
    res["pagina_do_jornal_acessivel"] = {"testado": False, "motivo": "robots.txt de www.in.gov.br (Disallow: /) e WAF em pesquisa.in.gov.br; não contornado",
                                         "links_coletados": res["todos303"]["funil"].get("publicacoes_com_link_para_a_pagina_do_jornal"),
                                         "host_dos_links": "pesquisa.in.gov.br (endereço legado)"}
    (D.DIAG / "e1_resumo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
