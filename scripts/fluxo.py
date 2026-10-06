"""
Câmara Aberta — onde a proposta morre entre as casas.

Uso:
    python scripts/fluxo.py                    # coleta o que falta ou mudou e grava site/dados/fluxo.json
    python scripts/fluxo.py --piloto 40        # amostra de 40 por direção; grava cache/fluxo/piloto.json (não toca o site)
    python scripts/fluxo.py --so-montar        # não baixa nada: monta o fluxo.json com o que já está no cache
    python scripts/fluxo.py --limite 6000      # no máximo 6.000 requisições nesta rodada (o cache se completa em alguns dias)

A UNIDADE É A PROPOSIÇÃO E A CASA — nunca o parlamentar. Nada aqui guarda autor, relator ou presidente de comissão, e
os agregados são por tipo, direção e ÓRGÃO.

UNIVERSO — só o que foi aprovado numa casa e remetido à outra (nunca as ~51 mil proposições):
  Câmara → Senado: processo do Senado com objetivo "Revisora" (siglaCasaIniciadora = CD) nas siglas PLC, PL, PLP, PEC,
      PDL, PDS. É o Senado dizendo que recebeu da Câmara.
  Senado → Câmara: processo do Senado com objetivo "Iniciadora" e deliberação aprovada (siglaTipoDeliberacao APROV*; e
      todo PDS, cujo objetivo nas listas antigas erra), com a direção conferida em siglaCasaIniciadora do detalhe,
      confirmado no detalhe por campo estruturado: destino da deliberação = CAMARA, situação REMETIDA À CÂMARA (RMCD) ou
      número da Câmara em outrosNumeros. Quem foi aprovado e ficou no Senado fica de fora.
  Situações que indicam a remessa, conferidas nos dados (2/10/2026):
    Câmara: tramitação de código 128 "Remessa ao Senado Federal"; situação 926 "Aguardando Apreciação pelo Senado
        Federal" (2.032 na base), 1293 "Aguardando Envio ao Senado Federal" e 1303 "Enviada ao Senado Federal".
    Senado: situação RMCD "REMETIDA À CÂMARA DOS DEPUTADOS", AGCD "AGUARDANDO DECISÃO DA CÂMARA DOS DEPUTADOS",
        ARQV_CD "ARQUIVADO NA CÂMARA DOS DEPUTADOS"; destino CAMARA em /processo/destinos; decisão REJEITADO_PLENARIO_CD.
  Fora do universo, de propósito: MPV e PLV (tramitam por comissão mista do Congresso, não "remetidas" de uma casa à
  outra), e o que o Senado ou a Câmara arquivou antes de remeter.

VÍNCULO — SÓ por identificador exato, NUNCA por título ou ementa:
  1. outrosNumeros do detalhe do Senado (sigla, número e ano na Câmara, casaIdentificadora = CD): vale nas duas direções.
     Medido numa amostra: 40 de 40 em Câmara → Senado; em Senado → Câmara, 20 de 20 de 2019 em diante e 7 de 20 nos PLS.
  2. (Senado → Câmara, quando o Senado não traz) a Câmara CITA o número de origem na tramitação do recebimento
     ("…encaminha o Projeto de Lei do Senado nº 53, de 2007…"). Só vale com UMA proposição da Câmara citando aquele número.
  Sem vínculo confirmado: vinculo = "nao_confirmado", fora das estatísticas, contado.

API DO SENADO (conferida em 2/10/2026, v4.1.3.99): /materia/* está DEPRECATED (inclusive /materia/movimentacoes). O atual é
/processo (lista, com sigla + ano) e /processo/{id} (detalhe: outrosNumeros, deliberacao, autuacoes com situações e
colegiado, movimentacoes, normaGerada). /processo?numdias=N (máx. 30) lista o que mudou: é o que torna a rodada diária barata.
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

import coleta
import tramitacoes
from coleta import CACHE, DADOS, gravar_cache, ler_cache

SF = "https://legis.senado.leg.br/dadosabertos"
PASTA = CACHE / "fluxo"
LISTAS = PASTA / "senado_lista"
DETALHES = PASTA / "senado"
STATUS_CD = PASTA / "camara_status"
ULTIMA = PASTA / "ultima.json"
SAIDA = DADOS / "fluxo.json"

SIGLAS_REVISORA = ("PLC", "PL", "PLP", "PEC", "PDL", "PDS")   # Câmara → Senado
SIGLAS_ORIGEM = ("PLS", "PL", "PLP", "PEC", "PDL", "PDS")     # Senado → Câmara
ANO_MIN = 1991                   # listas do Senado: pelo ano do número no Senado
CAMARA_ANO_MIN = 1980            # arquivos anuais da Câmara: o Senado cita números da Câmara anteriores a 1991
TAXA_SF = 5                      # req/s no Senado (a Câmara declara 10; o Senado não declara nada)
SF_WORKERS = 14                  # cada detalhe leva ~3,5 s: com 8 threads só se chegava a 2,3 req/s; o limitador segura em TAXA_SF
PARADA_DIAS = 365                # "parada" descreve, nunca acusa
TOLERANCIA_DIAS = 7              # diferença de registro entre as duas casas para o mesmo ofício
VALIDADE_ABERTO = 7              # dias: detalhe de processo ainda aberto, rebaixado se mais velho
VALIDADE_STATUS_ABERTO = 3       # dias: situação atual na Câmara de proposição aberta
VALIDADE_STATUS_FECHADO = 90
MAX_DATA_IMPOSSIVEL = 0.01       # de 2003 em diante: mais que 1% de datas em ordem impossível = a regra está errada, aborta
ANO_REGISTRO_MODERNO = 2003      # antes disso a Câmara anota a remessa em texto livre e as datas das duas Casas divergem mais

# Códigos do tipo de tramitação da Câmara (/referencias/proposicoes/codTipoTramitacao; ver tramitacoes.py)
REMESSA_SENADO = "128"                       # "Remessa ao Senado Federal": o código estruturado, na maior parte dos anos
# Sem o código, a Câmara só deixa o despacho do ofício, escrito de jeitos diferentes ao longo de 30 anos (medido em
# 22 mil proposições): "Remessa ao Senado Federal por meio do Of. nº…", "REMESSA AO SF, ATRAVES DO OF…", "REMESSA A SF",
# "REMESSA AO AF" (erro de digitação), "Remesssa ao Senado federal", "ARemessa…", "RMSF - REMETIDO AO SENADO FEDERAL" e, para
# decreto legislativo, "Of. nº 296/2024/PS-GSE, que encaminha ao Senado Federal o processado do PDL 659-2021, em revisão".
# Vale SÓ para a DATA da remessa, nunca para desfecho.
_REMESSA_TXT = re.compile(r"(?:rem\w{1,6}|encaminha\w*)\W+(?:\w+\W+){0,3}?(?:ao|a)\W+(?:senado|sf|af)\b", re.I)
# "A matéria vai ao Senado Federal (PEC 289-D/00)": a Câmara anota ao aprovar, antes do ofício. Só serve de data aproximada.
_VAI_AO_SENADO = re.compile(r"vai ao senado", re.I)
APROVACAO_ORIGEM = {"239", "244", "1235"}   # Aprovação da Redação Final (comissão, conclusiva) · Aprovação em Plenário
ARQUIVAMENTO = tramitacoes.ARQUIVA
LEI = tramitacoes.LEI
SANCAO = tramitacoes.SANCAO
# Siglas de tipo: o Senado chama de PLC/PDS o que a Câmara chama de PL/PDC
TIPO_DE = {"PL": "PL", "PLC": "PL", "PLS": "PL", "PLP": "PLP", "PEC": "PEC", "PDL": "PDL", "PDC": "PDL", "PDS": "PDL"}
TIPOS_CAMARA = ("PL", "PLP", "PEC", "PDC", "PDL")

# Situações do Senado (siglaSituacaoAtual, de /processo/tipos-situacao)
SIT_LEI = {"TNJR", "TNJRVETO"}
SIT_VETADA = {"VTDA", "VETADA"}
SIT_REJEITADA = {"RJTDA", "RJTDA(DT)", "RJTDA(ESF)", "RJTDA(MPV)", "RJTDA(PLV)"}
SIT_ARQUIVADA = {"ARQV", "ARQVD", "ARQV_CD", "ARQVO", "PREJFINLEG"}
SIT_PREJUDICADA = {"PRJDA"}
SIT_REMETIDA_A_CD = {"RMCD", "AGCD"}      # "remetida à Câmara" · "aguardando decisão da Câmara": o processo do Senado já saiu dele
SIT_RETIRADA = {"RTPA"}
DEC_REJEITADA = ("REJEITADO_",)


# ---------------------------------------------------------------- Senado (cliente)

_limite = coleta.Limitador(TAXA_SF)
_sessao = requests.Session()
_sessao.headers.update({"User-Agent": "CamaraAberta/1.0 (https://github.com/paulopottermarchi/Senado_Move)",
                        "Accept": "application/json"})
_sessao.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=12))
_reqs = [0]
_trava = threading.Lock()


def pedir(caminho, **params):
    """GET no Senado, no máximo TAXA_SF req/s somadas entre as threads; 404 → None; 429/5xx → espera crescente."""
    for n in range(6):
        _limite.esperar()
        with _trava:
            _reqs[0] += 1
        try:
            r = _sessao.get(SF + caminho, params=params, timeout=180)
        except requests.RequestException:
            time.sleep(3 * (n + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (400, 404):
            return None
        time.sleep(max(int(r.headers.get("retry-after") or 0), 4 * (n + 1)))
    raise RuntimeError(f"Senado sem resposta: {caminho} {params}")


# ---------------------------------------------------------------- Senado: listas e detalhes

CAMPOS_LISTA = ("id", "identificacao", "objetivo", "autoria", "dataApresentacao", "dataDeliberacao", "dataSituacaoAtual",
                "siglaTipoDeliberacao", "situacaoAtual", "tramitando", "normaGerada", "tipoDocumento", "codigoMateria")


def sigla_de(identificacao):
    return (identificacao or "").split(" ")[0]


def lista(sigla, ano, atualizar):
    c = LISTAS / f"{sigla}-{ano}.json"
    if not atualizar and c.exists():
        return ler_cache(c)
    d = pedir("/processo", sigla=sigla, ano=ano)
    d = [{k: x.get(k) for k in CAMPOS_LISTA} for x in (d or [])]
    gravar_cache(c, d)
    return d


def universo_do_senado():
    """{id: item da lista} de cada direção, a partir das listas em cache."""
    rev, ori = {}, {}
    for f in LISTAS.glob("*.json"):
        sigla = f.stem.split("-")[0]
        for x in ler_cache(f) or []:
            if sigla_de(x["identificacao"]) not in (SIGLAS_REVISORA + SIGLAS_ORIGEM):
                continue
            if x["objetivo"] == "Revisora" and sigla in SIGLAS_REVISORA:
                rev[x["id"]] = x
            elif (x["objetivo"] == "Iniciadora" and sigla in SIGLAS_ORIGEM
                  and ((x["siglaTipoDeliberacao"] or "").startswith("APROV") or sigla == "PDS")):
                # PDS: nas listas antigas o campo "objetivo" diz Iniciadora também para decretos que vieram da Câmara;
                # a direção vem do detalhe (siglaCasaIniciadora), então todos os PDS são conferidos lá
                ori[x["id"]] = x
    return rev, ori


def data10(t):
    return t[:10] if t else None


def reduz_detalhe(d):
    """O bruto tem ~70 kB por processo (autores, informes, documentos). Guarda só o que o fluxo usa — e NADA de
    autor, relator ou parlamentar."""
    doc = d.get("documento") or {}
    delib = d.get("deliberacao") or {}
    norma = d.get("normaGerada") or {}
    sits, movs = [], []
    for au in d.get("autuacoes") or []:
        for s in au.get("situacoes") or []:
            col = (s.get("colegiado") or {}).get("sigla")
            ente = (s.get("enteAdministrativo") or {}).get("sigla")
            sits.append([s.get("sigla"), data10(s.get("inicio")), data10(s.get("fim")), col, ente])
        movs += [data10(i.get("data")) for i in au.get("informesLegislativos") or []]
        for m in au.get("movimentacoes") or []:
            movs += [data10(m.get("dataEnvio")), data10(m.get("dataRecebimento"))]
    sits.sort(key=lambda s: (s[1] or "", s[2] or "9999"))
    ctrl = next((au for au in (d.get("autuacoes") or [])[:1]), {})
    return {
        "id": d["id"], "cod": d.get("codigoMateria"), "ident": d.get("identificacao"), "sigla": d.get("sigla"),
        "ementa": ((d.get("conteudo") or {}).get("ementa") or "")[:220] or None,
        "numero": d.get("numero"), "ano": d.get("ano"), "objetivo": d.get("objetivo"),
        "aberta": d.get("tramitando") == "Sim", "iniciadora": d.get("siglaCasaIniciadora"),
        "idInicial": d.get("idProcessoCasaInicial"), "identInicial": d.get("identificacaoProcessoInicial"),
        "outros": [[o.get("casaIdentificadora"), o.get("sigla"), str(int(o["numero"])) if str(o.get("numero") or "").isdigit() else o.get("numero"),
                    o.get("ano"), o.get("idOutroProcesso")] for o in d.get("outrosNumeros") or []],
        "apres": doc.get("dataApresentacao"), "leitura": doc.get("dataLeitura"),
        "sit": [d.get("siglaSituacaoAtual"), d.get("situacaoAtual"), d.get("dataSituacaoAtual")],
        "delib": [delib.get("data"), delib.get("siglaTipo"), delib.get("siglaDestino")] if delib else None,
        "norma": [norma.get("descricao"), norma.get("dataAssinatura") or norma.get("dataPublicacao")] if norma else None,
        "sits": sits, "controle": ctrl.get("siglaEnteControleAtual"),
        "mov": max([m for m in movs if m] or [None], key=lambda m: m or ""),
        # primeiro registro do Senado (informe, situação ou movimentação): a chegada. dataApresentacao traz data-padrão
        # nos registros antigos (3/1, início da legislatura) e nunca serve de chegada.
        "primeiro": min([m for m in movs + [x[1] for x in sits] if m] or [None], key=lambda m: m or "9999"),
        "busca": date.today().isoformat(),
    }


def precisa_detalhe(pid, velhos):
    c = ler_cache(DETALHES / f"{pid}.json")
    if c is None:
        return True
    if pid in velhos:
        return True
    return c["aberta"] and (date.today() - date.fromisoformat(c["busca"])).days >= VALIDADE_ABERTO


def baixa_detalhe(pid):
    d = pedir(f"/processo/{pid}")
    if d is None:
        return False
    gravar_cache(DETALHES / f"{pid}.json", reduz_detalhe(d))
    return True


def ids_atualizados():
    """Processos que o Senado diz ter mudado desde a última rodada (numdias, no máximo 30). Se passou mais que isso,
    ou é a primeira rodada, devolve None: os abertos são todos rebaixados."""
    ult = ler_cache(ULTIMA)
    if not ult:
        return None
    dias = (date.today() - date.fromisoformat(ult["em"])).days + 1
    if dias > 30:
        return None
    d = pedir("/processo", numdias=max(dias, 2)) or []
    return {x["id"]: x for x in d}


# ---------------------------------------------------------------- Câmara: índice dos números e tramitações

def garantir_arquivos_antigos(hoje):
    """Os arquivos anuais da Câmara anteriores a 1991 (o Senado cita PL 2528/1989) — pequenos, baixados uma vez."""
    import partidos
    for ano in range(CAMARA_ANO_MIN, 1991):
        for tipo in ("proposicoes", "proposicoesAutores"):
            partidos.obter(tipo, ano, hoje.year)


def indice_camara():
    """(tipo, número, ano) → [linha do arquivo anual], sem repetir o mesmo id. Só os tipos do universo; o arquivo traz o
    número ATUAL da proposição na Câmara."""
    import csv
    idx = defaultdict(dict)
    for f in sorted((CACHE / "bulk").glob("proposicoes-????.csv")):
        if int(f.stem[-4:]) < CAMARA_ANO_MIN:
            continue
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                if r["siglaTipo"] in TIPOS_CAMARA:
                    idx[(r["siglaTipo"], r["numero"], r["ano"])][int(r["id"])] = (
                        {"id": int(r["id"]), "apres": data10(r["dataApresentacao"]), "sit": r["ultimoStatus_descricaoSituacao"],
                         "cod": r["ultimoStatus_idSituacao"], "org": r["ultimoStatus_siglaOrgao"],
                         "dh": data10(r["ultimoStatus_dataHora"]), "sigla": r["siglaTipo"], "numero": r["numero"], "ano": r["ano"]})
    return {k: list(v.values()) for k, v in idx.items()}


def autoria_do_senado(idx):
    """Ids de PL, PLP, PEC e PDL da Câmara cuja autoria (proponente 1) é o Senado: o que chegou do Senado."""
    import csv
    do_tipo = {linha["id"] for linhas in idx.values() for linha in linhas}
    ids = set()
    for f in sorted((CACHE / "bulk").glob("proposicoesAutores-????.csv")):
        if int(f.stem[-4:]) < CAMARA_ANO_MIN:
            continue
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                if (r["proponente"] == "1" and r["tipoAutor"] == "Órgão do Poder Legislativo"
                        and r["nomeAutor"].startswith("Senado Federal") and int(r["idProposicao"]) in do_tipo):
                    ids.add(int(r["idProposicao"]))
    return ids


def camara_status(pid, baixar, aberta):
    """Situação atual na Câmara (statusProposicao), do cache; rebaixa se velha."""
    caminho = STATUS_CD / f"{pid}.json"
    c = ler_cache(caminho)
    validade = VALIDADE_STATUS_ABERTO if aberta else VALIDADE_STATUS_FECHADO
    if baixar and (c is None or (date.today() - date.fromisoformat(c["busca"])).days >= validade):
        r = coleta.get(f"{coleta.API}/proposicoes/{pid}")
        st = ((r or {}).get("dados") or {}).get("statusProposicao") or {}
        c = {"busca": date.today().isoformat(),
             "st": {"dh": data10(st.get("dataHora")), "org": st.get("siglaOrgao"), "sit": st.get("descricaoSituacao"),
                    "cod": str(st.get("codSituacao") or ""), "regime": st.get("regime")}}
        gravar_cache(caminho, c)
    return (c or {}).get("st")


# Citação do número de origem no recebimento: "…encaminha o Projeto de Lei do Senado nº 53, de 2007, a fim de que…"
_CITA = re.compile(r"(Projeto de Lei do Senado|Projeto de Lei Complementar do Senado|Projeto de Lei Complementar|"
                   r"Projeto de Lei|Proposta de Emenda à Constitui[çc][ãa]o|Projeto de Decreto Legislativo)"
                   r"[^,.;]{0,40}?n[ºo°.]*\s*([\d.]+)\s*,?\s*de\s*(\d{4})", re.I)
_FAMILIA = {"projeto de lei do senado": "LEI", "projeto de lei complementar do senado": "LEI",
            "projeto de lei complementar": "LEI", "projeto de lei": "LEI",
            "proposta de emenda à constituição": "PEC", "proposta de emenda à constituicao": "PEC",
            "projeto de decreto legislativo": "DEC"}
_FAM_SIGLA = {"PLS": "LEI", "PL": "LEI", "PLP": "LEI", "PEC": "PEC", "PDS": "DEC", "PDL": "DEC"}


def citacoes(eventos):
    """[(família, número, ano)] citados nos eventos de recebimento vindos do Senado."""
    saida = []
    for e in eventos or []:
        texto = f"{e[3] or ''} {e[4] or ''}"
        if "Senado" not in texto:
            continue
        for tipo, num, ano in _CITA.findall(texto):
            fam = _FAMILIA.get(tipo.lower().strip())
            if fam:
                saida.append((fam, num.replace(".", ""), ano))
    return saida


# ---------------------------------------------------------------- montagem de cada proposição

def numero_curto(sigla, numero, ano):
    return f"{sigla} {int(numero)}/{ano}" if str(numero).isdigit() else f"{sigla} {numero}/{ano}"


def sem_zeros(n):
    n = str(n)
    return str(int(n)) if n.isdigit() else n


def vinculo_camara(s, idx, por_id, citados):
    """(linha da Câmara, fonte) ou (None, motivo). Só por identificador exato: o número que o Senado traz em
    outrosNumeros, ou o número do Senado que a Câmara cita na tramitação — e só com UMA proposição possível."""
    cd = [o for o in s["outros"] if o[0] == "CD"]
    if cd:
        achados = {}
        for _, sigla, numero, ano, _ in cd:
            for linha in idx.get((sigla, sem_zeros(numero), str(ano)), []):
                achados[linha["id"]] = linha
        if len(achados) == 1:
            return next(iter(achados.values())), "outrosNumeros"
        return None, ("número da Câmara ambíguo no arquivo da Câmara" if achados
                      else "número da Câmara não encontrado no arquivo da Câmara")
    fam = _FAM_SIGLA.get(s["sigla"])
    cand = citados.get((fam, sem_zeros(s["numero"]), str(s["ano"]))) if fam else None
    if cand and len(cand) == 1:
        pid = next(iter(cand))
        if pid in por_id:
            return por_id[pid], "citacao"
    return None, ("citação ambígua" if cand and len(cand) > 1 else "citação ausente")


def remessa_ao_senado(e):
    return str(e[2] or "") == REMESSA_SENADO or bool(_REMESSA_TXT.search(e[4] or ""))


def cruzamentos(s, eventos, chegada_cd):
    """Passagens entre as casas, em ordem: [[data, de, para, exata]]. Câmara → Senado: tramitação 128 ou, sem o código, o
    despacho do ofício de remessa; ignorada depois da sanção, quando o mesmo texto só comunica o envio. Senado → Câmara:
    situação RMCD. Quando o Senado não registra a RMCD, a passagem ainda existe se a deliberação mandou à Câmara (data da
    deliberação) ou se a Câmara tem o registro; a data é então aproximada (exata = False) e NÃO vira a "remessa"."""
    cr = []
    fim = None
    for e in eventos or []:
        d = e[0]
        if str(e[2] or "") in LEI or str(e[2] or "") in SANCAO:
            fim = fim or d
        if remessa_ao_senado(e) and not (fim and d >= fim) and not any(c[0] == d and c[1] == "CD" for c in cr):
            cr.append([d, "CD", "SF", True])
    if not any(c[1] == "CD" for c in cr):          # sem ofício registrado: a anotação "vai ao Senado" dá a data aproximada
        for e in eventos or []:
            if _VAI_AO_SENADO.search(f"{e[3] or ''} {e[4] or ''}") and not (fim and e[0] >= fim):
                cr.append([e[0], "CD", "SF", False])
                break
    rmcd = [ini for sigla, ini, *_ in s["sits"] if sigla == "RMCD" and ini]
    if rmcd:
        cr += [[d, "SF", "CD", True] for d in rmcd]
    elif s["delib"] and s["delib"][2] == "CAMARA" and s["delib"][0]:
        cr.append([s["delib"][0], "SF", "CD", False])
    elif s["iniciadora"] == "SF" and chegada_cd:
        cr.append([chegada_cd, "SF", "CD", False])
    cr.sort(key=lambda c: (c[0], 0 if c[1] == "CD" else 1))
    return cr


def aprovacao_origem(direcao, s, eventos, data_passagem):
    if direcao == "CD>SF":
        if not eventos or not data_passagem:
            return None
        datas = [e[0] for e in eventos if str(e[2] or "") in APROVACAO_ORIGEM and e[0] and e[0] <= data_passagem]
        return max(datas) if datas else None
    # Senado → Câmara: a decisão que mandou à Câmara. O começo da situação "APROVADA" é a aprovação.
    if s["delib"] and s["delib"][2] == "CAMARA" and s["delib"][0]:
        return s["delib"][0]
    ap = [x[1] for x in s["sits"] if x[0] == "APRVD" and x[1] and data_passagem and x[1] <= data_passagem]
    return max(ap) if ap else None


def orgao_senado(s):
    """Onde está no Senado: a situação ainda aberta, pelo colegiado; sem colegiado, o ente administrativo."""
    abertas = [x for x in s["sits"] if not x[2]]
    for x in reversed(abertas):
        if x[3]:
            return x[3]
    for x in reversed(abertas):
        if x[4]:
            return x[4]
    return s.get("controle")


FECHADOS_CD = {str(coleta.COD_SITUACAO_LEI), "923", "1285", "950", "1120", "930"}   # lei, arquivada, finalizada, retirada, devolvida, ao arquivo


def desfecho(s, cd_st, eventos, casa_atual):
    """(desfecho, detalhe). SEMPRE de campo estruturado — situação, decisão, código de tramitação —, nunca de texto livre."""
    sit = s["sit"][0]
    cd_cod = (cd_st or {}).get("cod")
    lei_cd = cd_cod == str(coleta.COD_SITUACAO_LEI) or any(str(e[2] or "") in LEI for e in (eventos or []))
    if s["norma"] or sit in SIT_LEI or lei_cd:
        return "lei", None
    if sit in SIT_VETADA or cd_cod in {"937", "939"}:     # Vetado totalmente · Aguardando Apreciação do Veto (Câmara)
        return "outro", "vetada"
    if cd_cod == "1150":                               # Aguardando Sanção: está com o Executivo, não numa Casa
        return "outro", "aguardando sanção"
    decisao = (s["delib"] or [None, None])[1] or ""
    if casa_atual == "SF":
        if decisao.startswith(DEC_REJEITADA) or sit in SIT_REJEITADA:
            return "rejeitada", decisao or sit
        if sit in SIT_PREJUDICADA or decisao == "PREJUDICADO":
            return "outro", "prejudicada"
        if sit in SIT_RETIRADA or decisao == "RETIRADO_PELO_AUTOR":
            return "outro", "retirada pelo autor"
        if sit in SIT_ARQUIVADA or (not s["aberta"] and decisao.startswith("ARQUIVADO")):
            fim_leg = sit in ("ARQVD", "PREJFINLEG") or decisao == "ARQUIVADO_FIM_LEGISLATURA"
            return "arquivada", "ao final da legislatura" if fim_leg else None
        if not s["aberta"] and sit in SIT_REMETIDA_A_CD:
            return "outro", "situação diverge entre as casas"
        return ("tramitando", None) if s["aberta"] else ("outro", (s["sit"][1] or "").lower())
    # na Câmara. Situação oficial (código), nunca o texto de um despacho.
    if cd_cod in {"923", "1285", "930"} or any(str(e[2] or "") in ARQUIVAMENTO for e in (eventos or [])[-3:]):
        return "arquivada", None
    fins = [e for e in (eventos or []) if str(e[2] or "") in tramitacoes.FIM]
    if fins and tramitacoes.FIM[str(fins[-1][2])] == "rejeitada":
        return "rejeitada", "na Câmara"
    return "tramitando", None


def revisora(direcao, s, eventos, chegada, cr, des, casa_atual):
    """(aprovada na casa revisora, data em que saiu dela). Campos estruturados: no Senado, a deliberação; na Câmara, os
    códigos de aprovação (239, 244, 1235) depois da chegada, ou a lei. Quem ainda está na casa revisora não saiu."""
    casa = "SF" if direcao == "CD>SF" else "CD"
    volta = [c[0] for c in cr if c[1] == casa and c[2] != casa and c[3]]      # passagem exata que a tira da casa revisora
    if direcao == "CD>SF":
        dl = s["delib"] or [None, None, None]
        aprovada = des == "lei" or (dl[1] or "").startswith("APROV") or dl[2] in ("SANCAO", "PROMULGACAO", "CAMARA")
        saida = volta[0] if volta else dl[0]
        if saida is None and des != "tramitando":
            saida = (s["sit"][2] or "")[:10] or None
    else:
        ref = chegada or ""
        aps = [e[0] for e in (eventos or []) if str(e[2] or "") in APROVACAO_ORIGEM and e[0] and e[0] >= ref]
        arq = [e[0] for e in (eventos or []) if str(e[2] or "") in ARQUIVAMENTO and e[0] and e[0] >= ref]
        lei = [e[0] for e in (eventos or []) if str(e[2] or "") in LEI and e[0]]
        aprovada = bool(aps) or des == "lei"
        saidas = sorted(aps[:1] + arq[:1] + lei[:1])
        saida = volta[0] if volta else (saidas[0] if saidas else None)
    if des == "tramitando" and casa_atual == casa:
        saida = None
    return aprovada, saida


def monta(direcao, s, linha, fonte, eventos, cd_st, hoje):
    """Uma proposição com vínculo confirmado. Devolve (registro, problema); problema ≠ None: não se grava."""
    cr = cruzamentos(s, eventos, linha.get("apres") if direcao == "SF>CD" else None)
    esperado = "CD" if direcao == "CD>SF" else "SF"
    if not cr:
        return None, "sem passagem registrada entre as casas"
    if cr[0][1] != esperado:
        return None, f"primeira passagem sai de {cr[0][1]}, mas a origem é {esperado}"
    remessa = cr[0][0] if cr[0][3] else None
    data_passagem = cr[0][0]
    casa_atual = cr[-1][2]            # a casa que recebeu por último
    if direcao == "CD>SF":
        apres, chegada = linha.get("apres"), s["primeiro"] or s["apres"]
    else:
        apres, chegada = s["apres"], linha.get("apres")
    aprov = aprovacao_origem(direcao, s, eventos, data_passagem)
    # Voltou à casa de origem com a deliberação da revisora (emendas, substitutivo): campo estruturado do Senado. Câmara →
    # Senado: o Senado deliberou e mandou à Câmara. Senado → Câmara: o Senado deliberou DE NOVO (destino sanção ou
    # promulgação) depois de ter remetido à Câmara. O "Remessa ao Senado" que a Câmara registra no fim (devolução à casa
    # iniciadora para sancionar, ou ofício que comunica a sanção) NÃO é volta com emendas e não conta.
    if direcao == "CD>SF":
        voltou = retornou(s)
    else:
        voltou = bool(s["delib"] and s["delib"][2] in ("SANCAO", "PROMULGACAO"))
    cd = cd_st or {}
    des, det = desfecho(s, cd, eventos, casa_atual)
    aprovada, saida = revisora(direcao, s, eventos, chegada or data_passagem, cr, des, casa_atual)
    ult = max([x for x in (s["mov"], (s["sit"][2] or "")[:10] or None, eventos[-1][0] if eventos else None, cd.get("dh")) if x] or [None])
    if des == "tramitando":
        # "Parada" é sobre a casa ONDE ESTÁ: movimento administrativo na outra Casa não tira a proposição do lugar. O último
        # movimento é, então, o da casa atual (Senado: informes, movimentações e situação; Câmara: tramitação e situação).
        da_casa = ((s["mov"], (s["sit"][2] or "")[:10] or None) if casa_atual == "SF"
                   else (eventos[-1][0] if eventos else None, cd.get("dh")))
        ult = max([x for x in da_casa if x] or [ult])
    if ult and ult > hoje.isoformat():
        ult = hoje.isoformat()          # o Senado registra informes de pauta com data futura: nunca é "último movimento" de amanhã
    # Tramitando em conjunto (Câmara, situação 925): anda junto com a principal, e o parado dela não é seu. O registro do
    # processo no Senado não informa apensação (medido em 24.102 processos: nenhum com situação atual de anexada), então no
    # Senado a conta não separa as que tramitam em conjunto — a página diz isso.
    apensada = des == "tramitando" and casa_atual == "CD" and cd.get("cod") == "925"
    sit_txt = org = None
    if des == "tramitando":
        sit_txt, org = (s["sit"][1], orgao_senado(s)) if casa_atual == "SF" else (cd.get("sit"), cd.get("org"))
    else:
        sit_txt = s["sit"][1] if casa_atual == "SF" else cd.get("sit")
    dias = (hoje - date.fromisoformat(ult)).days if ult else None
    camara_id = linha["id"]
    reg = {
        "dir": direcao, "tipo": TIPO_DE.get(linha["sigla"], linha["sigla"]), "vinculo": "confirmado", "vinculoFonte": fonte,
        "idCamara": camara_id, "idSenado": s["id"], "codSenado": s["cod"],
        "numeroCamara": numero_curto(linha["sigla"], linha["numero"], linha["ano"]), "numeroSenado": s["ident"],
        "casaOrigem": esperado, "casaRevisora": "SF" if direcao == "CD>SF" else "CD",
        "datas": {"apresentacao": apres, "aprovacaoOrigem": aprov, "remessa": remessa, "chegada": chegada,
                  "saidaRevisora": saida, "ultimoMovimento": ult},
        "passagens": 1 + int(voltou), "aprovadaRevisora": bool(aprovada),
        "desfecho": des, "desfechoDetalhe": det,
        "casaAtual": casa_atual if des == "tramitando" else None,
        "situacao": sit_txt, "orgao": org, "ementa": s["ementa"] if des == "tramitando" else None,
        "diasSemMov": dias if des == "tramitando" else None,
        "apensada": bool(apensada),
        "parada": bool(des == "tramitando" and not apensada and dias is not None and dias > PARADA_DIAS),
        "urls": {"camara": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={camara_id}",
                 "senado": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{s['cod']}" if s["cod"] else None},
    }
    # Dentro da mesma casa a ordem é estrita; entre as duas, o Senado e a Câmara datam o mesmo ofício com alguns dias de
    # diferença (RMCD de um lado, recebimento do outro): até TOLERANCIA_DIAS vira ressalva, mais que isso não se grava.
    d = reg["datas"]
    futuras = [k for k in ("apresentacao", "aprovacaoOrigem", "remessa", "chegada", "saidaRevisora") if d[k] and d[k] > hoje.isoformat()]
    if futuras:
        return None, f"data no futuro em {futuras}"
    seq = [d[k] for k in ("apresentacao", "aprovacaoOrigem", "remessa") if d[k]]
    if seq != sorted(seq):
        return None, f"datas fora de ordem: {seq}"
    ref = d["remessa"] or data_passagem
    if d["chegada"] and ref and d["chegada"] < ref:
        atraso = (date.fromisoformat(ref) - date.fromisoformat(d["chegada"])).days
        if atraso > TOLERANCIA_DIAS:
            return None, f"chegada ({d['chegada']}) {atraso} dias antes da remessa ({ref})"
        reg["ressalva"] = f"a chegada consta {atraso} dia(s) antes da remessa, pela diferença de registro entre as casas"
    if d["aprovacaoOrigem"] and d["chegada"] and d["chegada"] < d["aprovacaoOrigem"]:
        return None, f"chegada ({d['chegada']}) antes da aprovação na origem ({d['aprovacaoOrigem']})"
    if d["saidaRevisora"] and d["chegada"] and d["saidaRevisora"] < d["chegada"]:
        reg["datas"]["saidaRevisora"] = None   # sem como medir o tempo na casa: não entra na mediana
    return reg, None


def sem_vinculo(direcao, s, motivo):
    return {"dir": direcao, "tipo": TIPO_DE.get(s["sigla"], s["sigla"]), "vinculo": "nao_confirmado", "motivo": motivo,
            "idSenado": s["id"], "numeroSenado": s["ident"],
            "urls": {"senado": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{s['cod']}" if s["cod"] else None}}


# ---------------------------------------------------------------- nomes dos órgãos

PREFERE_TIPO = ["COMISSAO_PERMANENTE", "COMISSAO_TEMPORARIA", "ORGAO_INTERNO", "SECRETARIA_COMISSAO", "EXPEDIENTE", "ARQUIVO",
                "UNIDADE_TRAMITACAO", "COLEGIADO_LEGISLATIVO"]


def _legivel(nome):
    """Alguns nomes do Senado vêm em CAIXA ALTA: só esses são levados para caixa de frase."""
    return nome[:1].upper() + nome[1:].lower() if nome and nome == nome.upper() else nome


def tabela_de_nomes(baixar):
    """({sigla: nome} da Câmara, {sigla: nome} do Senado), dos cadastros oficiais de órgãos, em cache por 90 dias."""
    cd_arq, sf_arq = PASTA / "camara_orgaos.json", PASTA / "senado_entes.json"

    def fresco(c):
        return c.exists() and (time.time() - c.stat().st_mtime) < 90 * 86400
    if baixar and not fresco(cd_arq):
        nomes, pagina = {}, 1
        while True:
            r = coleta.get(f"{coleta.API}/orgaos", {"itens": 100, "pagina": pagina, "ordenarPor": "sigla"})
            for o in (r or {}).get("dados", []):
                if o.get("sigla") and o.get("nome") and o["sigla"] not in nomes:
                    nomes[o["sigla"]] = o["nome"]
            if not r or not any(l["rel"] == "next" for l in r.get("links", [])):
                break
            pagina += 1
        gravar_cache(cd_arq, nomes)
    if baixar and not fresco(sf_arq):
        entes = [e for e in (pedir("/processo/entes") or []) if e.get("sigla") and e.get("casa") == "SF"]
        gravar_cache(sf_arq, [[e["sigla"], e["nome"], e.get("siglaTipo")] for e in entes])
    sf = {}
    for sigla, nome, tipo in sorted(ler_cache(sf_arq) or [], key=lambda x: (PREFERE_TIPO.index(x[2]) if x[2] in PREFERE_TIPO else 99,
                                                                          -len(x[1] or ""))):
        if nome and nome != sigla and sigla not in sf:
            sf[sigla] = _legivel(nome)
    return {k: _legivel(v) for k, v in (ler_cache(cd_arq) or {}).items()}, sf


def nomes_dos_orgaos(regs, baixar):
    cd, sf = tabela_de_nomes(baixar)
    usados = {(r["casaAtual"], r["orgao"]) for r in regs if r.get("orgao") and r.get("casaAtual")}
    return {f"{c}:{o}": (cd if c == "CD" else sf).get(o) for c, o in sorted(usados) if (cd if c == "CD" else sf).get(o)}


# ---------------------------------------------------------------- agregados

def mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def dias_entre(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days if a and b else None


def agrega(regs, hoje):
    """Por direção e tipo (e "todos"): funil, medianas, % lei, % parada e onde estão. Só vínculo confirmado."""
    ok = [r for r in regs if r["vinculo"] == "confirmado"]
    saida = {}
    for direcao in ("CD>SF", "SF>CD"):
        for tipo in ("todos", "PL", "PLP", "PEC", "PDL"):
            rs = [r for r in ok if r["dir"] == direcao and (tipo == "todos" or r["tipo"] == tipo)]
            if not rs:
                continue
            n = len(rs)
            lei = sum(1 for r in rs if r["desfecho"] == "lei")
            abertas = [r for r in rs if r["desfecho"] == "tramitando"]
            proprias = [r for r in abertas if not r["apensada"]]      # as apensadas andam com a principal
            paradas = [r for r in proprias if r["parada"]]
            # Dias na casa revisora: de chegar a sair. Quem ainda está lá entra à parte, como "esperam há": juntar os dois
            # esconderia as que não saem (as que saem são, por construção, as mais rápidas).
            saiu, esperam = [], []
            for r in rs:
                ch = r["datas"]["chegada"] or r["datas"]["remessa"]
                if not ch:
                    continue
                if r["desfecho"] == "tramitando" and r["casaAtual"] == r["casaRevisora"]:
                    esperam.append(dias_entre(ch, hoje.isoformat()))
                elif r["datas"]["saidaRevisora"]:
                    saiu.append(dias_entre(ch, r["datas"]["saidaRevisora"]))
            saiu = [d for d in saiu if d is not None and d >= 0]
            esperam = [d for d in esperam if d is not None and d >= 0]
            onde = Counter((r["casaAtual"], r["orgao"]) for r in proprias)
            onde_p = Counter((r["casaAtual"], r["orgao"]) for r in paradas)
            aprovadas = sum(1 for r in rs if r["aprovadaRevisora"])
            saida[f"{direcao}|{tipo}"] = {
                "proposicoes": n, "aprovadaRevisora": aprovadas, "lei": lei,
                "pctAprovadaRevisora": round(aprovadas / n, 4), "pctLei": round(lei / n, 4),
                "desfechos": dict(Counter(r["desfecho"] for r in rs)),
                "tramitando": len(abertas), "apensadas": len(abertas) - len(proprias), "paradas": len(paradas),
                "pctParadas": round(len(paradas) / len(proprias), 4) if proprias else None,
                "medianaDiasNaRevisora": mediana(saiu), "nSairam": len(saiu),
                "medianaDiasEsperando": mediana(esperam), "nEsperando": len(esperam),
                "retornaram": sum(1 for r in rs if r["passagens"] > 1),
                "porOrgao": [[c, o, k, onde_p.get((c, o), 0)] for (c, o), k in onde.most_common()],
            }
    return saida


# ---------------------------------------------------------------- coleta (incremental)

def precisa_tram(pid, aberta):
    c = tramitacoes.CACHE_T / f"{pid}.json"
    if not c.exists():
        return True
    return aberta and (time.time() - c.stat().st_mtime) > VALIDADE_ABERTO * 86400


def precisa_status(pid, aberta):
    c = ler_cache(STATUS_CD / f"{pid}.json")
    if c is None:
        return True
    limite = VALIDADE_STATUS_ABERTO if aberta else VALIDADE_STATUS_FECHADO
    return (date.today() - date.fromisoformat(c["busca"])).days >= limite


def baixar(alvos, workers=None):
    """alvos: ('sf', id) · ('cdTram', id) · ('cdSt', id, aberta). Throttle: 10 req/s na Câmara (coleta.Limitador), 5 no Senado."""
    feitos, falhas = 0, []

    def um(a):
        try:
            if a[0] == "sf":
                return baixa_detalhe(a[1])
            if a[0] == "cdTram":
                return tramitacoes.baixa(a[1])
            return camara_status(a[1], True, a[2])
        except Exception as e:           # uma falha não derruba a rodada: o cache guarda o resto e a próxima refaz
            falhas.append((a, repr(e)[:100]))
            return None
    with ThreadPoolExecutor(workers or coleta.WORKERS) as ex:
        for _ in ex.map(um, alvos):
            feitos += 1
            if feitos % 1000 == 0:
                print(f"   {feitos}/{len(alvos)}", flush=True)
    if falhas:
        print(f"   {len(falhas)} falhas (serão refeitas na próxima rodada): {falhas[:3]}")
    return feitos


def retornou(s):
    return bool((s["delib"] and s["delib"][2] == "CAMARA") or any(x[0] == "RMCD" for x in s["sits"]))


def enviada(s):
    return retornou(s) or any(o[0] == "CD" for o in s["outros"])


def preparar(args, hoje):
    """Coleta o que falta e devolve o que a montagem usa. Respeita --limite (requisições nesta rodada)."""
    for p in (LISTAS, DETALHES, STATUS_CD):
        p.mkdir(parents=True, exist_ok=True)
    limite = args.limite or 10 ** 9
    truncou = False
    velhos = {}
    if not args.so_montar:
        velhos = ids_atualizados()
        refazer = velhos is None
        velhos = velhos or {}
        if refazer:
            print("primeira rodada ou mais de 30 dias sem rodar: listas e abertos serão refeitos")
        t0 = time.monotonic()
        for sigla in sorted(set(SIGLAS_REVISORA) | set(SIGLAS_ORIGEM)):
            for ano in range(ANO_MIN, hoje.year + 1):
                lista(sigla, ano, args.atualizar or refazer or ano >= hoje.year - 1)
        print(f"listas do Senado prontas em {time.monotonic() - t0:.0f}s")
    rev, ori = universo_do_senado()
    todos = {**rev, **ori}
    # o que o Senado diz ter mudado e ainda não está nas listas (processo novo, com número de um ano antigo)
    for pid, x in velhos.items():
        if sigla_de(x["identificacao"]) in (SIGLAS_REVISORA + SIGLAS_ORIGEM) and pid not in todos:
            todos[pid] = {**{k: None for k in CAMPOS_LISTA}, **x}
    if args.piloto:
        random.seed(11)
        por = defaultdict(list)
        for pid, x in {**rev, **ori}.items():
            por[("R" if pid in rev else "O", sigla_de(x["identificacao"]), (x["dataApresentacao"] or "")[:3])].append(pid)
        escolha = {"R": [], "O": []}
        for k in sorted(por):
            escolha[k[0]] += random.sample(por[k], min(2, len(por[k])))
        for k in escolha:
            random.shuffle(escolha[k])
        todos = {pid: todos[pid] for k in escolha for pid in escolha[k][:args.piloto]}
        print(f"PILOTO: {len(todos)} processos")
    print(f"Senado: {len(rev)} Revisora · {len(ori)} Iniciadora aprovada (a confirmar pelo detalhe) · {len(todos)} a conferir")

    # 1. detalhes do Senado
    if not args.so_montar:
        alvos = [("sf", pid) for pid in todos if precisa_detalhe(pid, velhos)]
        if len(alvos) > limite:
            alvos, truncou = alvos[:limite], True
        if alvos:
            print(f"detalhes do Senado a baixar: {len(alvos)}", flush=True)
            baixar(alvos, SF_WORKERS)
            limite -= len(alvos)
    det = {pid: ler_cache(DETALHES / f"{pid}.json") for pid in todos}
    det = {k: v for k, v in det.items() if v}
    # A direção vem de siglaCasaIniciadora do DETALHE, não do "objetivo" da lista (que erra em decretos antigos).
    rev = {i: det[i] for i in det if det[i]["iniciadora"] == "CD" and det[i]["sigla"] in SIGLAS_REVISORA}
    ori = {i: det[i] for i in det if det[i]["iniciadora"] == "SF" and det[i]["sigla"] in SIGLAS_ORIGEM and enviada(det[i])}
    print(f"confirmadas por campo estruturado: Câmara → Senado {len(rev)} · Senado → Câmara {len(ori)}")

    # 2. vínculo com a Câmara, por número exato
    if not args.so_montar:
        garantir_arquivos_antigos(hoje)
    idx = indice_camara()
    por_id = {linha["id"]: linha for linhas in idx.values() for linha in linhas}
    ids_sf = autoria_do_senado(idx)
    print(f"índice da Câmara: {len(idx)} números · proposições de autoria do Senado: {len(ids_sf)}")
    if not args.so_montar:
        faltam = [("cdTram", i) for i in sorted(ids_sf) if not (tramitacoes.CACHE_T / f"{i}.json").exists()]
        if len(faltam) > limite:
            faltam, truncou = faltam[:limite], True
        if faltam:
            print(f"tramitações da Câmara (autoria do Senado, para achar a citação) a baixar: {len(faltam)}", flush=True)
            baixar(faltam)
            limite -= len(faltam)
    citados = defaultdict(set)
    for i in ids_sf:
        for fam, num, ano in citacoes(ler_cache(tramitacoes.CACHE_T / f"{i}.json")):
            citados[(fam, sem_zeros(num), str(ano))].add(i)
    ligados, semvinc = [], []
    for direcao, grupo in (("CD>SF", rev), ("SF>CD", ori)):
        for pid, s in grupo.items():
            linha, fonte = vinculo_camara(s, idx, por_id, citados)
            if linha:
                ligados.append((direcao, s, linha, fonte))
            else:
                semvinc.append(sem_vinculo(direcao, s, fonte))
    # O Senado registra as idas e voltas como processos separados ("PEC 1A/1995 (fase 2)", "PEC 1B/1995 (fase 3)") e, nos
    # decretos, às vezes dois PDS citam o mesmo PDC. Mais de um processo para a MESMA proposição da Câmara não é um vínculo
    # um-para-um: nenhum deles entra nas contas (e a proposição não vira duas linhas).
    por_camara = defaultdict(list)
    for item in ligados:
        por_camara[item[2]["id"]].append(item)
    repetidas = {cid for cid, v in por_camara.items() if len(v) > 1}
    if repetidas:
        semvinc += [sem_vinculo(d, s, "mais de um processo do Senado ligado à mesma proposição da Câmara")
                    for d, s, linha, fonte in ligados if linha["id"] in repetidas]
        ligados = [it for it in ligados if it[2]["id"] not in repetidas]
    print(f"vínculo confirmado: {len(ligados)} · nao_confirmado: {len(semvinc)} (a mesma proposição da Câmara em vários processos do Senado: {len(repetidas)})")

    # 3. dados da Câmara dos vinculados
    if not args.so_montar:
        faltam = []
        for direcao, s, linha, fonte in ligados:
            pid = linha["id"]
            volta = direcao == "SF>CD" or retornou(s)
            st = camara_status(pid, False, False) if volta else None
            aberta = volta and (st is None or st["cod"] not in FECHADOS_CD)
            if precisa_tram(pid, aberta):
                faltam.append(("cdTram", pid))
            if volta and precisa_status(pid, aberta):
                faltam.append(("cdSt", pid, aberta))
        faltam = list(dict.fromkeys(faltam))
        if len(faltam) > limite:
            faltam, truncou = faltam[:limite], True
        if faltam:
            print(f"dados da Câmara a baixar: {len(faltam)}", flush=True)
            baixar(faltam)
    return ligados, semvinc, rev, ori, truncou


# ---------------------------------------------------------------- montagem, checagens e saída

def montar(ligados, hoje):
    regs, problemas = [], []
    for direcao, s, linha, fonte in ligados:
        pid = linha["id"]
        eventos = ler_cache(tramitacoes.CACHE_T / f"{pid}.json")
        if eventos is None:
            problemas.append((direcao, s["ident"], "tramitação da Câmara ainda não baixada", (s["primeiro"] or s["apres"] or "0000")[:4]))
            continue
        st = camara_status(pid, False, False)
        if st is None:        # sem a situação fresca da Câmara: a do arquivo anual em lote
            st = {"dh": linha.get("dh"), "org": linha.get("org"), "sit": linha.get("sit"), "cod": linha.get("cod")}
        reg, prob = monta(direcao, s, linha, fonte, eventos, st, hoje)
        if reg is None:
            problemas.append((direcao, s["ident"], prob, (s["primeiro"] or s["apres"] or "0000")[:4]))
        else:
            regs.append(reg)
    return regs, problemas


def verificar(regs, problemas, semvinc, rev, ori, ligados, hoje, anterior, piloto=False):
    """Checagens que abortam: aparecem aqui porque o erro silencioso, num número publicado, é o pior."""
    erros = []
    n_universo = len(rev) + len(ori)
    if len(ligados) + len(semvinc) != n_universo:
        erros.append(f"conservação: {len(ligados)} vinculadas + {len(semvinc)} sem vínculo ≠ {n_universo} no universo")
    ausentes = [p for p in problemas if p[2].startswith("tramitação da Câmara ainda não baixada")]
    reais = [p for p in problemas if p not in ausentes]
    modernos = [p for p in reais if int(p[3]) >= ANO_REGISTRO_MODERNO]
    n_modernos = sum(1 for d, s, linha, fonte in ligados if int((s["primeiro"] or s["apres"] or "0000")[:4]) >= ANO_REGISTRO_MODERNO)
    if len(regs) + len(problemas) != len(ligados):
        erros.append(f"conservação: {len(regs)} montadas + {len(problemas)} com problema ≠ {len(ligados)} vinculadas")
    if not piloto and n_modernos and len(modernos) / n_modernos > MAX_DATA_IMPOSSIVEL:
        erros.append(f"{len(modernos)} de {n_modernos} com data impossível de {ANO_REGISTRO_MODERNO} em diante "
                     f"({len(modernos) / n_modernos:.1%}): acima de {MAX_DATA_IMPOSSIVEL:.0%}, a regra de datas está errada")
    for chave in ("idSenado", "idCamara"):
        for direcao in ("CD>SF", "SF>CD"):
            c = Counter(r[chave] for r in regs if r["dir"] == direcao)
            rep = [k for k, v in c.items() if v > 1]
            if rep:
                erros.append(f"{chave} repetido em {direcao}: {rep[:5]}")
    # uma proposição da Câmara só está em UMA direção
    ambos = {r["idCamara"] for r in regs if r["dir"] == "CD>SF"} & {r["idCamara"] for r in regs if r["dir"] == "SF>CD"}
    if ambos:
        erros.append(f"idCamara nas duas direções: {sorted(ambos)[:5]}")
    futuras = [r for r in regs for k, v in r["datas"].items() if v and v > hoje.isoformat()]
    if futuras:
        erros.append(f"{len(futuras)} datas no futuro")
    for r in regs:
        if r["desfecho"] not in ("lei", "rejeitada", "arquivada", "tramitando", "outro"):
            erros.append(f"desfecho inválido: {r['desfecho']}")
            break
        if r["aprovadaRevisora"] is False and r["desfecho"] == "lei":
            erros.append(f"lei sem aprovação na casa revisora: {r['numeroSenado']}")
            break
        if r["desfecho"] == "tramitando" and r["casaAtual"] not in ("CD", "SF"):
            erros.append(f"tramitando sem casa: {r['numeroSenado']}")
            break
        if r["apensada"] and r["parada"]:
            erros.append(f"apensada marcada como parada: {r['numeroSenado']}")
            break
    ag = agrega(regs, hoje)
    for k, v in ag.items():
        if not (v["lei"] <= v["aprovadaRevisora"] <= v["proposicoes"]):
            erros.append(f"funil fora de ordem em {k}: {v['lei']} lei, {v['aprovadaRevisora']} aprovadas, {v['proposicoes']}")
    if anterior and len(regs) < 0.95 * anterior:
        erros.append(f"o conjunto caiu de {anterior} para {len(regs)} (mais de 5%)")
    return erros, ag


CAMPOS = ["dir", "tipo", "idCamara", "idSenado", "codSenado", "numeroCamara", "numeroSenado", "apresentacao", "aprovacaoOrigem",
          "remessa", "chegada", "saidaRevisora", "ultimoMovimento", "passagens", "aprovadaRevisora", "desfecho", "desfechoDetalhe",
          "casaAtual", "situacao", "orgao", "diasSemMov", "parada", "apensada", "vinculoFonte", "ementa", "ressalva"]
CAMPOS_NC = ["dir", "tipo", "idSenado", "codSenado", "numeroSenado", "motivo"]


def linha_do_registro(r):
    d = r["datas"]
    return [r["dir"], r["tipo"], r["idCamara"], r["idSenado"], r["codSenado"], r["numeroCamara"], r["numeroSenado"],
            d["apresentacao"], d["aprovacaoOrigem"], d["remessa"], d["chegada"], d["saidaRevisora"], d["ultimoMovimento"],
            r["passagens"], int(r["aprovadaRevisora"]), r["desfecho"], r["desfechoDetalhe"], r["casaAtual"], r["situacao"],
            r["orgao"], r["diasSemMov"], int(r["parada"]), int(r["apensada"]), r["vinculoFonte"], r.get("ementa"), r.get("ressalva")]


def gravar(regs, semvinc, problemas, ag, rev, ori, ligados, hoje, orgaos=None):
    # "tramitação ainda não baixada" não é data impossível: a Câmara falhou nesta rodada e a próxima refaz. Contada à parte, para
    # não aparecer na página (nem em casosDatasImpossiveis) como se fosse erro de registro.
    ausentes = [p for p in problemas if p[2].startswith("tramitação da Câmara ainda não baixada")]
    problemas = [p for p in problemas if p not in ausentes]
    cobertura = {
        "universoSenado": {"CD>SF": len(rev), "SF>CD": len(ori)},
        "vinculoConfirmado": dict(Counter(r["dir"] for r in regs)),
        "vinculoPorFonte": dict(Counter(r["vinculoFonte"] for r in regs)),
        "naoConfirmado": dict(Counter(x["dir"] for x in semvinc)),
        "motivosNaoConfirmado": dict(Counter(x["motivo"] for x in semvinc)),
        "datasImpossiveis": dict(Counter(p[0] for p in problemas)),
        "tramitacaoNaoBaixada": dict(Counter(p[0] for p in ausentes)),
        "datasImpossiveisPorEra": {"antes de 2003": sum(1 for p in problemas if int(p[3]) < ANO_REGISTRO_MODERNO),
                                   "2003 em diante": sum(1 for p in problemas if int(p[3]) >= ANO_REGISTRO_MODERNO)},
        "vinculadasPorEra": {"antes de 2003": sum(1 for d, s, l, f in ligados if int((s["primeiro"] or s["apres"] or "0000")[:4]) < ANO_REGISTRO_MODERNO),
                             "2003 em diante": sum(1 for d, s, l, f in ligados if int((s["primeiro"] or s["apres"] or "0000")[:4]) >= ANO_REGISTRO_MODERNO)},
        "casosDatasImpossiveis": [[p[0], p[1], p[2]] for p in problemas][:400],
        "comRessalva": sum(1 for r in regs if r.get("ressalva")),
    }
    cab = {"_gerado": hoje.isoformat(),
           "_fonte": "Dados Abertos do Senado (/processo) e da Câmara (tramitações e arquivos anuais). A unidade é a "
                     "proposição e a casa, nunca o parlamentar.",
           "_regras": {"paradaDias": PARADA_DIAS, "toleranciaDiasEntreCasas": TOLERANCIA_DIAS,
                       "tipos": ["PL", "PLP", "PEC", "PDL"], "fora": "MPV e PLV (comissão mista do Congresso)"},
           "cobertura": cobertura, "agregados": ag, "orgaos": orgaos or {}}
    # Compacto: cada proposição é uma LISTA, na ordem de CAMPOS (cabeçalho), uma por linha. Como objeto, o arquivo passava de
    # 18 MB; assim fica em poucos MB e o diff diário do git mostra só as linhas que mudaram. Casas de origem e revisora saem de
    # `dir`; os links das fichas, dos ids (modelos em `urls`).
    cab["campos"] = CAMPOS
    cab["urls"] = {"camara": "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={idCamara}",
                   "senado": "https://www25.senado.leg.br/web/atividade/materias/-/materia/{codSenado}"}
    cab["camposNaoConfirmadas"] = CAMPOS_NC
    ordem = sorted(regs, key=lambda r: (r["dir"], r["numeroSenado"] or ""))
    linhas = [json.dumps(linha_do_registro(r), ensure_ascii=False, separators=(",", ":")) for r in ordem]
    nc = [json.dumps([x["dir"], x["tipo"], x["idSenado"], (x["urls"].get("senado") or "").rsplit("/", 1)[-1] or None,
                      x["numeroSenado"], x["motivo"]], ensure_ascii=False, separators=(",", ":"))
          for x in sorted(semvinc, key=lambda x: (x["dir"], x["numeroSenado"] or ""))]
    texto = (json.dumps(cab, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"proposicoes":[\n' + ",\n".join(linhas) +
             '\n],"naoConfirmadas":[\n' + ",\n".join(nc) + "\n]}\n")
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, SAIDA)
    return cobertura


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--piloto", type=int, default=0, help="amostra de N por direção; grava cache/fluxo/piloto.json, não toca o site")
    ap.add_argument("--so-montar", action="store_true", help="não baixa nada")
    ap.add_argument("--limite", type=int, default=0, help="no máximo N requisições nesta rodada (0 = sem limite)")
    ap.add_argument("--atualizar", action="store_true", help="rebaixa as listas por sigla e ano (as recentes sempre são)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    hoje = date.today()
    t0 = time.monotonic()
    ligados, semvinc, rev, ori, truncou = preparar(args, hoje)
    regs, problemas = montar(ligados, hoje)
    anterior = None
    if SAIDA.exists() and not args.piloto:
        try:
            anterior = json.loads(SAIDA.read_text(encoding="utf-8"))["cobertura"]["vinculoConfirmado"]
            anterior = sum(anterior.values())
        except (ValueError, KeyError):
            anterior = None
    erros, ag = verificar(regs, problemas, semvinc, rev, ori, ligados, hoje, anterior if not truncou else None, bool(args.piloto))
    print(f"\n{len(regs)} montadas · {len(problemas)} com problema · {len(semvinc)} nao_confirmado · "
          f"{time.monotonic() - t0:.0f}s · requisições ao Senado {_reqs[0]} · à Câmara {coleta.contador_req}")
    for p in problemas[:15]:
        print(f"   não gravada: {p[0]} {p[1]} — {p[2]}")
    if problemas:
        print(f"   por época: antes de 2003 {sum(1 for p in problemas if int(p[3]) < ANO_REGISTRO_MODERNO)} · "
              f"2003 em diante {sum(1 for p in problemas if int(p[3]) >= ANO_REGISTRO_MODERNO)}")
    if erros:
        print("\nCHECAGENS QUE FALHARAM — nada foi gravado:")
        for e in erros:
            print("  ✗", e)
        sys.exit(1)
    if args.piloto:
        import pickle
        pickle.dump((regs, problemas, semvinc, ag), open(PASTA / "piloto.pkl", "wb"))
        print("piloto: nada gravado no site")
        return
    if truncou:
        print("rodada truncada pelo --limite: o fluxo.json não foi regravado (faltam dados); rode de novo")
        return
    cob = gravar(regs, semvinc, problemas, ag, rev, ori, ligados, hoje, nomes_dos_orgaos(regs, not args.so_montar))
    gravar_cache(ULTIMA, {"em": hoje.isoformat()})
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB) · cobertura: {cob['vinculoConfirmado']} confirmadas, "
          f"{cob['naoConfirmado']} sem vínculo, {cob['datasImpossiveis']} com data impossível")


if __name__ == "__main__":
    main()
