"""
Câmara Aberta — DIAGNÓSTICO E3: edital, agente de contratação/pregoeiro e autoridade que homologou.

Uso:
    python scripts/diag_vinculo_e3_sonda.py [--todos]   # 1) consulta as rotas por identificador (cache)
    python scripts/diag_vinculo_e3.py [--todos]         # 2) lê os documentos do PNCP e gera as arestas

Vínculo: idCompra = UASG da compra + modalidade + número + ano (do registro do contrato). Compras.gov.br
(módulo legado) devolve `nome_responsavel` e `funcao_responsavel` do aviso de licitação; as dispensas trazem
declarante e ratificador; o PNCP lista os documentos da contratação (Lei 14.133). Só identificador exato.

Categorias (nunca equivalentes):
  - "agente de contratação/pregoeiro": o cargo LITERAL da fonte diz pregoeiro, agente de contratação ou
    presidente de comissão de licitação. Qualquer outro cargo de "responsável pelo aviso" (diretor, coordenador,
    ordenador) NÃO é pregoeiro: vai para e3_responsavel_cargo_outro.jsonl, sem categoria atribuída.
  - "autoridade que homologou": só de documento "Termo/Ato de Homologação" (PNCP tipo 26) assinado, ou do campo de
    ratificação de dispensa/inexigibilidade (que é RATIFICAÇÃO, não homologação — dito em `ato`).

Documentos do PNCP: download pela `url` que a própria listagem devolve (/pncp-api/v1/…); o caminho do manual
(/api/pncp/v1/…/arquivos/{n}) não conectou. Edital vem em ZIP (PDF + planilhas). Lê-se só o começo e o fim do PDF.
O arquivo baixado não fica em disco além do cache de desenvolvimento (apagado no fim do diagnóstico).
"""

import argparse
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict

import pypdf

import coleta
import diag_vinculo_comum as D
import diag_vinculo_e2 as E
import diag_vinculo_e3_sonda as SO

PASTA = D.DIAG / "e3"
CAT_AGENTE = "agente de contratação/pregoeiro"
CAT_HOMOLOG = "autoridade que homologou"
RX_AGENTE = re.compile(r"pregoeir|agente de contrata|presidente.{0,60}(?:comiss|\bcpl\b|\bcel\b)|comiss[ãa]o (?:permanente |especial )?de licita", re.I)
RX_NOME_APOS_PAPEL = re.compile(
    r"(Pregoeir[oa](?:\s*\(a\))?|Agente de Contrata[çc][ãa]o|Presidente da Comiss[ãa]o[^:]{0,30})\s*(?:[:\-–]|\bsr[ªa.]*\b|\bsra\b)\s*"
    r"([A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ'.]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ'.]+){1,5})")
RX_PORTARIA = re.compile(r"Portaria\s*(?:n[º°o.]*\s*)?[\d./-]{2,20}(?:\s*,?\s*de\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4})?", re.I)
TIPOS_DOC = {1: "Aviso de Contratação Direta", 2: "Edital", 20: "Ato que autoriza a Contratação Direta",
             26: "Termo/Ato de Homologação", 27: "Ata de sessão pública eletrônica", 28: "Ata de sessão pública presencial"}
MAX_DOC = 30 * 1024 * 1024
INTERVALO_PNCP = 4.0     # o PNCP deixou de aceitar conexões deste IP depois de ~100 pedidos a 1 s (medido): mais devagar


def paginas_selecionadas(pdf, ini=3, fim=5):
    """pypdf: só as primeiras `ini` e as últimas `fim` páginas (assinatura costuma ficar no fim)."""
    r = pypdf.PdfReader(io.BytesIO(pdf))
    n = len(r.pages)
    idx = sorted(set(list(range(min(ini, n))) + list(range(max(0, n - fim), n))))
    out = {}
    for i in idx:
        try:
            t = r.pages[i].extract_text() or ""
        except Exception:
            t = ""
        t = "".join(E.FFFD if (ord(ch) < 32 and ch not in "\n\t\r") else ch for ch in t)
        out[i + 1] = " ".join(t.split())
    return out, n


def pdfs_do_arquivo(b, prof=0):
    """(nome, bytes) dos PDFs de um download do PNCP: PDF direto ou ZIP (até 2 níveis). Planilhas ignoradas."""
    if b[:4] == b"%PDF":
        yield "documento.pdf", b
    elif b[:2] == b"PK" and prof < 2:
        try:
            z = zipfile.ZipFile(io.BytesIO(b))
        except Exception:
            return
        for i in z.infolist()[:12]:
            if i.file_size > MAX_DOC:
                continue
            nm = i.filename
            if nm.lower().endswith(".pdf"):
                yield nm, z.read(nm)
            elif nm.lower().endswith(".zip"):
                for x in pdfs_do_arquivo(z.read(nm), prof + 1):
                    yield nm + "/" + x[0], x[1]


def eh_agente(cargo):
    return bool(RX_AGENTE.search(cargo or ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--sem-docs", action="store_true", help="não baixa documentos do PNCP (só legado e listagens)")
    ap.add_argument("--docs-todos", action="store_true", help="baixa documentos de TODOS os contratos (padrão: só da amostra de 60)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cont, obras_de, _ = D.universo()
    alvo = sorted(cont) if args.todos else D.amostra()
    am = set(D.amostra())
    D.reescrever_arestas([a for a in D.ler_arestas() if a.get("script") != "e3"])
    arestas = D.Arestas()
    outros = D.DIAG / "e3_responsavel_cargo_outro.jsonl"
    outros.write_text("", encoding="utf-8")

    por_ano = defaultdict(Counter)
    funil = Counter()
    docs_vistos = {}
    cargos_outros = Counter()
    for cid in alvo:
        r = D.cache_contrato(cid)["registro"]
        o = json.loads((PASTA / f"{cid}.json").read_text(encoding="utf-8")) if (PASTA / f"{cid}.json").exists() else None
        if o is None:
            o = SO.sondar(cid, r)
        ano = o.get("ano_licitacao")
        pa = por_ano[ano]
        pa["contratos"] += 1
        funil["contratos"] += 1
        idc = o.get("idCompra")
        n, a_ = SO.numero_ano(r.get("licitacao_numero"))
        uasg = r["unidade_compra"]
        R = o.get("rotas", {})
        achou_resp = False
        # ---- legado: responsável pelo aviso
        for rota in ("legado_licitacao", "legado_rdc"):
            v = R.get(rota)
            if not v or not v["resultado"]:
                continue
            if rota == "legado_rdc":
                cand = [x for x in v["resultado"] if str(x.get("uasg")) == str(uasg) and x.get("numero_aviso") == int(f"{n}{a_}")]
            else:
                cand = [x for x in v["resultado"] if x.get("id_compra") == idc]
            if not cand:
                continue
            x = cand[0]
            funil[f"{rota}_registro_exato"] += 1
            pa["legado"] += 1
            nome, cargo = (x.get("nome_responsavel") or "").strip(), (x.get("funcao_responsavel") or "").strip()
            if not nome:
                continue
            achou_resp = True
            url = (SO.B + ("/modulo-legado/7_consultarRdc" if rota == "legado_rdc" else "/modulo-legado/1.1_consultarLicitacao_Id")
                   + ("?uasg=%s&numero_aviso=%s" % (uasg, x["numero_aviso"]) if rota == "legado_rdc" else "?id_compra=" + idc))
            port = None
            pg = (R.get("legado_pregao") or {}).get("resultado") or []
            if pg:
                port = {"co_portaria": pg[0].get("co_portaria"), "dt_portaria": pg[0].get("dt_portaria")}
            dados = dict(obra_id=None, contrato_id=cid, nome=nome.title() if nome.isupper() else nome, cargo=cargo,
                         via="E3", script="e3", identificador_exato={"tipo": "id_compra", "valor": idc},
                         metodo=f"compras_gov_{rota}", orgao_uasg=uasg, url_documento=url,
                         hash_documento=D.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False)),
                         trecho_literal=f"nome_responsavel={nome}; funcao_responsavel={cargo}; numero_aviso={x.get('numero_aviso')}; uasg={uasg}",
                         data_do_vinculo=None, data_publicacao=(x.get("data_publicacao") or "")[:10] or None,
                         vinculo_documental=True, em_amostra=cid in am, portaria_designacao=port,
                         campo_da_fonte="nome_responsavel (responsável pelo aviso de licitação)")
            if eh_agente(cargo):
                pa["agente"] += 1
                funil["agente_pelo_cargo_literal"] += 1
                for oid in obras_de[cid]:
                    arestas.nova(**{**dados, "obra_id": oid}, categoria=CAT_AGENTE)
            else:
                cargos_outros[cargo] += 1
                funil["responsavel_cargo_nao_e_de_pregoeiro"] += 1
                D.registrar(outros, contrato_id=cid, licitacao=idc, nome=nome, cargo=cargo, motivo="cargo literal não é pregoeiro/agente/presidente de comissão")
            break
        # ---- dispensa/inexigibilidade
        v = R.get("legado_sem_licitacao")
        if v and v["resultado"]:
            x = v["resultado"][0]
            funil["sem_licitacao_registro"] += 1
            for campo_n, campo_c, ato in (("no_responsavel_ratificacao", "no_cargo_resp_ratificacao", "ratificação da dispensa/inexigibilidade"),
                                           ("no_responsavel_decl_disp", "no_cargo_resp_decl_disp", "declaração de dispensa")):
                if (x.get(campo_n) or "").strip():
                    achou_resp = True
                    pa["dispensa_nome"] += 1
                    # só a ratificação entra como "autoridade que homologou" (ato diferente, dito em `ato`); a declaração fica à parte
                    if campo_n.endswith("ratificacao"):
                        for oid in obras_de[cid]:
                            arestas.nova(obra_id=oid, contrato_id=cid, nome=x[campo_n].strip(), cargo=x.get(campo_c), categoria=CAT_HOMOLOG,
                                         via="E3", script="e3", identificador_exato={"tipo": "id_compra", "valor": idc},
                                         metodo="compras_gov_legado_sem_licitacao", orgao_uasg=uasg,
                                         url_documento=SO.B + "/modulo-legado/5.1_consultarCompraSemLicitacao_Id?idCompra=" + idc,
                                         hash_documento=D.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False)),
                                         trecho_literal=f"{campo_n}={x[campo_n]}; {campo_c}={x.get(campo_c)}",
                                         data_do_vinculo=(x.get("dt_ratificacao") or "")[:10] or None,
                                         data_publicacao=(x.get("dt_publicacao") or "")[:10] or None,
                                         ato=ato, em_amostra=cid in am)
                    else:
                        D.registrar(outros, contrato_id=cid, licitacao=idc, nome=x[campo_n], cargo=x.get(campo_c), motivo=ato)
        # ---- PNCP (Lei 14.133)
        p = R.get("pncp_contratacao")
        compra = p["resultado"][0] if p and p["resultado"] else None
        if compra:
            pa["pncp_compra"] += 1
            funil["pncp_compra_localizada"] += 1
            nc = compra["numeroControlePNCP"]
            m = re.fullmatch(r"(\d{14})-1-(\d+)/(\d{4})", nc)
            cnpj, seq, anoc = m[1], int(m[2]), m[3]
            if nc not in docs_vistos:
                d, st = D.pedir(f"{SO.PNCP}/orgaos/{cnpj}/compras/{anoc}/{seq}/arquivos", via="E3", intervalo=INTERVALO_PNCP)
                docs_vistos[nc] = {"lista": d or [], "lidos": {}}
            lista = docs_vistos[nc]["lista"]
            tipos = Counter(a["tipoDocumentoId"] for a in lista if a.get("statusAtivo"))
            pa["pncp_com_documento"] += bool(lista)
            pa["pncp_com_edital"] += tipos.get(2, 0) > 0
            pa["pncp_com_homologacao"] += tipos.get(26, 0) > 0
            pa["pncp_com_ata"] += (tipos.get(27, 0) + tipos.get(28, 0)) > 0
            funil["pncp_docs_edital"] += tipos.get(2, 0) > 0
            funil["pncp_docs_homologacao"] += tipos.get(26, 0) > 0
            funil["pncp_docs_ata"] += (tipos.get(27, 0) + tipos.get(28, 0)) > 0
            if not args.sem_docs and (args.docs_todos or cid in am):
                for a in lista:
                    if not a.get("statusAtivo") or a["tipoDocumentoId"] not in TIPOS_DOC:
                        continue
                    chave_doc = f"{nc}|{a['sequencialDocumento']}"
                    if chave_doc not in docs_vistos[nc]["lidos"]:
                        docs_vistos[nc]["lidos"][chave_doc] = ler_documento(a, nc)
                    leitura = docs_vistos[nc]["lidos"][chave_doc]
                    funil["pncp_documentos_lidos"] += 1 if leitura.get("baixado") else 0
                    funil["pncp_documentos_com_texto"] += 1 if leitura.get("texto") else 0
                    funil["pncp_documentos_sem_texto_digitalizado"] += 1 if leitura.get("baixado") and not leitura.get("texto") else 0
                    for s in leitura.get("pessoas", []):
                        cat = CAT_HOMOLOG if a["tipoDocumentoId"] == 26 and not eh_agente(s["cargo"]) else (CAT_AGENTE if eh_agente(s["cargo"]) or s["metodo"] == "papel_no_texto" else None)
                        if cat is None:
                            funil["pncp_assinatura_sem_categoria"] += 1
                            continue
                        achou_resp = True
                        for oid in obras_de[cid]:
                            arestas.nova(obra_id=oid, contrato_id=cid, nome=s["nome"], cargo=s["cargo"], categoria=cat, via="E3", script="e3",
                                         identificador_exato={"tipo": "numeroControlePNCP", "valor": nc, "doc": a["sequencialDocumento"]},
                                         metodo="pncp_" + s["metodo"], orgao_uasg=uasg, url_documento=a["url"],
                                         hash_documento=leitura["sha256"], trecho_literal=s["trecho"], pagina=s["pagina"],
                                         data_do_vinculo=s.get("data"), data_publicacao=(a.get("dataPublicacaoPncp") or "")[:10] or None,
                                         tipo_documento_pncp=TIPOS_DOC[a["tipoDocumentoId"]], em_amostra=cid in am)
        pa["com_algum_responsavel"] += achou_resp
        funil["contratos_com_algum_responsavel"] += achou_resp

    # ----- resumo
    res = {"alvo": "todos" if args.todos else "amostra60", "funil": dict(funil),
           "por_ano_da_licitacao": {str(k): dict(v) for k, v in sorted(por_ano.items(), key=lambda kv: str(kv[0]))},
           "cargos_literais_de_responsavel_que_nao_sao_pregoeiro": cargos_outros.most_common(25),
           "documentos_pncp_lidos": {k: {kk: vv for kk, vv in v.items() if kk != "pessoas"} for lst in docs_vistos.values() for k, v in lst["lidos"].items()}}
    (D.DIAG / f"e3_resumo_{res['alvo']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("funil", "por_ano_da_licitacao")}, ensure_ascii=False, indent=1))


def ler_documento(a, nc):
    """Baixa e lê um documento do PNCP (PDF ou ZIP). Devolve sha256, se há texto e as pessoas achadas."""
    cam = D.DIAG / "e3_docs" / (re.sub(r"\W", "_", nc) + f"_{a['sequencialDocumento']}.json")
    cam.parent.mkdir(exist_ok=True)
    if cam.exists():
        return json.loads(cam.read_text(encoding="utf-8"))
    out = {"sequencial": a["sequencialDocumento"], "tipo": a["tipoDocumentoNome"], "baixado": False, "texto": False, "pessoas": []}
    b, st = D.pedir(a["url"], via="E3", bruto=True, cache_nome=f"pncp_{re.sub(chr(92)+'W', '_', nc)}_{a['sequencialDocumento']}",
                    intervalo=INTERVALO_PNCP, timeout=60, tentativas=3)
    if not b:
        out["erro"] = st
        if st in ("404",):                       # só resposta definitiva é guardada; queda de rede é tentada de novo
            cam.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    out["baixado"] = True
    out["sha256"] = D.sha256(b)
    out["bytes"] = len(b)
    out["formato"] = "zip" if b[:2] == b"PK" else "pdf" if b[:4] == b"%PDF" else "outro"
    pessoas, membros = [], 0
    for nome_m, pb in pdfs_do_arquivo(b):
        membros += 1
        if membros > 3:
            break
        try:
            pgs, total = paginas_selecionadas(pb)
        except Exception:
            continue
        if sum(len(t) for t in pgs.values()) < 200:
            continue
        out["texto"] = True
        plano = " ".join(f"[[p{p}]] {t}" for p, t in pgs.items())
        ass, plano2, _ = E.blocos_assinatura([pgs[p] for p in sorted(pgs)])
        mapa = sorted(pgs)
        for s in ass:
            if E.externo(s):
                continue
            pagina = mapa[min(s["pagina"] - 1, len(mapa) - 1)]
            pessoas.append({"nome": s["nome"], "cargo": s["cargo"], "data": E._iso(s["data"]), "pagina": pagina,
                            "metodo": "bloco_assinatura_" + s["formato"].lower() + f"[{nome_m[:40]}]", "trecho": s["trecho"]})
        for mm in RX_NOME_APOS_PAPEL.finditer(" ".join(pgs[p] for p in mapa)):
            ctx = mm.group(0)
            pessoas.append({"nome": mm.group(2).strip(), "cargo": mm.group(1).strip(), "data": None, "pagina": None,
                            "metodo": "papel_no_texto", "trecho": ctx[:300]})
    out["pessoas"] = pessoas
    out["membros_pdf_lidos"] = membros
    cam.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
