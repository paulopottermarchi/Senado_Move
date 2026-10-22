"""
Câmara Aberta — ilustrações de tema para as notícias sem foto de licença livre (site/img/noticias/*.jpg).

Uso:
    python scripts/ilustracoes_noticias.py            # baixa, confere a licença, processa e grava as imagens e os créditos no manifesto
    python scripts/ilustracoes_noticias.py --teste    # testes sintéticos, sem rede

Por que existe: a foto de uma notícia só entra no site se tiver licença que permita uso comercial (hoje, só as da Câmara dos Deputados, Creative Commons
BY). Para as outras notícias (bancos de imagens pagos, agências com uso só não comercial, órgãos com regra própria), o site mostra uma ILUSTRAÇÃO DO TEMA da
lei, nunca uma foto "da notícia", escolhida à mão no Wikimedia Commons (entradas/ilustracoes_noticias.json): uma por bloco de tema e uma geral.

Regras:
  * licença CC0, domínio público ou CC BY (nada com SA, NC ou ND), conferida na API do Commons a CADA execução: se a licença mudar, o script aborta;
  * sem rosto identificável (a escolha é à mão, ver "_regra" no manifesto);
  * a imagem é processada aqui (cinza, recorte 3:2, 300 px de largura, JPEG) e fica no repositório: a licença permite, e assim o site não depende do Commons;
  * o crédito (autor, licença, página do arquivo) volta ao manifesto e vai para o site em dados/noticias.json.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coleta
import wikipedia as W

MANIFESTO = coleta.ENTRADAS / "ilustracoes_noticias.json"
SAIDA = coleta.BASE / "site" / "img" / "noticias"
API = "https://commons.wikimedia.org/w/api.php"
LARGURA, ALTURA = 300, 188          # 3:2 aproximado; a página mostra 128×80 (a versão de 300 px serve com folga, também em tela de alta densidade)
LIC_OK = re.compile(r"^(CC0|Public domain|PD\b|CC BY (\d\.\d)( (BR|br|AT|DE|NL|ES|FR|IT|PL|UK|AU|CH|Brazil))?$)", re.I)


def licenca_ok(nome):
    """CC0, domínio público ou CC BY (qualquer versão); nunca SA, NC, ND nem GFDL."""
    n = (nome or "").strip()
    if re.search(r"\b(SA|NC|ND|GFDL|fair use)\b", n, re.I):
        return False
    return bool(LIC_OK.match(n))


def limpa(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or "")).strip()


def recorte(im, foco=(0.5, 0.5)):
    """Cinza e recorte 3:2 em torno do ponto `foco` (frações de largura e altura), na largura final."""
    from PIL import Image
    im = im.convert("L")
    w, h = im.size
    alvo = LARGURA / ALTURA
    if w / h > alvo:
        nw = round(h * alvo)
        x0 = min(max(round(foco[0] * w - nw / 2), 0), w - nw)
        im = im.crop((x0, 0, x0 + nw, h))
    else:
        nh = round(w / alvo)
        y0 = min(max(round(foco[1] * h - nh / 2), 0), h - nh)
        im = im.crop((0, y0, w, y0 + nh))
    return im.resize((LARGURA, ALTURA), Image.LANCZOS)


def info(titulo):
    d = W.pedir(API, {"action": "query", "format": "json", "titles": titulo, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 900,
                      "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl"})
    p = next(iter((d or {}).get("query", {}).get("pages", {}).values()), {})
    ii = (p.get("imageinfo") or [None])[0]
    if not ii:
        raise SystemExit(f"arquivo não encontrado no Commons: {titulo}")
    m = ii.get("extmetadata", {})
    return {"licenca": (m.get("LicenseShortName") or {}).get("value"), "licencaUrl": (m.get("LicenseUrl") or {}).get("value"),
            "autor": limpa((m.get("Artist") or {}).get("value"))[:120] or None, "pagina": ii["descriptionurl"], "thumb": ii["thumburl"]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if "--teste" in sys.argv[1:]:
        testar()
        return
    from PIL import Image
    man = json.loads(MANIFESTO.read_text(encoding="utf-8"))
    SAIDA.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now(timezone(timedelta(hours=-3))).date().isoformat()
    for slug, e in man["ilustracoes"].items():
        i = info(e["arquivo"])
        if not licenca_ok(i["licenca"]):
            raise SystemExit(f"{slug}: licença '{i['licenca']}' não vale para esta regra (só CC0, domínio público ou CC BY): {e['arquivo']}")
        r = W.sessao.get(i["thumb"], timeout=90)
        r.raise_for_status()
        im = recorte(Image.open(BytesIO(r.content)), tuple(e.get("foco") or (0.5, 0.5)))
        im.save(SAIDA / f"{slug}.jpg", "JPEG", quality=72, optimize=True, progressive=True)
        e.update({k: i[k] for k in ("autor", "licenca", "licencaUrl", "pagina")}, verificadoEm=hoje)
        print(f"  {slug:28} {i['licenca']:14} {(SAIDA / (slug + '.jpg')).stat().st_size / 1024:5.1f} kB  {i['autor'] or '(sem autor)'}", flush=True)
    MANIFESTO.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for velha in SAIDA.glob("*.jpg"):                 # imagem que não está mais no manifesto (slug trocado ou removido)
        if velha.stem not in man["ilustracoes"]:
            velha.unlink()
            print(f"  removida: {velha.name}")
    print(f"{len(man['ilustracoes'])} ilustrações em {SAIDA}")


def testar():
    ok = 0

    def igual(a, b, m):
        nonlocal ok
        assert a == b, f"{m}: {a!r} != {b!r}"
        ok += 1
    for lic in ("CC0", "Public domain", "PD-US-gov", "CC BY 2.0", "CC BY 4.0", "CC BY 3.0 BR"):
        igual(licenca_ok(lic), True, f"vale: {lic}")
    for lic in ("CC BY-SA 4.0", "CC BY-NC 2.0", "CC BY-ND 2.0", "CC BY-NC-SA 3.0", "GFDL", "Fair use", "", None):
        igual(licenca_ok(lic), False, f"não vale: {lic}")
    from PIL import Image
    for tam in ((4000, 3000), (3000, 4000), (900, 600)):
        im = recorte(Image.new("RGB", tam, (120, 40, 10)))
        igual((im.size, im.mode), ((LARGURA, ALTURA), "L"), f"recorte {tam}: tamanho final e tons de cinza")
    ok_borda = recorte(Image.new("RGB", (4000, 3000)), (1.0, 1.0)).size
    igual(ok_borda, (LARGURA, ALTURA), "foco na borda não passa da imagem")
    print(f"{ok} verificações passaram")


if __name__ == "__main__":
    main()
