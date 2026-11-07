"""
Câmara Aberta — páginas estáticas indexáveis, uma por deputado e uma por senador atual, mais sitemap.xml, robots.txt e as
tags de compartilhamento (canonical, Open Graph, Twitter) em todas as páginas do site.

Hoje cada perfil é deputado.html?id=… ou senador.html?id=…, preenchido por JavaScript: um buscador ou uma rede social vê uma
página sem nome e sem número. Aqui o pipeline gera, a partir dos JSON que já existem, páginas que dizem tudo sem JavaScript:

    site/deputado/<id>-<slug>/index.html   + og.png (cartão 1200×630, só texto)
    site/senador/<id>-<slug>/index.html    + og.png
    site/sitemap.xml  site/robots.txt  site/img/og-padrao.png  site/dados/paginas.json

Uso:
    python scripts/paginas.py                  # gera/atualiza tudo em site/ (só o que mudou)
    python scripts/paginas.py --dry-run        # gera tudo em cache/paginas_dryrun/, sem tocar em site/ nem no manifesto; mede peso e tempo
    python scripts/paginas.py --so-estaticas   # só as tags nas páginas atuais e o og-padrao.png
    python scripts/paginas.py --limite 20      # só 20 de cada (teste)
    python scripts/paginas.py --teste          # testes sintéticos, sem tocar em nada

Regras (o enquadramento é o do resto do site):
- Nenhum texto que não esteja nos dados. Os números são os de deputados.json / senadores.json e a frase-resumo é a de temas/resumo.json
  (modelo fixo, sem IA). Nada de ranking, nota ou adjetivo; a posição no espectro é a do PARTIDO, com a fonte escrita.
- Sem fotos: o projeto só referencia imagens de terceiros, e o cartão do og:image é só texto (Barlow Condensed, já versionada em site/vendor/fonts).
- O slug é fixado na primeira geração (manifesto) para a URL nunca mudar se o nome mudar. Quem sai do conjunto (deixou o cargo) perde a página.
- Uma página só é reescrita quando o conteúdo dela muda (hash do que aparece nela); o og.png, só quando mudam os números do cartão. Por isso a
  data "alterado pela última vez em" e o lastmod do sitemap são a data dos dados da rodada em que algo mudou naquela página.
"""

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import time
import unicodedata
import zlib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coleta

# ---------------------------------------------------------------------------------------------------------- constantes únicas
BASE_URL = "https://congressoclaro.com.br"
SITE_NOME = "Câmara Aberta"          # o og:site_name que o site já usa hoje

VERSAO = "paginas-1"                 # sobe a cada mudança de modelo de página: refaz todas as páginas
VERSAO_IMG = "cartao-1"              # sobe a cada mudança do desenho do cartão: refaz todos os og.png
SITE = coleta.BASE / "site"
DADOS = coleta.DADOS
MANIFESTO = DADOS / "paginas.json"
FONTES = SITE / "vendor" / "fonts"
DRY = coleta.CACHE / "paginas_dryrun"

# páginas atuais: arquivo → (canonical, descrição curta do cartão padrão). Só estas entram no sitemap.
PAGINAS_SITEMAP = ["", "deputados.html", "senadores.html", "leis.html", "blocos.html", "fluxo.html", "obras.html", "contexto.html", "origem.html"]
# páginas atuais que ficam fora do sitemap (precisam de ?id= para mostrar algo): ganham canonical para a versão sem parâmetros
PAGINAS_FORA_SITEMAP = ["votos.html"]
# páginas que só têm sentido com ?id=: o canonical é posto por JavaScript (aponta para a página nova); aqui só ganham a imagem e o card
PAGINAS_SO_IMAGEM = ["deputado.html", "senador.html"]
# página que só redireciona: canonical para o destino
PAGINAS_REDIRECIONAM = {"prototipo.html": "deputados.html"}

MARCA_INI = "<!-- seo:inicio -->"
MARCA_FIM = "<!-- seo:fim -->"
LEG_ATUAL_FALLBACK = "57"

TAGLINE = "O que o Congresso propõe, aprova e vota"


class Aborta(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------- texto
def esc(s):
    return html.escape(str(s), quote=True)


def num(v):
    return f"{int(v):,}".replace(",", ".")


def dataBR(iso):
    a, m, d = iso[:10].split("-")
    return f"{d}/{m}/{a}"


def slugar(nome):
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > 60:
        s = s[:60].rsplit("-", 1)[0]
    return s or "parlamentar"


def prop(n, sing, plur):
    return f"{num(n)} {sing if n == 1 else plur}"


def leis_txt(n):
    return "nenhuma virou lei" if n == 0 else ("1 virou lei" if n == 1 else f"{num(n)} viraram lei")


def nota_score(score):
    return f"{score:.1f}".replace(".", ",")


# ---------------------------------------------------------------------------------------------------------- dados
def ler(caminho):
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def data_dos_dados():
    """A data da rodada: o `geradoEm` de semana.json, que é o último passo do pipeline."""
    try:
        return ler(DADOS / "semana.json")["geradoEm"][:10]
    except (OSError, KeyError, ValueError):
        return date.today().isoformat()


def legislatura_atual(data):
    """(id, início) da legislatura em que a data cai, de legislaturas.json."""
    try:
        for L in ler(DADOS / "legislaturas.json"):
            if L["inicio"] <= data <= L["fim"]:
                return str(L["id"]), L["inicio"]
    except (OSError, KeyError, ValueError):
        pass
    return LEG_ATUAL_FALLBACK, "2023-02-01"


def fatos_deputado(d, frase, leg, leg_ini):
    pl = d.get("porLegislatura", {}).get(leg) or {}
    v = d.get("votos") or {}
    f = {"tipo": "deputado", "id": d["id"], "nome": d["nome"], "partido": d.get("partido"), "uf": d["uf"],
         "posse": d.get("condicaoPosse"), "apresentadas": d["apresentadas"], "pecs": d["pecs"], "pls": d["leis"], "viraramLei": d["viraramLei"],
         "legApresentadas": pl.get("apresentadas"), "legViraramLei": pl.get("viraramLei"), "legInicio": leg_ini,
         "votos": ({"registrados": v.get("chamadas"), "sim": v.get("sim"), "nao": v.get("nao"), "abstencao": v.get("abstencao")} if v.get("chamadas") else None),
         "score": d.get("score"), "scoreNota": d.get("scoreNota"), "frase": frase}
    return f


def fatos_senador(s, frase, leg, leg_ini):
    pl = s.get("porLegislatura", {}).get(leg) or {}
    v = s.get("votos") or {}
    rel = s.get("relatorias") or {}
    return {"tipo": "senador", "id": s["id"], "nome": s["nome"], "partido": s.get("partido"), "uf": s["uf"], "posse": s.get("participacao"),
            "mandato": s.get("mandato"), "apresentadas": s["apresentadas"], "pecs": s["pecs"], "pls": s["leis"], "viraramLei": s["viraramLei"],
            "legApresentadas": pl.get("apresentadas"), "legViraramLei": pl.get("viraramLei"), "legInicio": leg_ini,
            "relatorias": rel.get("processos"),
            "votos": ({"registrados": v.get("votacoes"), "sim": v.get("sim"), "nao": v.get("nao"), "abstencao": v.get("abstencao")} if v.get("votacoes") else None),
            "score": s.get("score"), "scoreNota": s.get("scoreNota"), "frase": frase, "urlOficial": s.get("url")}


def carregar_fatos():
    data = data_dos_dados()
    leg, leg_ini = legislatura_atual(data)
    deps = ler(DADOS / "deputados.json")
    sens = ler(DADOS / "senadores.json")
    fd = ler(DADOS / "temas" / "resumo.json").get("frases", {})
    try:
        fs = ler(DADOS / "senadores" / "resumo.json").get("frases", {})
    except OSError:
        fs = {}
    return (data, [fatos_deputado(d, fd.get(str(d["id"])), leg, leg_ini) for d in deps],
            [fatos_senador(s, fs.get(str(s["id"])), leg, leg_ini) for s in sens])


# ---------------------------------------------------------------------------------------------------------- textos de cada página
def cargo(f):
    return "Deputado(a) federal" if f["tipo"] == "deputado" else "Senador(a)"


def casa(f):
    return "Câmara dos Deputados" if f["tipo"] == "deputado" else "Senado Federal"


def partido_uf(f):
    return f"{f['partido']}/{f['uf']}" if f.get("partido") else f["uf"]


def titulo(f):
    return f"{f['nome']} ({partido_uf(f)}) — proposições, leis e votos | {SITE_NOME}"


def og_titulo(f):
    return f"{f['nome']} ({partido_uf(f)}) — proposições, leis e votos"


def descricao(f):
    """Só números que existem nos dados: apresentadas, viraram lei e votações."""
    base = (f"{f['nome']} ({partido_uf(f)}), {cargo(f).lower()}: {prop(f['apresentadas'], 'proposição', 'proposições')} (PEC e PL) "
            f"{'de autoria' if f['tipo'] == 'deputado' else 'como primeiro autor'} nos dados oficiais, {leis_txt(f['viraramLei'])}")
    v = f.get("votos")
    if v:
        base += (f"; voto registrado em {prop(v['registrados'], 'votação', 'votações')} finais de texto-base" if f["tipo"] == "deputado"
                 else f"; registro em {prop(v['registrados'], 'votação', 'votações')} nominais abertas do Plenário desde 2023")
    return base + f". Fonte: {casa(f)}."


def url_pagina(f, slug):
    return f"{BASE_URL}/{f['tipo']}/{f['id']}-{slug}/"


def json_ld(f, slug):
    o = {"@context": "https://schema.org", "@type": "Person", "name": f["nome"], "jobTitle": cargo(f),
         "worksFor": {"@type": "GovernmentOrganization", "name": casa(f)}, "url": url_pagina(f, slug)}
    if f.get("partido"):
        o["affiliation"] = {"@type": "Organization", "name": f["partido"]}
    o["sameAs"] = ([f"https://www.camara.leg.br/deputados/{f['id']}"] if f["tipo"] == "deputado" else ([f["urlOficial"]] if f.get("urlOficial") else []))
    if not o["sameAs"]:
        del o["sameAs"]
    return json.dumps(o, ensure_ascii=False).replace("</", "<\\/")


def corpo_numeros(f):
    """A grade de números, só o que existe nos dados. Cada item: (rótulo, valor, nota)."""
    itens = []
    itens.append(("Proposições apresentadas", num(f["apresentadas"]),
                  f"{prop(f['pecs'], 'PEC', 'PEC')} e {prop(f['pls'], 'projeto de lei (PL)', 'projetos de lei (PL)')}; soma de todos os mandatos que a fonte registra"))
    itens.append(("Viraram lei", num(f["viraramLei"]), "com situação oficial “Transformado em Norma Jurídica”"))
    if f.get("legApresentadas") is not None:
        itens.append((f"Nesta legislatura (desde {dataBR(f['legInicio'])})", f"{num(f['legApresentadas'])}",
                      f"apresentadas; {leis_txt(f['legViraramLei'] or 0)}"))
    v = f.get("votos")
    if v:
        if f["tipo"] == "deputado":
            sub = f"Sim {num(v['sim'])}, Não {num(v['nao'])}, abstenção {num(v['abstencao'])}"
            itens.append(("Votos registrados", num(v["registrados"]), "em votações finais de texto-base; " + sub))
        else:
            sub = f"Sim {num(v['sim'])}, Não {num(v['nao'])}, abstenção {num(v['abstencao'])}"
            itens.append(("Votações nominais abertas", num(v["registrados"]), "do Plenário, desde 2023; " + sub))
    if f["tipo"] == "senador" and f.get("relatorias") is not None:
        itens.append(("Processos relatados", num(f["relatorias"]), "designações como relator, na carreira registrada"))
    return itens


def nota_votos(f):
    if not f.get("votos"):
        return ""
    if f["tipo"] == "deputado":
        return ("<p>“Votos registrados” é a contagem de votações finais de texto-base em que o voto está registrado. Não é taxa de presença: "
                "a ausência de registro pode ser falta, missão oficial, licença ou não estar em exercício.</p>")
    return ("<p>“Votações nominais abertas” são as do Plenário desde 2023 em que o Senado registra o senador; quando o voto não é Sim, Não ou abstenção, o "
            "registro oficial traz o motivo (atividade parlamentar, licença, missão, presidindo a sessão, não compareceu). Não há taxa de presença.</p>")


def bloco_espectro(f):
    sc = f.get("score")
    if sc is not None:
        return (f"<p>Posição do partido ({esc(f['partido'])}) no espectro: <b>{nota_score(sc)} de 10</b> (1 = esquerda, 10 = direita). É a média das respostas "
                f"da wave 9 (2021) do Brazilian Legislative Survey, de Power e Zucco, calculada por este site; é a posição do partido, a mesma para todos os "
                f"seus parlamentares, e não uma medida da pessoa.</p>")
    if f.get("scoreNota"):
        return f"<p>Sem posição no espectro: {esc(f['scoreNota'])}.</p>"
    return ""


def metodo(f):
    if f["tipo"] == "deputado":
        return ("<b>Autoria</b>: PEC e PL em que o deputado é autor; as assinaturas de apoio não contam. <b>Virou lei</b>: situação oficial “Transformado em Norma "
                "Jurídica”. Os números da carreira somam todos os mandatos que a API da Câmara registra para esta pessoa.")
    return ("<b>Autoria</b>: PEC e PL em que o senador é o primeiro autor; coautorias e assinaturas de apoio não contam, e o PLS (nome do PL no Senado até 2018) "
            "conta como PL. <b>Virou lei</b>: situação “transformada em norma jurídica”. Os números da carreira somam o que o registro do Senado traz para esta pessoa.")


def html_pagina(f, slug, lastmod, rodape):
    url = url_pagina(f, slug)
    img = url + "og.png"
    nome = esc(f["nome"])
    ficha = f"../../{f['tipo']}.html?id={f['id']}"
    oficial = (f"https://www.camara.leg.br/deputados/{f['id']}" if f["tipo"] == "deputado" else (f.get("urlOficial") or ""))
    itens = "\n".join(f'      <div class="pg-n"><dt>{esc(r)}</dt><dd>{esc(v)}</dd><dd class="pg-nota">{esc(n)}</dd></div>' for r, v, n in corpo_numeros(f))
    posse = f" · {esc(f['posse'].lower())}" if f.get("posse") else ""
    mandato = (f" · mandato de {f['mandato'][0][:4]} a {f['mandato'][1][:4]}" if f["tipo"] == "senador" and f.get("mandato") and len(f["mandato"]) == 2 else "")
    frase = f'<p class="pg-frase">{esc(f["frase"])}</p>' if f.get("frase") else ""
    fonte_nome = ("Dados Abertos da Câmara dos Deputados" if f["tipo"] == "deputado" else "Dados Abertos do Senado Federal")
    fonte_url = "https://dadosabertos.camara.leg.br" if f["tipo"] == "deputado" else "https://legis.senado.leg.br/dadosabertos/"
    pagina_oficial = f' Página oficial: <a href="{esc(oficial)}" target="_blank" rel="noopener">{esc(oficial)}</a>.' if oficial else ""
    return f"""<!DOCTYPE html>
<html lang="pt-BR" data-escuro-ok data-ed>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{esc(titulo(f))}</title>
<meta name="description" content="{esc(descricao(f))}">
<link rel="canonical" href="{esc(url)}">
<meta name="theme-color" content="#ffffff">
<meta property="og:type" content="profile">
<meta property="og:site_name" content="{esc(SITE_NOME)}">
<meta property="og:title" content="{esc(og_titulo(f))}">
<meta property="og:description" content="{esc(descricao(f))}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(img)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(og_titulo(f))}: cartão com os números">
<meta property="og:locale" content="pt_BR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(og_titulo(f))}">
<meta name="twitter:description" content="{esc(descricao(f))}">
<meta name="twitter:image" content="{esc(img)}">
<link rel="icon" href="../../favicon.ico" sizes="32x32">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=Libre+Franklin:wght@400;500;600&display=swap" rel="stylesheet">
<script>try{{var _t=localStorage.getItem('tema');if(_t==='claro'||_t==='escuro')document.documentElement.dataset.tema=_t}}catch(e){{}}</script>
<link rel="stylesheet" href="../../estilo.css">
<script src="../../site.js" defer></script>
<script type="application/ld+json">{json_ld(f, slug)}</script>
</head>
<body>
<header class="topo">
  <div class="topo-in">
    <a class="marca" href="../../index.html" aria-label="{esc(SITE_NOME)}, página inicial"><svg class="marca-ico" viewBox="0 0 32 32" aria-hidden="true"><rect class="fundo" width="32" height="32" rx="8"/><path d="M8 13.5h16a8 8 0 0 1-16 0z" fill="#2dd4bf"/><rect x="7" y="23.5" width="18" height="2" rx="1" fill="#fff"/></svg><span>{esc(SITE_NOME)}</span></a>
    <nav class="nav" aria-label="Páginas do site">
      <a href="../../deputados.html"{' aria-current="page"' if f['tipo'] == 'deputado' else ''}>Deputados</a>
      <a href="../../senadores.html"{' aria-current="page"' if f['tipo'] == 'senador' else ''}>Senadores</a>
      <a href="../../leis.html">Leis votadas</a>
      <a href="../../blocos.html">Quem vota com quem</a>
      <a href="../../fluxo.html">Entre as casas</a>
      <a href="../../obras.html">Obras</a>
      <a href="../../contexto.html">Economia</a>
    </nav>
  </div>
</header>
<main class="wrap pg">
  <p class="kicker">{esc(cargo(f))} · {esc(casa(f))}</p>
  <h1>{nome}</h1>
  <p class="pg-sub">{esc(f['partido'] or 'sem partido')} de {esc(f['uf'])}{posse}{mandato}</p>
  {frase}
  <section class="painel pg-num" aria-labelledby="pg-h-num">
    <h2 id="pg-h-num">Em números</h2>
    <dl class="pg-grade">
{itens}
    </dl>
    {nota_votos(f)}
    {bloco_espectro(f)}
  </section>
  <p class="pg-acao"><a class="btn-tema" href="{esc(ficha)}"><span class="bt-txt"><b>Ver a ficha completa</b></span></a></p>
  <section class="aviso" aria-labelledby="pg-h-como">
    <h2 id="pg-h-como" class="pg-h2">Como ler</h2>
    <p>{metodo(f)}</p>
    <p>Quantidade de proposições apresentadas ou de leis não mede qualidade nem mérito; esta página mostra os números como constam nos dados, sem comparar pessoas.</p>
  </section>
  <p class="pg-fonte">Fonte: <a href="{esc(fonte_url)}" target="_blank" rel="noopener">{esc(fonte_nome)}</a>.{pagina_oficial}
    Números alterados pela última vez em <time datetime="{esc(lastmod)}">{dataBR(lastmod)}</time>. A <a href="{esc(ficha)}">ficha completa</a> traz o resto (temas, votos por tema, caminho de cada proposição) e é
    atualizada todos os dias.</p>
</main>
{rodape}
</body>
</html>
"""


# ---------------------------------------------------------------------------------------------------------- rodapé global, copiado da inicial
def rodape_global():
    t = (SITE / "index.html").read_text(encoding="utf-8")
    i = t.index('<footer class="rodape-global">')
    j = t.index("</footer>", i) + len("</footer>")
    bloco = t[i:j].replace("\r\n", "\n")
    # links relativos da raiz viram ../../ (a página está a dois níveis da raiz); externos e âncoras ficam
    return re.sub(r'href="(?!https?:|#|/|mailto:)([^"]+)"', r'href="../../\1"', bloco)


# ---------------------------------------------------------------------------------------------------------- cartão og:image
_FONTES = {}


def fonte(peso, px):
    from PIL import ImageFont
    k = (peso, px)
    if k not in _FONTES:
        try:
            _FONTES[k] = ImageFont.truetype(str(FONTES / f"barlow-condensed-{peso}.woff2"), px)
        except OSError as e:
            raise Aborta(f"o Pillow não abriu a fonte barlow-condensed-{peso}.woff2 ({e}): precisa de FreeType com suporte a WOFF2 (brotli)")
    return _FONTES[k]


def quebrar(draw, texto, peso, px, largura):
    """Quebra por palavras em linhas que cabem na largura. Devolve (linhas, fonte)."""
    f = fonte(peso, px)
    linhas, atual = [], ""
    for p in texto.split():
        t = (atual + " " + p).strip()
        if draw.textlength(t, font=f) <= largura or not atual:
            atual = t
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas, f


def desenhar_cartao(rotulos, destino):
    """rotulos: dict com kicker, nome, linha2, numeros [(valor, rótulo)], rodape, dominio. 1200×630, só texto, paleta de 16 cores."""
    from PIL import Image, ImageDraw
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#0f172a")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, H], fill="#2dd4bf")
    x0, larg = 72, 1056
    d.text((x0, 52), rotulos["kicker"].upper(), font=fonte(600, 30), fill="#2dd4bf")
    # nome: o maior tamanho (até 128 px) que cabe em duas linhas
    for px in range(128, 55, -4):
        linhas, f = quebrar(d, rotulos["nome"], 700, px, larg)
        if len(linhas) <= 2:
            break
    y = 104
    for ln in linhas[:2]:
        d.text((x0, y), ln, font=f, fill="#ffffff")
        y += int(px * 0.98)
    d.text((x0, y + 24), rotulos["linha2"], font=fonte(500, 42), fill="#a3b1c2")
    # números
    n = len(rotulos["numeros"])
    col = larg // n
    yn = 388
    for i, (v, r) in enumerate(rotulos["numeros"]):
        x = x0 + i * col
        d.text((x, yn), v, font=fonte(700, 118), fill="#ffffff")
        d.text((x, yn + 124), r, font=fonte(500, 32), fill="#a3b1c2")
    d.line([x0, 566, x0 + larg, 566], fill="#334155", width=2)
    d.text((x0, 580), rotulos["rodape"], font=fonte(500, 26), fill="#a3b1c2")
    dom = rotulos["dominio"]
    d.text((x0 + larg - d.textlength(dom, font=fonte(600, 26)), 580), dom, font=fonte(600, 26), fill="#2dd4bf")
    q = img.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    destino.parent.mkdir(parents=True, exist_ok=True)
    q.save(destino, format="PNG", optimize=True)


def rotulos_cartao(f):
    v = f.get("votos")
    if f["tipo"] == "deputado":
        terceiro = (num(v["registrados"]), "votos registrados") if v else None
    else:
        terceiro = (num(v["registrados"]), "votações nominais") if v else None
    nums = [(num(f["apresentadas"]), "PEC e PL apresentadas"), (num(f["viraramLei"]), "viraram lei")]
    if terceiro:
        nums.append(terceiro)
    return {"kicker": f"{SITE_NOME} · {cargo(f)}", "nome": f["nome"], "linha2": f"{f['partido'] or 'sem partido'} · {f['uf']}", "numeros": nums,
            "rodape": f"Dados oficiais da {casa(f)} · números da carreira registrada", "dominio": BASE_URL.split("//", 1)[-1]}


def cartao_padrao(destino):
    from PIL import Image, ImageDraw
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#0f172a")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, H], fill="#2dd4bf")
    x0 = 72
    d.text((x0, 52), "DADOS PÚBLICOS OFICIAIS, TODO DIA", font=fonte(600, 30), fill="#2dd4bf")
    linhas, f = quebrar(d, SITE_NOME, 700, 190, 1056)
    d.text((x0, 150), linhas[0], font=f, fill="#ffffff")
    d.text((x0, 360), TAGLINE, font=fonte(500, 64), fill="#ffffff")
    d.text((x0, 450), "Deputados, senadores, leis votadas, blocos de voto e o caminho de cada proposta.", font=fonte(500, 36), fill="#a3b1c2")
    d.line([x0, 566, x0 + 1056, 566], fill="#334155", width=2)
    d.text((x0, 580), "Site independente, sem vínculo com a Câmara, o Senado ou o TSE", font=fonte(500, 26), fill="#a3b1c2")
    dom = BASE_URL.split("//", 1)[-1]
    d.text((x0 + 1056 - d.textlength(dom, font=fonte(600, 26)), 580), dom, font=fonte(600, 26), fill="#2dd4bf")
    q = img.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    destino.parent.mkdir(parents=True, exist_ok=True)
    q.save(destino, format="PNG", optimize=True)


# ---------------------------------------------------------------------------------------------------------- tags nas páginas atuais
def bloco_seo(arquivo):
    """O bloco entre marcadores que cada página atual recebe antes de </head>."""
    img = f"{BASE_URL}/img/og-padrao.png"
    linhas = [MARCA_INI]
    if arquivo in PAGINAS_REDIRECIONAM:
        linhas.append(f'<link rel="canonical" href="{BASE_URL}/{PAGINAS_REDIRECIONAM[arquivo]}">')
    else:
        if arquivo not in PAGINAS_SO_IMAGEM:
            url = BASE_URL + "/" + ("" if arquivo == "index.html" else arquivo)
            linhas += [f'<link rel="canonical" href="{url}">', f'<meta property="og:url" content="{url}">']
        linhas += [f'<meta property="og:image" content="{img}">', '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
                   f'<meta property="og:image:alt" content="{esc(SITE_NOME)}: {esc(TAGLINE)}">', '<meta property="og:locale" content="pt_BR">',
                   '<meta name="twitter:card" content="summary_large_image">', f'<meta name="twitter:image" content="{img}">']
    linhas.append(MARCA_FIM)
    return linhas


def injetar(arquivo, escrever=True):
    """Põe (ou troca) o bloco SEO em site/<arquivo>, preservando o fim de linha da página. Devolve True se o arquivo mudou."""
    p = SITE / arquivo
    t = p.read_text(encoding="utf-8")            # newline universal: \n
    bruto = p.read_bytes()
    nl = "\r\n" if b"\r\n" in bruto else "\n"
    bloco = "\n".join(bloco_seo(arquivo))
    if MARCA_INI in t:
        novo = re.sub(re.escape(MARCA_INI) + r".*?" + re.escape(MARCA_FIM), lambda m: bloco, t, count=1, flags=re.S)
    else:
        if "</head>" not in t:
            raise Aborta(f"{arquivo} não tem </head>")
        novo = t.replace("</head>", bloco + "\n</head>", 1)
    if novo == t:
        return False
    if escrever:
        p.write_bytes(novo.replace("\n", nl).encode("utf-8"))
    return True


def paginas_atuais():
    return [a for a in PAGINAS_SITEMAP if a] + ["index.html"] + PAGINAS_FORA_SITEMAP + PAGINAS_SO_IMAGEM + list(PAGINAS_REDIRECIONAM)


# ---------------------------------------------------------------------------------------------------------- sitemap e robots
def sitemap(entradas, data):
    """entradas: [(url, lastmod)]."""
    l = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, m in entradas:
        l.append(f"  <url><loc>{esc(u)}</loc><lastmod>{m}</lastmod></url>")
    l.append("</urlset>")
    return "\n".join(l) + "\n"


def robots():
    return f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n"


# ---------------------------------------------------------------------------------------------------------- manifesto
def hash_de(obj, versao):
    return hashlib.sha1((versao + json.dumps(obj, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()[:16]


def fatos_cartao(f):
    return {k: f.get(k) for k in ("tipo", "nome", "partido", "uf", "apresentadas", "viraramLei")} | {"votos": (f.get("votos") or {}).get("registrados")}


def gravar_manifesto(m, destino):
    def linhas(d):
        return ",\n".join(f"  {json.dumps(str(k))}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in sorted(d.items(), key=lambda kv: int(kv[0])))
    texto = ('{\n"baseUrl": ' + json.dumps(m["baseUrl"]) + ',\n"versao": ' + json.dumps(m["versao"]) + ',\n"geradoEm": ' + json.dumps(m["geradoEm"]) +
             ',\n"padrao": ' + json.dumps(m.get("padrao")) + ',\n"deputados": {\n' + linhas(m["deputados"]) + '\n},\n"senadores": {\n' + linhas(m["senadores"]) + "\n}\n}\n")
    json.loads(texto)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------------------------------------- geração
def gerar(dry=False, limite=None, so_estaticas=False):
    t0 = time.time()
    raiz = DRY if dry else SITE
    if dry and DRY.exists():
        shutil.rmtree(DRY)
    manifesto_ant = {}
    if MANIFESTO.exists() and not dry:
        manifesto_ant = ler(MANIFESTO)
    elif MANIFESTO.exists():
        manifesto_ant = ler(MANIFESTO)           # no dry-run só se lê, para medir o que mudaria
    # 1. páginas atuais
    mudaram = [a for a in paginas_atuais() if injetar(a, escrever=not dry)]
    # 2. cartão padrão
    padrao_h = hash_de({"s": SITE_NOME, "t": TAGLINE, "b": BASE_URL}, VERSAO_IMG)
    img_padrao = raiz / "img" / "og-padrao.png"
    if manifesto_ant.get("padrao") != padrao_h or not (SITE / "img" / "og-padrao.png").exists() or dry:
        cartao_padrao(img_padrao)
    if so_estaticas:
        return {"estaticas": mudaram, "t": time.time() - t0}
    # 3. perfis
    data, deps, sens = carregar_fatos()
    if limite:
        deps, sens = deps[:limite], sens[:limite]
    rod = rodape_global()
    novo = {"baseUrl": BASE_URL, "versao": VERSAO, "geradoEm": data, "padrao": padrao_h, "deputados": {}, "senadores": {}}
    stats = {"escritas": 0, "mantidas": 0, "imagens": 0, "removidas": 0, "bytes_html": 0, "bytes_png": 0, "gz_html": 0}
    for fatos, chave in ((deps, "deputados"), (sens, "senadores")):
        tipo = "deputado" if chave == "deputados" else "senador"
        anteriores = manifesto_ant.get(chave, {})
        for f in fatos:
            pid = str(f["id"])
            ant = anteriores.get(pid, {})
            slug = ant.get("slug") or slugar(f["nome"])
            # o rodapé (copiado da inicial) entra no hash: mudou o rodapé, todas as páginas são refeitas, mesmo sem dado novo
            h, hi = hash_de({"f": f, "rodape": rod}, VERSAO), hash_de(fatos_cartao(f), VERSAO_IMG)
            pasta = raiz / tipo / f"{pid}-{slug}"
            existe = (SITE / tipo / f"{pid}-{slug}" / "index.html").exists() and not dry
            if ant.get("h") == h and existe:
                lastmod = ant["lastmod"]
                stats["mantidas"] += 1
            else:
                lastmod = data
                pagina = html_pagina(f, slug, lastmod, rod)
                pasta.mkdir(parents=True, exist_ok=True)
                (pasta / "index.html").write_text(pagina, encoding="utf-8", newline="\n")
                stats["escritas"] += 1
            png = pasta / "og.png"
            if ant.get("hi") != hi or not (SITE / tipo / f"{pid}-{slug}" / "og.png").exists() or dry:
                desenhar_cartao(rotulos_cartao(f), png)
                stats["imagens"] += 1
            novo[chave][pid] = {"slug": slug, "h": h, "hi": hi, "lastmod": lastmod}
        # quem saiu do conjunto perde a página
        if not dry and not limite:
            for pid, ant in anteriores.items():
                if pid not in novo[chave]:
                    shutil.rmtree(SITE / tipo / f"{pid}-{ant['slug']}", ignore_errors=True)
                    stats["removidas"] += 1
    # 4. sitemap e robots
    entradas = [(f"{BASE_URL}/{a}", data) for a in PAGINAS_SITEMAP]
    for chave, tipo in (("deputados", "deputado"), ("senadores", "senador")):
        entradas += [(f"{BASE_URL}/{tipo}/{pid}-{e['slug']}/", e["lastmod"]) for pid, e in sorted(novo[chave].items(), key=lambda kv: int(kv[0]))]
    (raiz / "sitemap.xml").write_text(sitemap(entradas, data), encoding="utf-8", newline="\n")
    (raiz / "robots.txt").write_text(robots(), encoding="utf-8", newline="\n")
    if not dry:
        gravar_manifesto(novo, MANIFESTO)
    # 5. medidas
    for p in raiz.rglob("*"):
        if p.is_file() and (p.parent.parent.name in ("deputado", "senador") or p.name in ("sitemap.xml", "robots.txt")):
            b = p.read_bytes()
            if p.suffix == ".png":
                stats["bytes_png"] += len(b)
            else:
                stats["bytes_html"] += len(b)
                stats["gz_html"] += len(zlib.compress(b, 9))
    stats.update(estaticas=mudaram, paginas=len(deps) + len(sens), deputados=len(deps), senadores=len(sens), urls_sitemap=len(entradas), t=time.time() - t0,
                 data=data, raiz=str(raiz))
    return stats


# ---------------------------------------------------------------------------------------------------------- testes
def testar():
    ok = 0

    def ve(cond, msg):
        nonlocal ok
        if not cond:
            raise AssertionError(msg)
        ok += 1
    ve(slugar("Acácio Favacho") == "acacio-favacho", "slug com acento")
    ve(slugar("Dr. João d'Ávila Filho") == "dr-joao-d-avila-filho", "slug com pontuação")
    ve(slugar("???") == "parlamentar", "slug vazio")
    ve(len(slugar("a " * 60)) <= 60, "slug limitado")
    ve(leis_txt(0) == "nenhuma virou lei" and leis_txt(1) == "1 virou lei" and leis_txt(3) == "3 viraram lei", "plural de leis")
    ve(num(1234567) == "1.234.567", "milhar")
    f = {"tipo": "deputado", "id": 1, "nome": "Fulana de Tal", "partido": "XYZ", "uf": "SP", "posse": "Titular", "apresentadas": 10, "pecs": 1, "pls": 9, "viraramLei": 0,
         "legApresentadas": 4, "legViraramLei": 0, "legInicio": "2023-02-01", "votos": {"registrados": 150, "sim": 80, "nao": 60, "abstencao": 1}, "score": 6.04,
         "scoreNota": None, "frase": "Nesta legislatura, apresentou 4 proposições; nenhuma virou lei."}
    d = descricao(f)
    ve("10 proposições" in d and "nenhuma virou lei" in d and "150 votações" in d, "descrição com os números")
    ve(not re.search(r"\b(melhor|pior|bom|ruim|ranking|eficiente|produtiv)", d, re.I), "descrição sem julgamento")
    pg = html_pagina(f, "fulana-de-tal", "2026-10-20", "<footer></footer>")
    for trecho in ('rel="canonical" href="https://congressoclaro.com.br/deputado/1-fulana-de-tal/"', 'property="og:type" content="profile"', 'name="twitter:card" content="summary_large_image"',
                   'application/ld+json', '"@type": "Person"', "Ver a ficha completa", "../../deputado.html?id=1", "20/10/2026", "Não é taxa de presença"):
        ve(trecho in pg, f"página sem {trecho}")
    ve("cpf" not in pg.lower() and "nascimento" not in pg.lower(), "sem dado sensível")
    ve(json.loads(re.search(r'ld\+json">(.*?)</script>', pg, re.S).group(1))["name"] == "Fulana de Tal", "JSON-LD válido")
    f2 = dict(f, nome="<script>x</script>")
    ve("<script>x</script>" not in html_pagina(f2, "x", "2026-10-20", ""), "escapa HTML")
    ve(hash_de(f, "a") == hash_de(dict(f), "a") and hash_de(f, "a") != hash_de(dict(f, apresentadas=11), "a"), "hash muda com o número")
    s = sitemap([("https://x/a", "2026-10-20")], "2026-10-20")
    ve("<loc>https://x/a</loc><lastmod>2026-10-20</lastmod>" in s, "sitemap")
    ve("Sitemap: https://congressoclaro.com.br/sitemap.xml" in robots() and "Allow: /" in robots(), "robots")
    b = "\n".join(bloco_seo("senadores.html"))
    ve('rel="canonical" href="https://congressoclaro.com.br/senadores.html"' in b and "twitter:card" in b, "bloco SEO de página atual")
    ve("canonical" not in "\n".join(bloco_seo("deputado.html")), "página só com ?id= não tem canonical fixo")
    # nome longo cabe em duas linhas no cartão
    from PIL import Image, ImageDraw
    dr = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    linhas, _ = quebrar(dr, "Dr. Francisco de Assis Pereira Rodrigues dos Santos Neto", 700, 64, 1056)
    ve(len(linhas) <= 2, "nome longo em até 2 linhas no tamanho mínimo")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="gera tudo em cache/paginas_dryrun/ sem tocar em site/ nem no manifesto")
    ap.add_argument("--so-estaticas", action="store_true", help="só as tags nas páginas atuais e o og-padrao.png")
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--teste", action="store_true")
    a = ap.parse_args()
    if a.teste:
        print(f"{testar()} verificações passaram")
        return 0
    if a.limite and not a.dry_run:
        ap.error("--limite só com --dry-run (senão o manifesto perderia os demais)")
    try:
        s = gerar(dry=a.dry_run, limite=a.limite, so_estaticas=a.so_estaticas)
    except Aborta as e:
        print(f"ABORTADO: {e}")
        return 1
    mb = lambda b: f"{b / 1e6:.1f} MB"
    print(f"{'DRY-RUN' if a.dry_run else 'Gerado'} em {s['t']:.1f}s — páginas atuais {'que receberiam' if a.dry_run else 'que receberam'} as tags: {len(s['estaticas'])}")
    if not a.so_estaticas:
        print(f"  perfis: {s['paginas']} ({s['deputados']} deputados + {s['senadores']} senadores), dados de {s['data']}")
        print(f"  escritas {s['escritas']} · mantidas {s['mantidas']} · imagens {s['imagens']} · removidas {s['removidas']} · URLs no sitemap {s['urls_sitemap']}")
        print(f"  peso: HTML {mb(s['bytes_html'])} ({mb(s['gz_html'])} comprimido) + PNG {mb(s['bytes_png'])} = {mb(s['bytes_html'] + s['bytes_png'])}")
        print(f"  saída: {s['raiz']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
