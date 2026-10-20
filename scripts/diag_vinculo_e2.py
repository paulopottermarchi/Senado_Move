"""
Câmara Aberta — DIAGNÓSTICO E2 (e E1, parte "Publicações DOU"): signatários nos PDFs do contrato e dos termos.

Uso:
    python scripts/diag_vinculo_e2.py              # amostra de 60 contratos (a mesma de todos os E*)
    python scripts/diag_vinculo_e2.py --todos      # os 303 contratos
    python scripts/diag_vinculo_e2.py --reler      # refaz a leitura a partir dos trechos guardados

Fonte (documentada em contratos.comprasnet.gov.br/docs/api-docs.json): GET /api/contrato/{id}/arquivos
→ id, tipo, descricao, path_arquivo (PDF no storage do Contratos.gov.br), origem, link_sei. Os PDFs são
anexados pelo órgão ao contrato e a cada termo. robots.txt do host: "Disallow:" vazio (tudo permitido).

O que se faz, por PDF: baixa (0,5 s entre pedidos), calcula o sha256 do arquivo, lê o texto com pypdf página
a página, separa os que têm camada de texto dos digitalizados (SEM OCR), localiza blocos de assinatura
eletrônica (SUAP, SEI, gov.br/ICP-Brasil) com a página, extrai nome, cargo, data e hora, e aplica a regra do
piloto signatarios.py: representante do contratante = nome que aparece no bloco de assinatura E no trecho
do CONTRATANTE do preâmbulo.

Privacidade: o PDF não é guardado. Fica em cache/diag_vinculo/pdf/{id}.json só o sha256, as contagens e os
trechos (preâmbulo e blocos de assinatura) com CPF, RG, SIAPE e matrícula apagados. Quem assina pela empresa
("Usuário Externo", testemunha, procurador, sócio) só é CONTADO: o nome não é gravado em lugar nenhum.

Arestas: categoria "signatário do contrato/termo (PDF)" (anexos de tipo Contrato, Termo Aditivo, Apostilamento,
Rescisão, Reconhecimento de dívida) e "signatário do extrato (DOU)" (anexos de tipo "Publicações DOU": a página
impressa do jornal, servida pelo próprio Contratos.gov.br — não é acesso ao in.gov.br).
"""

import argparse
import io
import json
import logging
import re
import sys
import time
from collections import Counter

import pypdf

import coleta
import obras
import signatarios as S                  # regex do piloto (preâmbulo, bloco, nomeação, delegação, externo)
import diag_vinculo_comum as D

logging.getLogger("pypdf").setLevel(logging.CRITICAL)

CACHE_PDF = D.DIAG / "pdf"
TIPOS_CONTRATO = ("Contrato", "Termo Aditivo", "Termo Apostilamento", "Termo de Apostilamento", "Termo Rescisão",
                  "Termo de Rescisão", "Termo de Reconhecimento de Dívida")
TIPOS_DOU = ("Publicações DOU", "Outras Publicações")
MAX_PDF = 40 * 1024 * 1024
FFFD = "�"

# --- formatos de assinatura -------------------------------------------------------------------------
_SEI = re.compile(r"Documento assinado eletronicamente por:?\s+([^,]{3,120}?),\s*(.{2,140}?),\s*em\s+(\d{2}/\d{2}/\d{4})(?:,?\s*às\s*(\d{2}:\d{2}))?")   # nome sem vírgula; o cargo pode ter vírgula ("Diretor(a) de Unidade, Substituto(a)")
_SUAP_MARCA = re.compile(r"Documento assinado eletronicamente por:\s")
_SUAP_ITEM = re.compile(r"(.{5,200}?)\s*,\s*([^,]{2,160}?)\s*,\s*em\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})")
_GOVBR = re.compile(r"Documento assinado digitalmente\s+(?:gov\.br\s+)?([A-ZÀ-Ý][A-Za-zÀ-ÿ' .-]{5,90}?)\s+(?:Data:\s*)?(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})")
_ICP = re.compile(r"Assinado de forma digital por\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ' .-]{5,90}?)(?::\s*\d{11}|\s+Dados:|\s*$)")
_FIM_SUAP = re.compile(r"Este documento foi emi|Para comprovar|A autenticidade deste documento|Código de Autentica|Código Verificador")
_CPF_NO_NOME = re.compile(r":?\d{11}")

# cargos de agente do órgão (para aceitar signatário SEI/SUAP que não está no preâmbulo)
_EXTERNO = S._EXTERNO


def dedup_nome(s):
    """pypdf repete o texto das assinaturas SUAP/SEI, às vezes sem espaço ("Ana SilvaAna Silva")."""
    s = " ".join((s or "").split()).strip(" .,:;-")
    for _ in range(2):
        n = len(s)
        if n >= 6 and n % 2 == 0 and s[: n // 2] == s[n // 2:]:
            s = s[: n // 2].strip()
        # repetição com espaço no meio de um lado só: "Ana Silva Ana Silva" / "Ana SilvaAna Silva"
        s = S.desdobra(s)
        m = re.fullmatch(r"(.{4,}?)\s*" + chr(92) + "1", s)
        if m:
            s = m[1].strip()
    return s


def camada(chars_pag):
    """'sim' (>= 60% das páginas com texto), 'nao' (digitalizado: quase nada), 'parcial'."""
    tot = sum(chars_pag)
    if not chars_pag or tot < 200:
        return "nao"
    boas = sum(1 for c in chars_pag if c >= 100)
    return "sim" if boas / len(chars_pag) >= 0.6 else "parcial"


MOTOR = "pypdf"


def textos_por_pagina(pdf, motor=None):
    """Texto de cada página. `pypdf` é o pedido; `pymupdf` é o do piloto (signatarios.py) e serve de
    comparação: o pypdf insere espaço no meio de palavras ("Amor im") e repete trechos, o pymupdf não."""
    motor = motor or MOTOR
    if motor == "pymupdf":
        import pymupdf
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            brutos = [p.get_text() or "" for p in doc]
    else:
        r = pypdf.PdfReader(io.BytesIO(pdf))
        brutos = []
        for p in r.pages:
            try:
                brutos.append(p.extract_text() or "")
            except Exception:
                brutos.append("")
    out = []
    for t in brutos:
        t = "".join(FFFD if (ord(ch) < 32 and ch not in "\n\t\r") else ch for ch in t)
        out.append(" ".join(t.split()))
    return out


def pagina_do(offs, pos):
    p = 0
    for i, o in enumerate(offs):
        if o <= pos:
            p = i
        else:
            break
    return p + 1


def apaga(t):
    return S.apaga_pessoal(_CPF_NO_NOME.sub("", t))


def blocos_assinatura(paginas):
    """Lista de assinaturas encontradas: {formato, nome, cargo, data, hora, pagina, trecho}. Nomes de
    quem assina pela empresa vêm marcados `externo=True` e SEM o nome."""
    plano, offs = "", []
    for t in paginas:
        offs.append(len(plano))
        plano += t + " "
    achados = []
    # SUAP: um marcador e vários itens "NOME NOME, CARGO CARGO, em dd/mm/aaaa hh:mm:ss."
    for m in _SUAP_MARCA.finditer(plano):
        trecho = plano[m.end(): m.end() + 2600]
        f = _FIM_SUAP.search(trecho)
        trecho = trecho[: f.start()] if f else trecho
        for mi in _SUAP_ITEM.finditer(trecho):
            n, c, d, h = mi.groups()
            achados.append({"formato": "SUAP", "nome": dedup_nome(n), "cargo": dedup_nome(c), "data": d, "hora": h,
                            "pagina": pagina_do(offs, m.end() + mi.start()),
                            "trecho": apaga("Documento assinado eletronicamente por: " + mi.group(0))})
    # SEI: cada assinatura tem o seu "Documento assinado eletronicamente por NOME, CARGO, em data, às hora"
    suap_pos = {a["pagina"] for a in achados}
    for m in _SEI.finditer(plano):
        n, c, d, h = m.groups()
        if re.search(r"\d{2}/\d{2}/\d{4}", n):         # engoliu outra assinatura
            continue
        n, c = dedup_nome(n), dedup_nome(c)
        # o formato SUAP também casa aqui quando há ":" depois de "por"; evita duplicar
        if any(a["formato"] == "SUAP" and S.chave(a["nome"]) == S.chave(n) and a["data"] == d for a in achados):
            continue
        achados.append({"formato": "SEI", "nome": n, "cargo": c, "data": d, "hora": h,
                        "pagina": pagina_do(offs, m.start()), "trecho": apaga(plano[m.start(): m.end() + 120])})
    for m in _GOVBR.finditer(plano):
        achados.append({"formato": "gov.br", "nome": dedup_nome(m[1]), "cargo": None, "data": m[2], "hora": m[3],
                        "pagina": pagina_do(offs, m.start()), "trecho": apaga(plano[m.start(): m.end() + 100])})
    for m in _ICP.finditer(plano):
        achados.append({"formato": "ICP-Brasil", "nome": dedup_nome(_CPF_NO_NOME.sub("", m[1])), "cargo": None,
                        "data": None, "hora": None, "pagina": pagina_do(offs, m.start()),
                        "trecho": apaga(plano[m.start(): m.end() + 100])})
    # (documento datado e assinado eletronicamente) / "datado e assinado eletronicamente pelas partes": sem nome
    sinais = {
        "corpo_datado_assinado": bool(re.search(r"datado e assinado eletronicamente", plano, re.I)),
        "verificador_iti": "verificador.iti.gov.br" in plano or "ICP-Brasil" in plano,
        "sei_rodape": bool(re.search(r"controlador\.php\?acao=documento_imprimir_web", plano)),
    }
    # únicos
    vistos, unicos = set(), []
    for a in achados:
        k = (S.chave(a["nome"]), a["data"], a["cargo"] and S.chave(a["cargo"]))
        if k not in vistos:
            vistos.add(k)
            unicos.append(a)
    return unicos, plano, sinais


def externo(a):
    return bool(_EXTERNO.search(a.get("cargo") or ""))


def preambulo_e_representante(plano, assinaturas):
    """A regra do piloto: nome do bloco de assinatura que aparece no trecho do CONTRATANTE do preâmbulo."""
    pre = S.apaga_pessoal(plano[:9000])
    ancora = S._FIM_CONTRATANTE.search(pre) or re.search(r"\bCONTRATANTE\b", pre)
    if not ancora:
        return None, False
    fim_j = S._FIM_JANELA.search(pre, ancora.end())
    preambulo = pre[: fim_j.start() if fim_j else ancora.end() + 600]
    lista = S.palavras(preambulo)
    for a in assinaturas:
        if not a["nome"] or externo(a):
            continue
        pos = S.acha(a["nome"], lista)
        if pos is not None:
            janela = preambulo[pos: pos + 500]
            nome_m, deleg_m = S._NOMEACAO.search(janela), S._DELEGACAO.search(janela)
            return {"nome": a["nome"], "cargo": a["cargo"], "data": a["data"], "pagina": a["pagina"],
                    "nomeacao": S._DOC_PESSOAL.sub("", nome_m[1]).strip() if nome_m else None,
                    "delegacao": S._DOC_PESSOAL.sub("", deleg_m[1]).strip() if deleg_m else None}, True
    return None, True


def numero_no_texto(numero, plano):
    """O número do contrato (ex.: 67154/2021) aparece no texto, tolerando '67-154/2021', 'N° 67154 / 2021'."""
    m = re.fullmatch(r"0*(\d+)/(\d{4})", numero or "")
    if not m:
        return False
    n, ano = m[1], m[2]
    rx = re.compile(r"(?<!\d)" + r"\D{0,3}".join(re.escape(ch) for ch in n) + r"\s*/\s*" + ano)
    return bool(rx.search(plano))


def processar_pdf(cid, c, arq):
    """Baixa e lê um PDF. Devolve o registro do documento (gravado em cache/diag_vinculo/pdf/{id}.json)."""
    CACHE_PDF.mkdir(exist_ok=True)
    cam = CACHE_PDF / f"{arq['id']}.json"
    if cam.exists():
        return json.loads(cam.read_text(encoding="utf-8"))
    reg = {"arquivo_id": arq["id"], "contrato_id": cid, "tipo": arq.get("tipo"), "descricao": arq.get("descricao"),
           "url": arq.get("path_arquivo"), "origem": arq.get("origem")}
    t0 = time.monotonic()
    b, st = D.pedir(arq["path_arquivo"], via="E2", bruto=True, cache_nome=f"pdf_{arq['id']}", intervalo=0.5,
                    timeout=180)
    reg["http"] = st
    if b is None:
        reg["erro"] = st
        cam.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return reg
    if b[:4] != b"%PDF" or len(b) > MAX_PDF:
        reg["erro"] = "não é PDF" if b[:4] != b"%PDF" else "maior que 40 MB"
        cam.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return reg
    reg["sha256"] = D.sha256(b)
    reg["bytes"] = len(b)
    t1 = time.monotonic()
    try:
        paginas = textos_por_pagina(b)
    except Exception as e:
        reg["erro"] = f"PDF ilegível ({type(e).__name__})"
        cam.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return reg
    reg["segundos_pypdf"] = round(time.monotonic() - t1, 2)
    reg["paginas"] = len(paginas)
    reg["chars_por_pagina"] = [len(p) for p in paginas]
    reg["camada_texto"] = camada(reg["chars_por_pagina"])
    reg["hash_texto"] = D.sha256("\n".join(paginas)) if reg["camada_texto"] != "nao" else None
    if reg["camada_texto"] != "nao":
        ass, plano, sinais = blocos_assinatura(paginas)
        reg["sinais"] = sinais
        reg["numero_no_texto"] = numero_no_texto(c["numero"], plano)
        reg["processo_no_texto"] = bool(c.get("processo")) and D.so_digitos(c["processo"]) in D.so_digitos(plano)
        rep, tem_pre = preambulo_e_representante(plano, ass)
        reg["tem_preambulo_contratante"] = tem_pre
        reg["representante"] = rep
        # Guarda nome só de quem é do órgão com segurança: bloco SUAP/SEI sem marca de externo, ou casado com o
        # preâmbulo. Assinatura ICP-Brasil/gov.br fora do preâmbulo pode ser da empresa: só CONTADA, sem nome.
        chave_rep = S.chave(rep["nome"]) if rep else None
        reg["assinaturas"] = [a for a in ass if not externo(a)
                              and (a["formato"] in ("SUAP", "SEI") or S.chave(a["nome"]) == chave_rep)]
        reg["indeterminadas_nao_gravadas"] = sum(1 for a in ass if not externo(a) and a not in reg["assinaturas"])
        reg["externos"] = sum(1 for a in ass if externo(a))         # só a contagem
        reg["formatos"] = sorted({a["formato"] for a in ass})
        # extrato do DOU (anexo "Publicações DOU"): o que vem depois de "Data de Assinatura" no extrato do contrato
        if arq.get("tipo") in TIPOS_DOU:
            reg["dou"] = extrato_dou(plano, c)
    cam.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return reg


def extrato_dou(plano, c):
    """Localiza o extrato do contrato na página do jornal (pelo número do contrato e/ou o processo) e vê o que
    vem depois dele: assinatura (nome + cargo) ou só o selo '(COMPRASNET 4.0 - dd/mm/aaaa)'."""
    num = c["numero"]
    m = re.fullmatch(r"0*(\d+)/(\d{4})", num or "")
    cand = []
    if m:
        n = m[1]
        for mm in re.finditer(r"EXTRATO D[EO][^.]{0,60}?\bN[º°o]?\s*\.?\s*(\d+)\s*/\s*(\d{4})\s*-\s*UASG\s*(\d+)", plano, re.I):
            cand.append(mm)
    proc = D.so_digitos(c.get("processo"))
    achado = None
    for mm in re.finditer(r"EXTRATO D[EO]", plano, re.I):
        trecho = plano[mm.start(): mm.start() + 3500]
        prox = re.search(r"EXTRATO D[EO]|AVISO D[EO]|DESPACHO|PORTARIA", trecho[20:])
        corpo = trecho[: prox.start() + 20] if prox else trecho
        n_ok = numero_no_texto(num, corpo)
        p_ok = bool(proc) and proc in D.so_digitos(corpo)
        if n_ok or p_ok:
            achado = {"numero_confere": n_ok, "processo_confere": p_ok, "corpo": corpo}
            break
    if not achado:
        return {"extrato_localizado": False}
    corpo = achado["corpo"]
    selo = re.search(r"\(\s*(COMPRASNET[^)]*|SICON[^)]*|SIDEC[^)]*|SIASG[^)]*)\)", corpo, re.I)
    depois = corpo[selo.end():].strip(" .") if selo else ""
    depois = re.sub(r"Este conte[úu]do n[ãa]o substitui o publicado na vers[ãa]o certi\S+\.?", "", depois).strip(" .")
    # O que vem logo depois do selo: o cabeçalho do ato seguinte da mesma página ("RESULTADO DE JULGAMENTO…",
    # "MINISTÉRIO…") ou uma assinatura "NOME EM CAIXA ALTA + cargo". Só a segunda conta.
    inicio = depois[:200]
    proximo_ato = bool(re.match(r"(?:EXTRATO|AVISO|RESULTADO|PORTARIA|DESPACHO|TERMO|ATA|EDITAL|COMUNICADO|ERRATA|RETIFICA|MINIST[ÉE]RIO|UNIVERSIDADE|"
                                r"FUNDA[ÇC][ÃA]O|SUPERINTEND|DEPARTAMENTO|HOSPITAL|INSTITUTO|CONSELHO|COMISS|SECRETARIA|CENTRO|UNIDADE|ÓRG[ÃA]O|PREFEITURA|"
                                r"EMPRESA|AG[ÊE]NCIA|POL[ÍI]CIA|DIRETORIA|COORDENA|ESCOLA|CAMPUS|CÂMARA|TRIBUNAL|[A-ZÀ-Ý]{3,}\s+[A-ZÀ-Ý]{3,}\s+[A-ZÀ-Ý]{3,})", inicio))         and not re.match(r"[A-ZÀ-Ý]{3,}(?:\s+(?:D[AEO]S?\s+)?[A-ZÀ-Ý]{2,}){1,5}\s+(?:Reitor|Diretor|Pr[óo]-Reitor|Coordenador|Chefe|Superintendente|Secret|Gestor|Presidente|Pregoeiro|Ordenador)", inicio)
    assinatura = bool(depois) and not proximo_ato and bool(re.match(
        r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'.-]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][A-Za-zÀ-ÿ'.-]+){1,5}\s+(?:Reitor|Diretor|Pr[óo]-Reitor|Coordenador|Chefe|Superintendente|Secret|Gestor|Presidente|Pregoeiro|Ordenador)", inicio, re.I))
    return {"extrato_localizado": True, "numero_confere": achado["numero_confere"],
            "processo_confere": achado["processo_confere"], "selo": selo[1] if selo else None,
            "texto_depois_do_selo": depois[:200], "tem_assinatura": assinatura}


# --- arestas -----------------------------------------------------------------------------------------

def gerar_arestas(arestas, cid, c, obra_ids, reg, arq, em_amostra):
    """Converte o registro do documento em arestas. Só agentes do órgão: o representante confirmado e os demais
    signatários SEI/SUAP não-externos; ICP/gov.br só se casarem com o preâmbulo (senão podem ser da empresa)."""
    if reg.get("erro") or reg.get("camada_texto") == "nao":
        return 0
    n = 0
    uasg = c.get("uasg")
    if arq.get("tipo") in TIPOS_DOU:
        # "Publicações DOU": o extrato do Comprasnet termina no selo, sem assinatura (medido). "Outras Publicações" traz
        # documentos internos (portaria de designação de fiscais, edital): outro tipo de ato, fora das 5 categorias.
        # Só entraria aresta "signatário do extrato (DOU)" se a assinatura estivesse DENTRO do extrato localizado.
        dou = reg.get("dou") or {}
        if not (dou.get("extrato_localizado") and dou.get("tem_assinatura")):
            return 0
    cat = "signatário do extrato (DOU)" if arq.get("tipo") in TIPOS_DOU else "signatário do contrato/termo (PDF)"
    rep = reg.get("representante")
    chaves_rep = {S.chave(rep["nome"])} if rep else set()
    ident = ({"tipo": "numero_contrato_no_documento", "valor": c["numero"]} if reg.get("numero_no_texto")
             else {"tipo": "contrato_id_na_api_arquivos", "valor": str(cid)})
    for a in reg.get("assinaturas", []):
        eh_rep = S.chave(a["nome"]) in chaves_rep
        if a["formato"] in ("gov.br", "ICP-Brasil") and not eh_rep:
            continue                  # sem cargo e fora do preâmbulo: pode ser da empresa; só conta
        for oid in obra_ids:
            ar = arestas.nova(
                obra_id=oid, contrato_id=cid, nome=a["nome"], cargo=a["cargo"], categoria=cat,
                via="E1" if arq.get("tipo") in TIPOS_DOU else "E2", script="e2",
                identificador_exato={**ident, "arquivo_id": arq["id"], "processo": c.get("processo")},
                metodo=("pdf_preambulo_e_assinatura_" if eh_rep else "pdf_bloco_assinatura_") + a["formato"].lower(),
                orgao_uasg=uasg, url_documento=arq["path_arquivo"], hash_documento=reg["sha256"],
                trecho_literal=a["trecho"], pagina=a["pagina"], data_do_vinculo=_iso(a["data"]),
                data_publicacao=None, vinculo_documental=True,
                papel_no_documento=("representante do contratante (preâmbulo + assinatura)" if eh_rep
                                    else "outro signatário do órgão (bloco de assinatura)"),
                em_amostra=em_amostra, tipo_arquivo=arq.get("tipo"),
                nomeacao_citada=rep.get("nomeacao") if eh_rep and rep else None,
                delegacao_citada=rep.get("delegacao") if eh_rep and rep else None)
            if ar is not None:
                n += 1
    return n


def _iso(d):
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--reler", action="store_true")
    ap.add_argument("--motor", choices=("pypdf", "pymupdf"), default="pypdf")
    args = ap.parse_args()
    global MOTOR, CACHE_PDF
    MOTOR = args.motor
    CACHE_PDF = D.DIAG / f"pdf_{MOTOR}"
    sys.stdout.reconfigure(encoding="utf-8")

    if args.reler:
        for f in CACHE_PDF.glob("*.json"):
            f.unlink()
    cont, obras_de, _ = D.universo()
    am = set(D.amostra())
    alvo = sorted(cont) if args.todos else sorted(am)
    arestas = D.Arestas()
    # as arestas de E2 são regeradas por inteiro a cada execução (a regra do leitor pode ter mudado)
    antigas = [a for a in D.ler_arestas() if a.get("script") != "e2"]
    D.reescrever_arestas(antigas)
    arestas = D.Arestas()

    funil = Counter()
    docs_regs = []
    t_ini = time.monotonic()
    for n, cid in enumerate(alvo, 1):
        c = cont[cid]
        arqs = coleta.ler_cache(obras.CACHE_O / "arquivos" / f"{cid}.json")
        if arqs is None:
            arqs, st = D.pedir(f"{obras.CT}/contrato/{cid}/arquivos", via="E2", intervalo=0.5)
            arqs = arqs or []
            coleta.gravar_cache(obras.CACHE_O / "arquivos" / f"{cid}.json", arqs)
        funil["contratos_tentados"] += 1
        funil["contratos_com_algum_arquivo"] += bool(arqs)
        doc_c = [a for a in arqs if a.get("tipo") in TIPOS_CONTRATO and a.get("path_arquivo")]
        doc_d = [a for a in arqs if a.get("tipo") in TIPOS_DOU and a.get("path_arquivo")]
        funil["contratos_com_pdf_contrato_ou_termo"] += bool(doc_c)
        funil["contratos_com_anexo_publicacao_dou"] += bool(doc_d)
        ok_c = ok_t = ok_a = ok_r = False
        for arq in doc_c + doc_d:
            reg = processar_pdf(cid, c, arq)
            reg["_em_amostra"] = cid in am
            docs_regs.append(reg)
            gerar_arestas(arestas, cid, c, obras_de[cid], reg, arq, cid in am)
            if arq in doc_c:
                ok_c = ok_c or "erro" not in reg
                ok_t = ok_t or reg.get("camada_texto") in ("sim", "parcial")
                ok_a = ok_a or bool(reg.get("assinaturas"))
                ok_r = ok_r or bool(reg.get("representante"))
        funil["contratos_com_pdf_baixado"] += ok_c
        funil["contratos_com_texto_extraivel"] += ok_t
        funil["contratos_com_assinatura_de_agente_do_orgao"] += ok_a
        funil["contratos_com_representante_confirmado"] += ok_r
        if n % 20 == 0:
            print(f"   {n}/{len(alvo)} contratos · {len(docs_regs)} documentos · {time.monotonic() - t_ini:.0f}s", flush=True)

    docs = [d for d in docs_regs]
    dc = [d for d in docs if d["tipo"] in TIPOS_CONTRATO]
    dd = [d for d in docs if d["tipo"] in TIPOS_DOU]
    def conta(lista):
        return {"documentos": len(lista), "baixados": sum(1 for d in lista if "erro" not in d),
                "erros": dict(Counter(d["erro"] for d in lista if "erro" in d)),
                "camada_texto": dict(Counter(d.get("camada_texto") for d in lista if "erro" not in d)),
                "com_assinatura_de_agente": sum(1 for d in lista if d.get("assinaturas")),
                "com_representante_confirmado": sum(1 for d in lista if d.get("representante")),
                "com_preambulo_contratante": sum(1 for d in lista if d.get("tem_preambulo_contratante")),
                "numero_do_contrato_no_texto": sum(1 for d in lista if d.get("numero_no_texto")),
                "formatos": dict(Counter(f for d in lista for f in d.get("formatos", []))),
                "assinaturas_de_externos_contadas": sum(d.get("externos", 0) for d in lista),
                "assinaturas_indeterminadas_so_contadas": sum(d.get("indeterminadas_nao_gravadas", 0) for d in lista)}
    res = {"motor": MOTOR, "alvo": "todos" if args.todos else "amostra60", "funil_contratos": dict(funil),
           "pdf_contrato_termo": conta(dc), "pdf_publicacao_dou": conta(dd),
           "segundos_download_medio": None}
    tempos = [json.loads(l) for l in D.TEMPOS.read_text(encoding="utf-8").splitlines() if '"E2"' in l]
    ok_t = [t["s"] for t in tempos if isinstance(t.get("status"), int) and t["status"] == 200]
    if ok_t:
        res["segundos_download_medio"] = round(sum(ok_t) / len(ok_t), 2)
        res["requisicoes_pdf"] = len(ok_t)
    dou_loc = [d["dou"] for d in dd if d.get("dou")]
    res["dou_pagina_via_contratos_gov"] = {
        "paginas_baixadas": sum(1 for d in dd if "erro" not in d),
        "com_camada_de_texto": sum(1 for d in dd if d.get("camada_texto") in ("sim", "parcial")),
        "extrato_localizado_por_numero_ou_processo": sum(1 for x in dou_loc if x.get("extrato_localizado")),
        "extrato_com_assinatura_depois_do_selo": sum(1 for x in dou_loc if x.get("tem_assinatura")),
        "selos": dict(Counter((x.get("selo") or "sem selo")[:14] for x in dou_loc if x.get("extrato_localizado")))}
    (D.DIAG / f"e2_resumo_{res['alvo']}_{MOTOR}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (D.DIAG / f"e2_documentos_{res['alvo']}_{MOTOR}.json").write_text(
        json.dumps([{k: v for k, v in d.items() if k not in ("assinaturas", "representante")} | {
            "n_assinaturas_orgao": len(d.get("assinaturas", [])), "tem_representante": bool(d.get("representante"))}
                    for d in docs], ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"{time.monotonic() - t_ini:.0f} s no total")


if __name__ == "__main__":
    main()
