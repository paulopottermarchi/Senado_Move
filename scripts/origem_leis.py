"""
Câmara Aberta — "De onde vêm as leis": a origem (autoria) das leis sancionadas na legislatura atual.

Uso:
    python scripts/origem_leis.py                # coleta o que falta, confere e grava site/dados/origem_leis.json
    python scripts/origem_leis.py --nao-gravar   # confere e imprime o relatório, sem gravar
    python scripts/origem_leis.py --diagnostico  # imprime também o que ficou Não classificado, os divergentes e a tabela de órgãos
    python scripts/origem_leis.py --sem-rede     # só o que já está em disco (listas do Senado em cache/origem_leis)

O QUE CONTA (decidido com o usuário em 12/10/2026)
  * LEIS: lei ordinária e lei complementar, distintas, sancionadas ou promulgadas na legislatura atual, pela DATA DA NORMA.
    Emenda constitucional não é lei sancionada: sai à parte, contada e listada. Uma lei com vários coautores, ou vinda de
    mais de um processo do Senado ("fase 2", "Substitutivo-CD"), conta UMA vez (chave = tipo + número).
  * QUEM DEFINE A LISTA É O SENADO: o campo `normaGerada` de /processo ("Lei nº 14.785 de 27/12/2023"), nas listas por sigla
    e ano (PL, PLP, PLC, PLS, PLN, MPV, PEC), baixadas de novo a cada dia: uma lei de hoje pode ser de um PLS de 2007. A Câmara
    só informa a AUTORIA. Única exceção: a lei que SÓ a Câmara registra (evento 251/1012 com número, situação 1140) entra com a
    marca `dataNaoConfirmadaSenado` e a data do evento.
  * DATA: a do Senado (confere com o Planalto nas 26 divergências medidas). Se a Câmara registra a MESMA norma em outra data,
    a lei fica na conta e leva a marca `dataCamaraDiverge`. NÚMERO diferente = DIVERGENTE: fora da conta, e listada.

LIGAÇÃO LEI → PROPOSIÇÃO DA CÂMARA — só por número/identificador EXATO, nunca por ementa, título ou nome. Quatro vias; cada lei
guarda em `via` a que decidiu a ligação (as outras que concordam ficam em `ligacao`):
  1. ligacao_direta: mesmo identificador nas duas casas (MPV e PLN sempre; PL e PLP desde 2019, a numeração única);
  2. fluxo: `outrosNumeros` do detalhe do Senado (casa CD) e os vínculos do fluxo.py (site/dados/fluxo.json), que também aceitam a
     citação do número do PLS na tramitação da Câmara;
  3. numero_lei_camara: o número da lei que a própria Câmara registra na proposição (evento 251/1012), só quando EXATAMENTE uma
     proposição o reivindica. Zero ou várias: não liga. Se o Senado liga essa proposição a OUTRA lei: divergente, não liga;
  4. excecao_curada: entradas/normas_excecoes.json. Cada exceção traz o link do Planalto (número e data) e o da ficha oficial da
     proposição (autoria), categoria, motivo e a data da verificação; sem tudo isso o script aborta. Teto de 5 exceções de lei.
  Os métodos que se sobrepõem têm de concordar. Lei sem proposição achada = "Não classificado": fatia própria, contada e listada
  (aparece na saída mesmo com 0, para auditoria).
  CONSISTÊNCIA: se a proposição ligada registra na Câmara (evento 251/1012) uma norma de OUTRO número, a lei é DIVERGENTE. Só uma
  exceção curada resolve isso, e só quando o Planalto arbitra o número e a data (a data da exceção tem de ser a do Senado).
  PLV é só o veículo da conversão: a lei dele vem da MPV principal (`uriPropPrincipal`), e a origem também.

ORIGEM
  * Pela autoria (`proponente = 1`) no arquivo `proposicoesAutores-{ano}`, pelo ID do autor na `uriAutor` (/deputados/{id} ou
    /orgaos/{id}) e a tabela scripts/mapa_origem.json (id → categoria, com o nome oficial do endpoint /orgaos). NUNCA por
    nome, ementa, texto livre nem pelo valor do tipo de autor. Id fora da tabela = "Não classificado".
  * Autores de categorias diferentes na mesma lei = "Misto". O Executivo se separa pela via: projeto de lei (ordinária ou
    complementar), medida provisória convertida e PLN (créditos e orçamento).

SAÍDA E CHECAGENS
  Grava site/dados/origem_leis.json de forma atômica, só depois de conferir tudo (abortam e mantêm o JSON anterior): soma das
  categorias = total; norma sem número ou data; norma repetida; data fora da legislatura; números e datas fora de ordem;
  contagem por ano e tipo diferente da conferida à mão no Planalto (entradas/origem_leis_referencia.json).
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import requests

import coleta
import fluxo
import partidos
import tramitacoes
from coleta import CACHE, DADOS, ENTRADAS, gravar_cache, ler_cache

csv.field_size_limit(1 << 28)

BULK = partidos.BULK
CACHE_O = CACHE / "origem_leis"
LISTAS_SF = CACHE_O / "senado_lista"
DETALHES_SF = CACHE_O / "senado_detalhe"
SAIDA = DADOS / "origem_leis.json"
MAPA = Path(__file__).resolve().with_name("mapa_origem.json")
EXCECOES = ENTRADAS / "normas_excecoes.json"
REFERENCIA = ENTRADAS / "origem_leis_referencia.json"
FLUXO_JSON = DADOS / "fluxo.json"

ANO_MIN_CAMARA = 1980               # o Senado cita números da Câmara antigos (outrosNumeros)
TIPOS_INDICE = ("PL", "PLP", "PLN", "MPV", "PLV")
SITUACAO_NORMA = "1140"             # "Transformado em Norma Jurídica"
EVENTO_NORMA = {"251", "1012"}      # Transformação em Norma Jurídica (com veto parcial)
LEI_RECENTE_DIAS = 60               # lei mais nova que isso e sem evento 251 na Câmara: o registro pode ainda chegar
# O Senado lista cada processo pelo ano do número NO SENADO: uma lei de 2026 pode vir de um PLS de 2007, então todos os anos
# entram. PLC e PLS existiram até 2019 (numeração única); PL e PLP desde então.
LISTAS_SENADO = {"PL": (2019, None), "PLP": (2019, None), "PLN": (2021, None), "MPV": (2019, None),
                 "PLC": (1991, 2019), "PLS": (1991, 2019), "PEC": (1991, None)}
CAMPOS_LISTA = ("id", "identificacao", "objetivo", "siglaTipoDeliberacao", "normaGerada", "codigoMateria")
VIA_PL = "projeto de lei (ordinária ou complementar)"
VIA_MPV = "medida provisória convertida"
VIA_PLN = "PLN (créditos e orçamento)"
VIAS = [VIA_PL, VIA_MPV, VIA_PLN]
# como a lei foi ligada à proposição (uma só via por lei, a que decidiu); a ordem é a das tabelas
VIAS_LIGACAO = ("ligacao_direta", "fluxo", "numero_lei_camara", "excecao_curada", "so_camara")
NAO_CLASSIFICADO, MISTO = "Não classificado", "Misto"
AVISO = "A origem é a autoria da proposição que virou lei. Não indica quem a aprovou nem quem mais contribuiu para o texto final."

PADRAO_NORMA = re.compile(r"(Emenda\s+Constitucional|Lei\s+Complementar|Lei\s+Ordin[áa]ria|Lei)\s*(?:n[ºo°.]?\s*)?([\d.]+)\s*/\s*(\d{2,4})", re.I)
PADRAO_SENADO = re.compile(r"^(Emenda Constitucional|Lei Complementar|Lei Ordin[áa]ria|Lei Delegada|Lei)\s+n[ºo°.]?\s*([\d.]+)\s+de\s+(\d{2})/(\d{2})/(\d{4})", re.I)
PADRAO_PROCESSO = re.compile(r"^(PLS|PLC|PLP|PLN|PLV|PL|MPV|PEC)\s+(\d+)/(\d{4})")
OFICIAIS = ("https://www.planalto.gov.br/", "https://www.in.gov.br/", "https://pesquisa.in.gov.br/", "https://www.imprensanacional.gov.br/")


class Aborta(Exception):
    """Checagem de sanidade falhou: nada é gravado e o JSON anterior fica."""


# ---------------------------------------------------------------- normas: chave, rótulo, links

def sigla_norma(texto):
    t = re.sub(r"\s+", " ", texto).lower()
    if t.startswith("emenda"):
        return "EC"
    if "complementar" in t:
        return "LC"
    if "delegada" in t:
        return "LD"
    return "LEI"


def ano4(a):
    a = int(a)
    return a if a > 99 else (2000 + a if a < 50 else 1900 + a)


def rotulo_norma(chave):
    sigla, numero = chave[0], chave[1]
    nome = {"EC": "Emenda Constitucional", "LC": "Lei Complementar", "LEI": "Lei", "LD": "Lei Delegada"}[sigla]
    return f"{nome} nº {numero:,}".replace(",", ".")


def rotulo_prop(info, pid):
    tipo, numero, ano = info[pid][:3]
    return f"{tipo} {numero}/{ano}"


def pasta_planalto(ano):
    """O Planalto separa as leis ordinárias em pastas de quatro anos (2019-2022, 2023-2026, 2027-2030…)."""
    base = 2019 + 4 * ((int(ano) - 2019) // 4)
    return f"_ato{base}-{base + 3}"


def urls_modelo(leg):
    pasta = pasta_planalto(leg["inicio"][:4])
    return {
        "planalto": {"LEI": f"https://www.planalto.gov.br/ccivil_03/{pasta}/{{ano}}/lei/L{{numero}}.htm",
                     "LC": "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp{numero}.htm",
                     "EC": "https://www.planalto.gov.br/ccivil_03/constituicao/emendas/emc/emc{numero}.htm"},
        "camara": "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={idCamara}",
        "senado": "https://www25.senado.leg.br/web/atividade/materias/-/materia/{codSenado}",
    }


# ---------------------------------------------------------------- Senado: registro de normas (/processo → normaGerada)

def ler_lista(sigla, ano, rede, hoje, falhas):
    """Itens (CAMPOS_LISTA) de uma lista do Senado. Uma consulta por dia: no mesmo dia vale o cache. Sem rede, vale o que há
    (e, se não há, a lista do fluxo.py, que é a mesma consulta)."""
    c = LISTAS_SF / f"{sigla}-{ano}.json"
    ant = ler_cache(c)
    if ant and (ant["consultadoEm"] == hoje.isoformat() or not rede):
        return ant["itens"]
    if not rede:
        alt = ler_cache(fluxo.LISTAS / f"{sigla}-{ano}.json")
        if alt is not None:
            falhas.append(f"{sigla}-{ano}: lista do fluxo.py, de outra data")
            return [{k: x.get(k) for k in CAMPOS_LISTA} for x in alt]
        falhas.append(f"{sigla}-{ano}: sem lista em disco")
        return []
    d = fluxo.pedir("/processo", sigla=sigla, ano=ano)
    itens = [{k: x.get(k) for k in CAMPOS_LISTA} for x in d or []]
    gravar_cache(c, {"consultadoEm": hoje.isoformat(), "itens": itens})
    return itens


def normas_do_senado(rede, hoje):
    """({(sigla, número): norma}, relatório) com TODAS as normas do registro do Senado, de qualquer data.
    norma = {chave, datas, processos: [{id, cod, ident}]}."""
    pedidos = [(s, a) for s, (a0, a1) in LISTAS_SENADO.items() for a in range(a0, (a1 or hoje.year) + 1)]
    falhas = []
    with ThreadPoolExecutor(6) as ex:
        listas = list(ex.map(lambda p: ler_lista(p[0], p[1], rede, hoje, falhas), pedidos))
    normas, desconhecidas, vistos = {}, [], set()
    for (sigla, ano), itens in zip(pedidos, listas):
        for x in itens:
            ng = x.get("normaGerada")
            if not ng or x["id"] in vistos:
                continue
            vistos.add(x["id"])
            m = PADRAO_SENADO.match(ng)
            if not m:
                if re.match(r"(Lei|Emenda)", ng, re.I):
                    desconhecidas.append(ng)
                continue
            chave = (sigla_norma(m[1]), int(m[2].replace(".", "")))
            n = normas.setdefault(chave, {"chave": chave, "datas": set(), "processos": []})
            n["datas"].add(f"{m[5]}-{m[4]}-{m[3]}")
            n["processos"].append({"id": x["id"], "cod": x["codigoMateria"], "ident": x["identificacao"]})
    if desconhecidas:
        raise Aborta(f"normaGerada do Senado em formato desconhecido ({len(desconhecidas)}): {desconhecidas[:3]}")
    return normas, {"listas": len(pedidos), "falhas": falhas}


def detalhe_senado(pid, rede):
    """Detalhe reduzido (fluxo.reduz_detalhe) de um processo do Senado: cache do fluxo.py, o meu e, por último, a rede."""
    for pasta in (fluxo.DETALHES, DETALHES_SF):
        d = ler_cache(pasta / f"{pid}.json")
        if d:
            return d
    if not rede:
        return None
    bruto = fluxo.pedir(f"/processo/{pid}")
    if bruto is None:
        return None
    d = fluxo.reduz_detalhe(bruto)
    gravar_cache(DETALHES_SF / f"{pid}.json", d)
    return d


# ---------------------------------------------------------------- Câmara: índice de números e candidatas

def indice_camara(ano_atual, rede):
    """idx: (tipo, número, ano) → [id]; info: id → (tipo, número, ano, situação, último status, id da MPV principal)."""
    idx, info = defaultdict(list), {}
    for ano in range(ANO_MIN_CAMARA, ano_atual + 1):
        caminho = partidos.obter("proposicoes", ano, ano_atual) if rede else BULK / f"proposicoes-{ano}.csv"
        if not caminho.exists():
            continue
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                tipo = r["siglaTipo"]
                if tipo not in TIPOS_INDICE:
                    continue
                numero, ano_p = int(r["numero"]), int(r["ano"])
                idx[(tipo, numero, ano_p)].append(r["id"])
                info[r["id"]] = (tipo, numero, ano_p, r["ultimoStatus_idSituacao"], (r["ultimoStatus_dataHora"] or "")[:10],
                                 r["uriPropPrincipal"].rsplit("/", 1)[-1] or None)
    return idx, info


def principal_de(pid, info):
    """A origem do PLV é a MPV principal; o resto é a própria proposição. None = PLV sem MPV achada."""
    tipo, principal = info[pid][0], info[pid][5]
    if tipo != "PLV":
        return pid
    return principal if principal in info and info[principal][0] == "MPV" else None


# ---------------------------------------------------------------- Câmara: norma citada nas tramitações (evento 251/1012)

def eventos_da_camara(pedidos, rede):
    """pedidos: {id: (último status, a lei é recente)} → {id: eventos reduzidos}. Baixa o que falta, o que está defasado e, se a
    lei é recente e o evento 251 ainda não chegou, de novo no máximo a cada 12 h."""
    def um(item):
        pid, (ult, recente) = item
        f = tramitacoes.CACHE_T / f"{pid}.json"
        ev = ler_cache(f)
        if rede:
            defasado = ev is not None and bool(ult) and bool(ev) and ev[-1][0] < ult
            sem_lei = ev is not None and not any(str(e[2]) in EVENTO_NORMA for e in ev)
            novo = ev is not None and (time.time() - f.stat().st_mtime) < 12 * 3600
            if ev is None or defasado or (sem_lei and recente and not novo):
                tramitacoes.baixa(pid)
                ev = ler_cache(f)
        return pid, ev
    with ThreadPoolExecutor(coleta.WORKERS) as ex:
        return dict(ex.map(um, pedidos.items()))


def normas_da_camara(ev):
    """{(sigla, número): data} das normas citadas no despacho dos eventos 251/1012 (a menor data se aparece mais de uma vez)."""
    normas = {}
    for d, _org, cod, _desc, desp in ev or []:
        if str(cod) not in EVENTO_NORMA:
            continue
        m = PADRAO_NORMA.search(desp or "")
        if m:
            chave = (sigla_norma(m[1]), int(m[2].replace(".", "")))
            normas[chave] = min(d, normas.get(chave, d))
    return normas


# ---------------------------------------------------------------- ligação lei → proposição da Câmara (só identificador exato)

def direto(p, idx):
    """Mesmo identificador nas duas casas: MPV e PLN sempre; PL e PLP desde a numeração única (2019)."""
    m = PADRAO_PROCESSO.match(p["ident"])
    if not m:
        return set()
    sigla, numero, ano = m[1], int(m[2]), int(m[3])
    if sigla in ("MPV", "PLN") or (sigla in ("PL", "PLP") and ano >= 2019):
        return set(idx.get((sigla, numero, ano), []))
    return set()


def outros_numeros(d, idx):
    """O número que o Senado guardou para a proposição na Câmara (casaIdentificadora = CD)."""
    ids = set()
    for casa, sigla, numero, ano, _ in (d or {}).get("outros") or []:
        if casa == "CD" and str(numero).isdigit() and str(ano).isdigit():
            ids |= set(idx.get((sigla, int(numero), int(ano)), []))
    return ids


def vinculos_do_fluxo():
    """{id do processo no Senado: id da proposição na Câmara}, dos vínculos que o fluxo.py confirmou por identificador."""
    d = ler_cache(FLUXO_JSON)
    if not d:
        return {}
    campos = d["campos"]
    i_sf, i_cd = campos.index("idSenado"), campos.index("idCamara")
    return {linha[i_sf]: str(linha[i_cd]) for linha in d["proposicoes"] if linha[i_cd]}


CAMPOS_EXCECAO = ("sigla", "tipo", "numero", "ano", "data", "categoria", "motivo", "urlPlanalto", "urlProposicao", "verificadoEm")
FICHAS_OFICIAIS = ("https://www.camara.leg.br/", "https://www25.senado.leg.br/", "https://www.senado.leg.br/",
                   "https://www.congressonacional.leg.br/")
EXCECOES_TETO = 5           # mais exceções de LEI/LC que isso: aborta, e é preciso avisar o usuário (decidido em 12/10/2026)


def validar_excecao(rotulo, e, categorias, hoje):
    """Uma exceção curada de lei ordinária ou complementar diz QUAL norma é, de que origem, POR QUE existe, e traz os dois links
    oficiais: o do Planalto, que prova o número e a data da norma, e o da ficha da proposição no Senado ou na Câmara, que prova a
    autoria (o Planalto sozinho não mostra quem foi o autor), com a data em que alguém abriu os dois. Qualquer falta: aborta."""
    faltam = [c for c in CAMPOS_EXCECAO if not str(e.get(c) or "").strip()]
    if faltam:
        raise Aborta(f"exceção {rotulo}: faltam campos obrigatórios: {', '.join(faltam)}")
    if e["sigla"] not in ("LEI", "LC"):
        raise Aborta(f"exceção {rotulo}: sigla {e['sigla']!r} (aqui só LEI ou LC)")
    if e["categoria"] not in categorias:
        raise Aborta(f"exceção {rotulo}: categoria {e['categoria']!r} fora da tabela")
    for campo in ("data", "verificadoEm"):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(e[campo])):
            raise Aborta(f"exceção {rotulo}: {campo} {e[campo]!r} não é uma data AAAA-MM-DD")
    if e["verificadoEm"] > hoje.isoformat():
        raise Aborta(f"exceção {rotulo}: verificadoEm {e['verificadoEm']} está no futuro")
    if not str(e["urlPlanalto"]).startswith("https://www.planalto.gov.br/"):
        raise Aborta(f"exceção {rotulo}: urlPlanalto não é um endereço do Planalto")
    numero, ano = int(e["numero"]), int(e["ano"])
    padrao = rf"/{ano}/lei/[lL]0*{numero}\.htm$" if e["sigla"] == "LEI" else rf"/lcp/[lL]cp0*{numero}\.htm$"
    if not re.search(padrao, e["urlPlanalto"]):
        raise Aborta(f"exceção {rotulo}: o link do Planalto não é o da {e['tipo']} nº {numero} de {ano}")
    if not str(e["urlProposicao"]).startswith(FICHAS_OFICIAIS):
        raise Aborta(f"exceção {rotulo}: urlProposicao não é a ficha oficial da proposição (Senado ou Câmara)")


def excecoes_por_norma(idx, categorias, hoje):
    """{(sigla, número): [{'pid', 'rotulo', 'e'}]} das exceções curadas de lei ordinária e complementar, já validadas. As de emenda
    constitucional são só do contexto.py e ficam de fora."""
    saida = defaultdict(list)
    for rotulo, e in ((ler_cache(EXCECOES) or {}).get("normas") or {}).items():
        if e.get("sigla") == "EC":
            continue
        validar_excecao(rotulo, e, categorias, hoje)
        m = re.match(r"^([A-Z]+)\s+(\d+)/(\d{4})$", rotulo)
        ids = idx.get((m[1], int(m[2]), int(m[3])), []) if m else []
        if not ids:
            raise Aborta(f"exceção {rotulo}: a proposição não existe nos arquivos da Câmara")
        for pid in ids:
            saida[(e["sigla"], int(e["numero"]))].append({"pid": pid, "rotulo": rotulo, "e": e})
    return saida


def ligar(norma, idx, fl_id, exc, rede):
    """{via: {ids da Câmara}} da lei, pelas três primeiras vias (identificador direto, outrosNumeros e vínculos do fluxo.py) e pela
    exceção curada. Detalhe do Senado (que pode custar uma requisição) só quando o resto não achou nada. A quarta via (o número da
    lei que a própria Câmara registra) precisa dos eventos da Câmara e entra depois, em construir()."""
    por = defaultdict(set)
    for p in norma["processos"]:
        por["identificador"] |= direto(p, idx)
        if p["id"] in fl_id:
            por["fluxo"].add(fl_id[p["id"]])
    for x in exc.get(norma["chave"], []):
        por["exceção"].add(x["pid"])
    for p in norma["processos"]:         # só do que já está em disco
        d = detalhe_senado(p["id"], False)
        if d:
            por["outrosNumeros"] |= outros_numeros(d, idx)
    if not any(por.values()) and rede:
        for p in norma["processos"]:
            por["outrosNumeros"] |= outros_numeros(detalhe_senado(p["id"], True), idx)
    return {k: v for k, v in por.items() if v}


# ---------------------------------------------------------------- autoria e categoria

def carregar_mapa():
    return json.loads(MAPA.read_text(encoding="utf-8"))


def chave_autor(uri):
    """'.../orgaos/78' → 'orgaos/78'; '.../deputados/73453' → 'deputados'; o resto → None."""
    m = re.search(r"/(orgaos|deputados)/(\d+)$", uri or "")
    if not m:
        return None
    return f"orgaos/{m[2]}" if m[1] == "orgaos" else "deputados"


def carregar_autores(ids_por_ano, ano_atual, rede):
    """{id da proposição: [(codTipoAutor, tipoAutor, uriAutor, nomeAutor)]}, só proponente = 1. O nome só serve para o relatório."""
    autores = defaultdict(list)
    for ano, ids in sorted(ids_por_ano.items()):
        caminho = partidos.obter("proposicoesAutores", ano, ano_atual) if rede else BULK / f"proposicoesAutores-{ano}.csv"
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["proponente"] == "1" and r["idProposicao"] in ids:
                    autores[r["idProposicao"]].append((r["codTipoAutor"], r["tipoAutor"], r["uriAutor"], r["nomeAutor"]))
    return autores


def categoria_do_autor(a, mapa):
    k = chave_autor(a[2])
    if k == "deputados":
        return mapa["recursos"]["deputados"]["categoria"]
    if k:
        o = mapa["orgaos"].get(k.split("/")[1])
        if o:
            return o["categoria"]
    return NAO_CLASSIFICADO


def categoria_da_proposicao(lista, mapa):
    """A única categoria dos autores; 'Misto' se mapeados e de categorias diferentes; 'Não classificado' se algum autor não
    está na tabela ou se não há autor."""
    if not lista:
        return NAO_CLASSIFICADO
    cats = {categoria_do_autor(a, mapa) for a in lista}
    if NAO_CLASSIFICADO in cats:
        return NAO_CLASSIFICADO
    return cats.pop() if len(cats) == 1 else MISTO


def categoria_da_norma(props, autores, mapa):
    cats = {categoria_da_proposicao(autores.get(p, []), mapa) for p in props}
    if len(cats) == 1:
        return next(iter(cats))
    return NAO_CLASSIFICADO if NAO_CLASSIFICADO in cats else MISTO


# ---------------------------------------------------------------- referência conferida à mão

def contagem_referencia(universo, tipo, ate):
    """{ano: n} das normas do tipo até a data, e os números."""
    por_ano, numeros = Counter(), []
    for n in universo:
        if n["tipo"] == tipo and n["data"] <= ate:
            por_ano[n["data"][:4]] += 1
            numeros.append(n["numero"])
    return dict(sorted(por_ano.items())), sorted(numeros)


def conferir_referencia(universo, emendas, leg):
    """Teste de regressão: a contagem por ano e tipo, e a faixa de números, até a data da conferência, tem de bater com a lista
    oficial do Planalto conferida à mão. Devolve o relatório; aborta se alguma coisa não bate."""
    ref = ((ler_cache(REFERENCIA) or {}).get("legislaturas") or {}).get(str(leg["id"]))
    if not ref:
        return {"conferida": False, "motivo": f"sem referência para a legislatura {leg['id']}"}
    linhas, erros = [], []
    for nome, tipo, base in (("ordinarias", "LEI", universo), ("complementares", "LC", universo), ("emendas", "EC", emendas)):
        r = ref[nome]
        por_ano, numeros = contagem_referencia(base, tipo, r["ate"])
        faixa = list(range(r["faixa"][0], r["faixa"][1] + 1))
        ok_ano = por_ano == r["porAno"]
        ok_faixa = numeros == faixa
        linhas.append({"tipo": nome, "ate": r["ate"], "pipeline": por_ano, "referencia": r["porAno"],
                       "totalPipeline": len(numeros), "totalReferencia": r["total"], "faixaConfere": ok_faixa})
        if not ok_ano:
            erros.append(f"{nome} até {r['ate']}: pipeline {por_ano} × referência {r['porAno']}")
        if not ok_faixa:
            faltam = sorted(set(faixa) - set(numeros))
            sobram = sorted(set(numeros) - set(faixa))
            erros.append(f"{nome}: números {r['faixa'][0]}–{r['faixa'][1]} não conferem (faltam {faltam[:10]}, sobram {sobram[:10]})")
    if erros:
        raise Aborta("contagem por ano e tipo diferente da referência do Planalto:\n   " + "\n   ".join(erros))
    return {"conferida": True, "conferidoEm": ref.get("conferidoEm"), "linhas": linhas}


# ---------------------------------------------------------------- orquestração

def legislatura_atual(hoje):
    """A legislatura de legislaturas.json (a lista que a coleta traz da Câmara) em que hoje cai. Início e fim nunca ficam fixos no
    código. Se hoje já passou do fim da última legislatura configurada, ABORTA: a 58ª começa em 1º/2/2027, e sem esta checagem o
    script seguiria contando a 57ª como se fosse a atual."""
    legs = ler_cache(DADOS / "legislaturas.json") or []
    if not legs:
        raise Aborta("legislaturas.json ausente ou vazio: não sei qual é a legislatura atual")
    hoje_iso = hoje.isoformat()
    dentro = [l for l in legs if l["inicio"] <= hoje_iso <= l["fim"]]
    if not dentro:
        ultima = max(legs, key=lambda x: x["id"])
        raise Aborta(f"hoje ({hoje_iso}) não cai em nenhuma legislatura de legislaturas.json; a última configurada é a {ultima['id']}ª, "
                     f"de {ultima['inicio']} a {ultima['fim']}. Atualizar legislaturas.json (coleta.py) e conferir "
                     f"entradas/origem_leis_referencia.json, que tem uma chave por legislatura")
    return max(dentro, key=lambda x: x["id"])


def cod_principal(processos):
    """O processo do Senado que leva à ficha da matéria: o original (sem "(fase 2)", "(Substitutivo-CD)"…) e, entre eles, o de menor id."""
    return min(processos, key=lambda p: ("(" in p["ident"], p["id"]))["cod"] if processos else None


def via_da_ligacao(por, necessaria, so_camara):
    """Uma só via por lei, a que decidiu a ligação: exceção curada (quando foi ela que fez a lei entrar na conta), ligação direta
    (mesmo identificador nas duas casas), fluxo.py (outrosNumeros e citação), número de lei registrado pela Câmara, ou só a Câmara
    registra a lei. Sem proposição: None."""
    if so_camara:
        return "so_camara"
    if necessaria:
        return "excecao_curada"
    por = por or {}
    if "identificador" in por:
        return "ligacao_direta"
    if "fluxo" in por or "outrosNumeros" in por:
        return "fluxo"
    if "numero_lei_camara" in por:
        return "numero_lei_camara"
    return None


def construir(rede=True, hoje=None):
    hoje = hoje or date.today()
    leg = legislatura_atual(hoje)
    inicio, fim = leg["inicio"], min(leg["fim"], hoje.isoformat())
    mapa = carregar_mapa()
    categorias = categorias_ordem(mapa)
    t0 = time.time()

    def passo(txt):
        print(f"[{time.time() - t0:4.0f} s] {txt}", flush=True)

    # 1. o registro do Senado define a lista de normas
    sf, leitura = normas_do_senado(rede, hoje)
    passo(f"Senado: {leitura['listas']} listas lidas, {len(sf)} normas no registro (qualquer data)"
          + (f"; AVISOS: {leitura['falhas'][:3]}" if leitura["falhas"] else ""))
    datas_discordam = [n for n in sf.values() if len(n["datas"]) > 1]
    no_periodo = {k: n for k, n in sf.items() if n["datas"] and inicio <= min(n["datas"]) <= fim}
    futuras = [k for k, n in sf.items() if max(n["datas"]) > hoje.isoformat()]
    if futuras:
        raise Aborta(f"norma do Senado com data no futuro: {futuras[:5]}")

    # 2. Câmara: índice de números, candidatas (situação 1140) e as vias 1 a 3 de ligação, mais a exceção curada
    idx, info = indice_camara(hoje.year, rede)
    cands = {pid: v for pid, v in info.items() if v[3] == SITUACAO_NORMA and v[0] in TIPOS_INDICE and v[4] >= inicio}
    passo(f"Câmara: {len(idx)} números indexados; {len(cands)} proposições com situação 1140 e último status desde {inicio}")
    fl_id = vinculos_do_fluxo()
    exc = excecoes_por_norma(idx, categorias, hoje)
    for k, lista in exc.items():          # o Planalto arbitra o número e a data: a data da exceção é a do Senado, ou alguém tem de olhar
        if k in no_periodo and lista[0]["e"]["data"] != min(no_periodo[k]["datas"]):
            raise Aborta(f"exceção {lista[0]['rotulo']}: a data {lista[0]['e']['data']} difere da do Senado "
                         f"({min(no_periodo[k]['datas'])}) para a {rotulo_norma(k)}")
    ligacao = {}
    with ThreadPoolExecutor(coleta.WORKERS) as ex:
        chaves = [k for k in no_periodo if k[0] in ("LEI", "LC")]
        for k, r in zip(chaves, ex.map(lambda k: ligar(no_periodo[k], idx, fl_id, exc, rede), chaves)):
            ligacao[k] = r
    passo(f"Ligação (identificador, outrosNumeros, fluxo.py, exceção): {sum(1 for r in ligacao.values() if r)} das {len(ligacao)} leis com proposição da Câmara")

    # 3. proposições de origem de cada lei (PLV → MPV) e conflito entre as vias
    props_de, motivos_nc, divergentes = {}, {}, []
    for k, por in ligacao.items():
        todos = set().union(*por.values()) if por else set()
        sem_mpv = {p for p in todos if principal_de(p, info) is None}
        props = {principal_de(p, info) for p in todos} - {None}
        if len(por) > 1:
            conflito = [(a, b) for a in por for b in por if a < b and not (por[a] & por[b])]
            if conflito:
                divergentes.append({"chave": k, "motivo": "ligacoesDiscordam",
                                    "detalhe": "; ".join(f"{a}: {sorted(por[a])} × {b}: {sorted(por[b])}" for a, b in conflito)})
                continue
        props_de[k] = props
        if not props:
            motivos_nc[k] = "plvSemMpv" if sem_mpv else "semProposicao"

    # 4. tramitações da Câmara: das proposições ligadas e de todas as candidatas
    corte = (hoje - timedelta(days=LEI_RECENTE_DIAS)).isoformat()
    pedidos = {}
    for k, props in props_de.items():
        recente = min(sf[k]["datas"]) >= corte
        for p in props:
            pedidos[p] = (info[p][4], recente)
    for p in cands:
        pedidos.setdefault(p, (info[p][4], info[p][4] >= corte))
    ev = eventos_da_camara(pedidos, rede)
    passo(f"Tramitações da Câmara: {len(ev)} proposições (requisições nesta execução: {coleta.contador_req})")

    # 5. quarta via: o número da lei que a própria Câmara registra na proposição (evento 251/1012), só quando EXATAMENTE uma
    #    proposição o reivindica. Zero ou mais de uma: não liga. Se essa proposição o Senado liga a OUTRA lei, é divergente.
    reivindica = defaultdict(set)
    for p in cands:
        origem = principal_de(p, info)
        if origem:
            for c in normas_da_camara(ev.get(p)):
                if c[0] in ("LEI", "LC"):
                    reivindica[c].add(origem)
    for k in [k for k, m in motivos_nc.items() if m == "semProposicao"]:
        quem = reivindica.get(k, set())
        if len(quem) > 1:
            motivos_nc[k] = "variasReivindicam"
        if len(quem) != 1:
            continue
        p = next(iter(quem))
        outras = sorted(k2 for k2, ps in props_de.items() if k2 != k and p in ps)
        if outras:
            divergentes.append({"chave": k, "motivo": "ligadoAOutraNorma",
                                "detalhe": f"a Câmara registra a {rotulo_norma(k)} em {rotulo_prop(info, p)}, mas o Senado liga essa proposição à "
                                           + " e à ".join(rotulo_norma(o) for o in outras)})
            del props_de[k], motivos_nc[k]
            continue
        props_de[k] = {p}
        ligacao[k] = {"numero_lei_camara": {p}}
        del motivos_nc[k]
    passo(f"Quarta via (número de lei registrado pela Câmara): {sum(1 for r in ligacao.values() if set(r) == {'numero_lei_camara'})} leis")

    # 6. consistência com a Câmara: o número da norma no evento 251/1012 de cada proposição ligada tem de ser o do Senado. Exceção
    #    curada (o Planalto arbitra o número e a data) resolve a divergência; sem ela, a lei fica fora da conta.
    exc_pid = {(k, x["pid"]): x for k, lista in exc.items() for x in lista}
    resolvidas = {}
    camara_diverge, outros_reclamantes = {}, defaultdict(list)
    for k, props in list(props_de.items()):
        data_sf = min(sf[k]["datas"])
        datas_c = set()
        for p in props:
            nc_lei = {c: dt for c, dt in normas_da_camara(ev.get(p)).items() if c[0] in ("LEI", "LC")}
            if nc_lei and k not in nc_lei:
                if (k, p) in exc_pid:
                    resolvidas[k] = {"pid": p, "camara": sorted(nc_lei.items())}
                    continue
                divergentes.append({"chave": k, "motivo": "numeroDiverge",
                                    "detalhe": f"{rotulo_prop(info, p)} registra na Câmara " + " e ".join(rotulo_norma(c) for c in nc_lei) +
                                               f"; o Senado registra {rotulo_norma(k)}"})
                del props_de[k]
                break
            if k in nc_lei and nc_lei[k] != data_sf:
                datas_c.add(nc_lei[k])
        else:
            if datas_c:
                camara_diverge[k] = min(datas_c)
    divergem = {x["chave"] for x in divergentes}
    prop_da_excecao = {x["pid"]: k for (k, _), x in exc_pid.items()}
    for p in cands:                       # quem mais reclama a mesma lei (só relatório: não vira ligação)
        origem = principal_de(p, info)
        for c in normas_da_camara(ev.get(p)):
            if c in props_de and origem not in props_de[c] and c not in divergem and prop_da_excecao.get(origem, c) == c:
                outros_reclamantes[c].append(rotulo_prop(info, p))

    # 7. normas que só a Câmara registra
    so_camara = {}
    ligadas_a = defaultdict(set)
    for k, props in props_de.items():
        for p in props:
            ligadas_a[p].add(k)
    for p in cands:
        for c, data in normas_da_camara(ev.get(p)).items():
            if c in sf or c[0] not in ("LEI", "LC") or not (inicio <= data <= fim):
                continue
            origem = principal_de(p, info)
            outra = ligadas_a.get(origem, set()) if origem else set()
            if outra:
                divergentes.append({"chave": c, "motivo": "ligadoAOutraNorma",
                                    "detalhe": f"{rotulo_prop(info, p)} registra a {rotulo_norma(c)}, mas o Senado liga {rotulo_prop(info, origem)} à "
                                               + " e à ".join(rotulo_norma(o) for o in sorted(outra))})
                continue
            n = so_camara.setdefault(c, {"chave": c, "datas": set(), "processos": [], "props": set(), "sem_mpv": False})
            n["datas"].add(data)
            if origem:
                n["props"].add(origem)
            else:
                n["sem_mpv"] = True

    # 8. autoria
    ids_por_ano = defaultdict(set)
    for props in list(props_de.values()) + [n["props"] for n in so_camara.values()]:
        for p in props:
            ids_por_ano[info[p][2]].add(p)
    autores = carregar_autores(ids_por_ano, hoje.year, rede)
    passo(f"Autoria: {sum(len(v) for v in ids_por_ano.values())} proposições")

    # 9. registros
    normas, excecoes_aplicadas = [], []

    def registrar(k, data, props, sf_norma, por, camara_data, so_da_camara, motivo_nc=None):
        tipos = {info[p][0] for p in props}
        categoria = categoria_da_norma(props, autores, mapa) if props else NAO_CLASSIFICADO
        x_exc = next((x for x in exc.get(k, []) if x["pid"] in props), None)
        necessaria = bool(x_exc) and (k in resolvidas or set(por or {}) == {"exceção"})
        if x_exc:
            if x_exc["e"]["categoria"] != categoria:
                raise Aborta(f"exceção {x_exc['rotulo']}: a categoria declarada ({x_exc['e']['categoria']}) difere da que a autoria dá ({categoria})")
            excecoes_aplicadas.append({
                "proposicao": x_exc["rotulo"], **{c: x_exc["e"][c] for c in CAMPOS_EXCECAO},
                "necessaria": necessaria, "resolveDivergencia": k in resolvidas,
                "camaraRegistra": [rotulo_norma(c) for c, _ in resolvidas[k]["camara"]] if k in resolvidas else None})
        marcas, motivo = [], None
        if camara_data:
            marcas.append("dataCamaraDiverge")
        if so_da_camara:
            marcas.append("dataNaoConfirmadaSenado")
        if necessaria:
            marcas.append("excecaoCurada")
        if not props:
            motivo = motivo_nc or "semProposicao"
            marcas.append(motivo)
        elif categoria == NAO_CLASSIFICADO:
            lista = [a for p in props for a in autores.get(p, [])]
            motivo = "semAutor" if not lista else "autoriaSemMapa"
            marcas.append(motivo)
        via_exec = None
        if categoria == "Executivo":
            via_exec = VIA_MPV if "MPV" in tipos else VIA_PLN if "PLN" in tipos else VIA_PL
        normas.append({
            "tipo": k[0], "numero": k[1], "data": data, "categoria": categoria, "viaExecutivo": via_exec, "pln": "PLN" in tipos,
            "props": sorted(props), "proposicao": " + ".join(sorted(rotulo_prop(info, p) for p in props)) or None,
            "codSenado": cod_principal(sf_norma["processos"]) if sf_norma else None,
            "processos": [(p["ident"], p["cod"]) for p in sf_norma["processos"]] if sf_norma else [],
            "via": via_da_ligacao(por, necessaria, so_da_camara) if props else None,
            "ligacao": sorted(por) if por else (["evento 251 da Câmara"] if so_da_camara else []),
            "dataCamara": camara_data, "marcas": marcas, "motivo": motivo,
            "reclamantes": outros_reclamantes.get(k, []), "excecao": x_exc["rotulo"] if x_exc else None,
        })

    ja_divergentes = {x["chave"] for x in divergentes if x["chave"] in no_periodo}
    for k, n in no_periodo.items():
        if k[0] not in ("LEI", "LC") or k in ja_divergentes:
            continue
        if len(n["datas"]) > 1:
            divergentes.append({"chave": k, "motivo": "datasDiscordamNoSenado", "detalhe": f"datas no Senado: {sorted(n['datas'])}"})
            continue
        registrar(k, min(n["datas"]), props_de.get(k, set()), n, ligacao.get(k), camara_diverge.get(k), False, motivo_nc=motivos_nc.get(k))
    for k, n in so_camara.items():
        registrar(k, min(n["datas"]), n["props"], None, None, None, True, motivo_nc="plvSemMpv" if n["sem_mpv"] else None)
    if len(excecoes_aplicadas) > EXCECOES_TETO:
        raise Aborta(f"{len(excecoes_aplicadas)} exceções curadas de lei aplicadas; o teto é {EXCECOES_TETO}. Parar e avisar o usuário.")
    emendas = [{"tipo": "EC", "numero": k[1], "data": min(n["datas"]),
                "processos": [p["ident"] for p in n["processos"]], "codSenado": cod_principal(n["processos"])}
               for k, n in sorted(no_periodo.items()) if k[0] == "EC" and len(n["datas"]) == 1]
    divergentes_rot = [{"tipo": x["chave"][0], "numero": x["chave"][1],
                        "data": (min(sf[x["chave"]]["datas"]) if x["chave"] in sf else None),
                        "motivo": x["motivo"], "detalhe": x["detalhe"]} for x in divergentes]
    return {"leg": leg, "inicio": inicio, "fim": fim, "hoje": hoje, "mapa": mapa, "sf": sf, "no_periodo": no_periodo, "info": info,
            "ligacao": ligacao, "normas": sorted(normas, key=lambda n: (n["data"], n["tipo"], n["numero"])), "emendas": emendas,
            "divergentes": divergentes_rot, "so_camara": so_camara, "autores": autores, "ev": ev, "cands": cands,
            "reclamantes": outros_reclamantes, "datas_discordam": datas_discordam, "leitura": leitura,
            "excecoes_aplicadas": excecoes_aplicadas, "resolvidas": resolvidas, "exc": exc}


# ---------------------------------------------------------------- composição, tabela de órgãos e saída

def categorias_ordem(mapa):
    return list(mapa["categorias"]) + [MISTO, NAO_CLASSIFICADO]


def composicao(normas, mapa):
    """Contagem por categoria, o Executivo por via, com e sem as leis que nasceram de PLN. Categorias com 0 ficam na saída."""
    def conta(lista):
        c = Counter(n["categoria"] for n in lista)
        return {"total": len(lista), "categorias": {k: c.get(k, 0) for k in categorias_ordem(mapa)},
                "executivoPorVia": {v: sum(1 for n in lista if n["viaExecutivo"] == v) for v in VIAS}}
    return {"comPLN": conta(normas), "semPLN": conta([n for n in normas if not n["pln"]])}


def tabela_de_orgaos(d):
    """Uma linha por INSTITUIÇÃO, com o nº de leis distintas em que alguma proposição tem autor dela, e o detalhe por id do cadastro
    da Câmara (o TCU tem dois ids; MPU e PGR formam uma linha só, mas os ids seguem separados no detalhe). Lei com autores de
    instituições diferentes conta em cada uma: a soma passa do total, e a página diz."""
    mapa = d["mapa"]
    por_lei = defaultdict(set)
    nomes_nao_mapeados = {}
    for n in d["normas"]:
        for p in n["props"]:
            for a in d["autores"].get(p, []):
                k = chave_autor(a[2]) or f"?{a[2] or a[3]}"
                por_lei[(n["tipo"], n["numero"])].add(k)
                if categoria_do_autor(a, mapa) == NAO_CLASSIFICADO:
                    nomes_nao_mapeados[k] = a[3]

    def instituicao_de(k):
        """(linha, categoria, grupo, nome do id)"""
        if k == "deputados":
            return ("Deputado(a): autoria individual", "Deputados", None, "Deputado(a): autoria individual")
        if k.startswith("orgaos/") and k.split("/")[1] in mapa["orgaos"]:
            o = mapa["orgaos"][k.split("/")[1]]
            return (o.get("instituicao") or o["nome"], o["categoria"], o.get("grupo"), o["nome"])
        nome = nomes_nao_mapeados.get(k) or "(autor fora da tabela)"
        return (nome, NAO_CLASSIFICADO, None, nome)

    linhas = {}
    for ks in por_lei.values():
        contadas = set()
        for k in ks:
            inst, cat, grupo, nome_id = instituicao_de(k)
            linha = linhas.setdefault((inst, cat, grupo), {"leis": 0, "ids": {}})
            linha["ids"].setdefault(k, {"id": k, "nome": nome_id, "leis": 0})["leis"] += 1
            if (inst, cat, grupo) not in contadas:
                linha["leis"] += 1
                contadas.add((inst, cat, grupo))
    saida = [{"nome": inst, "categoria": cat, "grupo": grupo, "leis": v["leis"], "ids": sorted(v["ids"]),
              "detalhe": sorted(v["ids"].values(), key=lambda x: x["id"])}
             for (inst, cat, grupo), v in linhas.items()]
    return sorted(saida, key=lambda r: (-r["leis"], r["nome"]))


def verificar(d, comp):
    """Checagens que abortam. Devolve o relatório da referência."""
    inicio, fim = d["inicio"], d["fim"]
    normas = d["normas"]
    vistos = set()
    for n in normas:
        if not n["numero"] or not re.match(r"^\d{4}-\d{2}-\d{2}$", n["data"] or ""):
            raise Aborta(f"norma sem número ou data: {n['tipo']} {n['numero']} {n['data']}")
        chave = (n["tipo"], n["numero"])
        if chave in vistos:
            raise Aborta(f"norma repetida: {chave}")
        vistos.add(chave)
        if not (inicio <= n["data"] <= fim):
            raise Aborta(f"data fora da legislatura: {n['tipo']} {n['numero']} em {n['data']} (legislatura {inicio} a {fim})")
        if n["categoria"] not in categorias_ordem(d["mapa"]):
            raise Aborta(f"categoria desconhecida em {n['tipo']} {n['numero']}: {n['categoria']}")
        if n["viaExecutivo"] and n["categoria"] != "Executivo":
            raise Aborta(f"via do Executivo sem Executivo em {n['tipo']} {n['numero']}")
        if n["props"] and n["via"] not in VIAS_LIGACAO:
            raise Aborta(f"lei com proposição e sem via de ligação reconhecida: {n['tipo']} {n['numero']} ({n['via']})")
    for nome, c in comp.items():
        if sum(c["categorias"].values()) != c["total"]:
            raise Aborta(f"soma das categorias ({sum(c['categorias'].values())}) diferente do total ({c['total']}) em {nome}")
        if sum(c["executivoPorVia"].values()) != c["categorias"]["Executivo"]:
            raise Aborta(f"o Executivo por via não soma o Executivo em {nome}")
    # números e datas na mesma ordem: uma data fora de lugar quase sempre é erro de leitura
    for tipo in ("LEI", "LC"):
        seq = sorted((n["numero"], n["data"]) for n in normas if n["tipo"] == tipo and "dataNaoConfirmadaSenado" not in n["marcas"])
        fora = [(a, b) for a, b in zip(seq, seq[1:]) if b[1] < a[1]]
        if fora:
            raise Aborta(f"{tipo}: número e data fora de ordem: {fora[:3]}")
    # a conta fecha: leis do Senado no período + só da Câmara = na conta + divergentes que o Senado registra
    div_sf = [x for x in d["divergentes"] if x["data"]]
    if len(normas) + len(div_sf) != len([k for k in d["no_periodo"] if k[0] in ("LEI", "LC")]) + len(d["so_camara"]):
        raise Aborta("a conta não fecha: normas + divergentes diferente do universo do Senado + só da Câmara")
    if len(d["excecoes_aplicadas"]) > EXCECOES_TETO:
        raise Aborta(f"mais de {EXCECOES_TETO} exceções curadas aplicadas")
    # a referência do Planalto conta as leis que EXISTEM, entradas na conta ou não: os divergentes entram na contagem
    return conferir_referencia(normas + div_sf, d["emendas"], d["leg"])


def gravar_json(caminho, obj, linhas):
    """JSON compacto com UM item por linha nas listas de `linhas` (diff diário legível), gravado de forma atômica."""
    partes = []
    for k, v in obj.items():
        if k in linhas:
            corpo = ",\n".join("  " + json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in v)
            partes.append(f' "{k}": [\n{corpo}\n ]' if v else f' "{k}": []')
        else:
            partes.append(f' "{k}": ' + json.dumps(v, ensure_ascii=False, separators=(",", ":")))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text("{\n" + ",\n".join(partes) + "\n}\n", encoding="utf-8")
    os.replace(tmp, caminho)


def montar_saida(d, comp, ref):
    mapa, normas = d["mapa"], d["normas"]
    cats = categorias_ordem(mapa)
    campos = ["tipo", "numero", "data", "categoria", "viaExecutivo", "pln", "proposicao", "idCamara", "codSenado", "via", "ligacao",
              "dataCamara", "marcas", "motivo"]

    def linha(n):
        return [n["tipo"], n["numero"], n["data"], cats.index(n["categoria"]),
                VIAS.index(n["viaExecutivo"]) if n["viaExecutivo"] else None, 1 if n["pln"] else 0, n["proposicao"], n["props"],
                n["codSenado"], n["via"], n["ligacao"], n["dataCamara"], n["marcas"], n["motivo"]]
    por_via = Counter(n["via"] for n in normas if n["via"])
    return {
        "_gerado": d["hoje"].isoformat(),
        "_fonte": "Lista de leis: registro do Senado (/processo, campo normaGerada). Autoria: arquivos proposicoesAutores da Câmara, pelo identificador do autor.",
        "_aviso": AVISO,
        "_regras": ["Lei = lei ordinária ou complementar sancionada ou promulgada na legislatura, pela data da norma; emenda constitucional fica à parte.",
                    "A lista de leis vem do Senado; a Câmara só informa a autoria. Lei que só a Câmara registra leva a marca dataNaoConfirmadaSenado.",
                    "A ligação lei → proposição é só por identificador exato (nunca por ementa ou nome), em quatro vias: ligacao_direta, fluxo, numero_lei_camara (o número da lei que a própria Câmara registra, só se uma única proposição o reivindica) e excecao_curada. Sem proposição achada: Não classificado.",
                    "Número da norma diferente entre o Senado e a Câmara: fora da conta (divergentes), salvo exceção curada com os dois links oficiais. Data diferente: dentro da conta, com a marca dataCamaraDiverge.",
                    "A categoria do autor sai de scripts/mapa_origem.json pelo identificador do órgão; id fora da tabela é Não classificado."],
        "legislatura": {"id": d["leg"]["id"], "inicio": d["inicio"], "fim": d["leg"]["fim"], "ate": d["fim"],
                        "ultimaLei": max((n["data"] for n in normas), default=None)},
        "categorias": cats,
        "vias": VIAS,
        "viasLigacao": list(VIAS_LIGACAO),
        "composicao": comp,
        "totais": {"leis": len(normas), "ordinarias": sum(1 for n in normas if n["tipo"] == "LEI"),
                   "complementares": sum(1 for n in normas if n["tipo"] == "LC"), "emendas": len(d["emendas"]),
                   "naoClassificado": sum(1 for n in normas if n["categoria"] == NAO_CLASSIFICADO),
                   "divergentes": len(d["divergentes"]), "dataCamaraDiverge": sum(1 for n in normas if "dataCamaraDiverge" in n["marcas"]),
                   "dataNaoConfirmadaSenado": sum(1 for n in normas if "dataNaoConfirmadaSenado" in n["marcas"]),
                   "excecoesAplicadas": len(d["excecoes_aplicadas"]), "excecoesTeto": EXCECOES_TETO,
                   "porVia": {v: por_via.get(v, 0) for v in VIAS_LIGACAO},
                   "porAno": {a: {"ordinarias": sum(1 for n in normas if n["tipo"] == "LEI" and n["data"][:4] == a),
                                  "complementares": sum(1 for n in normas if n["tipo"] == "LC" and n["data"][:4] == a)}
                              for a in sorted({n["data"][:4] for n in normas})}},
        "referencia": ref,
        "urls": urls_modelo(d["leg"]),
        "excecoes": d["excecoes_aplicadas"],
        "orgaos": tabela_de_orgaos(d),
        "campos": campos,
        "normas": [linha(n) for n in normas],
        "emendas": d["emendas"],
        "divergentes": d["divergentes"],
    }


# ---------------------------------------------------------------- relatório

def pct(q, t):
    return f"{100 * q / t:5.1f}%" if t else "   —  "


def rotulo_lei(n):
    return f"{'Lei Complementar' if n['tipo'] == 'LC' else 'Lei'} nº {n['numero']:,}".replace(",", ".")


def data_br(iso):
    return "/".join(reversed(iso.split("-"))) if iso else "—"


ROTULO_VIA = {"ligacao_direta": "ligação direta (mesmo identificador nas duas casas)",
              "fluxo": "fluxo.py (outrosNumeros e citação do número do PLS)",
              "numero_lei_camara": "número de lei que a própria Câmara registra",
              "excecao_curada": "exceção curada (o Planalto arbitra número e data)",
              "so_camara": "só a Câmara registra a lei (a lei não está no registro do Senado)"}


def depois_da_referencia(normas, ref):
    """Leis com data posterior à última conferida à mão no Planalto, por tipo (todas, se não há referência)."""
    ate = {l["tipo"]: l["ate"] for l in ref.get("linhas", [])}
    return [n for n in normas if n["data"] > ate.get({"LEI": "ordinarias", "LC": "complementares"}[n["tipo"]], "0000-00-00")]


def relatorio(d, comp, ref):
    normas, mapa = d["normas"], d["mapa"]
    print(f"\n=== LEGISLATURA {d['leg']['id']} ({d['inicio']} a {d['fim']}) ===")
    ordi = sum(1 for n in normas if n["tipo"] == "LEI")
    comp_ = sum(1 for n in normas if n["tipo"] == "LC")
    print(f"Leis na conta: {len(normas)} ({ordi} ordinárias, {comp_} complementares) · emendas constitucionais à parte: {len(d['emendas'])} · "
          f"divergentes (fora da conta): {len(d['divergentes'])}")
    for nome, c, filtro in (("COM PLN", comp["comPLN"], lambda n: True), ("SEM PLN", comp["semPLN"], lambda n: not n["pln"])):
        print(f"\n--- COMPOSIÇÃO {nome}: {c['total']} leis ---")
        for k in categorias_ordem(mapa):
            q = c["categorias"][k]
            print(f"   {k:56s} {q:5d} {pct(q, c['total'])}")
            if k == "Executivo" and q:
                for v in VIAS:
                    print(f"        via {v:52s} {c['executivoPorVia'][v]:5d} {pct(c['executivoPorVia'][v], c['total'])}")
        print(f"   {'Divergente (fora da conta)':56s} {len(d['divergentes']):5d}")
        sc = [n for n in normas if "dataNaoConfirmadaSenado" in n["marcas"] and filtro(n)]
        print(f"   {'Data não confirmada pelo Senado (dentro da conta)':56s} {len(sc):5d}"
              + (f"   ({', '.join(rotulo_lei(n) for n in sc)})" if sc else ""))
    por_via = Counter(n["via"] for n in normas if n["via"])
    print(f"\n--- LIGAÇÃO LEI → PROPOSIÇÃO, POR VIA ({len(normas)} leis) ---")
    for v in VIAS_LIGACAO:
        print(f"   {ROTULO_VIA[v]:66s} {por_via.get(v, 0):5d}")
    print(f"   {'sem proposição ligada (Não classificado)':66s} {sum(1 for n in normas if not n['props']):5d}")
    if ref.get("conferida"):
        print(f"\n--- REGRESSÃO CONTRA A REFERÊNCIA DO PLANALTO (conferida à mão em {ref['conferidoEm']}): BATE ---")
        for l in ref["linhas"]:
            print(f"   {l['tipo']:15s} até {l['ate']}: {l['totalPipeline']} (referência {l['totalReferencia']}) · por ano {l['pipeline']}")
    else:
        print(f"\n--- REFERÊNCIA DO PLANALTO: NÃO CONFERIDA ({ref['motivo']}) ---")
    print(f"\nLeis com data depois da última conferida à mão no Planalto: {len(depois_da_referencia(normas, ref))}")
    print("\n--- LEIS POR ANO E TIPO (todas as datas da legislatura) ---")
    for tipo in ("LEI", "LC"):
        c = Counter(n["data"][:4] for n in normas if n["tipo"] == tipo)
        print(f"   {tipo:3s} {dict(sorted(c.items()))}  total {sum(c.values())}")
    c = Counter(n["data"][:4] for n in d["emendas"])
    print(f"   EC  {dict(sorted(c.items()))}  total {sum(c.values())}")
    print(f"\nExceções curadas aplicadas: {len(d['excecoes_aplicadas'])} (teto {EXCECOES_TETO}): "
          + "; ".join(f"{x['proposicao']} → {x['tipo']} nº {x['numero']}" + (" [necessária]" if x["necessaria"] else " [não necessária]") for x in d["excecoes_aplicadas"]))


def url_norma(d, tipo, numero, data):
    return urls_modelo(d["leg"])["planalto"][tipo].format(ano=data[:4] if data else "", numero=numero)


def url_ficha(d, n):
    u = urls_modelo(d["leg"])
    return [u["camara"].format(idCamara=i) for i in n["props"]]


def diagnostico(d, comp, ref):
    """O que a pessoa que revisa precisa ver: as exceções curadas com o que cada fonte diz, a quarta via lei a lei, o que ficou Não
    classificado e por quê, os divergentes, as datas diferentes, como cada lei foi ligada e a tabela de órgãos."""
    normas, info, sf, ev = d["normas"], d["info"], d["sf"], d["ev"]
    u = urls_modelo(d["leg"])
    rot = rotulo_lei
    br = data_br

    print(f"\n=== EXCEÇÕES CURADAS APLICADAS: {len(d['excecoes_aplicadas'])} (teto {EXCECOES_TETO}) ===")
    for x in d["excecoes_aplicadas"]:
        k = ("LEI" if x["sigla"] == "LEI" else "LC", int(x["numero"]))
        print(f"\n   {x['proposicao']} → {x['tipo']} nº {x['numero']}, de {br(x['data'])} · categoria {x['categoria']} · "
              f"verificada em {x['verificadoEm']} · {'NECESSÁRIA' if x['necessaria'] else 'não necessária (as outras vias já ligam a lei)'}")
        print(f"      motivo: {x['motivo']}")
        print(f"      Planalto (número e data): {x['urlPlanalto']}")
        print(f"      Ficha da proposição (autoria): {x['urlProposicao']}")
        if x["resolveDivergencia"]:
            r = d["resolvidas"][k]
            n_sf = sf[k]
            print("      O QUE CADA FONTE DIZ:")
            print("        Senado (registro /processo): " + " · ".join(f"{p['ident']} → normaGerada \"{rot({'tipo': k[0], 'numero': k[1]})} de {br(min(n_sf['datas']))}\" (matéria {p['cod']})" for p in n_sf["processos"]))
            print(f"        Câmara (evento 251/1012 de {rotulo_prop(info, r['pid'])}): " + " | ".join(
                f"\"{e[4]}\" ({br(e[0])})" for e in (ev.get(r['pid']) or []) if str(e[2]) in EVENTO_NORMA)
                + " → registra a " + " e ".join(rotulo_norma(c) for c, _ in r["camara"]))
            outros = sorted(rotulo_prop(info, p) for p in d["cands"] if r["camara"][0][0] in normas_da_camara(ev.get(p)) and p != r["pid"])
            print(f"        Quem mais a Câmara registra como {rotulo_norma(r['camara'][0][0])}: {', '.join(outros) or 'ninguém'}"
                  f" (o Senado liga a {rotulo_norma(r['camara'][0][0])} a essa proposição)")
            print(f"        Sem a exceção: DIVERGENTE por número ({rotulo_norma(r['camara'][0][0])} na Câmara × {rotulo_norma(k)} no Senado); a data é a mesma ({br(min(n_sf['datas']))}).")

    q4 = [n for n in normas if n["via"] == "numero_lei_camara"]
    print(f"\n=== QUARTA VIA (número de lei que a Câmara registra, uma única proposição o reivindica): {len(q4)} leis ===")
    for n in q4:
        p = n["props"][0]
        ev_lei = [e for e in (ev.get(p) or []) if str(e[2]) in EVENTO_NORMA]
        print(f"\n   {rot(n)}, de {br(n['data'])} · origem {n['categoria']}")
        print(f"      lei (Planalto): {url_norma(d, n['tipo'], n['numero'], n['data'])}")
        print(f"      proposição que a Câmara registra como esta lei: {n['proposicao']} · ficha: {url_ficha(d, n)[0]}")
        print("      evento da Câmara: " + " | ".join(dict.fromkeys(f"\"{e[4]}\" ({br(e[0])})" for e in ev_lei)))
        print(f"      autoria nos dados da Câmara: {[(chave_autor(a[2]), a[3]) for a in d['autores'].get(p, [])]}")
        print("      processos do Senado desta lei: " + " · ".join(f"{ident} → {u['senado'].format(codSenado=cod)}" for ident, cod in n["processos"]))

    nc = [n for n in normas if n["categoria"] == NAO_CLASSIFICADO]
    print(f"\n=== NÃO CLASSIFICADO: {len(nc)} leis ({dict(Counter(n['motivo'] for n in nc))}) ===")
    for n in nc:
        print(f"   {rot(n)} de {br(n['data'])} · {n['motivo']} · proposição: {n['proposicao']}"
              + (f" · autores fora da tabela: {sorted({(chave_autor(a[2]) or a[2]) + ' ' + a[3] for p in n['props'] for a in d['autores'].get(p, []) if categoria_do_autor(a, d['mapa']) == NAO_CLASSIFICADO})}"
                 if n["props"] else "")
              + (f" · a Câmara registra a lei em: {n['reclamantes']}" if n["reclamantes"] else "")
              + f"\n      {url_norma(d, n['tipo'], n['numero'], n['data'])}")
    print(f"\n=== DIVERGENTES (fora da conta): {len(d['divergentes'])} ===")
    for x in d["divergentes"]:
        print(f"   {x['tipo']} {x['numero']} ({x['data']}) · {x['motivo']} · {x['detalhe']}")
    dc = [n for n in normas if "dataCamaraDiverge" in n["marcas"]]
    print(f"\n=== DATA DA CÂMARA DIVERGE (na conta, com a data do Senado): {len(dc)} ===")
    dias = Counter((date.fromisoformat(n["dataCamara"]) - date.fromisoformat(n["data"])).days for n in dc)
    print("   diferença em dias (Câmara − Senado):", dict(sorted(dias.items())))
    sc = [n for n in normas if "dataNaoConfirmadaSenado" in n["marcas"]]
    print(f"\n=== DATA NÃO CONFIRMADA PELO SENADO (só a Câmara registra a lei): {len(sc)} ===")
    for n in sc:
        print(f"   {rot(n)} · data do evento {br(n['data'])} · {n['proposicao']} · {n['categoria']}")
    print("\n=== COMO CADA LEI FOI LIGADA (todas as vias que concordam) ===")
    print("  ", dict(Counter("+".join(n["ligacao"]) or "(nenhuma)" for n in normas).most_common()))
    multi = [n for n in normas if len(n["props"]) > 1]
    print(f"   leis com mais de uma proposição ligada: {len(multi)}", [(rot(n), n["proposicao"], n["categoria"]) for n in multi][:5])
    rec = [n for n in normas if n["reclamantes"] and n["categoria"] != NAO_CLASSIFICADO]
    print(f"   leis em que OUTRA proposição da Câmara também registra o mesmo número (não entram na conta): {len(rec)}",
          [(rot(n), n["proposicao"], n["reclamantes"]) for n in rec][:6])
    print("\n=== ÓRGÃOS (instituição, categoria, nº de leis em que há autor dela; ids separados no detalhe) ===")
    for r in tabela_de_orgaos(d):
        print(f"   {r['nome'][:60]:60s} | {r['categoria'][:30]:30s} | {r['leis']:4d} | "
              + "; ".join(f"{x['id']} {x['nome'][:34]} ({x['leis']})" for x in r["detalhe"]) + (f" | {r['grupo']}" if r["grupo"] else ""))
    depois = depois_da_referencia(normas, ref)
    print(f"\n=== DEPOIS DAS DATAS DA REFERÊNCIA DO PLANALTO: {len(depois)} leis (ainda sem conferência à mão) ===")
    for n in depois:
        print(f"   {rot(n)} de {br(n['data'])} · {n['categoria']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sem-rede", action="store_true", help="só o que já está em disco")
    ap.add_argument("--nao-gravar", action="store_true", help="confere e imprime, sem gravar o JSON")
    ap.add_argument("--diagnostico", action="store_true", help="imprime também o que ficou Não classificado, os divergentes e a tabela de órgãos")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        d = construir(rede=not args.sem_rede)
        comp = composicao(d["normas"], d["mapa"])
        ref = verificar(d, comp)
        relatorio(d, comp, ref)
        if args.diagnostico:
            diagnostico(d, comp, ref)
        if not args.nao_gravar:
            saida = montar_saida(d, comp, ref)
            gravar_json(SAIDA, saida, linhas=("normas", "emendas", "divergentes", "orgaos"))
            print(f"\nGravado {SAIDA} ({SAIDA.stat().st_size / 1024:.0f} kB)")
    except (Aborta, RuntimeError, requests.RequestException) as e:
        # RuntimeError: o Senado ou a Câmara não responderam depois de todas as tentativas (fluxo.pedir, coleta.get)
        print(f"\nABORTADO, nada foi gravado (o JSON anterior fica): {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
