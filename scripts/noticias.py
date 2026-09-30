"""
Câmara Aberta — últimas notícias da Agência Câmara sobre PEC e PL, pelos feeds RSS.

Uso:
    python scripts/noticias.py     # lê os feeds, abre cada notícia nova uma vez, grava noticias.json

Fonte: os feeds RSS da Agência Câmara de Notícias (camara.leg.br/noticias/rss): "últimas
notícias" e os 21 temas. A lista de feeds é lida da própria página a cada rodada.

Regra de reprodução — CONFERIDA A CADA NOTÍCIA, não presumida: o rodapé de cada notícia diz
"A reprodução das notícias é autorizada desde que contenha a assinatura “Agência Câmara”".
(Um anúncio antigo falava em "Agência Câmara Notícias"; em 29/9/2026 o texto nas notícias é
"Agência Câmara".) O script guarda a assinatura exigida por notícia. Se o rodapé sumir ou
mudar numa notícia, ela entra só com título e link, sem o trecho, e o relatório avisa.
Só texto: as fotos têm crédito próprio (muitas são de banco de imagens) e não entram.

Ligação notícia → proposição, por id, nunca pelo título: a página da notícia linka a ficha
("fichadetramitacao?idProposicao=…") e a enquete ("/enquetes/…") da proposta. Ficam as
notícias ligadas a PEC, PL ou PLP. O feed não traz esses links; cada notícia nova é aberta
uma vez (cache) — uma requisição por segundo. /noticias não é vedado no robots.txt.
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import coleta

SAIDA = coleta.DADOS / "noticias.json"
CACHE_N = coleta.CACHE / "noticias"
HISTORICO = CACHE_N / "historico.json"
PAGINA_RSS = "https://www.camara.leg.br/noticias/rss"
TIPOS = ("PEC", "PL", "PLP")
DIAS = 120          # quanto do histórico vai para o site
MAX_TRECHO = 300
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}
BRASILIA = timezone(timedelta(hours=-3))

sessao = requests.Session()
sessao.headers["User-Agent"] = "camara-aberta/1.0 (dados públicos; github.com/paulopottermarchi/Senado_Move)"
_ultima = [0.0]
_REGRA = re.compile(r"A reprodução das notícias é autorizada desde que contenha a assinatura\s*[“\"]([^”\"]+)[”\"]")


def pedir(url):
    for n in range(3):
        espera = 1.0 - (time.monotonic() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.monotonic()
        try:
            r = sessao.get(url, timeout=60)
            if r.status_code == 200:
                return r.content.decode("utf-8", "replace")
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(5 * (n + 1))
    return None


def feeds():
    """[(nome do tema, url)] da página de RSS; 'Últimas notícias' primeiro."""
    t = pedir(PAGINA_RSS) or ""
    caminhos = sorted(set(re.findall(r'href="(/noticias/rss/(?:dinamico/[A-Z-]+|ultimas-noticias))"', t)))
    if not caminhos:
        raise SystemExit("A página de RSS da Câmara não listou feeds — conferir " + PAGINA_RSS)
    nome = lambda c: "Últimas notícias" if c.endswith("ultimas-noticias") else \
        c.rsplit("/", 1)[1].replace("-", " ").capitalize()
    return sorted(((nome(c), "https://www.camara.leg.br" + c) for c in caminhos),
                  key=lambda x: x[0] != "Últimas notícias")


def texto(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def trecho(conteudo):
    """O primeiro parágrafo de texto (sem crédito de foto, sem legenda), até ~300 caracteres."""
    for p in re.findall(r"<p[^>]*>(.*?)</p>", conteudo or "", re.S):
        t = texto(p)
        if len(t) >= 60:
            return t if len(t) <= MAX_TRECHO else t[:MAX_TRECHO].rsplit(" ", 1)[0] + "…"
    return None


def pagina(link):
    """Da página da notícia: ids das proposições linkadas e a assinatura exigida no rodapé."""
    chave = hashlib.sha1(link.encode()).hexdigest()[:16]
    caminho = CACHE_N / "paginas" / f"{chave}.json"
    c = coleta.ler_cache(caminho)
    if c is None:
        t = pedir(link)
        if t is None:
            return None
        # espaços normalizados: o rodapé quebra a frase em linhas ("contenha a⏎   assinatura")
        t = re.sub(r"\s+", " ", html.unescape(t))
        ids = sorted({int(x) for x in re.findall(r"fichadetramitacao\?idProposicao=(\d+)", t)} |
                     {int(x) for x in re.findall(r"camara\.leg\.br/enquetes/(\d+)", t)})
        regra = _REGRA.search(t)
        c = {"ids": ids, "assinatura": regra[1].strip() if regra else None}
        coleta.gravar_cache(caminho, c)
    return c


def tipo(id_prop):
    """(sigla, número/ano) da proposição — do cache da Etapa A ou da API, uma vez."""
    d = (coleta.ler_cache(coleta.CACHE / "proposicoes" / f"{id_prop}.json") or {}).get("detalhe")
    if not d:
        caminho = CACHE_N / "prop" / f"{id_prop}.json"
        d = coleta.ler_cache(caminho)
        if d is None:
            r = (coleta.get(f"{coleta.API}/proposicoes/{id_prop}") or {}).get("dados") or {}
            d = {k: r.get(k) for k in ("siglaTipo", "numero", "ano")}
            coleta.gravar_cache(caminho, d)
    return d.get("siglaTipo"), f"{d.get('siglaTipo')} {d.get('numero')}/{d.get('ano')}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    hist = coleta.ler_cache(HISTORICO) or {}
    lista = feeds()
    print(f"{len(lista)} feeds", flush=True)

    novos = 0
    for tema, url in lista:
        x = pedir(url)
        if not x:
            print(f"   {tema}: sem resposta", flush=True)
            continue
        try:
            canal = ET.fromstring(x.encode("utf-8")).find("channel")
        except ET.ParseError as e:
            print(f"   {tema}: XML inválido ({e})", flush=True)
            continue
        for it in canal.findall("item"):
            link = (it.findtext("link") or "").strip()
            if not link:
                continue
            e = hist.get(link)
            if e is None:
                conteudo = it.findtext("content:encoded", namespaces=NS) or ""
                try:
                    data = parsedate_to_datetime(it.findtext("pubDate")).astimezone(BRASILIA).isoformat()[:16]
                except (TypeError, ValueError):
                    data = None
                e = {"t": texto(it.findtext("title")), "d": data, "trecho": trecho(conteudo), "temas": []}
                hist[link] = e
                novos += 1
            if tema != "Últimas notícias" and tema not in e["temas"]:
                e["temas"].append(tema)

    # Abre cada notícia ainda sem os ids das proposições (as antigas já estão no cache).
    sem_regra = []
    for link, e in hist.items():
        if "ids" in e:
            continue
        pg = pagina(link)
        if pg is None:
            continue   # rede: próxima rodada
        e["ids"], e["assinatura"] = pg["ids"], pg["assinatura"]
    for link, e in hist.items():
        if e.get("ids") is not None and not e.get("assinatura"):
            sem_regra.append(link)

    # Só as ligadas a PEC, PL ou PLP, dos últimos DIAS dias.
    limite = (datetime.now(BRASILIA) - timedelta(days=DIAS)).isoformat()[:16]
    itens = []
    for link, e in hist.items():
        if not e.get("ids") or (e.get("d") or "") < limite:
            continue
        props = [[i, rot] for i in e["ids"] for sig, rot in [tipo(i)] if sig in TIPOS]
        if not props:
            continue
        itens.append({"t": e["t"], "u": link, "d": e["d"], "temas": e["temas"], "p": props,
                      # sem a regra de reprodução no rodapé, só título e link
                      "trecho": e["trecho"] if e.get("assinatura") else None,
                      "assinatura": e.get("assinatura")})
    itens.sort(key=lambda x: x["d"] or "", reverse=True)

    # O histórico guarda 1 ano; o site recebe DIAS dias.
    corte = (datetime.now(BRASILIA) - timedelta(days=365)).isoformat()[:16]
    hist = {k: v for k, v in hist.items() if (v.get("d") or "9") >= corte}
    coleta.gravar_cache(HISTORICO, hist)

    assinaturas = sorted({x["assinatura"] for x in itens if x["assinatura"]})
    obj = {
        "_fonte": "Agência Câmara de Notícias, feeds RSS (camara.leg.br/noticias/rss)",
        "_regra": "A reprodução das notícias é autorizada desde que contenha a assinatura "
                  f"“{assinaturas[0] if len(assinaturas) == 1 else ' / '.join(assinaturas) or 'Agência Câmara'}”"
                  " — texto do rodapé de cada notícia, conferido a cada rodada.",
        "_conferidoEm": datetime.now(BRASILIA).date().isoformat(),
        "itens": itens,
    }
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA)

    print(f"\nNotícias novas nos feeds: {novos} · no histórico: {len(hist)} · ligadas a PEC/PL/PLP "
          f"nos últimos {DIAS} dias: {len(itens)}")
    print(f"Assinatura exigida no rodapé: {assinaturas or 'NENHUMA ENCONTRADA'}")
    if sem_regra:
        print(f"ATENÇÃO: {len(sem_regra)} notícia(s) sem a regra de reprodução no rodapé — entram sem o trecho. "
              f"Ex.: {sem_regra[:3]}")
    for x in itens[:5]:
        print(f"   {x['d']}  {', '.join(r for _, r in x['p'])}: {x['t'][:80]}")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")


if __name__ == "__main__":
    main()
