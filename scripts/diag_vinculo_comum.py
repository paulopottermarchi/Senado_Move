"""
Câmara Aberta — DIAGNÓSTICO de viabilidade (não publica nada): vínculo entre obras e as pessoas
formalmente ligadas a elas, sempre com documento.

Este módulo é a base comum dos scripts diag_vinculo_e*.py:
  - `pedir()`: GET educado (intervalo por servidor, 0,5 s por padrão), cache em disco por URL,
    medição do tempo de cada requisição (cache/diag_vinculo/tempos.jsonl) e registro de falhas.
    Reaproveita a sessão (User-Agent identificado) e o TLS de obras.py/coleta.py (truststore).
    NUNCA desliga a verificação de certificado.
  - `Arestas`: grava UMA aresta por linha em cache/diag_vinculo_arestas.jsonl com os campos pedidos.
  - amostra de 60 contratos variados por órgão (determinística).

Regras do diagnóstico (do pedido):
  - Vínculo só por identificador exato ou chave posicional (órgão + cargo + data). Nome isolado não prova.
  - Só agentes públicos no exercício da função. Sem CPF. Representante da empresa contratada: só contagem.
  - Fato documentado, nunca "suspeito", "irregular" ou ranking de pessoas.
  - Via bloqueada (robots.txt, WAF, cadastro, captcha, termo): registra o motivo e segue, sem contornar.
"""

import hashlib
import json
import random
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import coleta                      # injeta o truststore (TLS) e traz os caminhos
import obras                       # sessão com User-Agent identificado; CT, OG; fresco()

BASE = coleta.BASE
CACHE = coleta.CACHE
DIAG = CACHE / "diag_vinculo"
DIAG.mkdir(parents=True, exist_ok=True)
ARESTAS = CACHE / "diag_vinculo_arestas.jsonl"
TEMPOS = DIAG / "tempos.jsonl"
FALHAS = DIAG / "falhas.jsonl"
BLOQUEIOS = DIAG / "bloqueios.json"

sessao = obras.sessao
_ultima = defaultdict(float)

CATEGORIAS = (
    "signatário do extrato (DOU)",
    "signatário do contrato/termo (PDF)",
    "agente de contratação/pregoeiro",
    "autoridade que homologou",
    "autor de emenda parlamentar vinculada documentalmente",
)


# ----------------------------------------------------------------------------- utilidades

def sha256(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def norm(s):
    """Sem acento, maiúsculo, só letras/dígitos/espaço."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", s).split())


def so_digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def pessoa_id(nome, orgao_uasg):
    """Provisório: hash do nome normalizado + órgão/UASG. NÃO une homônimos de órgãos diferentes."""
    return sha256(f"{norm(nome)}|{orgao_uasg}")[:16]


def aresta_id(obra_id, contrato_id, pessoa, categoria, identificador):
    return sha256(f"{obra_id}|{contrato_id}|{pessoa}|{categoria}|{identificador}")[:20]


def registrar(arq, **campos):
    with open(arq, "a", encoding="utf-8") as f:
        f.write(json.dumps(campos, ensure_ascii=False) + "\n")


def falha(via, **campos):
    registrar(FALHAS, via=via, **campos)


def bloqueio(via, host, motivo, **extra):
    d = {}
    if BLOQUEIOS.exists():
        d = json.loads(BLOQUEIOS.read_text(encoding="utf-8"))
    d[f"{via}|{host}"] = {"via": via, "host": host, "motivo": motivo, **extra}
    BLOQUEIOS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------- requisições

def pedir(url, params=None, via="", intervalo=0.5, tentativas=3, bruto=False, cache_nome=None, timeout=90):
    """GET educado, uma requisição por `intervalo` s por servidor, com cache em disco e tempo medido.

    Devolve (dados, situacao): situacao ∈ 'ok' | 'cache' | '404' | 'http_NNN' | 'erro_rede'.
    `bruto=True` devolve os bytes (PDF etc.) em vez de JSON. Respostas 200 viram cache; 404 também (por
    nome com sufixo .404), para não repetir pergunta sem resposta. 429/5xx: espera e tenta de novo.
    """
    chave = cache_nome or sha256(url + json.dumps(params or {}, sort_keys=True))[:32]
    pasta = DIAG / "http"
    pasta.mkdir(exist_ok=True)
    cam = pasta / (chave + (".bin" if bruto else ".json"))
    c404 = pasta / (chave + ".404")
    if cam.exists():
        return (cam.read_bytes() if bruto else json.loads(cam.read_text(encoding="utf-8"))), "cache"
    if c404.exists():
        return None, c404.read_text(encoding="utf-8") or "404"
    host = url.split("/")[2]
    ultimo = "erro_rede"
    for n in range(tentativas):
        espera = intervalo - (time.monotonic() - _ultima[host])
        if espera > 0:
            time.sleep(espera)
        t0 = time.monotonic()
        _ultima[host] = t0
        try:
            r = sessao.get(url, params=params, timeout=timeout)
        except Exception as e:
            ultimo = "erro_rede"
            registrar(TEMPOS, via=via, host=host, s=round(time.monotonic() - t0, 3), status=f"erro:{type(e).__name__}")
            time.sleep(5 * (n + 1))
            continue
        dt = round(time.monotonic() - t0, 3)
        registrar(TEMPOS, via=via, host=host, s=dt, status=r.status_code, bytes=len(r.content))
        if r.status_code in (404, 204):         # 204 = consulta sem resultado (PNCP)
            c404.write_text(str(r.status_code), encoding="utf-8")
            return None, str(r.status_code)
        if r.status_code == 200:
            ct = r.headers.get("content-type", "")
            if bruto:
                cam.write_bytes(r.content)
                return r.content, "ok"
            if "json" in ct:
                cam.write_text(r.text, encoding="utf-8")
                return r.json(), "ok"
            ultimo = f"http_200_{ct.split(';')[0]}"     # 200 mas não é JSON (página de bloqueio, HTML…)
            falha(via, url=url, motivo=ultimo, inicio=r.text[:120])
            return None, ultimo
        ultimo = f"http_{r.status_code}"
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(max(int(r.headers.get("retry-after") or 0), 10 * (n + 1)))
            continue
        falha(via, url=url, motivo=ultimo, inicio=r.text[:120])
        return None, ultimo
    falha(via, url=url, motivo=ultimo)
    return None, ultimo


# ----------------------------------------------------------------------------- arestas

class Arestas:
    """Escreve uma aresta por linha. `aresta_id` é estável: o mesmo achado não duplica entre execuções."""

    def __init__(self, caminho=ARESTAS):
        self.caminho = caminho
        self.vistos = set()
        if caminho.exists():
            for linha in caminho.read_text(encoding="utf-8").splitlines():
                try:
                    self.vistos.add(json.loads(linha)["aresta_id"])
                except Exception:
                    pass

    def nova(self, *, obra_id, contrato_id, nome, cargo, categoria, via, identificador_exato, metodo,
             orgao_uasg, url_documento, hash_documento, trecho_literal, data_do_vinculo=None,
             data_publicacao=None, pagina=None, vinculo_documental=True, confirmacao_independente=None,
             vinculo_temporal="nao_verificado", ref_quadro=None, script=None, **extra):
        assert categoria in CATEGORIAS, categoria
        pid = pessoa_id(nome, orgao_uasg)
        ident = f"{identificador_exato.get('tipo')}={identificador_exato.get('valor')}"
        aid = aresta_id(obra_id, contrato_id, pid, categoria, ident)
        a = {
            "aresta_id": aid, "obra_id": obra_id, "contrato_id": contrato_id, "pessoa_id": pid,
            "nome": nome, "cargo": cargo, "categoria": categoria,
            "data_do_vinculo": data_do_vinculo, "data_publicacao": data_publicacao,
            "via": via, "url_documento": url_documento, "pagina": pagina, "hash_documento": hash_documento,
            "identificador_exato": identificador_exato, "trecho_literal": (trecho_literal or "")[:300],
            "metodo": metodo, "vinculo_documental": bool(vinculo_documental),
            "confirmacao_independente": list(confirmacao_independente or []),
            "vinculo_temporal": vinculo_temporal, "ref_quadro": ref_quadro,
            "validacao_manual": "pendente", "orgao_uasg": orgao_uasg, "script": script,
        }
        a.update(extra)
        if aid in self.vistos:
            return None
        self.vistos.add(aid)
        with open(self.caminho, "a", encoding="utf-8") as f:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
        return a


def ler_arestas(caminho=ARESTAS):
    if not caminho.exists():
        return []
    return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]


def reescrever_arestas(lista, caminho=ARESTAS):
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in lista), encoding="utf-8")
    tmp.replace(caminho)


# ----------------------------------------------------------------------------- universo e amostra

def universo():
    """{contrato_id: {contrato (de obras.json), obras: [ids]}} — as 260 obras / 303 contratos do site."""
    base = json.loads(obras.SAIDA.read_text(encoding="utf-8"))
    cont, obras_de = {}, defaultdict(list)
    for o in base["obras"]:
        for c in o["contratos"]:
            cont[str(c["id"])] = c
            obras_de[str(c["id"])].append(o["id"])
    return cont, obras_de, base


def cache_contrato(cid):
    return coleta.ler_cache(obras.CACHE_O / "contratos" / f"{cid}.json")


def amostra(n=60, semente=20261016):
    """n contratos variados por órgão: sorteio estratificado (proporcional, ao menos 1 por órgão) com
    semente fixa — a mesma amostra em toda execução. Devolve a lista de ids de contrato."""
    cont, _, _ = universo()
    por_orgao = defaultdict(list)
    for cid, c in sorted(cont.items()):
        por_orgao[c["orgao"]].append(cid)
    rng = random.Random(semente)
    total = len(cont)
    # alocação proporcional com piso de 1 por órgão, acertada até somar n
    aloc = {o: max(1, round(n * len(v) / total)) for o, v in por_orgao.items()}
    ordem = sorted(por_orgao, key=lambda o: -len(por_orgao[o]))
    while sum(aloc.values()) > n:
        for o in ordem:
            if aloc[o] > 1 and sum(aloc.values()) > n:
                aloc[o] -= 1
    while sum(aloc.values()) < n:
        for o in ordem:
            if aloc[o] < len(por_orgao[o]) and sum(aloc.values()) < n:
                aloc[o] += 1
    escolha = []
    for o in ordem:
        escolha += rng.sample(por_orgao[o], min(aloc[o], len(por_orgao[o])))
    return sorted(escolha)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    cont, obras_de, _ = universo()
    am = amostra()
    print(len(cont), "contratos;", len(am), "na amostra")
    print(Counter(cont[c]["orgao"] for c in am).most_common())
