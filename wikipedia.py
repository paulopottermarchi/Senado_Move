"""
Câmara Aberta — notoriedade de cada deputado: leituras do artigo dele na Wikipédia em português.

Uso:
    python wikipedia.py              # liga deputado → artigo (com cache) e baixa as visitas
    python wikipedia.py --religar    # refaz a ligação deputado → artigo

Mede procura, não aprovação nem trabalho: escândalo também gera leitura. É o único número
de "notoriedade" do deputado no site — sem índice composto, sem outras fontes somadas.

Ligação deputado → artigo, só por identificador, nunca pelo nome (homônimos):
  1. Wikidata: item com a propriedade P7480 ("Brazilian federal deputy ID") igual ao id da
     Câmara, e o artigo da pt.wikipedia ligado a esse item.
  2. Para quem não tem P7480: artigo da pt.wikipedia com link externo para
     camara.leg.br/deputados/{id} — o id exato. Páginas que linkam mais de 2 deputados
     (listas, legislaturas) são descartadas; o item do Wikidata do artigo tem de ser de um ser
     humano (P31 = Q5) e não pode ter P7480 de OUTRO deputado; só vale se sobrar exatamente um.
Quem não tem artigo ligado fica sem número — nunca zero.

Visitas: API de pageviews da Wikimedia, leitores humanos (agent=user), todos os acessos,
últimos 90 dias completos e os 12 meses completos anteriores ao mês corrente. Soma o artigo
e os redirecionamentos para ele (quem entra por "Nikolas Ferreira de Oliveira" lê o mesmo
artigo; a Wikimedia conta a visita no título do redirecionamento).
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import quote, unquote

import requests

import coleta  # truststore, caminhos, ler/gravar cache

SAIDA = coleta.BASE / "wikipedia.json"
CACHE_WP = coleta.CACHE / "wikipedia"
MAPA = CACHE_WP / "mapa.json"
# Formato pedido pela Wikimedia (URL completa e contato): sem ele o cliente cai no limite de
# "não identificado", 10 req/min, em vez de 200 (mediawiki.org/wiki/Wikimedia_APIs/Rate_limits).
UA = "CamaraAberta/1.0 (https://github.com/paulopottermarchi/Senado_Move; https://github.com/paulopottermarchi/Senado_Move/issues)"
API = "https://pt.wikipedia.org/w/api.php"
PV = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/pt.wikipedia/"
      "all-access/user/{titulo}/{gran}/{ini}/{fim}")

sessao = requests.Session()
sessao.headers["User-Agent"] = UA
_ultima = [0.0]


def pedir(url, params=None, intervalo=1.0, tentativas=8):
    """GET educado: intervalo mínimo, maxlag na API do MediaWiki, e espera em 429/5xx.
    Uma requisição por segundo na API do MediaWiki: a 4/s, a Wikimedia devolveu 429
    ("too many requests") para acesso sem login — medido em 29/9/2026. Mesmo a 1/s o bloqueio
    voltou uma vez e durou mais de 2,5 min: a espera cresce (30 s, 1, 2, 4… min, até 10) e
    respeita o retry-after."""
    for n in range(tentativas):
        espera = intervalo - (time.monotonic() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.monotonic()
        try:
            r = sessao.get(url, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(5 * (n + 1))
            continue
        if r.status_code == 404:
            return None
        # "json" em qualquer variante: o Wikidata responde application/sparql-results+json
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            d = r.json()
            if isinstance(d, dict) and d.get("error", {}).get("code") == "maxlag":
                time.sleep(5 * (n + 1))
                continue
            return d
        # respeita o tempo pedido pelo servidor (429 veio com retry-after de 19 s)
        espera = max(int(r.headers.get("retry-after") or 0), min(600, 30 * 2 ** n))
        print(f"      HTTP {r.status_code} ({r.headers.get('content-type', '')[:30]}); "
              f"nova tentativa em {espera} s", flush=True)
        time.sleep(espera)
    raise RuntimeError(f"sem resposta depois de {tentativas} tentativas: {url}")


def mw(params):
    return pedir(API, {**params, "format": "json", "maxlag": 5})


def titulo_da_url(url):
    return unquote(url.rsplit("/wiki/", 1)[1]).replace("_", " ")


def ligar(ids):
    """{id do deputado: (título do artigo, via)}."""
    q = """SELECT ?item ?cam ?art WHERE { ?item wdt:P7480 ?cam .
           OPTIONAL { ?art schema:about ?item ; schema:isPartOf <https://pt.wikipedia.org/> . } }"""
    rows = pedir("https://query.wikidata.org/sparql", {"query": q, "format": "json"}, 1.0)["results"]["bindings"]
    p7480 = {}   # item → id da Câmara
    mapa = {}
    for b in rows:
        cam, item = b["cam"]["value"], b["item"]["value"].rsplit("/", 1)[1]
        if not cam.isdigit():
            continue
        p7480[item] = int(cam)
        if int(cam) in ids and "art" in b:
            mapa[int(cam)] = (titulo_da_url(b["art"]["value"]), "Wikidata")
    print(f"   via Wikidata (P7480): {len(mapa)}", flush=True)

    # 2ª via numa varredura só: todos os artigos com link para camara.leg.br/deputados/{id}, paginado
    # (~30 requisições). Antes era uma consulta por deputado (~600, com 429 a cada poucas).
    ids_da_pagina = defaultdict(set)      # título → ids de deputado que o artigo linka
    for proto in ("https", "http"):
        cont = {}
        while True:
            d = mw({"action": "query", "list": "exturlusage", "euquery": "www.camara.leg.br/deputados/",
                    "euprotocol": proto, "eunamespace": 0, "eulimit": "max", **cont})
            for x in d["query"]["exturlusage"]:
                m = re.search(r"camara\.leg\.br/deputados/(\d+)(?:$|[/?#])", x["url"])
                if m:
                    ids_da_pagina[x["title"]].add(int(m[1]))
            if "continue" not in d:
                break
            cont = d["continue"]
    print(f"   artigos com link para a página de um deputado: {len(ids_da_pagina)}", flush=True)

    # Candidatos: artigos que linkam o deputado e no máximo mais um (3+ é lista ou legislatura).
    candidatos = defaultdict(list)
    for t, lig in ids_da_pagina.items():
        if len(lig) <= 2:
            for i in lig:
                if i in ids and i not in mapa:
                    candidatos[i].append(t)
    # Item do Wikidata de cada candidato, 50 por requisição: descarta o que tem P7480 de OUTRO deputado.
    titulos = sorted({t for ts in candidatos.values() for t in ts})
    item_de = {}
    for k in range(0, len(titulos), 50):
        d = mw({"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                "titles": "|".join(titulos[k:k + 50])})
        normal = {x["to"]: x["from"] for x in d["query"].get("normalized", [])}
        for pg in d["query"]["pages"].values():
            item_de[normal.get(pg["title"], pg["title"])] = (pg.get("pageprops") or {}).get("wikibase_item")
    # Só artigo sobre uma PESSOA: item do Wikidata com P31 = Q5 (ser humano), por identificador.
    # Medido em 29/9/2026: sem isso, 7 dos 91 ligados por esta via eram listas de deputados
    # estaduais, "Governo do Paraná", o município "Maravilha (SC)", "Câmara dos Deputados do Brasil".
    itens = sorted({it for it in item_de.values() if it})
    humanos = set()
    for k in range(0, len(itens), 200):
        valores = " ".join(f"wd:{x}" for x in itens[k:k + 200])
        q = f"SELECT ?item WHERE {{ VALUES ?item {{ {valores} }} ?item wdt:P31 wd:Q5 . }}"
        rows = pedir("https://query.wikidata.org/sparql", {"query": q, "format": "json"})["results"]["bindings"]
        humanos |= {b["item"]["value"].rsplit("/", 1)[1] for b in rows}
    nao_pessoa = 0
    for i, ts in candidatos.items():
        pessoas = [t for t in ts if item_de.get(t) in humanos]
        nao_pessoa += len(ts) - len(pessoas)
        bons = [t for t in pessoas if p7480.get(item_de.get(t), i) == i]
        if len(bons) == 1:                # exatamente um artigo; dois ou mais = ambíguo, fica sem
            mapa[i] = (bons[0], "link para a página da Câmara")
    print(f"   candidatos descartados por não serem artigo sobre pessoa (P31 ≠ Q5): {nao_pessoa}", flush=True)
    print(f"   total ligado: {len(mapa)} de {len(ids)}", flush=True)
    return mapa


def redirecionamentos(titulos):
    """{título: [redirecionamentos para ele]}, 50 títulos por requisição (com continuação)."""
    saida = {t: [] for t in titulos}
    for k in range(0, len(titulos), 50):
        lote, cont = titulos[k:k + 50], {}
        while True:
            d = mw({"action": "query", "prop": "redirects", "titles": "|".join(lote),
                    "rdlimit": "max", "rdnamespace": 0, **cont})
            normal = {x["to"]: x["from"] for x in d["query"].get("normalized", [])}
            for pg in d["query"]["pages"].values():
                t = normal.get(pg["title"], pg["title"])
                saida.setdefault(t, []).extend(r["title"] for r in pg.get("redirects", []))
            if "continue" not in d:
                break
            cont = d["continue"]
    return saida


def diarias(titulo, ini, fim):
    """Leituras por dia (AAAAMMDD → n). Uma série só por título: os 90 dias e os meses saem dela."""
    d = pedir(PV.format(titulo=quote(titulo.replace(" ", "_"), safe=""), gran="daily", ini=ini, fim=fim),
              intervalo=0.5)
    return {x["timestamp"][:8]: x["views"] for x in (d or {}).get("items", [])}


def meses(fim, n=12):
    """Os n meses completos até `fim` (inclusive), como date no dia 1."""
    a, m = fim.year, fim.month
    saida = []
    for _ in range(n):
        saida.append(date(a, m, 1))
        a, m = (a, m - 1) if m > 1 else (a - 1, 12)
    return saida[::-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--religar", action="store_true", help="refazer a ligação deputado → artigo")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    deputados = json.loads(coleta.SAIDA.read_text(encoding="utf-8"))
    ids = {d["id"] for d in deputados}
    mapa = None if args.religar else coleta.ler_cache(MAPA)
    # Refaz a ligação se nunca foi feita ou se entrou deputado novo (posse de suplente).
    if mapa is None or ids - set(mapa.get("_vistos", [])):
        print("Ligando deputados aos artigos…", flush=True)
        m = ligar(ids)
        mapa = {str(k): v for k, v in m.items()}
        mapa["_vistos"] = sorted(ids)
        coleta.gravar_cache(MAPA, mapa)

    hoje = date.today()
    fim90 = hoje - timedelta(days=1)
    ini90 = fim90 - timedelta(days=89)
    fim_m = hoje.replace(day=1) - timedelta(days=1)            # último dia do mês passado
    lista_meses = meses(fim_m)
    ini_m = lista_meses[0]
    print(f"Visitas de {ini90} a {fim90} e mensais de {ini_m:%Y-%m} a {fim_m:%Y-%m}…", flush=True)

    artigos = sorted((k, v) for k, v in mapa.items() if not k.startswith("_"))
    redir = redirecionamentos(sorted({t for _, (t, _) in artigos}))
    ini, fim = min(ini90, ini_m), fim90
    saida = {}
    for n, (k, (titulo, via)) in enumerate(artigos, 1):
        if n % 50 == 0:
            print(f"   {n}/{len(artigos)} artigos", flush=True)
        titulos = [titulo] + redir.get(titulo, [])
        todas = defaultdict(int)
        for t in titulos:
            for d_, v in diarias(t, f"{ini:%Y%m%d}", f"{fim:%Y%m%d}").items():
                todas[d_] += v
        dia = {d_: v for d_, v in todas.items() if f"{ini90:%Y%m%d}" <= d_ <= f"{fim90:%Y%m%d}"}
        mes = defaultdict(int)
        for d_, v in todas.items():
            mes[d_[:6]] += v
        pico = max(dia.items(), key=lambda x: x[1]) if dia else None
        saida[k] = {
            "artigo": titulo, "via": via, "redirecionamentos": len(titulos) - 1,
            "visitas90": sum(dia.values()),
            "pico": [f"{pico[0][:4]}-{pico[0][4:6]}-{pico[0][6:]}", pico[1]] if pico else None,
            "mensal": [mes.get(f"{m:%Y%m}", 0) for m in lista_meses],
        }

    obj = {
        "_fonte": "Wikimedia pageviews (pt.wikipedia, leitores humanos, todos os acessos), artigo + redirecionamentos",
        "_periodo90": [str(ini90), str(fim90)],
        "_meses": [f"{m:%Y-%m}" for m in lista_meses],
        "_ligacao": "Wikidata P7480 (id da Câmara) ou artigo com link para camara.leg.br/deputados/{id}",
        "_cobertura": [len(saida), len(ids)],
        **saida,
    }
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA)

    nome = {str(d["id"]): f"{d['nome']} ({d['partido']}-{d['uf']})" for d in deputados}
    v = sorted(saida.items(), key=lambda kv: -kv[1]["visitas90"])
    vs = sorted(x["visitas90"] for x in saida.values())
    print(f"\nCom artigo ligado: {len(saida)} de {len(ids)} "
          f"({sum(1 for x in saida.values() if x['via'] == 'Wikidata')} pelo Wikidata) · "
          f"visitas em 90 dias: mediana {vs[len(vs) // 2]:,} · máx {vs[-1]:,}".replace(",", "."))
    for k, x in v[:10]:
        print(f"   {nome[k]:<42} {x['visitas90']:>9,}  pico {x['pico'][0]} ({x['pico'][1]:,})".replace(",", "."))
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")


if __name__ == "__main__":
    main()
