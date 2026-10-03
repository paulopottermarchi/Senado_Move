"""
Câmara Aberta — logos dos partidos.

Uso:
    python scripts/logos.py            # depois de partidos.py (lê as siglas dos arquivos gerados)
    python scripts/logos.py --refazer  # ignora o cache de 30 dias

Grava site/dados/partidos/logos.json: {sigla: [{u, de?, ate?, f, ...}]}. A página só referencia o endereço da
imagem (como faz com as fotos dos deputados); este site não guarda cópia de nenhuma.

FONTES, nesta ordem:
  1. Logo oficial da Câmara: o /partidos/{id} dá `urlLogo`, no padrão .../img/partidos/{SIGLA}.gif. O arquivo é por
     sigla DA ÉPOCA (PMDB, PFL, DEM), o que combina com as siglas da aba. Medido em 2/10/2026: de 62 registros, 34
     abrem (GIF de ~57×47 px) e 28 devolvem 404 — inclusive MDB, PL, NOVO, REPUBLICANOS, UNIÃO e SOLIDARIEDADE. O
     script testa cada endereço; nunca se confia no campo.
  2. Wikimedia Commons, só pela tabela curada entradas/logos_partidos.json (item do Wikidata com o nome oficial do
     partido, ou arquivo conferido na imagem). Arquivo do Commons é livre ou de domínio público; marca registrada
     do partido continua sendo do partido, e a página diz que o logo só identifica.
  Sigla sem logo seguro fica sem logo (a lista sai no relatório): nada de achar por busca de título.

A TSE seria a fonte oficial dos símbolos, mas o portal recusa acesso automatizado e isso não é contornado.
"""

import argparse
import json
import sys
import time
from datetime import date

import coleta
from coleta import CACHE, DADOS, ENTRADAS, ler_cache, gravar_cache
import wikipedia   # a mesma sessão com User-Agent identificado e espera entre pedidos

SAIDA = DADOS / "partidos" / "logos.json"
CURADO = ENTRADAS / "logos_partidos.json"
DERIVADO = CACHE / "logos"
CAMARA = "https://www.camara.leg.br/internet/Deputado/img/partidos/{sigla}.gif"
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
LARGURA = 96            # px do miniatura; a página mostra em ~28 px (2× para tela de alta densidade)
VALIDADE = 30 * 86400
SEM_PARTIDO = "S.PART."


def com_cache(nome, refazer, buscar):
    caminho = DERIVADO / f"{nome}.json"
    c = None if refazer else ler_cache(caminho)
    if c is not None and time.time() - c.get("em", 0) < VALIDADE:
        return c["v"]
    v = buscar()
    gravar_cache(caminho, {"em": time.time(), "v": v})
    return v


def siglas_usadas():
    """Toda sigla que alguma página mostra: as da aba Partidos (proposições e alinhamento ao Governo), a bancada de hoje e os senadores."""
    s = set()
    for f in (DADOS / "partidos").glob("*.json"):
        if f.stem in ("indice", "logos", "governo"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for linhas in d["sel"].values():
            s.update(l["s"] for l in linhas)
    try:   # o quadro de alinhamento ao Governo usa as siglas dos votos, que podem não ter proposição no período
        s.update(json.loads((DADOS / "partidos" / "governo.json").read_text(encoding="utf-8"))["partidos"])
    except FileNotFoundError:
        pass
    for arq, campo in (("deputados.json", "partido"), ("senadores.json", "partido")):
        try:
            s.update(x[campo] for x in json.loads((DADOS / arq).read_text(encoding="utf-8")) if x.get(campo))
        except FileNotFoundError:
            pass
    s.difference_update({SEM_PARTIDO, "S/Partido"})   # sem partido não é partido (o Senado escreve de outro jeito)
    return sorted(s)


def logo_camara(sigla, refazer):
    """URL do GIF oficial, se ele abrir de verdade (200 e image/*); senão None."""
    url = CAMARA.format(sigla=sigla)

    def buscar():
        try:
            # a sessão do projeto pede JSON; o servidor de imagens responde 404 a isso
            r = coleta.sessao.get(url, timeout=30, headers={"Accept": "image/*,*/*"})
        except Exception:
            return None
        return url if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") else None
    return com_cache(f"camara-{sigla}", refazer, buscar)


def arquivo_do_item(qid, refazer):
    """Nome do arquivo do logo (P154) de um item do Wikidata: o de rank preferido, senão o último."""
    def buscar():
        d = wikipedia.pedir(WIKIDATA, {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"}, 1.0)
        claims = ((d.get("entities") or {}).get(qid) or {}).get("claims", {}).get("P154", [])
        claims = [c for c in claims if (c.get("mainsnak") or {}).get("datavalue")]
        pref = [c for c in claims if c.get("rank") == "preferred"] or claims
        return pref[-1]["mainsnak"]["datavalue"]["value"] if pref else None
    return com_cache(f"wd-{qid}", refazer, buscar)


def info_commons(arquivo, refazer):
    """Miniatura, página e licença de um arquivo do Commons."""
    def buscar():
        d = wikipedia.pedir(COMMONS, {"action": "query", "titles": f"File:{arquivo}", "prop": "imageinfo",
                                      "iiprop": "url|extmetadata", "iiurlwidth": LARGURA, "format": "json"}, 1.0)
        pag = next(iter((d.get("query") or {}).get("pages", {}).values()), {})
        ii = (pag.get("imageinfo") or [None])[0]
        if not ii:
            return None
        em = ii.get("extmetadata") or {}
        # a API acrescenta parâmetros de rastreamento (?utm_source=…) ao endereço; a imagem é a mesma sem eles
        return {"u": (ii.get("thumburl") or ii.get("url") or "").split("?")[0], "p": ii.get("descriptionurl"),
                "l": (em.get("LicenseShortName") or {}).get("value")}
    return com_cache(f"cm-{arquivo}", refazer, buscar)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--refazer", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    siglas = siglas_usadas()
    curado = json.loads(CURADO.read_text(encoding="utf-8"))["partidos"]
    saida, sem, falhas = {}, [], []
    for sigla in siglas:
        entradas = []
        if sigla.endswith("*") and sigla not in curado:   # homônimo extinto: partido distinto; só com logo curado
            sem.append(sigla)
            continue
        for e in curado.get(sigla) or [{"fonte": "camara"}]:
            lim = {k: e[k] for k in ("de", "ate") if k in e}
            if e["fonte"] == "camara":
                u = logo_camara(sigla, args.refazer)
                if u:
                    entradas.append({"u": u, "f": "camara", **lim})
            elif e["fonte"] == "commons":
                arq = e.get("arquivo") or (arquivo_do_item(e["wikidata"], args.refazer) if e.get("wikidata") else None)
                info = info_commons(arq, args.refazer) if arq else None
                if info and info.get("u"):
                    entradas.append({"u": info["u"], "f": "commons", "a": arq, "p": info["p"], "l": info["l"], **lim})
                else:
                    falhas.append(f"{sigla}: {e.get('wikidata') or e.get('arquivo')}")
        if entradas:
            saida[sigla] = entradas
        else:
            sem.append(sigla)
    out = {"_gerado": date.today().isoformat(), "partidos": saida, "sem": sem}
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    cam = sum(1 for v in saida.values() if any(e["f"] == "camara" for e in v))
    com = sum(1 for v in saida.values() if any(e["f"] == "commons" for e in v))
    print(f"{len(siglas)} siglas · com logo {len(saida)} (Câmara {cam}, Commons {com}) · sem logo {len(sem)}")
    print(f"Sem logo: {sem}")
    if falhas:
        print(f"ATENÇÃO — entrada curada sem arquivo no Commons: {falhas}")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")


if __name__ == "__main__":
    main()
