"""
Câmara Aberta — histórico de tramitação de cada PEC e PL: quanto tempo levou e por onde passou.

Uso:
    python scripts/tramitacoes.py              # baixa o que falta ou mudou; grava cache/tramitacoes/{id}.json
    python scripts/tramitacoes.py --so-medir   # não baixa nada; mede o que já está no cache

Fonte: /proposicoes/{id}/tramitacoes (Dados Abertos da Câmara), uma chamada por proposição, a
10 req/s como o resto da coleta. Universo: as PEC e PL de autoria dos 513 (temas/) mais as 164
votações finais (proposicoes.json).

O que se guarda (o bruto passaria de 500 MB): de cada evento, data, órgão, código e descrição do
tipo de tramitação; o texto do despacho só nos eventos que marcam etapa (remessa, Senado, sanção,
lei, apensação, arquivo). Medido no PL 1998/2020: 122 eventos, 82 kB brutos.

ARMADILHA medida: a "situação" gravada em cada evento NÃO é a situação daquele dia — eventos de
2020 do PL 1998/2020 aparecem como "Transformado em Norma Jurídica", a situação de hoje. A data da
lei vem do EVENTO "Transformação em Norma Jurídica" ("Transformado na Lei Ordinária 14510/2022.
DOU 28/12/2022"), nunca da situação.

Rebaixa uma proposição quando o último andamento no cache da Etapa A (statusProposicao.dataHora)
é mais novo que o último evento guardado.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import coleta

CACHE_T = coleta.CACHE / "tramitacoes"
_MARCO = re.compile(r"Remessa|Senado|San[çc][ãa]o|Lei |Norma Jur|Apens|Desapens|Arquiv|Promulga|Veto|"
                    r"Retirad|Devolv|Prejudicad|Rejeitad", re.I)


# ---------------------------------------------------------------- etapas
# Códigos oficiais (/referencias/proposicoes/codTipoTramitacao, guardado em cache/tramitacoes_tipos.json).
LEI = {"251", "1012"}                                   # Transformação em Norma Jurídica (com veto parcial)
SANCAO = {"127", "609", "137"}                          # despacho / remessa à sanção, à Presidência
PROMULGACAO = {"126", "608"}
SENADO_IDA = {"128"}                                    # Remessa ao Senado Federal
SENADO_VOLTA = {"1510", "1518", "1519"}                 # ofício / aprovação no Senado
ARQUIVA = {"502", "134", "1024"}
DESARQUIVA = {"503", "135", "640", "650", "140"}
APENSA = {"129", "106"}
DESAPENSA = {"104", "130", "505"}
REQUERIMENTO = {"194", "260", "263", "362", "1262", "1263", "1264"}
FIM = {"133": "devolvida ao autor", "1020": "devolvida ao autor", "200": "retirada pelo autor",
       "1025": "retirada pelo autor", "1241": "retirada pelo autor", "197": "rejeitada", "1236": "rejeitada",
       "198": "prejudicada", "435": "prejudicada", "1240": "prejudicada"}
# Órgãos administrativos: registram publicação, notificação, despacho — não são lugar onde a
# proposição "está". Comissões permanentes, especiais (PEC23195…) e o Plenário são.
ADMIN = {"MESA", "CCP", "PTCOM", "CN"}
_SIGLA = r"(?:PEC|PLP|PL|PDL|MPV)\s*-?\s*\d[\d.]*/\d{4}"
# "Apense-se à(ao) PL-916/2020" · "Apense-se este ao PL-5/1999" (esta proposição vai para a principal)
_APENSADA_A = re.compile(rf"Apense-se\s+(?:est[ea]\s+)?(?:à|ao|a)\s*\(?(?:ao)?\)?\s*(?:o\(a\)\s*)?({_SIGLA})", re.I)
# "Apensação da proposição PL-144/2025 à proposição PL-1530/2024" (vale se a primeira for esta)
_APENSACAO_DE = re.compile(rf"Apensa[çc][ãa]o da proposi[çc][ãa]o\s+({_SIGLA})\s+[àa]\s+proposi[çc][ãa]o\s+({_SIGLA})", re.I)


def lugar(org):
    if not org or org in ADMIN or "SGM" in org or org.startswith(("SEC", "DCD")):
        return None
    return org


def _cita(texto, numeros):
    """O texto cita esta proposição por algum dos números dela ("PL 1.998/2020", "n. 1998/2020")?
    `numeros` = [(número, ano), ...]: o atual e o anterior, quando a Câmara renumerou (o PL
    6056/2025 é o PL 2664/2003, e o despacho de desapensação cita o número antigo)."""
    citados = {(n.replace(".", ""), a) for n, a in re.findall(r"(\d[\d.]*)\s*/\s*(\d{4})", texto or "")}
    return any((str(n), str(a)) in citados for n, a in numeros)


def numeros_do_titulo(titulo):
    """[(número, ano)] de "PL 5809/2025 (Nº Anterior: PL 347/2003)"."""
    return [(int(n), int(a)) for n, a in re.findall(r"(\d+)/(\d{4})", titulo or "")]


def etapas(ev, numeros=()):
    """Da lista reduzida de eventos:
      et  — [[lugar, desde], ...]: APRESENTADA, comissão (sigla), PLEN, SENADO, SANÇÃO, PROMULGAÇÃO,
            LEI. Cada lugar vai do primeiro evento nele até o começo do seguinte.
      per — [[tipo, desde, até ou None, detalhe], ...]: períodos 'arquivada' e 'apensada' (com a
            principal). São situação, não lugar: apensada, a proposição anda junto com a principal.
      lei — data do evento "Transformação em Norma Jurídica"; fim — [tipo, data] (retirada,
            devolvida, rejeitada, prejudicada); ua — data do último evento.
    Idas e voltas no mesmo dia (parecer de comissão dado no Plenário) viram uma etapa só."""
    et, per, lei, fim, marco = [], [], None, None, None   # marco: lugar que só um evento-marco encerra
    aberto = {}                                            # tipo de período → índice em per

    def entra(rot, data):
        if not et or et[-1][0] != rot:
            et.append([rot, data])

    def abre(tipo, data, detalhe=None):
        if tipo not in aberto:
            aberto[tipo] = len(per)
            per.append([tipo, data, None, detalhe])

    def fecha(tipo, data):
        if tipo in aberto:
            per[aberto.pop(tipo)][2] = data

    for d, org, cod, desc, desp in ev:
        cod = str(cod or "")
        if not d:
            continue
        if cod in LEI:
            lei = lei or d
            entra("LEI", d)
            break                              # depois da lei, só registro administrativo
        if cod in FIM:
            fim = [FIM[cod], d]
        if cod in ARQUIVA:
            abre("arquivada", d)
        elif cod in DESARQUIVA:
            fecha("arquivada", d)
        principal = None
        if cod in APENSA or cod == "110":
            m = _APENSADA_A.search(desp or "")
            if m and not _cita(m[1], numeros):
                principal = m[1]
            m = _APENSACAO_DE.search(desp or "")
            if m and _cita(m[1], numeros):
                principal = m[2]
        if principal:
            principal = re.sub(r"\s*-\s*|\s+", " ", principal).upper()
            abre("apensada", d, principal)
            entra(f"APENSADA:{principal}", d)
            continue
        if "apensada" in aberto and (cod in DESAPENSA or
                                     ("desapense" in (desp or "").lower() and _cita(desp, numeros))):
            fecha("apensada", d)
            entra("DESPACHO", d)               # desapensada: aguarda novo despacho
            continue
        # Depois do despacho à sanção, a "remessa ao Senado" é só o ofício que comunica o envio à
        # sanção (em registros antigos, com o mesmo código): a proposição não volta ao Senado.
        if cod in SENADO_IDA and marco not in ("SANÇÃO", "PROMULGAÇÃO"):
            entra("SENADO", d); marco = "SENADO"; continue
        if cod in SENADO_VOLTA and marco == "SENADO":
            marco = None; continue
        if cod in SANCAO:
            entra("SANÇÃO", d); marco = "SANÇÃO"; continue
        if cod in PROMULGACAO:
            entra("PROMULGAÇÃO", d); marco = "PROMULGAÇÃO"; continue
        if marco in ("SANÇÃO", "PROMULGAÇÃO"):
            continue                           # só a lei (ou o veto registrado depois) encerra
        lg = lugar(org)
        if lg is None:
            continue
        # Requerimento sobre a proposição (audiência, apensação, retirada de pauta…) é registrado no
        # Plenário ou na comissão onde foi apresentado — não move a proposição. Medido no PL 1998/2020:
        # sem isso, o projeto "ia" ao Plenário e voltava à CSAUDE a cada requerimento.
        if cod in REQUERIMENTO or "requerimento" in (desc or "").lower():
            continue
        if not et and cod == "100":            # o protocolo da apresentação é registrado no Plenário
            entra("APRESENTADA", d)
            continue
        marco = None                           # evento em órgão da Câmara: voltou do Senado
        entra(lg, d)
    # idas e voltas no mesmo dia: a etapa que começa e termina no mesmo dia some; iguais seguidas se juntam
    limpo = []
    for i, (rot, d) in enumerate(et):
        if i + 1 < len(et) and et[i + 1][1] == d and rot not in ("SENADO", "SANÇÃO", "PROMULGAÇÃO", "LEI"):
            continue
        if limpo and limpo[-1][0] == rot:
            continue
        limpo.append([rot, d])
    return {"et": limpo, "per": per, "lei": lei, "fim": None if lei else fim,
            "ua": ev[-1][0] if ev else None}


def universo():
    ids = set()
    for f in (coleta.DADOS / "temas").glob("*.json"):
        if f.stem.isdigit():
            ids |= {p["id"] for p in json.loads(f.read_text(encoding="utf-8"))["props"]}
    for p in json.loads((coleta.DADOS / "proposicoes.json").read_text(encoding="utf-8")):
        if p.get("idProposicao"):
            ids.add(int(p["idProposicao"]))
    return sorted(ids)


def reduz(ev):
    """[data, órgão, código do tipo, descrição do tipo, despacho só se marca etapa]."""
    saida = []
    for e in sorted(ev, key=lambda e: (e.get("dataHora") or "", e.get("sequencia") or 0)):
        texto = f"{e.get('descricaoTramitacao') or ''} {e.get('despacho') or ''}"
        saida.append([(e.get("dataHora") or "")[:10], e.get("siglaOrgao"), e.get("codTipoTramitacao"),
                      e.get("descricaoTramitacao"),
                      (e.get("despacho") or "")[:240] if _MARCO.search(texto) else None])
    return saida


def ultimo_andamento(pid):
    det = (coleta.ler_cache(coleta.CACHE / "proposicoes" / f"{pid}.json") or {}).get("detalhe") or {}
    return ((det.get("statusProposicao") or {}).get("dataHora") or "")[:10]


def precisa(pid):
    c = coleta.ler_cache(CACHE_T / f"{pid}.json")
    if c is None:
        return True
    ua = ultimo_andamento(pid)
    return bool(ua) and ua > (c[-1][0] if c else "")


def baixa(pid):
    r = coleta.get(f"{coleta.API}/proposicoes/{pid}/tramitacoes")
    ev = reduz((r or {}).get("dados") or [])
    coleta.gravar_cache(CACHE_T / f"{pid}.json", ev)
    return len(ev)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--so-medir", action="store_true")
    ap.add_argument("--limite", type=int, default=0,
                    help="máximo de downloads nesta rodada (0 = sem limite). No workflow, o cache "
                         "começa vazio: com limite, ele se completa em alguns dias sem estourar a rodada")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    ids = universo()
    falta = [] if args.so_medir else [p for p in ids if precisa(p)]
    if args.limite and len(falta) > args.limite:
        # primeiro as que mudaram (já têm cache), depois as mais recentes: o que o site mostra primeiro
        tem = [p for p in falta if (CACHE_T / f"{p}.json").exists()]
        falta = (tem + sorted(set(falta) - set(tem), reverse=True))[:args.limite]
    print(f"{len(ids)} proposições; a baixar: {len(falta)}", flush=True)
    feitos = 0
    with ThreadPoolExecutor(coleta.WORKERS) as ex:
        for _ in ex.map(baixa, falta):
            feitos += 1
            if feitos % 1000 == 0:
                print(f"   {feitos}/{len(falta)}", flush=True)
    tem = sum(1 for p in ids if (CACHE_T / f"{p}.json").exists())
    print(f"No cache: {tem} de {len(ids)} · requisições nesta execução: {coleta.contador_req}")


if __name__ == "__main__":
    main()
