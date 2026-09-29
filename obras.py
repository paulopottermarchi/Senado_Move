"""
Câmara Aberta — obras públicas: quanto o contrato cresceu, termo a termo, com o documento de cada passo.

Uso:
    python obras.py                  # obras de SP; contrato e obra no cache são relidos a cada 7–13 dias
    python obras.py --atualizar      # relê todos os contratos e obras
    python obras.py --uf MG          # outra UF (o site hoje publica só SP)

Fontes — todas ligadas por IDENTIFICADOR, nunca por nome:
  1. ObrasGov.br, API pública (api-publica.obrasgov.gestao.gov.br/obras): a obra, pelo id do Cadastro
     Integrado de Projetos de Investimento (CIPI); órgão, situação, município, execução física,
     paralisações, empenhos (com o código da emenda parlamentar) e os CONTRATOS que o gestor ligou à
     obra. Um contrato pode estar ligado a várias obras (manutenção de trechos, por ex.).
  2. Contratos.gov.br (contratos.comprasnet.gov.br/api), sistema de contratos do governo federal:
     para cada contrato ligado, o registro (valor inicial e valor global atual), o histórico de
     termos (aditivos, apostilamentos, rescisão), os empenhos com o valor pago e os extratos
     publicados no Diário Oficial da União, com o link da página do DOU.
     Ligação: o link de transparência que o ObrasGov grava (…/transparencia/contratos/{id}) —
     CONFERIDA: número do contrato e CNPJ do fornecedor têm de coincidir nas duas bases; se não
     coincidem, o contrato fica fora e o motivo vai para o relatório.
  3. CGU, arquivo de emendas parlamentares (download direto do Portal da Transparência): código da
     emenda → autor, como a CGU grava. O ObrasGov traz o código no empenho da obra. Código
     incompleto (sem o ano) não é completado: fica de fora e é contado.

Cobertura, dita sem rodeio: só obras com contrato no Contratos.gov.br, isto é, contratadas por
órgão federal. Obra estadual ou municipal contratada pelo próprio estado ou prefeitura (Metrô, DER,
prefeituras) NÃO está aqui — o contrato dela não está nessa base. Próxima fase: PNCP (contratos de
todos os entes, mas só a partir de 2021–2023).

Medidas (regras — ver CLAUDE.md, "Obras"):
  - Crescimento do contrato = valor global atual − valor inicial, os DOIS campos do registro do
    contrato no Contratos.gov.br. Nominal (sem correção pela inflação); o reajuste de preços
    previsto no contrato entra no valor atual, e a página diz quantos termos são de reajuste.
  - "Valor acumulado" (ObrasGov e Contratos.gov.br) NÃO é usado: medido nos 7.626 contratos do
    ObrasGov, a mediana é 2 vezes o valor global e 10% passam de 28 vezes. O valor global do
    ObrasGov também não: diverge do registro do contrato em 12 de 80 da amostra (desatualizado).
  - Cada termo mostra o valor que o sistema registrou nele. O sistema às vezes só registra o novo
    valor no termo seguinte (um aditivo de acréscimo com o valor antigo, seguido de apostilamento
    com o novo). Por isso a variação por termo é "registrada neste termo", não "causada por ele",
    e o texto do próprio termo vai junto. Se a sequência não fecha com o valor atual, a página diz.
  - Pago = pago + restos a pagar pagos, nos empenhos que o Contratos.gov.br liga ao contrato.

Pessoas: só vínculo com trilha de identificador. Nesta fase, apenas o autor da emenda que empenhou
recurso na obra (código da emenda → CGU). Menção por nome em proposição, notícia ou discurso NÃO
liga ninguém a obra nenhuma (homônimos e nomes genéricos: "Linha 17" aparece em centenas de textos).
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import coleta  # truststore (TLS), caminhos, ler/gravar cache

SAIDA = coleta.BASE / "obras.json"
SAIDA_DEP = coleta.BASE / "obras_deputados.json"   # para o perfil: obras com emenda de cada deputado
CACHE_O = coleta.CACHE / "obras"
OG = "https://api-publica.obrasgov.gestao.gov.br/obras"
CT = "https://contratos.comprasnet.gov.br/api"
EMENDAS_ZIP = ("https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/"
               "emendas-parlamentares/EmendasParlamentares.zip")
UA = "camara-aberta/1.0 (dados públicos; github.com/paulopottermarchi/Senado_Move)"
VALIDADE = timedelta(days=7)   # contrato e dados da obra: relidos depois disso
MAX_TEXTO = 400
BRASILIA = timezone(timedelta(hours=-3))

sessao = requests.Session()
sessao.headers["User-Agent"] = UA
_ultima = defaultdict(float)


class LeiauteMudou(Exception):
    pass


def pedir(url, params=None, intervalo=1.0, tentativas=6):
    """GET educado, uma requisição por segundo por servidor. O ObrasGov devolveu 429 a quem pediu
    rápido (medido em 29/9/2026); o PNCP também. 404 → None."""
    host = url.split("/")[2]
    for n in range(tentativas):
        espera = intervalo - (time.monotonic() - _ultima[host])
        if espera > 0:
            time.sleep(espera)
        _ultima[host] = time.monotonic()
        try:
            r = sessao.get(url, params=params, timeout=120)
        except requests.RequestException:
            time.sleep(10 * (n + 1))
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            return r.json()
        time.sleep(max(int(r.headers.get("retry-after") or 0), 15 * (n + 1)))
    raise RuntimeError(f"sem resposta depois de {tentativas} tentativas: {url} {params or ''}")


def paginado(caminho, **filtro):
    """Todas as páginas de um endpoint do ObrasGov (200 por página, o máximo aceito)."""
    itens, pag = [], 1
    while True:
        d = pedir(OG + caminho, dict(pagina=pag, tamanho_da_pagina=200, **filtro))
        if d is None or "data" not in d or "total_pages" not in d:
            raise LeiauteMudou(f"ObrasGov {caminho}: resposta sem 'data'/'total_pages'")
        itens += d["data"]
        if pag >= d["total_pages"]:
            return itens
        pag += 1


def num(v):
    """'1.234.567,89' (Contratos.gov.br) ou número → float."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(v.replace(".", "").replace(",", "."))


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def curto(t, n=MAX_TEXTO):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def fresco(caminho, atualizar):
    """Cache válido se existe, é recente e não se pediu --atualizar. A validade varia de 7 a 13
    dias conforme o nome do arquivo, para a releitura se espalhar pelos dias em vez de vencer
    tudo junto uma semana depois da primeira coleta."""
    if atualizar or not caminho.exists():
        return None
    folga = timedelta(hours=zlib.crc32(caminho.name.encode()) % 144)
    if datetime.now() - datetime.fromtimestamp(caminho.stat().st_mtime) > VALIDADE + folga:
        return None
    return coleta.ler_cache(caminho)


# ---------------------------------------------------------------- CGU: emendas

def emendas_cgu():
    """{código da emenda (12 dígitos): [código do autor, nome do autor, tipo, ano]}.
    O zip (~32 MB) só é baixado de novo quando a CGU publica outro (Last-Modified)."""
    mapa_c = CACHE_O / "emendas_cgu.json"
    zip_c = CACHE_O / "EmendasParlamentares.zip"
    atual = coleta.ler_cache(mapa_c) or {}
    try:
        h = sessao.head(EMENDAS_ZIP, timeout=60)
        versao = h.headers.get("last-modified")
    except requests.RequestException:
        versao = None
    if atual and (versao is None or atual.get("_versao") == versao):
        return atual
    r = sessao.get(EMENDAS_ZIP, timeout=600)
    if r.status_code != 200:
        print(f"   emendas da CGU: HTTP {r.status_code}; usando a cópia anterior", flush=True)
        return atual
    zip_c.parent.mkdir(parents=True, exist_ok=True)
    zip_c.write_bytes(r.content)
    campos = {"Código da Emenda", "Código do Autor da Emenda", "Nome do Autor da Emenda",
              "Tipo de Emenda", "Ano da Emenda"}
    mapa = {"_versao": versao}
    with zipfile.ZipFile(zip_c) as z, z.open("EmendasParlamentares.csv") as f:
        rd = csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
        if not campos <= set(rd.fieldnames or []):
            raise LeiauteMudou(f"CGU emendas: colunas mudaram ({rd.fieldnames})")
        for x in rd:
            c = x["Código da Emenda"]
            if re.fullmatch(r"\d{12}", c or "") and c not in mapa:
                mapa[c] = [x["Código do Autor da Emenda"], x["Nome do Autor da Emenda"].strip(),
                           x["Tipo de Emenda"], x["Ano da Emenda"]]
    coleta.gravar_cache(mapa_c, mapa)
    zip_c.unlink()   # o mapa basta; o zip não fica no cache do Actions (fechado antes: Windows)
    return mapa


def autores_deputados(emendas):
    """{código do autor na CGU: id do deputado na Câmara}. Só com as duas conferências:
    1. nome exato: o nome que a CGU grava, normalizado (coleta.nome_chave, a mesma regra do TSE),
       é igual ao nome parlamentar ou ao nome eleitoral de UM e só um deputado do site;
    2. mandato: TODOS os anos das emendas daquele código caem num período em exercício do deputado
       na Câmara (o ano da emenda ou o anterior: a emenda ao orçamento do ano Y é apresentada em Y−1).
    O mesmo nome com dois códigos é, em geral, a mesma pessoa como senador e como deputado (Aécio
    Neves, Gleisi Hoffmann): o código de senador não passa na conferência do mandato. Homônimo de
    outra época (Antonio Andrade, MG × TO) também não. Só emenda individual — bancada, comissão e
    relator não são uma pessoa. Devolve também a contagem dos motivos de fora."""
    deps = json.loads(coleta.SAIDA.read_text(encoding="utf-8"))
    por_chave = defaultdict(set)
    for d in deps:
        for n in (d.get("nome"), d.get("nomeEleitoral")):
            if n:
                por_chave[coleta.nome_chave(n)].add(d["id"])
    dep = {d["id"]: d for d in deps}
    cod = defaultdict(lambda: {"nomes": set(), "anos": set(), "individual": True})
    for k, v in emendas.items():
        if k.startswith("_"):
            continue
        a = cod[v[0]]
        a["nomes"].add(coleta.nome_chave(v[1]))
        a["anos"].add(int(v[3]))
        a["individual"] &= "Individual" in v[2]

    def em_exercicio(d, ano):
        return any(p["de"][:4] <= str(ano) and (p["ate"] or "9999")[:4] >= str(ano - 1)
                   for per in (d.get("mandatos") or {}).values() for p in per)

    ligados, fora = {}, Counter()
    for c, a in cod.items():
        ids = set().union(*(por_chave.get(n, set()) for n in a["nomes"]))
        if not ids:
            continue                      # não é deputado do site (senador, ex-deputado, bancada…)
        if not a["individual"]:
            fora["emenda de bancada, comissão ou relator"] += 1
        elif len(ids) > 1:
            fora["nome de mais de um deputado"] += 1
        elif not all(em_exercicio(dep[next(iter(ids))], y) for y in a["anos"]):
            fora["ano da emenda fora do mandato na Câmara"] += 1
        else:
            ligados[c] = next(iter(ids))
    return ligados, fora


def codigo_emenda(v):
    """'202271020008.0' → '202271020008'. Outros formatos ('0', '@', 8 dígitos sem o ano) → None."""
    v = re.sub(r"\.0$", "", str(v or "").strip())
    return v if re.fullmatch(r"\d{12}", v) else None


# ---------------------------------------------------------------- Contratos.gov.br

_ID_LINK = re.compile(r"contratos\.comprasnet\.gov\.br/transparencia/contratos/(\d+)$")


def sem_cpf(lista):
    """Responsáveis do contrato só com função, nome, portaria e datas. O campo "usuario" vem como
    "***.490.498-** - NOME": o pedaço do CPF, mesmo mascarado, não é gravado nem no cache."""
    saida = []
    for x in lista or []:
        nome = (x.get("usuario") or "").split(" - ", 1)[-1].strip()
        saida.append({"funcao": x.get("funcao_id"), "nome": None if re.search(r"\d", nome) else nome,
                      "portaria": x.get("portaria"), "de": x.get("data_inicio"), "ate": x.get("data_fim"),
                      "situacao": x.get("situacao")})
    return saida


def contrato(cid, atualizar):
    """Registro, histórico, empenhos, publicações no DOU e responsáveis de um contrato (cache)."""
    caminho = CACHE_O / "contratos" / f"{cid}.json"
    c = fresco(caminho, atualizar)
    if c is None:
        reg = pedir(f"{CT}/contrato/id/{cid}", intervalo=0.5)
        if isinstance(reg, list):
            reg = reg[0] if reg else None
        c = {"registro": reg,
             "historico": pedir(f"{CT}/contrato/{cid}/historico", intervalo=0.5) or [],
             "empenhos": pedir(f"{CT}/contrato/{cid}/empenhos", intervalo=0.5) or [],
             "publicacoes": pedir(f"{CT}/contrato/{cid}/publicacoes", intervalo=0.5) or []}
    if "responsaveis" not in c:   # cache anterior a 29/9/2026: só esta parte é pedida
        c["responsaveis"] = sem_cpf(pedir(f"{CT}/contrato/{cid}/responsaveis", intervalo=0.5))
        coleta.gravar_cache(caminho, c)
    elif not caminho.exists():
        coleta.gravar_cache(caminho, c)
    return c


# ---------------------------------------------------------------- natureza de cada termo

def _maiusculo(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().upper()


# Natureza pelo TEXTO do próprio termo (o que ele diz e a fundamentação que cita), nunca pelo campo
# "qualificação" do sistema, que o gestor preenche. Medido no contrato 36154/2022 do IFSP (Reitoria):
# o sistema marca "reajuste" em dois aditivos de acréscimo de itens (art. 65, I, "a") e num de
# reequilíbrio (art. 65, II, "d"); só os dois apostilamentos são reajuste (INCC).
NATUREZAS = [
    ("acréscimo", re.compile(r"ACRESC")),
    ("supressão", re.compile(r"SUPRESS|SUPRIM")),
    ("reequilíbrio", re.compile(r"REEQUILIBRIO|RECOMPOSICAO|EQUILIBRIO ECONOMICO")),
    ("reajuste", re.compile(r"REAJUST|REPACTUA|\bINCC\b|\bIPCA\b")),
    ("prazo", re.compile(r"PRORROG|VIGENCIA|PRAZO")),
]
DE_VALOR = ("acréscimo", "supressão", "reequilíbrio", "reajuste")
_REAIS = re.compile(r"R\$\s*(-?\s*[\d.]+,\d{2})")
_PCT = re.compile(r"(\d{1,3}(?:,\d+)?)\s*%")


def natureza(texto):
    t = _maiusculo(texto)
    return [n for n, rx in NATUREZAS if rx.search(t)]


def natureza_de_valor(nat, delta):
    """A natureza a que a variação do termo é atribuída, ou None. Acréscimo e supressão no mesmo
    termo são uma alteração quantitativa só (o saldo). Texto que só fala de prazo com o valor mudando
    é prorrogação — em serviço contínuo (manutenção de rodovia), prorrogar acrescenta o novo período."""
    dv = [n for n in nat if n in DE_VALOR]
    if "acréscimo" in dv and "supressão" in dv:
        dv = [n for n in dv if n not in ("acréscimo", "supressão")] + ["acréscimo/supressão"]
    if not dv and "prazo" in nat and delta:
        dv = ["prorrogação"]
    return dv[0] if len(dv) == 1 else None


def confere(delta, valor, anterior, texto):
    """O valor que o texto do termo declara bate com a variação registrada? True / False / None.
    True: um R$ igual à variação ou ao novo total, ou um percentual que, aplicado ao valor anterior,
    dá a variação (tolerância de 1% ou R$ 1). False: SÓ quando o texto traz valor em R$ e nenhum bate —
    é o caso do valor registrado no termo seguinte. Percentual que não bate não contradiz (a base pode
    ser o saldo a executar, o valor inicial atualizado…): None."""
    t = _maiusculo(texto)
    reais = [num(x.replace(" ", "")) for x in _REAIS.findall(t)]
    pcts = [float(p.replace(",", ".")) for p in _PCT.findall(t)]
    perto = lambda a, b: abs(abs(a) - abs(b)) <= max(1.0, 0.01 * abs(b))
    if any(perto(v, delta) or (valor and perto(v, valor)) for v in reais):
        return True
    if anterior and any(perto(anterior * p / 100, delta) for p in pcts):
        return True
    return False if reais else None


# ---------------------------------------------------------------- CGU: sanções (CEIS e CNEP)

SANCOES = "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/{c}/{d}_{C}.zip"


def sancoes(cnpjs):
    """({cnpj: [[cadastro, categoria, início, fim, órgão sancionador, processo]]}, data do arquivo).
    Cadastros da CGU do dia mais recente publicado (tenta 7 dias). Só pessoa jurídica com CNPJ igual
    ao de uma contratada; as linhas de pessoa física (CPF) não são guardadas. Os cadastros listam
    sanções VIGENTES: sanção já encerrada não aparece, então "vigente na data da assinatura" não é
    verificável por aqui."""
    hoje = datetime.now(BRASILIA).date()
    for k in range(7):
        d = f"{hoje - timedelta(days=k):%Y%m%d}"
        achou, saida = False, defaultdict(list)
        for cad in ("ceis", "cnep"):
            try:
                r = sessao.get(SANCOES.format(c=cad, d=d, C=cad.upper()), timeout=180)
            except requests.RequestException:
                break
            if r.status_code != 200:
                break
            with zipfile.ZipFile(io.BytesIO(r.content)) as z, z.open(z.namelist()[0]) as f:
                rd = csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
                if "CPF OU CNPJ DO SANCIONADO" not in (rd.fieldnames or []):
                    raise LeiauteMudou(f"CGU {cad}: colunas mudaram ({rd.fieldnames})")
                for x in rd:
                    doc = so_digitos(x["CPF OU CNPJ DO SANCIONADO"])
                    if x.get("TIPO DE PESSOA") == "J" and doc in cnpjs:
                        saida[doc].append([cad.upper(), x["CATEGORIA DA SANÇÃO"], x["DATA INÍCIO SANÇÃO"],
                                           x["DATA FINAL SANÇÃO"], x["ÓRGÃO SANCIONADOR"], x["NÚMERO DO PROCESSO"]])
            achou = cad == "cnep"
        if achou:
            return dict(saida), f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return None, None


def montar_contrato(cid, og, c):
    """Contrato para o site, ou (None, motivo) se a ligação não confere."""
    reg = c["registro"]
    if not reg:
        return None, "contrato não encontrado no Contratos.gov.br"
    # Conferência da ligação ObrasGov → Contratos.gov.br: número e CNPJ do fornecedor.
    if so_digitos(reg.get("numero")) != so_digitos(og.get("numero_contrato")):
        return None, f"número diferente ({og.get('numero_contrato')} × {reg.get('numero')})"
    if so_digitos(reg.get("fonecedor_cnpj_cpf_idgener")) != so_digitos(og.get("cnpj_fornecedor_contrato")):
        return None, "CNPJ do fornecedor diferente nas duas bases"
    if reg.get("receita_despesa") == "Receita":
        return None, "contrato de receita, não de despesa"

    inicial, atual = num(reg.get("valor_inicial")), num(reg.get("valor_global"))
    dou = defaultdict(list)
    for p in c["publicacoes"]:
        if p.get("status") == "PUBLICADA" and p.get("link_publicacao"):
            dou[p.get("contratohistorico_id")].append([p.get("data_publicacao"), p["link_publicacao"]])

    termos, anterior = [], None
    composicao = defaultdict(float)
    for h in sorted(c["historico"], key=lambda x: x["id"]):     # ordem de registro no sistema
        v = num(h.get("valor_global"))
        registrado = v if v > 0 else None       # encerramento e rescisão vêm com 0: não é valor
        delta = round(registrado - anterior, 2) if registrado is not None and anterior is not None else None
        texto = " ".join(filter(None, [h.get("observacao"), h.get("objeto") if h.get("tipo") != "Contrato" else None]))
        nat = natureza(texto) if h.get("tipo") not in ("Contrato", "Empenho") else []
        bate = confere(delta, registrado, anterior, texto) if delta else None
        if registrado is not None:
            anterior = registrado
        termos.append({
            "id": h["id"], "data": h.get("data_assinatura"), "tipo": h.get("tipo"),
            "numero": h.get("numero"),
            "nat": nat,                                                   # pelo texto do termo
            "qual": [q["descricao"] for q in h.get("qualificacao_termo") or []],   # campo do sistema
            "valor": registrado, "delta": delta if delta else None,
            "confere": bate,
            "texto": curto(h.get("observacao")),
            "dou": (dou.get(h["id"]) or [None])[0],
        })
    valores = [t["valor"] for t in termos if t["valor"] is not None]
    fecha = bool(valores) and abs(valores[0] - inicial) < 1 and abs(valores[-1] - atual) < 1
    # Decomposição por natureza SÓ quando a sequência de valores fecha com o registro nas duas pontas:
    # em contrato antigo, migrado para o sistema, o valor gravado no histórico não é o total (medido:
    # "Contrato" com R$ 6,47 bi para um registro de R$ 139 mi). A variação conta para uma natureza se
    # o termo tem UMA natureza de valor e o texto não contradiz o valor (valor registrado no termo
    # seguinte → o texto fala de outro montante → "sem natureza conferida").
    for t in termos:
        nv = natureza_de_valor(t["nat"], t["delta"]) if fecha and t["delta"] else None
        t["atribuido"] = nv if nv and t["confere"] is not False else None
        if t["atribuido"]:
            composicao[t["atribuido"]] += t["delta"]
    conta = Counter(n for t in termos for n in t["nat"])
    # Gestor por nome (quem responde pelo contrato); fiscais e demais só pela função e portaria.
    responsaveis = [{**r, "nome": r["nome"] if (r["funcao"] or "").startswith("Gestor") else None}
                    for r in c.get("responsaveis") or []]

    pago = sum(num(e.get("pago")) + num(e.get("rppago")) for e in c["empenhos"])
    return {
        "id": int(cid),
        "numero": reg.get("numero"),
        "orgao": reg.get("orgao_nome"), "unidade": reg.get("unidade_nome"),
        "uasg": reg.get("unidade_codigo"),
        "fornecedor": [reg.get("fornecedor_nome"), reg.get("fonecedor_cnpj_cpf_idgener")],
        "objeto": curto(reg.get("objeto"), 300),
        "categoria": reg.get("categoria"), "modalidade": reg.get("modalidade"),
        "licitacao": reg.get("licitacao_numero"), "processo": reg.get("processo"),
        "assinatura": reg.get("data_assinatura"),
        "vigencia": [reg.get("vigencia_inicio"), reg.get("vigencia_fim")],
        "situacao": reg.get("situacao"),
        "inicial": round(inicial, 2), "atual": round(atual, 2),
        "pago": round(pago, 2), "empenhos": len(c["empenhos"]),
        "termos": termos, "fecha": fecha,
        "naturezas": {n: conta[n] for n, _ in NATUREZAS if conta[n]},       # nº de termos, pelo texto
        "composicao": {n: round(v, 2) for n, v in composicao.items()},      # R$ por natureza, conferido
        "semNatureza": round(atual - inicial - sum(composicao.values()), 2),
        "responsaveis": responsaveis,
        "url": f"https://contratos.comprasnet.gov.br/transparencia/contratos/{cid}",
    }, None


# ---------------------------------------------------------------- ObrasGov por obra

def dados_da_obra(i, atualizar):
    caminho = CACHE_O / "obra" / f"{i.replace('/', '_')}.json"
    d = fresco(caminho, atualizar)
    if d is None:
        f = dict(id_projeto_investimento=i)
        d = {"fisica": paginado("/execucao-fisica", **f),
             "geometria": paginado("/geometria", **f),
             "empenho": paginado("/empenho", **f)}
        coleta.gravar_cache(caminho, d)
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--atualizar", action="store_true", help="reler todos os contratos e obras")
    ap.add_argument("--uf", default="SP")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    uf = args.uf.upper()

    atualizado = (pedir(OG + "/data-atualizacao") or {}).get("data_ultima_atualizacao")
    print(f"ObrasGov atualizado em {atualizado}. Lendo obras de {uf}…", flush=True)
    obras = {o["id_projeto_investimento"]: o for o in paginado("/projeto-investimento", uf_principal=uf)}
    print(f"   {len(obras)} obras de {uf}; lendo contratos ligados (base nacional)…", flush=True)
    contratos_og = paginado("/contrato")
    paralis = defaultdict(list)
    for p in paginado("/historico-situacao-cancelada-paralisada"):
        paralis[p["id_projeto_investimento"]].append(p)

    # Ligações obra → contrato. Conferência de leiaute: todo contrato tem o link de transparência.
    por_obra, obras_do_contrato, sem_link = defaultdict(list), defaultdict(set), 0
    for x in contratos_og:
        m = _ID_LINK.search(x.get("link_transparencia") or "")
        if not m:
            sem_link += 1
            continue
        obras_do_contrato[m[1]].add(x["id_projeto_investimento"])
        if x["id_projeto_investimento"] in obras:
            por_obra[x["id_projeto_investimento"]].append((m[1], x))
    if sem_link > 0.01 * len(contratos_og):
        raise LeiauteMudou(f"{sem_link} de {len(contratos_og)} contratos do ObrasGov sem o link do Contratos.gov.br")
    print(f"   {len(por_obra)} obras de {uf} com contrato ligado "
          f"({sum(len(v) for v in por_obra.values())} ligações)", flush=True)

    emendas = emendas_cgu()
    print(f"   emendas da CGU: {len(emendas) - 1} códigos (arquivo de {emendas.get('_versao')})", flush=True)
    autor_dep, fora_dep = autores_deputados(emendas)
    print(f"   autores de emenda ligados a deputado do site: {len(autor_dep)} códigos "
          f"({len(set(autor_dep.values()))} deputados); fora: {dict(fora_dep)}", flush=True)

    fora, cont_emendas, saida = Counter(), Counter(), []
    for n, (i, lig) in enumerate(sorted(por_obra.items()), 1):
        if n % 25 == 0:
            print(f"   {n}/{len(por_obra)} obras", flush=True)
        o = obras[i]
        contratos = []
        for cid, x in lig:
            c, motivo = montar_contrato(cid, x, contrato(cid, args.atualizar))
            if c is None:
                fora[motivo.split(" (")[0]] += 1
                continue
            c["outrasObras"] = len(obras_do_contrato[cid]) - 1
            contratos.append(c)
        if not contratos:
            continue
        d = dados_da_obra(i, args.atualizar)

        # Emendas: código completo no empenho da obra → autor na CGU.
        por_emenda = defaultdict(float)
        for e in d["empenho"]:
            bruto = e.get("codigo_autor_emenda")
            if bruto in (None, "", "0", 0, "0.0"):
                continue
            k = codigo_emenda(bruto)
            if k is None:
                # medido: "@", "s/e", "-8", "2" — o campo vem preenchido sem código de emenda;
                # 8 dígitos seriam o código sem o ano, que não se completa por aproximação
                cont_emendas["código de 8 dígitos (sem o ano)" if re.fullmatch(r"\d{8}", re.sub(r"\.0$", "", str(bruto)))
                             else "campo sem código válido (@, s/e…)"] += 1
            elif k not in emendas:
                cont_emendas["código sem par na CGU"] += 1
            else:
                cont_emendas["ligada"] += 1
                por_emenda[k] += num(e.get("valor_empenho"))
        lista_emendas = [{"codigo": k, "autor": emendas[k][1], "codigoAutor": emendas[k][0],
                          "tipo": emendas[k][2], "ano": emendas[k][3], "empenhado": round(v, 2),
                          "deputado": autor_dep.get(emendas[k][0])}
                         for k, v in sorted(por_emenda.items())]

        fis = sorted(d["fisica"], key=lambda f: f.get("dt_atualizacao_execucao") or "")
        fis = fis[-1] if fis else None
        municipios = sorted({g["no_municipio"] for g in d["geometria"] if g.get("no_municipio")})
        inicial = sum(c["inicial"] for c in contratos)
        atual = sum(c["atual"] for c in contratos)
        tipos = sorted({t.get("tipo") for t in o.get("eixos_tipos") or [] if t.get("tipo")})
        saida.append({
            "id": i,
            "nome": curto(o.get("desc_nome"), 200),
            "descricao": curto(o.get("desc_projeto"), 300),
            "orgao": o.get("organizacao_resp"),
            "executores": sorted({e["organizacao_executor"] for e in o.get("executores") or []}),
            "tomadores": sorted({e["organizacao_tomador"] for e in o.get("tomadores") or []}),
            "repassadores": sorted({e["organizacao_repassador"] for e in o.get("repassadores") or []}),
            "municipios": municipios,
            "situacao": o.get("situacao"), "natureza": o.get("natureza_intervencao"),
            "especie": o.get("especie_intervencao"), "tipos": tipos,
            "cadastro": o.get("dt_cadastro"),
            "previsto": [o.get("dt_inicial_prevista"), o.get("dt_final_prevista")],
            "efetivo": [o.get("dt_inicial_efetiva"), o.get("dt_final_efetiva")],
            "investimentoPrevisto": round(sum(num(v.get("vl_investimento_previsto"))
                                              for v in o.get("investimentos_previstos") or []), 2),
            "fontes": [[v.get("desc_nome_fonte_recurso"), num(v.get("vl_investimento_previsto"))]
                       for v in o.get("investimentos_previstos") or []],
            "fisica": {"pct": fis.get("percentual_execucao_fisica"), "atualizada": fis.get("dt_atualizacao_execucao"),
                       "fim": (fis.get("dt_final_execucao") or "")[:10] or None} if fis else None,
            "paralisacoes": [[p.get("data_historico_situacao_investimento"),
                              p.get("descricao_historico_situacao_investimento"),
                              curto(p.get("justificativa_cancelada_paralisada"), 300)]
                             for p in sorted(paralis.get(i, []),
                                             key=lambda p: p.get("data_historico_situacao_investimento") or "")],
            "emendas": lista_emendas,
            "contratos": sorted(contratos, key=lambda c: c["assinatura"] or ""),
            "inicial": round(inicial, 2), "atual": round(atual, 2),
            "pago": round(sum(c["pago"] for c in contratos), 2),
        })

    # Sanções vigentes das contratadas (CEIS e CNEP), pelo CNPJ. Falha de rede não para a rodada:
    # sem o arquivo, o site diz "não consultado", nunca "sem sanção".
    cnpjs = {so_digitos(c["fornecedor"][1]) for o in saida for c in o["contratos"]}
    sanc, sanc_data = sancoes(cnpjs)
    for o in saida:
        for c in o["contratos"]:
            c["sancoes"] = None if sanc is None else sanc.get(so_digitos(c["fornecedor"][1]), [])

    # Sanidade (aborta sem gravar): a ligação conferida tem de valer para quase todos.
    total_lig = sum(len(v) for v in por_obra.values())
    if sum(fora.values()) > 0.05 * total_lig:
        raise LeiauteMudou(f"ligação ObrasGov → Contratos.gov.br não conferiu em {sum(fora.values())} "
                           f"de {total_lig}: {dict(fora)}")

    saida.sort(key=lambda o: o["id"])
    meta = {
        "_fonte": "ObrasGov.br (API pública) + Contratos.gov.br (API) + CGU (emendas parlamentares)",
        "_uf": uf,
        "_obrasgovAtualizado": atualizado,
        # data do arquivo da CGU (Last-Modified), em ISO
        "_sancoes": sanc_data,   # data do arquivo CEIS/CNEP da CGU; None = não consultado
        "_emendasCgu": parsedate_to_datetime(emendas["_versao"]).date().isoformat() if emendas.get("_versao") else None,
        "_geradoEm": datetime.now(BRASILIA).isoformat(timespec="minutes"),
        "_cobertura": {"obrasNaUf": len(obras), "obrasComContrato": len(saida),
                       "ligacoes": total_lig, "foraDaConferencia": dict(fora),
                       "emendas": dict(cont_emendas)},
    }
    tmp = SAIDA.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False)[:-1] + ',"obras":[\n')
        f.write(",\n".join(json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in saida))
        f.write("\n]}\n")
    os.replace(tmp, SAIDA)

    # Por deputado (perfil): obras acompanhadas em que há emenda dele. Todas, cresça ou não o
    # contrato — o perfil diz em quantas cresceu, e que o deputado não assina contrato nem aditivo.
    por_dep = defaultdict(dict)
    for o in saida:
        for e in o["emendas"]:
            if not e.get("deputado"):
                continue
            x = por_dep[e["deputado"]].setdefault(o["id"], {
                "obra": o["id"], "nome": o["nome"], "municipios": o["municipios"], "situacao": o["situacao"],
                "inicial": o["inicial"], "atual": o["atual"], "emendas": []})
            x["emendas"].append([e["codigo"], e["ano"], e["empenhado"]])
    obj = {"_uf": uf, "_geradoEm": meta["_geradoEm"], "_obrasAcompanhadas": len(saida),
           "_regra": "autor da emenda (CGU) = deputado só por nome exato e único e com todos os anos "
                     "das emendas do código dentro do mandato na Câmara",
           "deputados": {str(k): sorted(v.values(), key=lambda x: x["obra"]) for k, v in sorted(por_dep.items())}}
    tmp = SAIDA_DEP.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA_DEP)

    # Relatório
    cont = [c for o in saida for c in o["contratos"]]
    pct = sorted((c["atual"] / c["inicial"] - 1) * 100 for c in cont if c["inicial"] > 0)
    q = lambda p: pct[min(len(pct) - 1, int(len(pct) * p))]
    print(f"\n{len(saida)} obras de {uf} com contrato conferido ({len(obras)} no ObrasGov) · {len(cont)} contratos")
    print(f"Fora da conferência: {dict(fora) or 'nenhum'}")
    print(f"Crescimento do contrato (atual ÷ inicial − 1): mediana {q(.5):.1f}% · p75 {q(.75):.1f}% · "
          f"p90 {q(.9):.1f}% · máx {pct[-1]:.1f}% · sem variação {sum(1 for p in pct if abs(p) < .01)}")
    print(f"Histórico que fecha com o registro: {sum(c['fecha'] for c in cont)} de {len(cont)}")
    print(f"Contratos ligados a mais de uma obra: {sum(1 for c in cont if c['outrasObras'])}")
    comp = Counter()
    for c in cont:
        comp.update(c["composicao"])
    cresc_total = sum(c["atual"] - c["inicial"] for c in cont)
    print("Natureza do aumento, pelo texto dos termos (R$ mi): " +
          " · ".join(f"{n} {v / 1e6:.1f}" for n, v in comp.most_common()) +
          f" · sem natureza conferida {(cresc_total - sum(comp.values())) / 1e6:.1f} (de {cresc_total / 1e6:.1f})")
    diverge = sum(1 for c in cont for t in c["termos"]
                  if "REAJUSTE" in t["qual"] and t["nat"] and "reajuste" not in t["nat"])
    print(f"Termos que o sistema chama de reajuste e o texto não: {diverge}")
    print(f"Contratos com responsáveis cadastrados: {sum(1 for c in cont if c['responsaveis'])} de {len(cont)} · "
          f"com gestor: {sum(1 for c in cont if any((r['funcao'] or '').startswith('Gestor') for r in c['responsaveis']))}")
    print(f"Sanções vigentes (CGU, {sanc_data}): " + ("não consultado" if sanc is None else
          f"{sum(1 for v in sanc.values() if v)} contratadas de {len(cnpjs)}"))
    print(f"Emendas nos empenhos das obras: {dict(cont_emendas) or 'nenhuma'}")
    cresc = lambda x: x["atual"] > x["inicial"] + 0.5
    print(f"Deputados com emenda em obra acompanhada: {len(por_dep)} · em obra cujo contrato cresceu: "
          f"{sum(1 for v in por_dep.values() if any(cresc(x) for x in v.values()))}")
    top = sorted(saida, key=lambda o: -(o["atual"] / o["inicial"] if o["inicial"] else 0))[:8]
    for o in top:
        print(f"   {(o['atual'] / o['inicial'] - 1) * 100:6.1f}%  R$ {o['inicial']:>15,.0f} → {o['atual']:>15,.0f}  "
              f"{o['nome'][:60]} ({', '.join(o['municipios'][:2])})".replace(",", "."))
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")


if __name__ == "__main__":
    main()
