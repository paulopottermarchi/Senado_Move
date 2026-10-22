"""
Câmara Aberta — "Pessoas documentadas" de cada obra, por três vias e nada além delas.

  E2  assinou pelo órgão contratante: o REPRESENTANTE que assina o contrato ou o termo, lido do PDF anexado
      ao contrato (Contratos.gov.br, /api/contrato/{id}/arquivos). Só entra se o nome está no bloco de
      assinatura E no trecho do CONTRATANTE do preâmbulo, e se o número do contrato (ou o processo) consta no
      texto do PDF. Testemunhas e demais signatários não entram, e os nomes deles não são guardados.
  E3  responsável registrado no aviso de licitação (Compras.gov.br, módulo legado), por licitação + UASG, com
      nome e cargo literais da fonte. Não é pregoeiro, nem homologação, nem quem decidiu.
  E8  responsável registrado no contrato (/api/contrato/{id}/responsaveis): nome só do gestor; fiscais, só a
      função e a contagem.

Uso (a partir da raiz; roda depois de obras.py):
    python scripts/pessoas.py --dry-run     # roda tudo, escreve só em cache/, mostra o relatório e o diff
    python scripts/pessoas.py               # idem, e deixa o candidato em cache/pessoas_candidato.json
    python scripts/pessoas.py --publicar    # além disso grava site/dados/obras_pessoas.json (só se as checagens passarem)
    python scripts/pessoas.py --vias e2,e8  # as vias que entram no candidato e na publicação (padrão: e2,e8; o E3 está desligado)
    python scripts/pessoas.py --amostra     # regrava cache/amostra_fase1.csv (por padrão NÃO regrava: é a amostra já conferida)
    python scripts/pessoas.py --so-montar   # sem rede: remonta tudo do cache
    python scripts/pessoas.py --limite 20   # só os 20 primeiros contratos (teste)
    python scripts/pessoas.py --revalidar   # baixa os PDFs de novo e confere o hash
A PUBLICAÇÃO É DESLIGADA POR PADRÃO: sem --publicar este script nunca escreve em site/.

Regras (as do pedido, e do CLAUDE.md, "Obras"):
  - vínculo só por identificador exato (contrato, processo, licitação + UASG); nome isolado nunca prova vínculo;
  - lacuna fica lacuna: "—" com o motivo, nunca estimativa;
  - cada vínculo é uma aresta (cache/pessoas_arestas.jsonl, inclusive as retidas, com o motivo): fonte, url do
    documento, página, hash, identificador que casou, data do vínculo (separada da de publicação), trecho literal
    (até 300 caracteres) e método; as categorias nunca são equivalentes;
  - só agente público no exercício da função; sem CPF, RG ou SIAPE; quem assina pela empresa só é contado; testemunha
    e "outro signatário do órgão" ficam fora e sem nome; nome de fiscal não é publicado; nome com "�" não é publicado;
  - E2, filtro conservador (20/10/2026, sem conferência manual adicional): o nome só é publicado se (a) for IGUAL ao literal do documento,
    sem reconstrução; (b) uma segunda leitura, com outro leitor de PDF (pdfminer.six), concordar com o nome; (c) o cargo estiver preenchido.
    O resto fica retido, com o motivo (nome_reconstruido, leitores_divergem, sem_cargo). O cargo, ao contrário do nome, ainda pode ser
    refeito: pelo mesmo órgão e, para "Substituto(a)", por um vocabulário fechado (`VOCABULARIO_CARGO`);
  - `entradas/pessoas_supressoes.json` (lista de aresta_id) retira uma aresta da publicação, aplicado antes de publicar;
  - o PDF não fica em disco: é lido na memória; no cache ficam só os trechos e as contagens (sem CPF/RG/SIAPE).

Cache incremental (cache/pessoas/): um arquivo por arquivo_id de PDF (com o sha256) e um por licitação. Só contrato
ou termo novo é baixado; PDF com erro definitivo (404) é tentado de novo depois de 7 dias; falha de rede não é
guardada (a rodada seguinte tenta). Mudou o leitor (P.VERSAO_LEITOR): os PDFs são lidos de novo.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests

import coleta
import obras
import pessoas_pdf as P

SAIDA = coleta.DADOS / "obras_pessoas.json"
CACHE_P = coleta.CACHE / "pessoas"
ARESTAS = coleta.CACHE / "pessoas_arestas.jsonl"
CANDIDATO = coleta.CACHE / "pessoas_candidato.json"
AMOSTRA = coleta.CACHE / "amostra_fase1.csv"
RELATORIO = coleta.CACHE / "pessoas_relatorio.txt"
ESTADO = CACHE_P / "estado.json"
VERSAO = "pessoas-2"
SUPRESSOES = coleta.ENTRADAS / "pessoas_supressoes.json"
VIAS_PADRAO = ("E2", "E8")     # o E3 (aviso de licitação) está desligado: só 5 das 20 linhas foram conferíveis, nenhuma recusada, 15 n/c
COMPRAS = "https://dadosabertos.compras.gov.br"
INTERVALO_PDF = 0.5            # como em obras.py para o Contratos.gov.br
INTERVALO_COMPRAS = 1.0
VALIDADE_ERRO = timedelta(days=7)
VALIDADE_COMPRA = timedelta(days=30)
QUEDA_MAXIMA = 0.20            # a rodada falha se uma via perder mais que isso das pessoas da rodada anterior

CAT_E2 = "assinou pelo órgão contratante (representante, contrato ou termo em PDF)"
CAT_E3 = "responsável registrado no aviso de licitação (Compras.gov.br)"
CAT_E8 = "responsável registrado no contrato (registro do sistema)"

MOTIVOS = {   # texto que a interface mostra no lugar do nome ("—")
    "sem_arquivo": "o contrato não tem arquivo anexado no Contratos.gov.br",
    "sem_contrato_ou_termo": "o contrato tem anexos, mas nenhum é o contrato ou um termo em PDF",
    "pdf_indisponivel": "o PDF anexado não pôde ser lido (indisponível ou corrompido no sistema)",
    "escaneado": "o PDF é imagem, sem texto (não há leitura por OCR)",
    "sem_representante_no_documento": "o texto do PDF não traz o representante do órgão ao mesmo tempo no preâmbulo e no bloco de assinatura",
    "identificador_nao_localizado": "o número do contrato (nem o processo) não consta no texto do PDF",
    "nome_ilegivel": "o nome saiu corrompido do PDF e não foi possível reconciliar",
    "pendente": "ainda não lido (falha de rede nesta rodada)",
    "sem_registro_na_fonte": "o Compras.gov.br não tem registro desta licitação",
    "ano_nao_coberto": "licitação de 2025 em diante: o Compras.gov.br não traz o nome do responsável neste aviso (a licitação consta do PNCP, que não foi consultado para nomes)",
    "sem_nome_no_registro": "o registro da licitação existe, mas sem nome de responsável",
    "fora_do_escopo": "dispensa e inexigibilidade: fora desta fase",
    "sem_registro_de_responsavel": "nenhum responsável cadastrado no Contratos.gov.br para este contrato",
    "sem_gestor": "há fiscais cadastrados, mas nenhum gestor",
    "nome_reconstruido": "o texto do PDF veio com letra perdida e o nome só poderia ser reconstruído; só se publica o nome igual ao do documento",
    "leitores_divergem": "dois leitores de PDF diferentes não concordaram com o nome; sem concordância o nome não é publicado",
    "segunda_leitura_indisponivel": "a segunda leitura do PDF não pôde ser feita; sem ela o nome não é publicado",
    "sem_cargo": "o nome está no documento, mas o cargo não saiu legível; sem cargo o nome não é publicado",
    "suprimida_manualmente": "retirado da publicação por revisão manual",
    "via_desligada": "esta fonte está desligada nesta fase",
}
# Retenções novas do E2, na ordem em que a página escolhe o motivo mostrado quando um contrato fica sem ninguém.
ORDEM_RETENCAO_E2 = ("nome_ilegivel", "nome_reconstruido", "leitores_divergem", "segunda_leitura_indisponivel", "sem_cargo",
                     "suprimida_manualmente", "identificador_nao_localizado")
# Palavras de cargo que a ligadura perdida estraga ("Subs�tuto(a)") e que se refazem sem outro documento: vocabulário FECHADO e curto.
# Qualquer outra palavra estragada continua exigindo a grafia completa do mesmo órgão; sem ela, o cargo fica vazio (e o nome, retido).
VOCABULARIO_CARGO = ("Substituto(a)", "Substituto", "Substituta")

MESES = {m: i for i, m in enumerate(["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                                     "setembro", "outubro", "novembro", "dezembro"], 1)}


# ---------------------------------------------------------------- utilidades

def agora():
    return datetime.now(obras.BRASILIA).isoformat(timespec="seconds")


def sha(x):
    return hashlib.sha256(x if isinstance(x, bytes) else x.encode("utf-8")).hexdigest()


def velho(quando, validade):
    try:
        return datetime.now(obras.BRASILIA) - datetime.fromisoformat(quando) > validade
    except (TypeError, ValueError):
        return True


def pessoa_id(nome, uasg):
    return sha(f"{P.chave(nome or '')}|{uasg}")[:16]


def exibicao(nome):
    """Nome tudo em caixa alta (como o Comprasnet e o Contratos.gov.br gravam) sai em caixa de título para ler;
    o literal da fonte continua no campo `nome`."""
    if not nome or not nome.isupper():
        return nome
    peq = {"DE", "DA", "DO", "DAS", "DOS", "E"}
    return " ".join(w.lower() if w in peq else w.title() for w in nome.split())


def numero_ano(lic):
    m = re.fullmatch(r"0*(\d+)/(\d{4})", lic or "")
    return (int(m[1]), int(m[2])) if m else (None, None)


_ultimo = defaultdict(float)


def baixar(url):
    """(bytes, estado): PDF na memória, nunca em disco. 404, 'nao_pdf' e 'grande' são definitivos; 'rede' e 'http_5xx' não."""
    host = url.split("/")[2]
    for tentativa in range(2):
        espera = INTERVALO_PDF - (time.monotonic() - _ultimo[host])
        if espera > 0:
            time.sleep(espera)
        _ultimo[host] = time.monotonic()
        try:
            r = obras.sessao.get(url, timeout=180)
        except requests.RequestException:
            time.sleep(5)
            continue
        if r.status_code == 404:
            return None, "404"
        if r.status_code != 200:
            time.sleep(5)
            continue
        if len(r.content) > P.MAX_PDF:
            return None, "grande"
        if r.content[:4] != b"%PDF":
            return None, "nao_pdf"
        return r.content, "ok"
    return None, "rede"


# ---------------------------------------------------------------- E2: PDF do contrato e dos termos

def listar_arquivos(cid, so_montar):
    cam = obras.CACHE_O / "arquivos" / f"{cid}.json"
    arqs = obras.fresco(cam, False)
    if arqs is None:
        if so_montar:
            return coleta.ler_cache(cam)
        try:
            arqs = obras.pedir(f"{obras.CT}/contrato/{cid}/arquivos", intervalo=INTERVALO_PDF) or []
        except RuntimeError:
            return coleta.ler_cache(cam)          # a API cai por minutos às vezes: vale o antigo, se houver
        coleta.gravar_cache(cam, arqs)
    return arqs


def ler_pdf(arq, c, args, est):
    """Registro do PDF (cache/pessoas/pdf/{arquivo_id}.json). None = ainda não lido (falha de rede ou --so-montar)."""
    cam = CACHE_P / "pdf" / f"{arq['id']}.json"
    rec = coleta.ler_cache(cam)
    # com representante, o registro só está em dia se já tem a segunda leitura (do pdfminer atual); senão o PDF é baixado de novo
    com_segunda = not (rec and rec.get("representantes")) or rec.get("leitor2") == P.leitor2_id()
    em_dia = (rec and rec.get("leitor") == P.VERSAO_LEITOR and rec.get("url") == arq["path_arquivo"] and com_segunda
              and not (rec.get("erro") and velho(rec.get("consultado_em"), VALIDADE_ERRO)))
    if args.so_montar or (em_dia and not args.revalidar):
        est["pdf_em_cache"] += bool(rec)
        return rec
    b, estado = baixar(arq["path_arquivo"])
    registro = {"arquivo_id": arq["id"], "tipo": arq.get("tipo"), "descricao": arq.get("descricao"),
                "url": arq["path_arquivo"], "leitor": P.VERSAO_LEITOR, "consultado_em": agora()}
    if b is None:
        if estado == "rede":
            est["pdf_falha_de_rede"] += 1
            return rec                      # mantém o que havia; a próxima rodada tenta de novo
        novo = dict(registro, erro=estado)
        coleta.gravar_cache(cam, novo)
        est["pdf_erro_" + estado] += 1
        return novo
    est["pdf_baixados"] += 1
    est["bytes_baixados"] += len(b)
    h = sha(b)
    if rec and not rec.get("erro") and rec.get("sha256") == h and rec.get("leitor") == P.VERSAO_LEITOR and com_segunda:
        rec["consultado_em"] = agora()      # --revalidar: o mesmo arquivo; não precisa ler de novo
        coleta.gravar_cache(cam, rec)
        est["pdf_revalidado_igual"] += 1
        return rec
    novo = dict(registro, sha256=h, bytes=len(b))
    try:
        t0 = time.monotonic()
        novo.update(P.analisar(b, c["numero"], c.get("processo")))
        est["segundos_leitura"] += time.monotonic() - t0
        t1 = time.monotonic()
        P.segunda_leitura(b, novo)
        est["segundos_leitura2"] += time.monotonic() - t1
    except Exception as e:                  # PDF corrompido
        novo["erro"] = f"pdf_ilegivel:{type(e).__name__}"
    del b                                   # o PDF não fica em lugar nenhum
    coleta.gravar_cache(cam, novo)
    return novo


def _sem_degradadas(tabela):
    """Tira de cada contador (por UASG) as grafias de que existe, no mesmo órgão, uma versão completa."""
    for cont in tabela.values():
        for c in [c for c in cont if any(P.degradada(c, d) for d in cont)]:
            del cont[c]


def corpus_de(registros):
    """Grafias legíveis dos representantes, por UASG, para reconciliar nome e cargo. As versões estragadas pela ligadura
    (aquelas de que existe, no mesmo órgão, uma grafia completa: "Basta" × "Batista") não entram: só valem as completas."""
    nomes, cargos = defaultdict(Counter), defaultdict(Counter)
    for uasg, rec in registros:
        for r in rec.get("representantes", []):
            if not P.tem_ilegivel(r["nome"]):
                nomes[uasg][r["nome"]] += 1
            if r.get("cargo") and not P.tem_ilegivel(r["cargo"]):
                cargos[uasg][r["cargo"]] += 1
    _sem_degradadas(nomes)
    _sem_degradadas(cargos)
    return nomes, cargos


def _unica(texto, candidatos):
    """A ÚNICA grafia legível (sem acento e caixa, mesma chave) que casa com `texto`; ou None. Vale a mais frequente."""
    grupos = defaultdict(Counter)
    for cand, n in candidatos.items():
        if P.casa(texto, cand):
            grupos[P.chave(cand)][cand] += n
    if len(grupos) != 1:
        return None
    return max(next(iter(grupos.values())).items(), key=lambda kv: (kv[1], len(kv[0])))[0]


def _completa(texto, candidatos):
    """(grafia completa, ambígua?): a ÚNICA grafia de `candidatos` de que `texto` é versão estragada, quando a ligadura se perdeu
    SEM rastro ("Silmario Basta dos Santos" → "Silmario Batista dos Santos"). Duas possibilidades diferentes: ambígua."""
    achadas = {c: n for c, n in candidatos.items() if P.degradada(texto, c)}
    if len({P.chave(c) for c in achadas}) > 1:
        return None, True
    return (max(achadas.items(), key=lambda kv: (kv[1], len(kv[0])))[0], False) if achadas else (None, False)


def _limpa_funcao(f):
    """A função como o preâmbulo a escreve, sem o resto da frase ("..., o Senhor"); vazia se sobrou só tratamento."""
    f = re.sub(r"[,\s]+(?:o|a)?\s*Sr[a.]*\s*$|[,\s]+(?:o|a)\s+Senhor(?:a)?\s*$", "", (f or "").strip(), flags=re.I).strip(" ,")
    # o PDF as vezes parte a palavra antes de "acao" ("Administr acao"): so junta quando a palavra anterior termina em consoante
    saida = []
    for w in f.split(" "):
        if saida and w in ("ação", "ações") and len(saida[-1]) >= 6 and saida[-1][-1].isalpha() and saida[-1][-1].lower() not in "aeiouáéíóúãõâêôàç":
            saida[-1] += w
        else:
            saida.append(w)
    f = " ".join(saida)
    return None if not f or P.chave(f) in ("SENHOR", "SENHORA", "SR", "SRA") else f


def corpus_funcoes(registros):
    """Grafias limpas da função do preâmbulo, por UASG (sem as estragadas pela ligadura)."""
    fun = defaultdict(Counter)
    for uasg, rec in registros:
        for r in rec.get("representantes", []):
            f = _limpa_funcao(r.get("funcaoPreambulo"))
            if f and not P.tem_ilegivel(f):
                fun[uasg][f] += 1
    _sem_degradadas(fun)
    return fun


def reconciliar_funcao(rep, uasg, funcoes):
    """A função para publicar, ou None. Vale só texto limpo: ligadura estragada é trocada pela grafia completa do mesmo órgão
    (ou a função não sai); espaço partido ("Administr ação") vira a grafia inteira se ela existir; letra maiúscula no meio
    da palavra ("LogísAca") é sinal de ligadura estragada e a função não sai."""
    f = _limpa_funcao(rep.get("funcaoPreambulo"))
    if not f or P.tem_ilegivel(f) or not re.fullmatch(r"[A-Za-zÀ-ÿ ,-]+", f):
        return None
    cont = funcoes.get(uasg, {})
    sem_espaco = {P.chave(c).replace(" ", ""): c for c in cont}
    f = sem_espaco.get(P.chave(f).replace(" ", ""), f)
    comp, ambigua = _completa(f, cont)
    if ambigua:
        return None
    if comp:
        return comp
    if re.search(r"[a-zà-ÿ][A-ZÀ-Ý]", f):
        return None
    return f


def reconciliar_nome(rep, uasg, nomes):
    """(nome para publicar ou None, como foi reconciliado). Ordem pedida: 1º a grafia do preâmbulo do MESMO documento;
    2º outro documento do MESMO órgão (marcado). Depois, o nome é conferido contra a grafia completa do mesmo órgão, porque
    a ligadura também some sem deixar "�". Sobrou "�" ou grafia ambígua: None ("nome ilegível")."""
    nome, por = rep["nome"], None
    if P.tem_ilegivel(nome):
        g = rep.get("grafiaPreambulo")
        if g and not P.tem_ilegivel(g) and P.casa(nome, g):
            nome, por = g, "preambulo_do_mesmo_documento"
        else:
            outro = _unica(nome, nomes.get(uasg, {}))
            if not outro:
                return None, None
            nome, por = outro, "outro_documento_do_mesmo_orgao"
    comp, ambigua = _completa(nome, nomes.get(uasg, {}))
    if ambigua:
        return None, None
    if comp:
        return comp, (por + "+" if por else "") + "ligadura_perdida_sem_rastro_confirmada_por_outro_documento_do_mesmo_orgao"
    return nome, por


def _refaz_por_vocabulario(c):
    """O cargo com as palavras estragadas refeitas pelo `VOCABULARIO_CARGO`, ou None se alguma palavra estragada não está nele
    (ou casa com mais de uma). Só toca em palavra com U+FFFD; "Subs�tuto(a)" → "Substituto(a)"."""
    saida = []
    for w in c.split(" "):
        if not P.tem_ilegivel(w):
            saida.append(w)
            continue
        achadas = [v for v in VOCABULARIO_CARGO if P.palavra_igual(P._tok(w), P._tok(v))]
        if len(achadas) != 1:
            return None
        v = achadas[0]
        saida.append((v[0].lower() + v[1:] if w[:1].islower() else v) + (w[len(w.rstrip(",.;:")):] if w.rstrip(",.;:") != w else ""))
    return " ".join(saida)


def reconciliar_cargo(rep, uasg, cargos):
    c, por = rep.get("cargo"), None
    if not c:
        return None, None
    if P.tem_ilegivel(c):
        outro = _unica(c, cargos.get(uasg, {}))
        if outro:
            c, por = outro, "outro_documento_do_mesmo_orgao"
        else:
            voc = _refaz_por_vocabulario(c)
            if not voc:
                return None, None
            c, por = voc, "vocabulario_de_cargos"
    comp, ambigua = _completa(c, cargos.get(uasg, {}))
    if ambigua:
        return None, None
    return (comp, "ligadura_perdida_sem_rastro_confirmada_por_outro_documento_do_mesmo_orgao") if comp else (c, por)


# ---------------------------------------------------------------- E3: responsável pelo aviso de licitação

def id_compra(reg):
    n, ano = numero_ano(reg.get("licitacao_numero"))
    if n is None or not reg.get("unidade_compra") or not reg.get("codigo_modalidade"):
        return None
    return f"{reg['unidade_compra']}{int(reg['codigo_modalidade']):02d}{n:05d}{ano}"


def consultar_compra(reg, so_montar):
    """Aviso da licitação por identificador exato (UASG da compra + modalidade + número + ano).
    Endpoints do OpenAPI do Compras.gov.br (cache/diag_vinculo/doc_dadosabertos.compras.gov.br_v3.json):
    /modulo-legado/1.1_consultarLicitacao_Id (pregão, concorrência, tomada de preços) e /modulo-legado/7_consultarRdc."""
    idc = id_compra(reg)
    n, ano = numero_ano(reg.get("licitacao_numero"))
    if idc is None:
        return {"estado": "sem_registro_na_fonte", "ano": ano, "id_compra": None}
    mod = int(reg["codigo_modalidade"])
    if mod not in (2, 3, 5, 99):
        return {"estado": "fora_do_escopo", "ano": ano, "id_compra": idc}
    cam = CACHE_P / "compras" / f"{idc}.json"
    rec = coleta.ler_cache(cam)
    if rec and (so_montar or not velho(rec.get("consultado_em"), VALIDADE_COMPRA)):
        return rec
    if so_montar:
        return {"estado": "pendente", "ano": ano, "id_compra": idc}
    try:
        if mod == 99:
            url, params = COMPRAS + "/modulo-legado/7_consultarRdc", dict(
                uasg=reg["unidade_compra"], numero_aviso=int(f"{n}{ano}"), data_publicacao_min=f"{ano}-01-01",
                data_publicacao_max=f"{ano}-12-31", pagina=1, tamanhoPagina=50)
        else:
            url, params = COMPRAS + "/modulo-legado/1.1_consultarLicitacao_Id", dict(id_compra=idc)
        d = obras.pedir(url, params, intervalo=INTERVALO_COMPRAS)
    except RuntimeError:
        return rec or {"estado": "pendente", "ano": ano, "id_compra": idc}
    achados = (d or {}).get("resultado") or []
    if mod == 99:
        achados = [x for x in achados if str(x.get("uasg")) == str(reg["unidade_compra"]) and x.get("numero_aviso") == int(f"{n}{ano}")]
    else:
        achados = [x for x in achados if x.get("id_compra") == idc]
    q = "&".join(f"{k}={v}" for k, v in params.items() if k not in ("pagina", "tamanhoPagina"))
    novo = {"id_compra": idc, "ano": ano, "consultado_em": agora(), "url": f"{url}?{q}"}
    if not achados:
        novo["estado"] = "sem_registro_na_fonte"
    else:
        x = achados[0]
        novo.update({"estado": "ok", "nome": (x.get("nome_responsavel") or "").strip(), "cargo": (x.get("funcao_responsavel") or "").strip(),
                     "data_publicacao": (x.get("data_publicacao") or "")[:10] or None, "numero_aviso": x.get("numero_aviso"),
                     "uasg": x.get("uasg"), "hash": sha(json.dumps(x, sort_keys=True, ensure_ascii=False))})
    coleta.gravar_cache(cam, novo)
    return novo


# ---------------------------------------------------------------- E8: responsáveis do contrato

def portaria(texto):
    """{numero, data, texto} só quando o registro traz NÚMERO E DATA COMPLETA; senão None (e a página diz o que consta)."""
    t = texto or ""
    d = re.search(r"(\d{1,2})\s+de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})", t, re.I)
    iso = None
    if d and d[2].lower() in MESES:
        iso = f"{d[3]}-{MESES[d[2].lower()]:02d}-{int(d[1]):02d}"
    else:
        d2 = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
        if d2:
            iso = f"{d2[3]}-{int(d2[2]):02d}-{int(d2[1]):02d}"
    n = re.search(r"PORTARIA\s*(?:N[º°O.]*\s*)?(\d[\d./-]*[A-Za-z/-]*)", t, re.I)
    if not iso or not n:
        return None
    return {"numero": n[1].strip(" ,.-/"), "data": iso, "texto": t.strip()}


# ---------------------------------------------------------------- arestas

def aresta(**k):
    k.setdefault("publicavel", True)
    k.setdefault("motivo_retencao", None)
    base = (f"{k['contrato_id']}|{k['via']}|{k.get('pessoa_id')}|{k['identificador_exato'].get('valor')}|"
            f"{k.get('arquivo_id') or ''}|{k.get('pagina') or ''}|{k.get('data_do_vinculo') or ''}")
    k["aresta_id"] = sha(base)[:20]
    return k


def retida(via, cid, uasg, obras_do, motivo, **extra):
    """Registro de exclusão ou lacuna, SEM nome: testemunha, outro signatário, empresa, escaneado, sem arquivo…"""
    return aresta(via=via, categoria={"E2": CAT_E2, "E3": CAT_E3, "E8": CAT_E8}[via], contrato_id=cid, obras=obras_do,
                  orgao_uasg=uasg, nome=None, cargo=None, pessoa_id=None, publicavel=False, motivo_retencao=motivo,
                  identificador_exato={"tipo": "contrato_id", "valor": str(cid)}, **extra)


def carregar_supressoes(caminho=None):
    """{aresta_id: motivo} de entradas/pessoas_supressoes.json. Formato: {"suprimir": ["id", {"aresta_id": "id", "motivo": "…"}]}.
    Arquivo ausente = nenhuma. Id que não tem 20 caracteres hexadecimais, ou arquivo que não é JSON, derruba a rodada: uma supressão
    que não pega em silêncio publicaria o que se queria tirar."""
    cam = caminho or SUPRESSOES
    if not cam.exists():
        return {}
    try:
        d = json.loads(cam.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"{cam.name} não é JSON válido: {e}")
    itens = d.get("suprimir") if isinstance(d, dict) else d
    if not isinstance(itens, list):
        sys.exit(f'{cam.name}: esperado {{"suprimir": [...]}} (ou uma lista) de aresta_id')
    out = {}
    for it in itens:
        aid = it if isinstance(it, str) else (it or {}).get("aresta_id")
        if not isinstance(aid, str) or not re.fullmatch(r"[0-9a-f]{20}", aid):
            sys.exit(f"{cam.name}: aresta_id inválido: {aid!r}")
        out[aid] = (it.get("motivo") if isinstance(it, dict) else None) or "sem motivo escrito"
    return out


# ---------------------------------------------------------------- montagem

def montar(args):
    base = json.loads(obras.SAIDA.read_text(encoding="utf-8"))
    contratos, obras_de = {}, defaultdict(list)
    for o in base["obras"]:
        for c in o["contratos"]:
            contratos[str(c["id"])] = c
            obras_de[str(c["id"])].append(o["id"])
    ids = sorted(contratos)[: args.limite] if args.limite else sorted(contratos)
    est = Counter()
    fases = {}
    t0 = time.monotonic()
    print(f"{len(ids)} contratos de {len(base['obras'])} obras", flush=True)

    # 1) E2: PDFs
    por_contrato, regs_c = {}, {}
    for n, cid in enumerate(ids, 1):
        c = contratos[cid]
        cc = obras.contrato(cid, False) if not args.so_montar else (coleta.ler_cache(obras.CACHE_O / "contratos" / f"{cid}.json") or {})
        regs_c[cid] = cc
        arqs = listar_arquivos(cid, args.so_montar)
        docs = []
        if arqs is not None:
            for a in arqs:
                if a.get("tipo") in P.TIPOS_ARQUIVO and a.get("path_arquivo"):
                    docs.append((a, ler_pdf(a, c, args, est)))
        por_contrato[cid] = (arqs, docs)
        if n % 25 == 0:
            print(f"   E2 {n}/{len(ids)} contratos · {est['pdf_baixados']} PDFs baixados · {est['pdf_em_cache']} do cache · "
                  f"{time.monotonic() - t0:.0f}s", flush=True)
    fases["E2 (PDFs)"] = time.monotonic() - t0

    # 2) E3: avisos de licitação
    t1 = time.monotonic()
    compras = {}
    for n, cid in enumerate(ids, 1):
        reg = (regs_c[cid] or {}).get("registro")
        if "E3" not in args.vias:                      # via desligada: nem consulta o Compras.gov.br
            compras[cid] = {"estado": "via_desligada", "ano": numero_ano((reg or {}).get("licitacao_numero"))[1], "id_compra": None}
            continue
        compras[cid] = consultar_compra(reg, args.so_montar) if reg else {"estado": "pendente", "ano": None, "id_compra": None}
        if n % 50 == 0:
            print(f"   E3 {n}/{len(ids)} licitações · {time.monotonic() - t1:.0f}s", flush=True)
    fases["E3 (Compras.gov.br)"] = time.monotonic() - t1
    return base, contratos, obras_de, ids, por_contrato, regs_c, compras, est, fases


def montar_arestas(contratos, obras_de, ids, por_contrato, regs_c, compras, supressoes=None):
    """Aplica as regras e devolve (arestas, blocos por contrato). `supressoes`: {aresta_id: motivo} (padrão: entradas/pessoas_supressoes.json);
    a aresta suprimida fica retida (motivo `suprimida_manualmente`) e a pessoa não entra no bloco."""
    supressoes = carregar_supressoes() if supressoes is None else supressoes
    regs = [(str(contratos[cid]["uasg"]), rec) for cid in ids for _, rec in por_contrato[cid][1] if rec and not rec.get("erro")]
    nomes, cargos = corpus_de(regs)
    funcoes = corpus_funcoes(regs)
    arestas, blocos = [], {}
    for cid in ids:
        c = contratos[cid]
        uasg = str(c["uasg"])
        ob = obras_de[cid]
        reg = (regs_c[cid] or {}).get("registro") or {}
        ano = numero_ano(reg.get("licitacao_numero"))[1]
        bl = {"ano": ano}

        # ---- E2
        arqs, docs = por_contrato[cid]
        pessoas = {}
        lidos = escaneados = indisponiveis = pendentes = 0
        motivos = Counter()
        for a, rec in docs:
            if rec is None:
                pendentes += 1
                arestas.append(retida("E2", cid, uasg, ob, "pendente", arquivo_id=a["id"], url_documento=a["path_arquivo"]))
                continue
            comum = dict(arquivo_id=a["id"], tipo_arquivo=a.get("tipo"), url_documento=a["path_arquivo"], hash_documento=rec.get("sha256"))
            if rec.get("erro"):
                indisponiveis += 1
                arestas.append(retida("E2", cid, uasg, ob, "pdf_indisponivel", detalhe=rec["erro"], **comum))
                continue
            if rec["camada_texto"] == "nao":
                escaneados += 1
                arestas.append(retida("E2", cid, uasg, ob, "escaneado", paginas=rec["paginas"], **comum))
                continue
            lidos += 1
            for kind, motivo in (("testemunha", "testemunha_excluida"), ("outro_orgao", "outro_signatario_excluido"),
                                 ("empresa", "empresa_contada"), ("indeterminado", "assinatura_indeterminada_contada")):
                q = rec["contagens"].get(kind, 0)
                if q:
                    arestas.append(retida("E2", cid, uasg, ob, motivo, quantidade=q, **comum))
            if not rec["representantes"]:
                motivos["sem_representante_no_documento"] += 1
                arestas.append(retida("E2", cid, uasg, ob, "sem_representante_no_documento",
                                      tem_preambulo_contratante=rec.get("tem_preambulo_contratante"), **comum))
            ident = ({"tipo": "numero_do_contrato_no_texto", "valor": c["numero"]} if rec.get("numero_no_texto")
                     else {"tipo": "processo_no_texto", "valor": c.get("processo")} if rec.get("processo_no_texto") else None)
            for r in rec["representantes"]:
                nome_final, por = reconciliar_nome(r, uasg, nomes)
                cargo_final, cargo_por = reconciliar_cargo(r, uasg, cargos)
                funcao_final = reconciliar_funcao(r, uasg, funcoes)
                # filtro conservador do E2: cada falha é anotada; o motivo mostrado é a primeira da lista
                falhas = []
                if ident is None:
                    falhas.append("identificador_nao_localizado")
                if nome_final is None:
                    falhas.append("nome_ilegivel")
                elif nome_final != r["nome"]:
                    falhas.append("nome_reconstruido")           # (a) só vale o nome IGUAL ao literal do documento
                if r.get("leitor2_nome") is False:
                    falhas.append("leitores_divergem")           # (b) o segundo leitor tem de concordar
                elif r.get("leitor2_nome") is None:
                    falhas.append("segunda_leitura_indisponivel")
                if not cargo_final:
                    falhas.append("sem_cargo")                   # (c) cargo preenchido
                motivo = next((m for m in ("identificador_nao_localizado", "nome_ilegivel", "nome_reconstruido", "leitores_divergem",
                                           "segunda_leitura_indisponivel", "sem_cargo") if m in falhas), None)
                ar = aresta(via="E2", categoria=CAT_E2, contrato_id=cid, obras=ob, orgao_uasg=uasg,
                            nome=nome_final or r["nome"], cargo=cargo_final, pessoa_id=pessoa_id(nome_final or r["nome"], uasg),
                            data_do_vinculo=r["data"], data_publicacao=None, pagina=r["pagina"],
                            identificador_exato=ident or {"tipo": "contrato_id_na_api_arquivos", "valor": str(cid)},
                            trecho_literal=r["trecho"][:300], metodo=f"pdf_{r['formato'].lower()}+preambulo_contratante",
                            publicavel=motivo is None, motivo_retencao=motivo, nome_literal=r["nome"], nome_reconciliado_por=por,
                            cargo_literal=r["cargo"], cargo_reconciliado_por=cargo_por, funcao_no_preambulo=r.get("funcaoPreambulo"), funcao_publicada=funcao_final,
                            leitor2_nome=r.get("leitor2_nome"), leitor2_obs=r.get("leitor2_obs"), retencoes=falhas or None,
                            **comum)
                if motivo is None and ar["aresta_id"] in supressoes:
                    motivo = ar["motivo_retencao"] = "suprimida_manualmente"
                    ar["publicavel"] = False
                    ar["suprimida_porque"] = supressoes[ar["aresta_id"]]
                arestas.append(ar)
                if motivo:
                    motivos[motivo] += 1
                    continue
                p = pessoas.setdefault(P.chave(nome_final), {"nome": nome_final, "cargos": Counter(), "funcoes": Counter(), "docs": []})
                if cargo_final:
                    p["cargos"][cargo_final] += 1
                # a função que o preâmbulo dá a quem representa o órgão: só se o texto estiver limpo (a ligadura
                # perdida também vira "@", "?", ">" em alguns PDFs: "Logís@ca")
                if funcao_final:
                    p["funcoes"][funcao_final] += 1
                p["docs"].append({"tipo": a.get("tipo"), "data": r["data"], "url": a["path_arquivo"], "pagina": r["pagina"],
                                  "cargo": cargo_final, "arquivoId": a["id"], "hash": rec.get("sha256")})
        if arqs is None:
            lacuna = "pendente"
        elif not arqs:
            lacuna = "sem_arquivo"
            arestas.append(retida("E2", cid, uasg, ob, "sem_arquivo"))
        elif not docs:
            lacuna = "sem_contrato_ou_termo"
            arestas.append(retida("E2", cid, uasg, ob, "sem_contrato_ou_termo"))
        elif pessoas:
            lacuna = None
        elif pendentes and not (lidos or escaneados or indisponiveis):
            lacuna = "pendente"
        else:
            lacuna = next((m for m in ORDEM_RETENCAO_E2 if motivos[m]), None) or \
                     ("escaneado" if escaneados and not lidos else "sem_representante_no_documento" if lidos else
                      "pdf_indisponivel")
        bl["assinou"] = {
            "pessoas": [{"nome": p["nome"], "nomeExibicao": exibicao(p["nome"]),
                         "cargos": [k for k, _ in p["cargos"].most_common()], "funcoes": [k for k, _ in p["funcoes"].most_common(2)],
                         "docs": sorted(p["docs"], key=lambda d: d["data"] or "")} for p in pessoas.values()],
            "lacuna": None if lacuna is None else {"motivo": lacuna, "texto": MOTIVOS[lacuna]},
            "documentos": {"total": len(docs), "lidos": lidos, "escaneados": escaneados, "indisponiveis": indisponiveis,
                           "pendentes": pendentes}}

        # ---- E3
        comp = compras[cid]
        avisos, lacuna = [], None
        if comp["estado"] == "ok" and comp.get("nome"):
            if P.tem_ilegivel(comp["nome"]):
                lacuna = "nome_ilegivel"
            else:
                ar = aresta(via="E3", categoria=CAT_E3, contrato_id=cid, obras=ob, orgao_uasg=str(reg.get("unidade_compra") or uasg),
                            nome=comp["nome"], cargo=comp["cargo"] or None, pessoa_id=pessoa_id(comp["nome"], reg.get("unidade_compra")),
                            data_do_vinculo=None, data_publicacao=comp["data_publicacao"], pagina=None,
                            url_documento=comp["url"], hash_documento=comp["hash"],
                            identificador_exato={"tipo": "id_compra", "valor": comp["id_compra"], "numero_aviso": comp["numero_aviso"], "uasg": comp["uasg"]},
                            trecho_literal=(f"nome_responsavel={comp['nome']}; funcao_responsavel={comp['cargo']}; "
                                            f"numero_aviso={comp['numero_aviso']}; uasg={comp['uasg']}")[:300],
                            metodo="compras_gov_modulo_legado_por_id_compra")
                if ar["aresta_id"] in supressoes:
                    ar["publicavel"], ar["motivo_retencao"], ar["suprimida_porque"] = False, "suprimida_manualmente", supressoes[ar["aresta_id"]]
                    lacuna = "suprimida_manualmente"
                arestas.append(ar)
                if lacuna is None:
                    avisos.append({"nome": comp["nome"], "nomeExibicao": exibicao(comp["nome"]), "cargo": comp["cargo"] or None,
                                   "dataPublicacao": comp["data_publicacao"], "url": comp["url"], "idCompra": comp["id_compra"],
                                   "numeroAviso": comp["numero_aviso"], "hash": comp["hash"]})
        elif comp["estado"] == "ok":
            lacuna = "sem_nome_no_registro"
        elif comp["estado"] == "sem_registro_na_fonte":
            lacuna = "ano_nao_coberto" if (comp.get("ano") or 0) >= 2025 else "sem_registro_na_fonte"
        else:
            lacuna = comp["estado"]
        if lacuna and lacuna not in ("via_desligada", "suprimida_manualmente"):
            arestas.append(retida("E3", cid, uasg, ob, lacuna, id_compra=comp.get("id_compra")))
        bl["aviso"] = {"pessoas": avisos, "lacuna": None if lacuna is None else {"motivo": lacuna, "texto": MOTIVOS[lacuna]}}

        # ---- E8
        resp = (regs_c[cid] or {}).get("responsaveis")
        gestores, fiscais, outros, suprimidos8 = [], 0, Counter(), 0
        url_api = f"{obras.CT}/contrato/{cid}/responsaveis"
        for r in resp or []:
            f = r.get("funcao") or ""
            if f.startswith("Gestor"):
                nome = r.get("nome")
                if not nome or P.tem_ilegivel(nome):
                    arestas.append(retida("E8", cid, uasg, ob, "nome_ausente_ou_ilegivel", funcao=f))
                    continue
                pt = portaria(r.get("portaria"))
                trecho = f"funcao_id={f}; situacao={r.get('situacao')}; portaria={r.get('portaria')}; data_inicio={r.get('de')}; data_fim={r.get('ate')}"
                ar8 = aresta(via="E8", categoria=CAT_E8, contrato_id=cid, obras=ob, orgao_uasg=uasg, nome=nome, cargo=f,
                                      pessoa_id=pessoa_id(nome, uasg), data_do_vinculo=r.get("de"), data_publicacao=None, pagina=None,
                                      url_documento=url_api, hash_documento=sha(json.dumps(r, sort_keys=True, ensure_ascii=False)),
                                      identificador_exato={"tipo": "contrato_id", "valor": str(cid)}, trecho_literal=trecho[:300],
                                      metodo="contratos_gov_api_responsaveis", situacao=r.get("situacao"), data_fim=r.get("ate"),
                                      portaria=pt, portaria_texto=r.get("portaria"))
                if ar8["aresta_id"] in supressoes:
                    ar8["publicavel"], ar8["motivo_retencao"], ar8["suprimida_porque"] = False, "suprimida_manualmente", supressoes[ar8["aresta_id"]]
                    arestas.append(ar8)
                    suprimidos8 += 1
                    continue
                arestas.append(ar8)
                gestores.append({"nome": nome, "nomeExibicao": exibicao(nome), "funcao": f, "situacao": r.get("situacao"),
                                 "de": r.get("de"), "ate": r.get("ate"), "portaria": pt,
                                 "portariaMotivo": None if pt else (f"o registro traz «{r.get('portaria')}», sem número e data completos"
                                                                    if r.get("portaria") else "o registro não traz portaria")})
            elif f.startswith("Fiscal"):
                fiscais += 1
                outros[f] += 1
            else:
                outros[f or "função não informada"] += 1
        if fiscais or outros:
            arestas.append(retida("E8", cid, uasg, ob, "nome_nao_publicado_fiscal_ou_apoio", quantidade=fiscais + sum(v for k, v in outros.items() if not k.startswith("Fiscal")),
                                  funcoes=dict(outros)))
        lac8 = None if gestores else ("suprimida_manualmente" if suprimidos8 else "sem_registro_de_responsavel" if not resp else "sem_gestor")
        bl["registro"] = {"gestores": gestores, "fiscais": fiscais, "outros": dict(outros),
                          "url": f"https://contratos.comprasnet.gov.br/transparencia/contratos/{cid}",
                          "lacuna": None if lac8 is None else {"motivo": lac8, "texto": MOTIVOS[lac8]}}
        blocos[cid] = bl
    return arestas, blocos


# ---------------------------------------------------------------- saída, checagens, relatório

PADROES_PESSOAIS = {
    "CPF": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "CPF mascarado": re.compile(r"\*{3}\.\d{3}\.\d{3}-\*{2}"),
    "RG": re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dXx]\b"),
    "SIAPE": re.compile(r"SIAPE\s*(?:n[º°o.]*\s*)?\d{5,}", re.I),
    "11 dígitos soltos": re.compile(r"(?<![0-9A-Za-z/.-])\d{11}(?![0-9A-Za-z/.-])"),
}


VIA_BLOCO = (("E2", "assinou", "pessoas"), ("E3", "aviso", "pessoas"), ("E8", "registro", "gestores"))


def so_as_vias(blocos, vias):
    """Cópia dos blocos em que as vias desligadas ficam sem pessoa e com a lacuna `via_desligada`: o que sai (ou seria publicado) nunca
    traz nome de uma via que não foi ligada, mesmo que as arestas do cache a tenham."""
    out = {}
    for cid, b in blocos.items():
        nb = dict(b)
        for via, chave_, pess in VIA_BLOCO:
            if via not in vias:
                nb[chave_] = {**b[chave_], pess: [], "lacuna": {"motivo": "via_desligada", "texto": MOTIVOS["via_desligada"]}}
        out[cid] = nb
    return out


def saida_publicavel(contratos, ids, blocos, arestas, vias=VIAS_PADRAO):
    vias = tuple(v.upper() for v in vias)
    blocos = so_as_vias(blocos, vias)
    cob = {"contratos": len(ids)}
    por_ano = defaultdict(Counter)
    for cid in ids:
        b = blocos[cid]
        ano = str(b["ano"] or "sem ano")
        por_ano[ano]["contratos"] += 1
        for via, chave_, pess in VIA_BLOCO:
            if via not in vias:
                continue
            tem = bool(b[chave_][pess])
            por_ano[ano][via] += tem
            cob.setdefault(via, Counter())["com_pessoa" if tem else "sem"] += 1
            if not tem:
                cob.setdefault(via + "_motivos", Counter())[b[chave_]["lacuna"]["motivo"] if b[chave_]["lacuna"] else "?"] += 1
    cob = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in cob.items()}
    return {
        "_versao": VERSAO, "_geradoEm": agora(), "_vias": list(vias),
        "_regras": ("Só por identificador exato; lacuna fica lacuna; só agentes públicos no exercício da função; "
                    "testemunhas, outros signatários e quem assina pela empresa não são listados; nomes de fiscais não são publicados."),
        "_cobertura": {**cob, "porAno": {a: dict(v) for a, v in sorted(por_ano.items())}},
        "contratos": {cid: blocos[cid] for cid in ids},
    }


def checagens(saida, arestas):
    """Lista de problemas. Qualquer um derruba a rodada: nada é publicado."""
    erros = []
    for a in arestas:
        if a.get("nome") and (not a.get("url_documento") or not a.get("trecho_literal")):
            erros.append(f"aresta {a['aresta_id']} ({a['via']}, contrato {a['contrato_id']}) sem url_documento ou sem trecho literal")
    for a in arestas:
        if a["via"] == "E2" and a.get("publicavel") and a.get("nome") and a.get("motivo_retencao") is None:
            if a.get("nome") != a.get("nome_literal") or a.get("nome_reconciliado_por"):
                erros.append(f"aresta {a['aresta_id']} (E2) publicável com nome reconstruído: {a.get('nome_literal')!r} → {a.get('nome')!r}")
            if a.get("leitor2_nome") is not True:
                erros.append(f"aresta {a['aresta_id']} (E2) publicável sem a concordância do segundo leitor")
            if not a.get("cargo"):
                erros.append(f"aresta {a['aresta_id']} (E2) publicável sem cargo")
    for cid, b in saida["contratos"].items():
        for p in b["assinou"]["pessoas"] + b["aviso"]["pessoas"] + b["registro"]["gestores"]:
            for campo in ("nome", "cargo", "funcao"):
                if P.tem_ilegivel(p.get(campo)):
                    erros.append(f"contrato {cid}: {campo} com caractere ilegível no que seria publicado: {p.get(campo)!r}")
            for cg in p.get("cargos", []):
                if P.tem_ilegivel(cg):
                    erros.append(f"contrato {cid}: cargo com caractere ilegível no que seria publicado: {cg!r}")
    textos = {"obras_pessoas.json": json.dumps(saida, ensure_ascii=False),
              "pessoas_arestas.jsonl": "\n".join(json.dumps(a, ensure_ascii=False) for a in arestas)}
    for nome, t in textos.items():
        for rot, rx in PADROES_PESSOAIS.items():
            m = rx.search(t)
            if m:
                erros.append(f"padrão de {rot} em {nome}: {m.group(0)[:30]!r}")
    return erros


def contagens(saida):
    c = Counter()
    for b in saida["contratos"].values():
        c["E2"] += len(b["assinou"]["pessoas"])
        c["E3"] += len(b["aviso"]["pessoas"])
        c["E8"] += len(b["registro"]["gestores"])
    return dict(c)


def queda(saida, anterior):
    """A rodada falha se uma via LIGADA perder mais que QUEDA_MAXIMA das pessoas da anterior. Não compara rodadas de regras diferentes
    (`versao` do estado): a queda que o filtro conservador causa de propósito não é falha."""
    if not anterior or anterior.get("versao") != VERSAO:
        return []
    novo, velho_ = contagens(saida), anterior.get("pessoas", {})
    return [f"{via}: {velho_[via]} pessoas na rodada anterior, {novo.get(via, 0)} agora (queda maior que {QUEDA_MAXIMA:.0%})"
            for via in velho_ if via in saida.get("_vias", ["E2", "E3", "E8"]) and velho_[via] >= 20 and novo.get(via, 0) < velho_[via] * (1 - QUEDA_MAXIMA)]


def amostra_csv(arestas, caminho, n2=60, n3=20, semente=20261019):
    """60 arestas de E2 (estratificadas por órgão e ano) e 20 de E3. A coluna `correto` fica vazia: quem confere é você."""
    import random
    rng = random.Random(semente)

    def estratificar(lista, n, ano_de):
        grupos = defaultdict(list)
        for a in lista:
            grupos[(a["orgao_uasg"], ano_de(a))].append(a)
        for g in grupos.values():
            rng.shuffle(g)
        chaves = sorted(grupos, key=lambda k: (len(grupos[k]), str(k)))
        out = []
        while len(out) < n and any(grupos.values()):
            for k in chaves:
                if grupos[k] and len(out) < n:
                    out.append(grupos[k].pop())
        return out

    e2 = [a for a in arestas if a["via"] == "E2" and a["publicavel"] and a["nome"]]
    e3 = [a for a in arestas if a["via"] == "E3" and a["publicavel"] and a["nome"]]
    # uma aresta por (contrato, pessoa) para a amostra não repetir o mesmo vínculo
    uniq = lambda l: list({(a["contrato_id"], a["pessoa_id"]): a for a in l}.values())
    linhas = estratificar(uniq(e2), n2, lambda a: (a.get("data_do_vinculo") or "")[:4]) + \
        estratificar(uniq(e3), n3, lambda a: (a.get("data_publicacao") or "")[:4])
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["aresta_id", "via", "url_documento", "pagina", "nome", "cargo", "trecho_literal", "correto"])
        for a in linhas:
            w.writerow([a["aresta_id"], a["via"], a["url_documento"], a.get("pagina") or "", a["nome"], a.get("cargo") or "",
                        (a["trecho_literal"] or "").replace("\n", " ")[:300], ""])
    return Counter(a["via"] for a in linhas)


def diff(saida, anterior_site, anterior_cand):
    """Resumo do que mudaria em relação ao arquivo publicado (ou, se não há, ao candidato da rodada anterior). Só as vias LIGADAS entram
    na comparação: o que o candidato anterior tinha de uma via desligada não conta como "saiu"."""
    ref, rotulo = (anterior_site, "site/dados/obras_pessoas.json") if anterior_site else (anterior_cand, "candidato da rodada anterior")
    if not ref:
        return ["arquivo NOVO: nada publicado antes e nenhuma rodada anterior para comparar"]
    vias = saida.get("_vias", ["E2", "E3", "E8"])
    campos = {"E2": ("assinou", "pessoas"), "E3": ("aviso", "pessoas"), "E8": ("registro", "gestores")}

    def nomes(x, via):
        if not x:
            return set()
        k, q = campos[via]
        return {p["nome"] for p in x[k][q]}
    linhas = [f"em relação ao {rotulo} (só as vias ligadas: {', '.join(vias)}):"]
    for via in vias:
        antes = {(cid, n) for cid, r in ref["contratos"].items() for n in nomes(r, via)}
        agora_ = {(cid, n) for cid, b in saida["contratos"].items() for n in nomes(b, via)}
        linhas.append(f"   {via}: {len(antes)} nomes (por contrato) antes · {len(agora_)} agora · saem {len(antes - agora_)} · entram {len(agora_ - antes)}")
    mudou = 0
    for cid, b in saida["contratos"].items():
        r = ref["contratos"].get(cid)
        atual = {(v, n) for v in vias for n in nomes(b, v)}
        antes = {(v, n) for v in vias for n in nomes(r, v)}
        if atual != antes:
            mudou += 1
            if mudou <= 10:
                linhas.append(f"   contrato {cid}: +{len(atual - antes)} −{len(antes - atual)}")
    linhas.append(f"   {mudou} de {len(saida['contratos'])} contratos mudaram")
    return linhas


def filtro_e2(arestas):
    """Texto: o E2 por motivo de retenção do filtro conservador, e quantas arestas passariam pelo filtro antigo (só identificador e nome legível)."""
    e2 = [a for a in arestas if a["via"] == "E2" and a.get("nome")]
    antigo = [a for a in e2 if not ({"identificador_nao_localizado", "nome_ilegivel"} & set(a.get("retencoes") or []))]
    ok = [a for a in e2 if a.get("publicavel") and a.get("motivo_retencao") is None]
    por_motivo = Counter(a["motivo_retencao"] for a in e2 if a.get("motivo_retencao"))
    todos = Counter(m for a in e2 for m in (a.get("retencoes") or []))
    l2 = Counter(str(a.get("leitor2_nome")) for a in e2)
    cargos = Counter(a.get("cargo_reconciliado_por") or "literal" for a in ok)
    linhas = ["E2 — FILTRO CONSERVADOR (nome igual ao literal · segundo leitor concorda · cargo preenchido)",
              f"   representantes com nome lidos: {len(e2)} arestas · passariam pelo filtro ANTIGO: {len(antigo)} · passam pelo NOVO: {len(ok)}",
              "   retidas por motivo (o primeiro de cada aresta): " + ", ".join(f"{m} {n}" for m, n in sorted(por_motivo.items(), key=lambda kv: -kv[1])),
              "   cada falha, contada em todas as arestas que a têm: " + ", ".join(f"{m} {n}" for m, n in sorted(todos.items(), key=lambda kv: -kv[1])),
              f"   segunda leitura: confere {l2.get('True', 0)} (destes, {sum(1 for a in e2 if a.get('leitor2_obs') == 'igualdade_sem_espacos')} pela igualdade sem espaços, ancorada na assinatura)"
              f" · diverge {l2.get('False', 0)} (destes, {sum(1 for a in e2 if a.get('leitor2_obs') == 'so_substring_sem_espacos')} têm as letras no texto, mas as âncoras não fecham: retidos)"
              f" · não feita {l2.get('None', 0)}",
              "   cargo das publicáveis: " + ", ".join(f"{k} {v}" for k, v in sorted(cargos.items(), key=lambda kv: -kv[1])), ""]
    return "\n".join(linhas)


def relatorio(saida, arestas, ids, est, fases, args, erros, diff_linhas, amostra):
    L = []
    cob = saida["_cobertura"]
    vias = saida.get("_vias", ["E2", "E3", "E8"])
    L.append(f"PESSOAS DOCUMENTADAS — {saida['_geradoEm']} — {'DRY-RUN' if args.dry_run else 'rodada'}"
             f"{' (--publicar)' if args.publicar else ' (publicação desligada)'} — vias ligadas: {', '.join(vias)}")
    L.append(f"contratos: {len(ids)} · obras: {len({o for a in arestas for o in a['obras']})}")
    L.append("")
    L.append("PESSOAS PUBLICÁVEIS (contratos com ao menos uma pessoa)")
    for via, rot in (("E2", "assinou pelo órgão"), ("E3", "responsável no aviso"), ("E8", "responsável no contrato")):
        if via not in vias:
            L.append(f"   {via} {rot:24} DESLIGADA (não entra no candidato nem na publicação)")
            continue
        c = cob[via]
        t = c.get("com_pessoa", 0) + c.get("sem", 0)
        n_p = contagens(saida).get(via, 0)
        L.append(f"   {via} {rot:24} {c.get('com_pessoa', 0):4} de {t} contratos ({100 * c.get('com_pessoa', 0) / max(1, t):.0f}%) · {n_p} pessoas")
    L.append("")
    L.append("ARESTAS (todas, incluindo as retidas)")
    pub = Counter(a["via"] for a in arestas if a["publicavel"] and a["nome"] and a["via"] in vias)
    L.append(f"   total {len(arestas)} · publicáveis com nome: " + ", ".join(f"{k} {v}" for k, v in sorted(pub.items())))
    ret = Counter((a["via"], a["motivo_retencao"]) for a in arestas if not (a["publicavel"] and a["nome"]))
    L.append("   retidas por motivo (o nº de PDFs/contratos/pessoas, ou a `quantidade` de assinaturas excluídas):")
    q = Counter()
    for a in arestas:
        if not (a["publicavel"] and a["nome"]):
            q[(a["via"], a["motivo_retencao"])] += a.get("quantidade") or 1
    for (via, m), n in sorted(q.items()):
        L.append(f"      {via} {m:36} {ret[(via, m)]:5} registros · {n} unidades")
    L.append("")
    L.append(filtro_e2(arestas))
    L.append("LACUNAS POR CONTRATO (motivo principal mostrado no lugar do nome)")
    for via in vias:
        L.append(f"   {via}: " + ", ".join(f"{m} {n}" for m, n in sorted(cob.get(via + "_motivos", {}).items(), key=lambda kv: -kv[1])))
    L.append("")
    L.append("COBERTURA POR ANO DA LICITAÇÃO (contratos com pessoa / contratos)")
    L.append(f"   {'ano':8}{'contratos':>10}{'E2':>8}{'E3':>8}{'E8':>8}")
    for ano, v in cob["porAno"].items():
        L.append(f"   {ano:8}{v.get('contratos', 0):>10}{v.get('E2', 0):>8}{v.get('E3', 0):>8}{v.get('E8', 0):>8}")
    L.append("")
    L.append("ATIVIDADE")
    L.append(f"   PDFs baixados {est['pdf_baixados']} ({est['bytes_baixados'] / 1e6:.0f} MB) · do cache {est['pdf_em_cache']} · "
             f"falha de rede {est['pdf_falha_de_rede']} · 404 {est['pdf_erro_404']} · não é PDF {est['pdf_erro_nao_pdf']} · grandes {est['pdf_erro_grande']}")
    L.append("   tempo: " + " · ".join(f"{k} {v:.0f}s" for k, v in fases.items()) + f" · total {sum(fases.values()):.0f}s")
    L.append(f"   leitura de PDF (pypdf): {est['segundos_leitura']:.0f}s · segunda leitura ({P.leitor2_id() or 'NÃO INSTALADA'}): {est['segundos_leitura2']:.0f}s")
    L.append("")
    L.append("AMOSTRA DE VALIDAÇÃO: " + (", ".join(f"{k} {v}" for k, v in sorted(amostra.items())) + f" linhas em {AMOSTRA.name}" if amostra
                                           else f"não regravada ({AMOSTRA.name} é a amostra já conferida; --amostra a refaz)"))
    sup = getattr(args, "supressoes", {})
    ids_arestas = {a["aresta_id"] for a in arestas}
    L.append(f"SUPRESSÕES (entradas/pessoas_supressoes.json): {len(sup)} listadas, {sum(1 for i in sup if i in ids_arestas)} casam com arestas desta rodada"
             + ("" if all(i in ids_arestas for i in sup) else f" — SEM ARESTA CORRESPONDENTE: {[i for i in sup if i not in ids_arestas]}"))
    L.append("")
    L.append("DIFF")
    L += ["   " + x for x in diff_linhas]
    L.append("")
    L.append("CHECAGENS: " + ("OK" if not erros else f"FALHARAM ({len(erros)})"))
    L += ["   ✗ " + e for e in erros[:20]]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="não atualiza o estado da rodada anterior; nunca escreve em site/")
    ap.add_argument("--publicar", action="store_true", help="grava site/dados/obras_pessoas.json (só se as checagens passarem)")
    ap.add_argument("--so-montar", action="store_true", help="sem rede: remonta do cache")
    ap.add_argument("--revalidar", action="store_true", help="baixa os PDFs de novo e confere o hash")
    ap.add_argument("--limite", type=int, default=0, help="só os N primeiros contratos")
    ap.add_argument("--vias", default=",".join(v.lower() for v in VIAS_PADRAO),
                    help="vias que entram no candidato e na publicação, separadas por vírgula (e2,e3,e8); padrão: e2,e8 (o E3 está desligado)")
    ap.add_argument("--amostra", action="store_true", help="regrava cache/amostra_fase1.csv (por padrão não: é a amostra já conferida)")
    args = ap.parse_args()
    args.vias = tuple(dict.fromkeys(v.strip().upper() for v in args.vias.split(",") if v.strip()))
    if not args.vias or any(v not in ("E2", "E3", "E8") for v in args.vias):
        sys.exit(f"--vias: use e2, e3 e/ou e8 (recebi {args.vias})")
    sys.stdout.reconfigure(encoding="utf-8")
    if args.publicar and args.dry_run:
        sys.exit("--publicar e --dry-run não combinam")
    if args.publicar and args.limite:
        sys.exit("--publicar e --limite não combinam (publicaria só uma parte dos contratos)")
    base, contratos, obras_de, ids, por_contrato, regs_c, compras, est, fases = montar(args)
    t = time.monotonic()
    args.supressoes = carregar_supressoes()
    arestas, blocos = montar_arestas(contratos, obras_de, ids, por_contrato, regs_c, compras, args.supressoes)
    saida = saida_publicavel(contratos, ids, blocos, arestas, args.vias)
    fases["regras e arestas"] = time.monotonic() - t

    anterior_estado = coleta.ler_cache(ESTADO)
    erros = checagens(saida, arestas) + queda(saida, anterior_estado if not args.limite else None)
    anterior_site = json.loads(SAIDA.read_text(encoding="utf-8")) if SAIDA.exists() else None
    anterior_cand = coleta.ler_cache(CANDIDATO)

    # tudo em cache/: as arestas (todas) e o candidato (o que seria publicado)
    ARESTAS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARESTAS.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in arestas), encoding="utf-8")
    os.replace(tmp, ARESTAS)
    coleta.gravar_cache(CANDIDATO, saida)
    amostra = amostra_csv(arestas, AMOSTRA) if args.amostra and not erros else {}
    dl = diff(saida, anterior_site, anterior_cand)
    texto = relatorio(saida, arestas, ids, est, fases, args, erros, dl, amostra)
    RELATORIO.write_text(texto, encoding="utf-8")
    print("\n" + texto)
    if erros:
        sys.exit(f"checagem falhou ({len(erros)}): nada publicado")
    if args.publicar:
        tmp = SAIDA.with_suffix(".tmp")
        tmp.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, SAIDA)
        print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")
    if not args.dry_run and not args.limite:
        coleta.gravar_cache(ESTADO, {"quando": agora(), "versao": VERSAO, "pessoas": contagens(saida)})


if __name__ == "__main__":
    main()
