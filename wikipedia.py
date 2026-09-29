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
     (listas, legislaturas) são descartadas; só vale se sobrar exatamente um artigo, e se o
     item do Wikidata dele não tiver P7480 de OUTRO deputado.
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
from datetime import date, timedelta
from urllib.parse import quote, unquote

import requests

import coleta  # truststore, caminhos, ler/gravar cache

SAIDA = coleta.BASE / "wikipedia.json"
CACHE_WP = coleta.CACHE / "wikipedia"
MAPA = CACHE_WP / "mapa.json"
UA = "camara-aberta/1.0 (github.com/paulopottermarchi/Senado_Move; dados públicos)"
API = "https://pt.wikipedia.org/w/api.php"
PV = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/pt.wikipedia/"
      "all-access/user/{titulo}/{gran}/{ini}/{fim}")

sessao = requests.Session()
sessao.headers["User-Agent"] = UA
_ultima = [0.0]


def pedir(url, params=None, intervalo=1.0):
    """GET educado: intervalo mínimo, maxlag na API do MediaWiki, e espera em 429/5xx.
    Uma requisição por segundo na API do MediaWiki: a 4/s, a Wikimedia devolveu 429
    ("too many requests") para acesso sem login — medido em 29/9/2026."""
    for n in range(5):
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
        time.sleep(max(int(r.headers.get("retry-after") or 0), 10 * (n + 1)))
    raise RuntimeError(f"sem resposta depois de 5 tentativas: {url}")


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

    for i in ids:
        if i in mapa:
            continue
        candidatos = set()
        for proto in ("https", "http"):
            d = mw({"action": "query", "list": "exturlusage", "euquery": f"www.camara.leg.br/deputados/{i}",
                    "euprotocol": proto, "eunamespace": 0, "eulimit": 50})
            for x in d["query"]["exturlusage"]:
                if re.search(rf"camara\.leg\.br/deputados/{i}(?:$|[/?#])", x["url"]):
                    candidatos.add(x["title"])
        bons = []
        for t in candidatos:
            d = mw({"action": "query", "prop": "extlinks|pageprops", "titles": t, "ellimit": 500,
                    "elquery": "www.camara.leg.br/deputados/", "elprotocol": "https", "ppprop": "wikibase_item"})
            pg = next(iter(d["query"]["pages"].values()))
            outros = {int(x) for l in pg.get("extlinks", []) for x in re.findall(r"deputados/(\d+)", l["*"])}
            item = (pg.get("pageprops") or {}).get("wikibase_item")
            if len(outros) <= 2 and p7480.get(item, i) == i:
                bons.append(t)
        if len(bons) == 1:
            mapa[i] = (bons[0], "link para a página da Câmara")
    print(f"   total ligado: {len(mapa)} de {len(ids)}", flush=True)
    return mapa


def redirecionamentos(titulo):
    d = mw({"action": "query", "prop": "redirects", "titles": titulo, "rdlimit": "max", "rdnamespace": 0})
    pg = next(iter(d["query"]["pages"].values()))
    return [r["title"] for r in pg.get("redirects", [])]


def visitas(titulo, gran, ini, fim):
    d = pedir(PV.format(titulo=quote(titulo.replace(" ", "_"), safe=""), gran=gran, ini=ini, fim=fim), intervalo=0.5)
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

    saida = {}
    for k, (titulo, via) in sorted((k, v) for k, v in mapa.items() if not k.startswith("_")):
        titulos = [titulo] + redirecionamentos(titulo)
        dia, mes = {}, {}
        for t in titulos:
            for d_, v in visitas(t, "daily", f"{ini90:%Y%m%d}", f"{fim90:%Y%m%d}").items():
                dia[d_] = dia.get(d_, 0) + v
            for d_, v in visitas(t, "monthly", f"{ini_m:%Y%m%d}", f"{fim_m:%Y%m%d}").items():
                mes[d_[:6]] = mes.get(d_[:6], 0) + v
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
