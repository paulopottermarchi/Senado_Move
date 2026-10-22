"""
Câmara Aberta — últimas notícias da Agência Câmara sobre PEC e PL, pelos feeds RSS.

Uso:
    python scripts/noticias.py     # lê os feeds, abre cada notícia nova uma vez, grava noticias.json
    python scripts/noticias.py --teste    # testes sintéticos do leitor da imagem, sem rede

Fonte: os feeds RSS da Agência Câmara de Notícias (camara.leg.br/noticias/rss): "últimas
notícias" e os 21 temas. A lista de feeds é lida da própria página a cada rodada.

Regra de reprodução — CONFERIDA A CADA NOTÍCIA, não presumida: o rodapé de cada notícia diz
"A reprodução das notícias é autorizada desde que contenha a assinatura “Agência Câmara”".
(Um anúncio antigo falava em "Agência Câmara Notícias"; em 29/9/2026 o texto nas notícias é
"Agência Câmara".) O script guarda a assinatura exigida por notícia. Se o rodapé sumir ou
mudar numa notícia, ela entra só com título e link, sem o trecho, e o relatório avisa.
Imagem de cada notícia (desde 22/10/2026, a pedido): a primeira figura do corpo da notícia. O site só REFERENCIA o endereço da imagem no servidor da Câmara
(como faz com a foto dos deputados), nunca a copia, e mostra sempre o crédito que a Câmara escreve nela. As fotos são de donos diferentes (a maioria é
"Câmara dos Deputados"; há também Agência Brasil, Divulgação e banco de imagens como GettyImages): o crédito aparece junto de todas. Com SO_FOTO_DA_CAMARA = True,
só entram as fotos cujo crédito diz "Câmara dos Deputados". Notícia sem rodapé de reprodução confirmado continua só com título e link, sem a imagem.

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
from temas import CATEGORIAS, CURTO, classificar

SAIDA = coleta.DADOS / "noticias.json"
CACHE_N = coleta.CACHE / "noticias"
HISTORICO = CACHE_N / "historico.json"
PAGINA_RSS = "https://www.camara.leg.br/noticias/rss"
TIPOS = ("PEC", "PL", "PLP")
DIAS = 120          # quanto do histórico vai para o site
MAX_TRECHO = 300
PREFIXO_IMG = "https://www.camara.leg.br/midias/image/"
SO_FOTO_DA_CAMARA = False   # True: só a foto cujo crédito diz "Câmara dos Deputados" (as de Agência Brasil, Divulgação e banco de imagens ficam de fora)
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


def ajusta(t):
    """Espaço solto que sobra quando o texto de um link é separado do resto: "( PLP 108/21 )", "Lei 15.526/26 ,", "gov.br .".
    Só mexe em espaço; nenhuma palavra muda. Idempotente: roda também sobre o que já está no histórico."""
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"(?<=\S)\s+([,;:.!?])(?=\s|$)", r"\1", t)
    return t


def texto(h):
    return ajusta(html.unescape(re.sub(r"<[^>]+>", " ", h or "")))


def trecho(conteudo):
    """O primeiro parágrafo de texto (sem crédito de foto, sem legenda), até ~300 caracteres."""
    for p in re.findall(r"<p[^>]*>(.*?)</p>", conteudo or "", re.S):
        t = texto(p)
        if len(t) >= 60:
            return t if len(t) <= MAX_TRECHO else t[:MAX_TRECHO].rsplit(" ", 1)[0] + "…"
    return None


_FIGURA = re.compile(r'<figure class="materia__midia"[^>]*>(.*?)</figure>', re.S)


def figura(t):
    """Da página da notícia (espaços normalizados e entidades resolvidas): a imagem principal, que é a primeira figura do corpo, com o crédito e a legenda
    que a Câmara escreve nela. `img` é o caminho depois de PREFIXO_IMG. Sem figura da Câmara, tudo None (e a página não é aberta de novo por isso)."""
    vazio = {"img": None, "cred": None, "leg": None}
    i = t.find("materia__corpo")
    m = _FIGURA.search(t, max(i, 0))
    if not m:
        return vazio
    f = m[1]
    s = re.search(r'<img[^>]+src="([^"]+)"', f)
    if not s or not s[1].startswith(PREFIXO_IMG):
        return vazio
    limpa = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x or "")).strip()
    cr = re.search(r'materia__creditos-imagem[^>]*>([^<]*)<', f)
    lg = re.search(r'materia__legenda-imagem[^>]*>([^<]*)<', f)
    cred = re.sub(r"^(?:foto|imagem)s?\s*:\s*", "", limpa(cr[1] if cr else ""), flags=re.I)
    return {"img": s[1][len(PREFIXO_IMG):], "cred": cred or None, "leg": limpa(lg[1] if lg else "")[:140] or None}


def pagina(link):
    """Da página da notícia: ids das proposições linkadas, a assinatura exigida no rodapé e a imagem principal com o crédito. O cache antigo (sem `img`) é refeito uma vez."""
    chave = hashlib.sha1(link.encode()).hexdigest()[:16]
    caminho = CACHE_N / "paginas" / f"{chave}.json"
    c = coleta.ler_cache(caminho)
    if c is None or "img" not in c:
        t = pedir(link)
        if t is None:
            return c          # rede: fica o que já havia (sem a imagem) e a próxima rodada tenta de novo
        # espaços normalizados: o rodapé quebra a frase em linhas ("contenha a⏎   assinatura")
        t = re.sub(r"\s+", " ", html.unescape(t))
        ids = sorted({int(x) for x in re.findall(r"fichadetramitacao\?idProposicao=(\d+)", t)} |
                     {int(x) for x in re.findall(r"camara\.leg\.br/enquetes/(\d+)", t)})
        regra = _REGRA.search(t)
        c = {"ids": ids, "assinatura": regra[1].strip() if regra else None, **figura(t)}
        coleta.gravar_cache(caminho, c)
    return c


def tipo(id_prop):
    """(sigla, número/ano, categorias) da proposição — do cache da Etapa A ou da API, uma vez.
    Categorias = a classificação do site (temas.classificar, a mesma da página do deputado e de "Esta semana"),
    sobre a ementa e a indexação: [[nome curto, termo que a definiu, "e" ementa | "k" indexação]]."""
    d = (coleta.ler_cache(coleta.CACHE / "proposicoes" / f"{id_prop}.json") or {}).get("detalhe")
    if not d:
        caminho = CACHE_N / "prop" / f"{id_prop}.json"
        d = coleta.ler_cache(caminho)
        if d is None or "ementa" not in d:          # o cache antigo guardava só sigla, número e ano
            r = (coleta.get(f"{coleta.API}/proposicoes/{id_prop}") or {}).get("dados") or {}
            d = {k: r.get(k) for k in ("siglaTipo", "numero", "ano", "ementa", "keywords")}
            coleta.gravar_cache(caminho, d)
    cats = [[CURTO[CATEGORIAS[i]], termo, fonte]
            for i, termo, fonte in classificar((d.get("ementa") or "").strip(), d.get("keywords") or "")]
    return d.get("siglaTipo"), f"{d.get('siglaTipo')} {d.get('numero')}/{d.get('ano')}", cats


def testar():
    ok = 0

    def igual(a, b, m):
        nonlocal ok
        assert a == b, f"{m}: {a!r} != {b!r}"
        ok += 1
    pg = ('<section class="materia__corpo"><figure class="materia__midia" style="float: none;" data-midia="1">'
          '<img src="https://www.camara.leg.br/midias/image/2026/10/img20261016120913996-768x473.jpg" alt="Erika Hilton - deputada">'
          '<figcaption><span class="materia__legenda-imagem">Erika Hilton: as Paradas devem ser protegidas</span> - '
          '<span class="materia__creditos-imagem" style="color:#5f5f5e">Foto: Marina Ramos / Câmara dos Deputados</span></figcaption></figure><p>texto</p>')
    igual(figura(pg), {"img": "2026/10/img20261016120913996-768x473.jpg", "cred": "Marina Ramos / Câmara dos Deputados",
                       "leg": "Erika Hilton: as Paradas devem ser protegidas"}, "figura principal, crédito sem o prefixo 'Foto:' e legenda")
    igual(figura('<aside><figure class="materia__midia"><img src="https://www.camara.leg.br/midias/image/2026/10/outra-768x473.jpg"></figure></aside>'
                 '<section class="materia__corpo"><p>sem figura</p></section>')["img"], None, "só vale a figura do corpo da notícia")
    igual(figura('<section class="materia__corpo"><p>sem imagem</p></section>'), {"img": None, "cred": None, "leg": None}, "notícia sem figura")
    igual(figura('<section class="materia__corpo"><figure class="materia__midia"><img src="https://outro.site/x.jpg"></figure></section>')["img"], None,
          "imagem que não é do servidor da Câmara não entra")
    igual(figura('<section class="materia__corpo"><figure class="materia__midia"><img src="https://www.camara.leg.br/midias/image/a-768x512.jpg"></figure></section>'),
          {"img": "a-768x512.jpg", "cred": None, "leg": None}, "figura sem crédito nem legenda: a imagem fica, o crédito vem vazio")
    igual(figura('<section class="materia__corpo"><figure class="materia__midia"><img src="https://www.camara.leg.br/midias/image/a-768x512.jpg"><figcaption>'
                 '<span class="materia__creditos-imagem">Foto:   GettyImages </span></figcaption></figure></section>')["cred"], "GettyImages", "espaços do crédito")
    print(f"{ok} verificações passaram")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if "--teste" in sys.argv[1:]:
        testar()
        return
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
        e["img"], e["cred"], e["leg"] = pg.get("img"), pg.get("cred"), pg.get("leg")
    for link, e in hist.items():
        if e.get("ids") is not None and not e.get("assinatura"):
            sem_regra.append(link)

    # Só as ligadas a PEC, PL ou PLP, dos últimos DIAS dias.
    limite = (datetime.now(BRASILIA) - timedelta(days=DIAS)).isoformat()[:16]
    # A imagem das que vão para o site e ainda não a têm (histórico anterior à imagem): a página é aberta de novo uma vez, 1 requisição por segundo.
    for link, e in hist.items():
        if not e.get("ids") or (e.get("d") or "") < limite or "img" in e:
            continue
        pg = pagina(link)
        if pg is not None and "img" in pg:
            e["img"], e["cred"], e["leg"] = pg["img"], pg.get("cred"), pg.get("leg")
    itens = []
    for link, e in hist.items():
        if not e.get("ids") or (e.get("d") or "") < limite:
            continue
        props, cats = [], []
        for i in e["ids"]:
            sig, rot, cs = tipo(i)
            if sig not in TIPOS:
                continue
            props.append([i, rot])
            for c in cs:                                  # a categoria da lei: a união das proposições da notícia, sem repetir
                if c[0] not in [x[0] for x in cats]:
                    cats.append(c)
        if not props:
            continue
        foto = bool(e.get("assinatura") and e.get("img") and e.get("cred")     # sem crédito escrito pela Câmara, a foto não entra
                    and (not SO_FOTO_DA_CAMARA or "câmara dos deputados" in (e.get("cred") or "").lower()))
        itens.append({"t": ajusta(e["t"]), "u": link, "d": e["d"], "temas": e["temas"], "p": props, "cats": cats,
                      # sem a regra de reprodução no rodapé, só título e link
                      "trecho": ajusta(e["trecho"]) if e.get("assinatura") and e["trecho"] else None,
                      "assinatura": e.get("assinatura"),
                      # a imagem: caminho no servidor da Câmara (depois de PREFIXO_IMG), o crédito e a legenda dela; nunca copiada para o site
                      **({"img": e["img"], "cred": e.get("cred"), "leg": e.get("leg")} if foto else {})})
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
        "_imagens": "Fotos referenciadas no servidor da Câmara (camara.leg.br/midias/image/), nunca copiadas, sempre com o crédito que a Câmara escreve nelas.",
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
