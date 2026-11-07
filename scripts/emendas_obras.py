"""
Câmara Aberta — emendas de deputados nas obras: o autor de emenda INDIVIDUAL cujo código está no empenho de uma obra de SP.

Uso:
    python scripts/emendas_obras.py --dry-run   # o padrão: grava só em cache/emendas/ (candidato, auditoria e relatório)
    python scripts/emendas_obras.py --publicar  # além disso troca site/dados/emendas/ pelo candidato (só se as checagens passarem)
    python scripts/emendas_obras.py --so-montar # sem rede: remonta do cache
    python scripts/emendas_obras.py --teste     # casos sintéticos, sem rede
A PUBLICAÇÃO É DESLIGADA POR PADRÃO: sem --publicar este script nunca escreve em site/.
Publicação = um arquivo por deputado (site/dados/emendas/{id}.json, o único que o perfil baixa) e um índice leve
(site/dados/emendas/indice.json: as contagens por deputado e as emendas das obras que estão em obras.html). A pasta inteira é
montada ao lado e só então trocada: rodada que falha não deixa arquivo pela metade nem apaga o que estava publicado.

De onde vem (só identificador exato, nunca nome de obra ou texto livre):
  - ObrasGov (API pública): as obras de SP (/projeto-investimento?uf_principal=SP), os empenhos do país inteiro (/empenho, ~450
    páginas; não há filtro por UF) e os municípios (/geometria?sg_uf=SP). O empenho traz `codigo_autor_emenda`, que é o código
    da emenda (12 dígitos: ano, autor, número).
  - CGU, EmendasParlamentares.csv (o mesmo arquivo de obras.py): código da emenda → autor (código e nome), tipo, ano, UF de
    aplicação. O nome do autor só vira deputado pela regra do site (obras.autores_deputados: nome exato de um único deputado e
    todos os anos das emendas daquele código dentro do mandato na Câmara).
Por que existe: o diagnóstico E9 (diag_vinculo_e9e10.py) mediu que o caminho empenho → emenda → autor liga obras a deputados sem
passar pelo contrato; o E5 só tinha olhado as 260 obras com contrato federal, onde não há emenda individual.

Regras (a que falhar retém o vínculo; o relatório conta quantos caem em cada uma):
  - só emenda INDIVIDUAL; comissão, relator e bancada aparecem pelo tipo, sem nome de pessoa;
  - código de 12 dígitos exato e presente na CGU; 8 dígitos (sem o ano) nunca se completam;
  - ano da emenda = ano de TODOS os empenhos daquela emenda na obra; deputado em exercício no ano da emenda ou no anterior;
  - (a) o código do autor corresponde a um único nome em todo o arquivo da CGU, e esse nome a um único código; o código com alias
    explícito em entradas/emendas_aliases.json (o mesmo código da CGU gravado com duas grafias do mesmo deputado) conta como
    resolvido, se cada grafia for só daquele código na CGU e a Câmara registrar cada uma para aquele deputado; sem alias, fica retido;
  - (b) UF de aplicação da emenda = São Paulo ou "Múltiplo" (outra UF fica pendente; "Sem informação" também fica de fora);
  - (c) valor empenhado maior que zero;
  - entradas/emendas_supressoes.json retira um vínculo depois de revisão (id = "obra:código da emenda").
Sem ex-deputados nem senadores nesta fase: o relatório conta quantos entrariam com a mesma regra.
Valores, nunca o custo da obra: (1) o empenhado NESTA OBRA, soma dos empenhos desta emenda ligados à obra no ObrasGov; (2) o
empenhado e o pago DA EMENDA INTEIRA, da CGU (pago = valor pago + restos a pagar pagos), que pode financiar mais de um destino.
O "pago" do empenho no ObrasGov NÃO é usado: medido em 24/10/2026, é zero em 421 dos 424 vínculos publicáveis (e liquidado e restos a
pagar são zero em todos os 1.536 empenhos de emenda de 2019 no país): o campo não é preenchido para essas obras.
Fonte de cada item em texto (obra, empenho, emenda): o empenho do ObrasGov traz a UG e o número, mas não a gestão (o esquema da API,
EmpenhoResponse, não tem o campo), e o endereço do empenho no Portal da Transparência precisa dos três; a gestão não é adivinhada.
Nunca ranking, nunca soma de valores por pessoa, e nenhum item traz situação da obra, aditivo ou crescimento do contrato.
"""

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain
from datetime import datetime, timedelta

import requests

import coleta
import obras

OG = obras.OG
CACHE_E = coleta.CACHE / "emendas"
PUBLICO = coleta.DADOS / "emendas"                # a pasta publicada: {id}.json por deputado + indice.json
CANDIDATO = CACHE_E / "candidato"                # a mesma pasta, antes de publicar
AUDITORIA = CACHE_E / "auditoria.json"           # todas as obras e todos os vínculos, inclusive os retidos (nunca publicado)
RELATORIO = CACHE_E / "relatorio.txt"
SUPRESSOES = coleta.ENTRADAS / "emendas_supressoes.json"
ALIASES = coleta.ENTRADAS / "emendas_aliases.json"
INDICE = "indice.json"
VERSAO = "emendas-2"
ESQUEMA_CGU = 2                 # sobe quando muda o que se guarda do arquivo da CGU (refaz o cache)
UF = "SP"
VALIDADE = timedelta(days=7)    # listas do ObrasGov
VALIDADE_DEP = timedelta(days=30)
IDADE_MAX_CGU = timedelta(days=3)   # se o zip novo da CGU falhar, a cópia anterior só vale até esta idade; acima, a rodada para
QUEDA_MAXIMA = 0.20            # vínculos publicados: queda maior contra o índice publicado derruba a rodada
QUEDA_FONTE = 0.10             # lista da fonte (ObrasGov, CGU) 10% menor que a anterior: download suspeito, derruba a rodada
POR_PAGINA = 200               # o máximo que o ObrasGov aceita
INTERVALO = 1.0                # segundos entre o início de dois pedidos ao ObrasGov (o ritmo de obras.pedir)
PARALELO = 3                   # páginas em voo ao mesmo tempo (a resposta leva 1 a 3 s; o ritmo continua 1 pedido por segundo)
PRAZO = [None]                 # instante (time.monotonic) depois do qual nenhuma página nova é pedida (--minutos)
BAIXADO = []                   # o que esta rodada baixou ou leu do cache (para o relatório)
TEXTO = ("Autor de emenda individual vinculada ao empenho desta obra; o autor da emenda não é quem contratou ou executou a obra.")
ORDEM = ("ano_diferente", "fora_do_mandato", "codigo_nome_ambiguo", "localidade_outra_uf", "localidade_sem_informacao",
         "sem_valor_empenhado", "suprimida_manualmente")
MOTIVOS = {
    "ano_diferente": "o ano da emenda não é o ano do empenho",
    "fora_do_mandato": "o deputado não estava em exercício no ano da emenda",
    "codigo_nome_ambiguo": "o código do autor e o nome não se correspondem um a um no arquivo da CGU",
    "localidade_outra_uf": "a emenda é registrada para outra UF (vínculo pendente)",
    "localidade_sem_informacao": "a CGU não informa a UF de aplicação da emenda",
    "sem_valor_empenhado": "o empenho desta emenda na obra não tem valor empenhado",
    "suprimida_manualmente": "retirado depois de revisão",
    "fora_desta_fase": "o autor não é deputado em exercício neste site (ex-deputado, senador ou sem conferência): fora desta fase",
}
NOTA_VALORES = ("Empenhado nesta obra: os empenhos desta emenda ligados a esta obra (ObrasGov). Emenda inteira: o empenhado e o pago "
                "da emenda em todos os destinos (CGU), que pode financiar mais de uma obra. Nenhum dos dois é o custo da obra.")
TIPOS = {"individual": "emenda individual", "comissão": "emenda de comissão", "relator": "emenda de relator",
         "bancada": "emenda de bancada", "outro": "emenda (tipo não informado)"}
REGRAS = ("Só emenda individual com código de 12 dígitos exato no empenho da obra (ObrasGov) e na CGU; ano da emenda = ano do "
          "empenho; deputado em exercício no ano; código do autor e nome um a um na CGU (ou o mesmo código com duas grafias do mesmo "
          "deputado, listadas em tabela e registradas pela Câmara); UF de aplicação São Paulo ou Múltiplo; valor empenhado maior que "
          "zero. Comissão, relator e bancada aparecem pelo tipo, sem nome.")
# o que nenhum arquivo publicado pode ter (tarefa: nenhum item mostra situação da obra, aditivo, estouro ou valor total por deputado).
# As chaves valem para tudo; as palavras, só para os textos fixos do site (nome de obra vem da fonte: "Estádio João Crescêncio").
CHAVES_PROIBIDAS = {"situacao", "situação", "sistema", "aditivo", "aditivos", "crescimento", "estouro", "inicial", "atual", "total",
                    "valorTotal", "totalEmpenhado", "totalPago", "soma"}
PALAVRAS_PROIBIDAS = re.compile(r"aditiv|estour|cresc|superfatur|situa[cç][aã]o da obra|total (empenhado|pago|por)", re.I)


# ---------------------------------------------------------------- utilidades

def agora():
    return datetime.now(obras.BRASILIA).isoformat(timespec="seconds")


def velho(quando, validade):
    try:
        return datetime.now(obras.BRASILIA) - datetime.fromisoformat(quando) > validade
    except (TypeError, ValueError):
        return True


def aviso(msg):
    print(f"::warning title=emendas_obras.py::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"AVISO: {msg}", flush=True)


def tipo_emenda(t):
    t = (t or "").lower()
    for chave, rot in (("individual", "individual"), ("bancada", "bancada"), ("comiss", "comissão"), ("relator", "relator")):
        if chave in t:
            return rot
    return "outro"


def classifica_codigo(v):
    """O campo `codigo_autor_emenda` do empenho: ('12 dígitos', código) é o único que liga. O resto só é contado."""
    s = re.sub(r"\.0$", "", str(v if v is not None else "").strip())
    if not s:
        return "sem código", None
    if re.fullmatch(r"0+", s):
        return "zero", None
    if re.fullmatch(r"\d{12}", s):
        return "12 dígitos", s
    if re.fullmatch(r"\d{8}", s):
        return "8 dígitos (sem o ano: não se completa)", None
    return "outro formato", None


def uf_categoria(uf):
    """A CGU grava a UF por extenso: "SÃO PAULO", "Múltiplo", "Sem informação"."""
    u = coleta.nome_chave(uf or "")
    if u in ("SAO PAULO", "SP"):
        return "SP"
    if u.startswith("MULTIPLO"):
        return "múltiplo"
    if not u or u.startswith("SEM INFORMA"):
        return "sem informação"
    return "outra UF"


def em_exercicio(mandatos, ano):
    """A mesma regra de obras.autores_deputados: exercício no ano da emenda ou no anterior (a emenda ao orçamento de Y é de Y−1)."""
    try:
        y = int(ano)
    except (TypeError, ValueError):
        return False
    return any(p["de"][:4] <= str(y) and (p.get("ate") or "9999")[:4] >= str(y - 1)
               for per in (mandatos or {}).values() for p in per)


def rel(p):
    """O caminho a partir da raiz do projeto, para as mensagens (o absoluto, se estiver fora dela)."""
    try:
        return p.relative_to(coleta.BASE).as_posix()
    except ValueError:
        return str(p)


def numero(v):
    return obras.num(v)


# ---------------------------------------------------------------- fontes

class DownloadIncompleto(RuntimeError):
    """A fonte devolveu erro, página vazia ou truncada, ou encolheu: a rodada para e nada é publicado. Erro de API nunca vira
    "sem emenda" (uma lista de empenhos pela metade apagaria os vínculos de quem ficou na outra metade)."""


class FonteMudou(DownloadIncompleto):
    """Os totais mudaram no meio do download: as páginas guardadas não servem para retomar."""


def conferir_pagina(r, caminho, pag, esperado=None):
    """Uma página do ObrasGov: `data` é lista, `total_items` e `total_pages` são inteiros coerentes entre si e iguais aos da 1ª
    página, toda página antes da última tem POR_PAGINA itens e a última tem exatamente o resto. Devolve (total_items, total_pages)."""
    onde = f"ObrasGov {caminho}, página {pag}"
    if not isinstance(r, dict) or not isinstance(r.get("data"), list):
        raise DownloadIncompleto(f"{onde}: resposta sem a lista 'data'")
    ti, tp = r.get("total_items"), r.get("total_pages")
    if isinstance(ti, bool) or not isinstance(ti, int) or not isinstance(tp, int) or ti <= 0 or tp != -(-ti // POR_PAGINA):
        raise DownloadIncompleto(f"{onde}: totais inválidos (total_items={ti!r}, total_pages={tp!r})")
    if esperado and (ti, tp) != esperado:
        raise FonteMudou(f"{onde}: a fonte mudou durante o download ({esperado[0]} itens na 1ª página, {ti} nesta)")
    if pag > tp:
        raise DownloadIncompleto(f"{onde}: além da última página ({tp})")
    n = POR_PAGINA if pag < tp else ti - POR_PAGINA * (tp - 1)
    if len(r["data"]) != n:
        raise DownloadIncompleto(f"{onde}: {len(r['data'])} itens, esperado {n} (página vazia ou truncada)")
    return ti, tp


def lista(nome, caminho, so_montar=False, validade=VALIDADE, **filtro):
    """Todas as páginas de um endpoint do ObrasGov. O resultado inteiro fica em cache/emendas/{nome}.json; enquanto baixa, cada
    página conferida fica em cache/emendas/paginas/{nome}/, para uma rodada interrompida retomar do ponto em que parou. Qualquer
    página com erro, vazia ou truncada, totais que mudam no meio, ou uma lista 10% menor que a anterior: DownloadIncompleto, e o
    cache da lista anterior fica como estava."""
    cam = CACHE_E / f"{nome}.json"
    d = coleta.ler_cache(cam)
    if d and (so_montar or not velho(d.get("baixadoEm"), validade)):
        BAIXADO.append({"fonte": f"ObrasGov {caminho}", "de": "cache", "itens": len(d["itens"]), "baixadoEm": d.get("baixadoEm")})
        return d["itens"], d
    if so_montar:
        raise RuntimeError(f"--so-montar sem cache de {nome}: rode sem --so-montar antes")
    pasta = CACHE_E / "paginas" / nome
    pasta.mkdir(parents=True, exist_ok=True)
    marca = coleta.ler_cache(pasta / "_inicio.json")
    if not marca or velho(marca.get("quando"), timedelta(days=1)):
        for p in pasta.glob("p*.json"):
            p.unlink()
        coleta.gravar_cache(pasta / "_inicio.json", {"quando": agora()})
    t0, baixadas = time.monotonic(), []

    def pagina(pag, esperado):
        """A página do cache, se confere; senão, da API (conferida antes de guardar)."""
        cp = pasta / f"p{pag}.json"
        r = coleta.ler_cache(cp)
        if r is not None:
            try:
                return r, conferir_pagina(r, caminho, pag, esperado)
            except DownloadIncompleto:
                pass                                 # página guardada que não confere: pede de novo
        if PRAZO[0] and time.monotonic() > PRAZO[0]:
            raise DownloadIncompleto(f"ObrasGov {caminho}, página {pag}: tempo da rodada esgotado (--minutos); as páginas baixadas ficam para "
                                     "a próxima tentativa do dia")
        no_ritmo()
        try:
            r = obras.pedir(OG + caminho, dict(pagina=pag, tamanho_da_pagina=POR_PAGINA, **filtro), intervalo=0)
        except (RuntimeError, ValueError, requests.RequestException) as e:   # ValueError: JSON cortado no meio
            raise DownloadIncompleto(f"ObrasGov {caminho}, página {pag}: {e}") from e
        if r is None:
            raise DownloadIncompleto(f"ObrasGov {caminho}, página {pag}: HTTP 404")
        tot = conferir_pagina(r, caminho, pag, esperado)
        coleta.gravar_cache(cp, r)
        baixadas.append(pag)
        return r, tot

    try:
        r1, esperado = pagina(1, None)               # a 1ª página dá os totais; as outras vão em paralelo, no mesmo ritmo
        paginas = {1: r1["data"]}
        with ThreadPoolExecutor(max_workers=PARALELO) as ex:
            futuros = {ex.submit(pagina, p, esperado): p for p in range(2, esperado[1] + 1)}
            try:
                for n, f in enumerate(as_completed(futuros), 2):
                    paginas[futuros[f]] = f.result()[0]["data"]
                    if n % 50 == 0:
                        print(f"   {caminho}: {n}/{esperado[1]} páginas", flush=True)
            except BaseException:
                for f in futuros:
                    f.cancel()                       # a 1ª falha para tudo; o que já foi conferido fica guardado
                raise
    except FonteMudou:
        shutil.rmtree(pasta, ignore_errors=True)     # a próxima tentativa começa do zero
        raise
    itens = [x for p in sorted(paginas) for x in paginas[p]]
    if len(itens) != esperado[0]:
        raise DownloadIncompleto(f"ObrasGov {caminho}: {len(itens)} itens baixados, a API informou {esperado[0]}")
    if d and len(itens) < (1 - QUEDA_FONTE) * len(d.get("itens") or []):
        raise DownloadIncompleto(f"ObrasGov {caminho}: {len(itens)} itens agora, {len(d['itens'])} na lista anterior (queda maior que "
                                 f"{QUEDA_FONTE:.0%}); se a queda for real, apague {rel(cam)} e rode de novo")
    d = {"baixadoEm": agora(), "total": esperado[0], "itens": itens}
    coleta.gravar_cache(cam, d)
    shutil.rmtree(pasta, ignore_errors=True)
    BAIXADO.append({"fonte": f"ObrasGov {caminho}", "de": "rede", "itens": len(itens), "paginas": len(baixadas), "de_total": esperado[1],
                    "segundos": round(time.monotonic() - t0)})
    return itens, d


_ritmo, _ultimo_pedido = threading.Lock(), [0.0]


def no_ritmo():
    """No máximo um pedido ao ObrasGov iniciado a cada INTERVALO segundos, com qualquer número de páginas em voo: o mesmo ritmo de
    obras.pedir (1 por segundo), sem esperar a resposta de uma para começar a outra (a resposta leva de 1 a 3 s)."""
    with _ritmo:
        espera = INTERVALO - (time.monotonic() - _ultimo_pedido[0])
        if espera > 0:
            time.sleep(espera)
        _ultimo_pedido[0] = time.monotonic()


def ler_csv(z, nome):
    with z.open(nome) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")


def cgu(so_montar=False):
    """Do arquivo de emendas da CGU, só o que a regra usa: {emendas: {código: [autor, nome, tipo, ano, [UFs], empenhado, pago]}, autorNomes:
    {código do autor: [nomes]}, nomeAutores: {nome: [códigos]}}. O zip (~32 MB) só é baixado de novo quando a CGU publica outro."""
    cam = CACHE_E / "cgu.json"
    atual = coleta.ler_cache(cam)
    if so_montar:
        if not atual:
            raise RuntimeError("--so-montar sem cache da CGU: rode sem --so-montar antes")
        return atual
    try:
        versao = obras.sessao.head(obras.EMENDAS_ZIP, timeout=60, allow_redirects=True).headers.get("last-modified")
    except requests.RequestException:
        versao = None
    valida = atual if atual and atual.get("_esquema") == ESQUEMA_CGU else None
    cam_conf = CACHE_E / "cgu_conferido.json"        # a última vez em que a CGU confirmou que a cópia é a versão vigente
    if valida and versao is not None and valida.get("_versao") == versao:
        coleta.gravar_cache(cam_conf, {"versao": versao, "conferidoEm": agora()})
        BAIXADO.append({"fonte": "CGU, emendas parlamentares", "de": "cache", "itens": len(valida["emendas"]), "baixadoEm": valida.get("_baixadoEm")})
        return valida
    t0 = time.monotonic()
    try:                                             # versão nova, ou a CGU não disse a versão (HEAD falhou): baixa
        r = obras.sessao.get(obras.EMENDAS_ZIP, timeout=600)
        if r.status_code != 200:
            raise DownloadIncompleto(f"HTTP {r.status_code}")
        obj = ler_zip_cgu(r.content, versao)
    except (DownloadIncompleto, requests.RequestException, zipfile.BadZipFile, zlib.error, EOFError) as e:
        # zip cortado, CRC errado ou fora do ar: vale a cópia anterior, completa (nunca um arquivo pela metade), só se tiver até 3 dias
        if not valida:
            raise DownloadIncompleto(f"arquivo de emendas da CGU: {e} (e não há cópia anterior)") from e
        idade = idade_da_copia(valida, coleta.ler_cache(cam_conf))
        horas = f"{idade.total_seconds() / 3600:.0f} h" if idade < timedelta(days=2) else f"{idade.days} dias"
        if idade > IDADE_MAX_CGU:
            raise DownloadIncompleto(f"arquivo de emendas da CGU: {e}; a cópia anterior tem {horas}, mais que o limite de "
                                     f"{IDADE_MAX_CGU.days} dias: nada é publicado") from e
        aviso(f"arquivo de emendas da CGU: {e}; usando a cópia completa anterior, de {horas} (limite: {IDADE_MAX_CGU.days} dias)")
        BAIXADO.append({"fonte": "CGU, emendas parlamentares", "de": f"cache (falha no download; cópia de {horas})", "itens": len(valida["emendas"]),
                        "baixadoEm": valida.get("_baixadoEm")})
        return valida
    if valida and len(obj["emendas"]) < (1 - QUEDA_FONTE) * len(valida["emendas"]):
        raise DownloadIncompleto(f"arquivo de emendas da CGU: {len(obj['emendas'])} emendas agora, {len(valida['emendas'])} na cópia anterior "
                                 f"(queda maior que {QUEDA_FONTE:.0%}); se a queda for real, apague {rel(cam)}")
    coleta.gravar_cache(cam, obj)
    BAIXADO.append({"fonte": "CGU, emendas parlamentares", "de": "rede", "itens": len(obj["emendas"]),
                    "mb": round(len(r.content) / 2 ** 20, 1), "segundos": round(time.monotonic() - t0)})
    return obj


def idade_da_copia(copia, conferido):
    """Há quanto tempo a cópia guardada deixou de ser confirmada como a versão vigente da CGU: desde que foi baixada, ou desde a
    última rodada em que a CGU disse a mesma versão (cgu_conferido.json), o que for mais recente."""
    datas = [copia.get("_baixadoEm")]
    if conferido and conferido.get("versao") == copia.get("_versao"):
        datas.append(conferido.get("conferidoEm"))
    vistas = [datetime.fromisoformat(d) for d in datas if d]
    return datetime.now(obras.BRASILIA) - max(vistas) if vistas else timedelta.max


def ler_zip_cgu(conteudo, versao=None):
    """O que a regra usa do EmendasParlamentares.csv. Zip cortado ou com CRC errado levanta BadZipFile (o zipfile confere ao ler)."""
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    campos = {"Código da Emenda", "Ano da Emenda", "Tipo de Emenda", "Código do Autor da Emenda", "Nome do Autor da Emenda", "UF",
              "Valor Empenhado", "Valor Pago", "Valor Restos A Pagar Pagos"}
    emendas, autor_nomes, nome_autores, conflito = {}, defaultdict(set), defaultdict(set), set()
    linhas = ler_csv(z, "EmendasParlamentares.csv")
    primeira = next(linhas, None)
    if primeira is None:
        raise DownloadIncompleto("arquivo de emendas da CGU vazio")
    if not campos <= set(primeira):
        raise obras.LeiauteMudou(f"CGU emendas: colunas mudaram ({sorted(primeira)})")
    for x in chain([primeira], linhas):
        c = (x.get("Código da Emenda") or "").strip()
        if not re.fullmatch(r"\d{12}", c):
            continue
        ca, nome = (x.get("Código do Autor da Emenda") or "").strip(), (x.get("Nome do Autor da Emenda") or "").strip()
        autor_nomes[ca].add(coleta.nome_chave(nome))
        nome_autores[coleta.nome_chave(nome)].add(ca)
        e = emendas.setdefault(c, [ca, nome, (x.get("Tipo de Emenda") or "").strip(), (x.get("Ano da Emenda") or "").strip(), [], 0.0, 0.0])
        if e[0] != ca:
            conflito.add(c)                   # o mesmo código de emenda com dois autores: não liga ninguém
        uf = (x.get("UF") or "").strip()
        if uf not in e[4]:
            e[4].append(uf)
        e[5] = round(e[5] + numero(x.get("Valor Empenhado")), 2)       # a emenda tem uma linha por localidade e função: soma
        e[6] = round(e[6] + numero(x.get("Valor Pago")) + numero(x.get("Valor Restos A Pagar Pagos")), 2)
    if not emendas:
        raise DownloadIncompleto("arquivo de emendas da CGU sem nenhuma emenda de 12 dígitos")
    return {"_versao": versao, "_esquema": ESQUEMA_CGU, "_baixadoEm": agora(), "emendas": emendas,
            "autorNomes": {k: sorted(v) for k, v in autor_nomes.items()},
            "nomeAutores": {k: sorted(v) for k, v in nome_autores.items()}, "conflitos": sorted(conflito)}


def ex_deputados(legislaturas, ids_atuais, so_montar=False):
    """Para a CONTAGEM de quem entraria sem esta fase: {nome: {legislatura: {id}}} dos deputados de cada legislatura (lista
    /deputados?idLegislatura=N da Câmara), fora os 513 de hoje. É aproximado: a lista diz quem exerceu na legislatura, não quando."""
    out = defaultdict(lambda: defaultdict(set))
    for n in legislaturas:
        cam = CACHE_E / f"deputados_leg{n}.json"
        d = coleta.ler_cache(cam)
        if not d or (not so_montar and velho(d.get("baixadoEm"), VALIDADE_DEP)):
            if so_montar:
                continue
            itens, url, params = [], f"{coleta.API}/deputados", dict(idLegislatura=n, itens=100, ordem="ASC", ordenarPor="nome")
            while url:
                try:
                    r = obras.pedir(url, params)
                except (RuntimeError, ValueError) as e:
                    raise DownloadIncompleto(f"Câmara, deputados da legislatura {n}: {e}") from e
                if not isinstance(r, dict) or not isinstance(r.get("dados"), list):
                    raise DownloadIncompleto(f"Câmara, deputados da legislatura {n}: resposta sem 'dados'")   # nunca grava lista vazia
                itens += r["dados"]
                url = next((ln["href"] for ln in r.get("links") or [] if ln.get("rel") == "next"), None)
                params = None
            if not itens:
                raise DownloadIncompleto(f"Câmara, deputados da legislatura {n}: lista vazia")
            d = {"baixadoEm": agora(), "itens": [[x["id"], x["nome"]] for x in itens]}
            coleta.gravar_cache(cam, d)
            BAIXADO.append({"fonte": f"Câmara, deputados da legislatura {n}", "de": "rede", "itens": len(itens)})
        for i, nome in d["itens"]:
            if i not in ids_atuais:
                out[coleta.nome_chave(nome)][n].add(i)
    return out


# ---------------------------------------------------------------- regras

def pares_das_obras(empenhos, sp):
    """{(obra, código da emenda): [empenhos]} só com o código de 12 dígitos; as contagens de cada formato do campo; as obras com
    empenho; e quantos registros IDÊNTICOS a fonte repete num par (contam uma vez: somados, dobrariam o empenhado; medido em
    24/10/2026: 275 repetidos no país, nenhum num par de obra de SP com emenda)."""
    cont, com_empenho, pares, vistos, repetidos = Counter(), set(), defaultdict(list), set(), 0
    for e in empenhos:
        oid = e.get("id_projeto_investimento")
        if oid not in sp:
            continue
        com_empenho.add(oid)
        cat, cod = classifica_codigo(e.get("codigo_autor_emenda"))
        cont[cat] += 1
        if cod:
            chave = json.dumps(e, sort_keys=True, ensure_ascii=False)
            if chave in vistos:
                repetidos += 1
                continue
            vistos.add(chave)
            pares[(oid, cod)].append(e)
    return pares, cont, com_empenho, repetidos


def carregar_supressoes(caminho=SUPRESSOES):
    """{id: motivo}. Arquivo malformado ou id fora do formato "obra:código" derruba a rodada (uma supressão que não pega em silêncio
    publicaria o que se queria tirar)."""
    if not caminho.exists():
        return {}
    try:
        d = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{caminho.name}: JSON inválido ({e})")
    out = {}
    for x in d.get("suprimir", []):
        i, motivo = (x, None) if isinstance(x, str) else (x.get("id"), x.get("motivo"))
        if not isinstance(i, str) or not re.fullmatch(r"[^:\s]+:\d{12}", i):
            raise ValueError(f"{caminho.name}: id fora do formato 'obra:código da emenda': {x!r}")
        out[i] = motivo or "revisão manual"
    return out


def carregar_aliases(caminho=ALIASES):
    """{código do autor na CGU: {"deputado": id, "grafias": {nomes normalizados}}}. Arquivo malformado, código repetido ou entrada sem
    duas grafias derruba a rodada (um alias que não pega em silêncio deixaria o vínculo retido sem ninguém saber por quê)."""
    if not caminho.exists():
        return {}
    try:
        d = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{caminho.name}: JSON inválido ({e})")
    out = {}
    for x in d.get("aliases", []):
        c, i, g = (x.get("codigoAutor"), x.get("deputado"), x.get("grafias")) if isinstance(x, dict) else (None, None, None)
        if not (isinstance(c, str) and re.fullmatch(r"\d+", c) and isinstance(i, int) and not isinstance(i, bool)
                and isinstance(g, list) and len(g) >= 2 and all(isinstance(n, str) and n.strip() for n in g)):
            raise ValueError(f"{caminho.name}: entrada fora do formato (codigoAutor, deputado e 2 grafias ou mais): {x!r}")
        if c in out:
            raise ValueError(f"{caminho.name}: código de autor repetido: {c}")
        out[c] = {"deputado": i, "grafias": {coleta.nome_chave(n) for n in g}}
    return out


def nomes_na_camara(d):
    """Os nomes que a própria Câmara registra para o deputado: o parlamentar e o eleitoral de hoje e os de cada período do histórico
    de exercício (cache/historico/{id}.json, gravado por coleta.py)."""
    nomes = {coleta.nome_chave(n) for n in (d.get("nome"), d.get("nomeEleitoral")) if n}
    h = coleta.ler_cache(coleta.CACHE / "historico" / f"{d['id']}.json")
    for x in (h.get("dados") or [] if isinstance(h, dict) else h or []):
        nomes |= {coleta.nome_chave(n) for n in (x.get("nome"), x.get("nomeEleitoral")) if n}
    return nomes


def conferir_aliases(aliases, cg, autor_dep, deps, nomes_de=nomes_na_camara):
    """Os aliases que valem nesta rodada ({código: id}) e os avisos dos que não valem. O alias só resolve o filtro (a): quem liga o
    código ao deputado continua sendo a regra do site (obras.autores_deputados), e ela tem de dar o mesmo id (id diferente derruba a
    rodada). Não vale, e o vínculo fica retido como antes: deputado fora dos de hoje; código que a regra do site não liga; grafia na
    CGU que a tabela não lista; grafia que, na CGU, também é de outro código; grafia que a Câmara não registra para o deputado."""
    validos, avisos = {}, []
    for c, a in sorted(aliases.items()):
        i, motivo = a["deputado"], None
        if autor_dep.get(c) is not None and autor_dep[c] != i:
            raise ValueError(f"alias {c}: a tabela diz deputado {i}, a regra do site liga o código ao {autor_dep[c]}")
        na_cgu = set(cg["autorNomes"].get(c, []))
        outros = sorted({o for n in a["grafias"] for o in cg["nomeAutores"].get(n, []) if o != c})
        if i not in deps:
            motivo = f"o deputado {i} não está entre os de hoje"
        elif autor_dep.get(c) is None:
            motivo = "a regra do site não liga este código a deputado"
        elif not na_cgu <= a["grafias"]:
            motivo = f"a CGU grava grafia que a tabela não lista: {', '.join(sorted(na_cgu - a['grafias']))}"
        elif outros:
            motivo = f"grafia da tabela também é de outro código na CGU: {', '.join(outros)}"
        elif a["grafias"] - nomes_de(deps[i]):
            motivo = f"a Câmara não registra para o deputado {i}: {', '.join(sorted(a['grafias'] - nomes_de(deps[i])))}"
        if motivo:
            avisos.append(f"alias {c} não aplicado: {motivo}")
        else:
            validos[c] = i
    return validos, avisos


def avaliar(pares, cg, autor_dep, deps, supressoes, senadores=None, exdeps=None, leg_do_ano=None, aliases=None):
    """Aplica as regras a cada par (obra, emenda). Devolve (vínculos, estatística). `senadores` ({nome: [(id, mandatos)]}),
    `exdeps` (de ex_deputados) e `leg_do_ano` ({ano: [legislaturas]}) só servem à contagem de quem ficaria de fora desta fase.
    `aliases` = os que valem nesta rodada (conferir_aliases): {código do autor: id do deputado}."""
    aliases = aliases or {}
    emendas, autor_nomes, nome_autores = cg["emendas"], cg["autorNomes"], cg["nomeAutores"]
    conflitos = set(cg.get("conflitos") or [])
    vinc, est = [], defaultdict(Counter)
    for (oid, cod), emps in sorted(pares.items()):
        em = emendas.get(cod)
        if not em:
            est["pares"]["código fora da CGU"] += 1
            continue
        ca, nome, tipo_bruto, ano, ufs = em[0], em[1], em[2], em[3], em[4]
        tipo = tipo_emenda(tipo_bruto)
        empenhado = round(sum(numero(e.get("valor_empenho")) for e in emps), 2)
        # o "pago" do empenho no ObrasGov só é medido (não é publicado: zero em quase todos)
        pago_og = sum(numero(e.get("pago")) + numero(e.get("rppago")) for e in emps)
        est["valores"]["pago do empenho no ObrasGov igual a zero"] += pago_og <= 0
        est["valores"]["pago do empenho no ObrasGov maior que zero"] += pago_og > 0
        v = {"id": f"{oid}:{cod}", "obra": oid, "codigo": cod, "tipo": tipo, "ano": ano, "empenhado": empenhado,
             "emendaEmpenhado": em[5] if len(em) > 6 else None, "emendaPago": em[6] if len(em) > 6 else None, "_pagoOgZero": pago_og <= 0,
             "empenhos": sorted([[e.get("ug_emitente"), e.get("nr_empenho")] for e in emps], key=lambda x: str(x[1])),
             "deputado": None, "retencoes": [], "publicavel": False, "semAutor": None, "alias": None}
        est["pares"]["na CGU"] += 1
        est["pares_por_tipo"][tipo] += 1
        if tipo != "individual":
            v["semAutor"] = "tipo"
            vinc.append(v)
            continue
        # a bateria (cada filtro é medido sozinho; o motivo mostrado é o primeiro, na ordem de ORDEM)
        r = []
        if any((e.get("nr_empenho") or "")[:4] != ano for e in emps):
            r.append("ano_diferente")
        dep = autor_dep.get(ca) if cod not in conflitos else None
        if dep is not None and not em_exercicio(deps[dep].get("mandatos"), ano):
            r.append("fora_do_mandato")
        ambiguo = len(autor_nomes.get(ca, [])) != 1 or len(nome_autores.get(coleta.nome_chave(nome), [])) != 1
        if cod in conflitos or (ambiguo and ca not in aliases):
            r.append("codigo_nome_ambiguo")
        elif ambiguo:
            v["alias"] = ca              # o mesmo código com duas grafias do mesmo deputado, pela tabela de aliases
            est["alias_pares"][ca] += 1
        cats = {uf_categoria(u) for u in ufs} or {"sem informação"}
        if "outra UF" in cats:
            r.append("localidade_outra_uf")
        elif not cats <= {"SP", "múltiplo"}:
            r.append("localidade_sem_informacao")
        if empenhado <= 0:
            r.append("sem_valor_empenhado")
        if v["id"] in supressoes:
            r.append("suprimida_manualmente")
        v["retencoes"] = r
        for x in r:
            est["filtro_isolado"][x] += 1
        if dep is None:
            v["semAutor"] = "fora_desta_fase"
            est["individuais"]["sem deputado do site"] += 1
            if not (set(r) - {"suprimida_manualmente"}):
                quem = fora_da_fase(nome, ano, senadores or {}, exdeps, leg_do_ano or {})
                est["entrariam"][quem] += 1
                est["entrariam_obras_" + quem][oid] += 1
                est["entrariam_pessoas_" + quem][coleta.nome_chave(nome)] += 1
            vinc.append(v)
            continue
        est["individuais"]["com deputado do site"] += 1
        v["deputado"] = dep
        if r:
            v["semAutor"] = sorted(r, key=ORDEM.index)[0]
            est["funil"][v["semAutor"]] += 1
        else:
            v["publicavel"] = True
            est["funil"]["publicável"] += 1
            est["pago_zero_publicaveis"]["n"] += v["_pagoOgZero"]
        if v["alias"]:
            est["alias_resultado"][(v["alias"], v["semAutor"] or "publicável")] += 1
        vinc.append(v)
    return vinc, est


def fora_da_fase(nome, ano, senadores, exdeps, leg_do_ano):
    """Com a mesma regra (nome exato de UMA pessoa em exercício no ano), quem é o autor fora desta fase: senador, ex-deputado ou
    nenhum dos dois. Só para a contagem; nada disto é publicado."""
    k = coleta.nome_chave(nome)
    s = [i for i, m in senadores.get(k, []) if em_exercicio(m, ano)]
    if len(s) == 1:
        return "senador"
    try:
        legs = set(leg_do_ano.get(int(ano), [])) | set(leg_do_ano.get(int(ano) - 1, []))
    except (TypeError, ValueError):
        legs = set()
    if exdeps is None:
        return "não contado (lista da Câmara indisponível)"
    ids = set().union(*(exdeps.get(k, {}).get(n, set()) for n in legs)) if legs else set()
    if len(ids) == 1:
        return "ex-deputado"
    return "nenhum (sem pessoa única pela regra)"


# ---------------------------------------------------------------- saída

def montar_saida(vinc, sp, geo, deps, com_contrato, meta):
    obras_out, por_dep = {}, defaultdict(list)
    for v in vinc:
        o = sp[v["obra"]]
        b = obras_out.setdefault(v["obra"], {"nome": (o.get("desc_nome") or "").strip()[:200], "municipios": geo.get(v["obra"], []),
                                             "situacao": o.get("situacao"), "sistema": o.get("sistema_resp"),
                                             "noSite": v["obra"] in com_contrato, "emendas": []})
        d = deps.get(v["deputado"]) if v["publicavel"] else None
        b["emendas"].append({"id": v["id"], "codigo": v["codigo"], "tipo": v["tipo"], "ano": v["ano"], "empenhado": v["empenhado"],
                             "emendaEmpenhado": v["emendaEmpenhado"], "emendaPago": v["emendaPago"], "empenhos": v["empenhos"],
                             "autor": {"id": d["id"], "nome": d["nome"], "partido": d.get("partido"), "uf": d.get("uf")} if d else None,
                             "semAutor": None if d else v["semAutor"]})
        if d:
            por_dep[str(d["id"])].append({"id": v["id"], "obra": v["obra"], "nome": b["nome"], "municipios": b["municipios"],
                                          "noSite": b["noSite"], "ano": v["ano"], "codigo": v["codigo"], "empenhado": v["empenhado"],
                                          "emendaEmpenhado": v["emendaEmpenhado"], "emendaPago": v["emendaPago"], "empenhos": v["empenhos"],
                                          "alias": v["alias"]})
    for lst in por_dep.values():            # ordem do perfil: do ano mais recente ao mais antigo, depois pelo nome; nunca pelo valor
        lst.sort(key=lambda x: (-int(x["ano"] or 0), x["nome"]))
    return {**meta, "obras": dict(sorted(obras_out.items())), "deputados": dict(sorted(por_dep.items(), key=lambda kv: int(kv[0])))}


def publicacao(saida, cg):
    """A saída dividida em um arquivo por deputado e um índice leve. O arquivo do deputado traz só o que o perfil mostra e nenhuma data
    de geração (a pasta é regravada a cada rodada e o git só vê o arquivo cujo conteúdo mudou); o índice traz as contagens de vínculos
    por deputado e as emendas das obras que estão em obras.html, a outra página que mostra esta camada."""
    fixos = {"_versao": VERSAO, "_uf": UF, "_texto": TEXTO, "_notaValores": NOTA_VALORES, "_regras": REGRAS}
    de = str((saida.get("_fontes") or {}).get("empenhosBaixadosEm") or "")[:10] or None
    arquivos = {}
    for i, itens in saida["deputados"].items():
        alias = sorted({x["alias"] for x in itens if x.get("alias")})
        arquivos[i] = {**fixos, "_empenhosDe": de, "deputado": int(i),
                       **({"grafiasCgu": [{"codigo": c, "grafias": cg["autorNomes"].get(c, [])} for c in alias]} if alias else {}),
                       "itens": [{k: v for k, v in x.items() if k != "alias"} for x in itens]}
    indice = {**fixos, "_geradoEm": saida.get("_geradoEm"), "_fontes": saida.get("_fontes"), "_motivos": MOTIVOS, "_tipos": TIPOS,
              "deputados": {i: len(x) for i, x in saida["deputados"].items()},
              "obrasDoSite": {o: b["emendas"] for o, b in saida["obras"].items() if b["noSite"]}}
    return indice, arquivos


def contagens(saida):
    return {"pessoas": len(saida.get("deputados") or {}),
            "vinculos": sum(1 for b in (saida.get("obras") or {}).values() for e in b["emendas"] if e.get("autor"))}


def checagens(saida, vinc):
    erros = []
    for v in vinc:
        if v["publicavel"] and (v["retencoes"] or v["tipo"] != "individual" or not v["deputado"]):
            erros.append(f"{v['id']}: publicável sem cumprir a regra ({v['retencoes']}, {v['tipo']})")
    for oid, b in saida["obras"].items():
        for e in b["emendas"]:
            if e["autor"] and e["tipo"] != "individual":
                erros.append(f"{e['id']}: nome de pessoa em emenda de {e['tipo']}")
            if not e["autor"] and not e["semAutor"]:
                erros.append(f"{e['id']}: sem autor e sem motivo")
    n_dep = sum(len(x) for x in saida["deputados"].values())
    n_aut = contagens(saida)["vinculos"]
    if n_dep != n_aut:
        erros.append(f"o perfil tem {n_dep} vínculos e as obras {n_aut}")
    if re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", json.dumps(saida, ensure_ascii=False)):
        erros.append("padrão de CPF na saída")
    return erros


def proibidos(obj, onde):
    """Chave proibida em qualquer nível; palavra proibida só nos textos fixos (as chaves que começam com "_"). Ver CHAVES_PROIBIDAS."""
    erros = []

    def chaves(x, caminho):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.lstrip("_") in CHAVES_PROIBIDAS:
                    erros.append(f"{onde}: chave proibida {caminho}{k}")
                chaves(v, f"{caminho}{k}.")
        elif isinstance(x, list):
            for v in x:
                chaves(v, caminho)

    def textos(x):
        if isinstance(x, str):
            return [x]
        return [t for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else []) for t in textos(v)]
    chaves(obj, "")
    for k, v in obj.items():
        for t in textos(v) if k.startswith("_") else []:
            m = PALAVRAS_PROIBIDAS.search(t)
            if m:
                erros.append(f"{onde}: {k} diz {m.group(0)!r}")
    return erros


def checagens_publicacao(indice, arquivos, saida):
    """O que vai para site/dados/emendas/: os arquivos dos deputados fecham com o índice e com os vínculos publicáveis; nenhum traz
    situação da obra, aditivo, estouro ou valor somado por deputado; nenhum padrão de CPF."""
    erros = []
    n_arq = sum(len(a["itens"]) for a in arquivos.values())
    if not n_arq == sum(indice["deputados"].values()) == contagens(saida)["vinculos"]:
        erros.append(f"vínculos: {n_arq} nos arquivos, {sum(indice['deputados'].values())} no índice, {contagens(saida)['vinculos']} nas obras")
    if set(arquivos) != set(indice["deputados"]):
        erros.append("os arquivos de deputado não são os do índice")
    for i, a in arquivos.items():
        if a["deputado"] != int(i) or len(a["itens"]) != indice["deputados"].get(i):
            erros.append(f"{i}.json: deputado ou contagem diferente do índice")
        somas = [k for k, v in a.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "deputado"]
        if somas:
            erros.append(f"{i}.json: número no nível do deputado ({', '.join(somas)}): nada é somado por pessoa")
        erros += proibidos(a, f"{i}.json")
    erros += proibidos(indice, INDICE)
    if re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", json.dumps([indice, arquivos], ensure_ascii=False)):
        erros.append("padrão de CPF na publicação")
    return erros


def validar_esquemas(indice, arquivos):
    """A publicação contra schemas/emendas_indice e emendas_deputado ANTES de trocar a pasta: um arquivo fora do contrato não chega ao
    site (e não derruba o passo de validação do workflow, que roda depois e não é opcional)."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema não instalado: a publicação não pode ser conferida contra schemas/ (pip install -r requirements.txt)"]
    esq = lambda n: Draft202012Validator(json.loads((coleta.BASE / "schemas" / n).read_text(encoding="utf-8")))
    vi, vd = esq("emendas_indice.schema.json"), esq("emendas_deputado.schema.json")
    erros = [f"{INDICE}: {'.'.join(map(str, e.absolute_path)) or '<raiz>'}: {e.message}" for e in vi.iter_errors(indice)]
    for i, a in arquivos.items():
        erros += [f"{i}.json: {'.'.join(map(str, e.absolute_path)) or '<raiz>'}: {e.message}" for e in vd.iter_errors(a)]
    return erros[:20]


def contagens_indice(ind):
    return {"pessoas": len(ind.get("deputados") or {}), "vinculos": sum((ind.get("deputados") or {}).values())}


def queda(indice, publicado):
    """Queda grande contra o índice publicado (da mesma versão) derruba a rodada: o mais provável é falha de fonte, não emenda que sumiu."""
    if not publicado or publicado.get("_versao") != VERSAO:
        return []
    a, n = contagens_indice(publicado), contagens_indice(indice)
    return [f"{k}: {a[k]} no índice publicado, {n[k]} agora (queda maior que {QUEDA_MAXIMA:.0%})"
            for k in a if a[k] >= 20 and n[k] < a[k] * (1 - QUEDA_MAXIMA)]


def escrever(caminho, obj, longo):
    """JSON compacto com `longo` (a lista ou o dicionário grande) uma entrada por linha: o diff do git mostra só o que mudou."""
    cab = json.dumps({k: v for k, v in obj.items() if k != longo}, ensure_ascii=False, separators=(",", ":"))
    x, sep = obj[longo], (lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":")))
    if isinstance(x, dict):
        corpo, a, b = ",\n".join(f"{json.dumps(k)}:{sep(v)}" for k, v in x.items()), "{", "}"
    else:
        corpo, a, b = ",\n".join(sep(v) for v in x), "[", "]"
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        f.write(cab[:-1] + ("," if len(cab) > 2 else "") + f"\n{json.dumps(longo)}:{a}\n{corpo}\n{b}}}\n")


def gravar_pasta(destino, indice, arquivos):
    """Monta a pasta inteira ao lado ({destino}.novo), relê cada arquivo e só então troca. O que estava publicado só sai depois que o
    novo está completo; arquivo de deputado que não tem mais vínculo some junto."""
    novo, antigo = destino.with_name(destino.name + ".novo"), destino.with_name(destino.name + ".velho")
    if antigo.exists() and not destino.exists():
        os.replace(antigo, destino)                  # uma rodada morreu no meio da troca: volta o que estava publicado
    shutil.rmtree(novo, ignore_errors=True)
    novo.mkdir(parents=True)
    try:
        escrever(novo / INDICE, indice, "obrasDoSite")
        for i, a in arquivos.items():
            escrever(novo / f"{i}.json", a, "itens")
        for p in novo.iterdir():
            json.loads(p.read_text(encoding="utf-8"))
    except BaseException:
        shutil.rmtree(novo, ignore_errors=True)
        raise
    shutil.rmtree(antigo, ignore_errors=True)
    if destino.exists():
        os.replace(destino, antigo)
    try:
        os.replace(novo, destino)
    except OSError:
        if antigo.exists():
            os.replace(antigo, destino)          # a troca não completou: volta o que estava publicado
        shutil.rmtree(novo, ignore_errors=True)
        raise
    shutil.rmtree(antigo, ignore_errors=True)


def gravar(caminho, obj):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    cab = {k: v for k, v in obj.items() if k not in ("obras", "deputados")}
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cab, ensure_ascii=False)[:-1] + ',\n"obras":{\n')
        f.write(",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in obj["obras"].items()))
        f.write('\n},\n"deputados":{\n')
        f.write(",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in obj["deputados"].items()))
        f.write("\n}}\n")
    json.loads(tmp.read_text(encoding="utf-8"))
    os.replace(tmp, caminho)


def relatorio(est, cont_cod, n_sp, n_emp, com_emp, saida, erros, args, extra):
    p = lambda a, b: f"{100 * a / b:.1f}%" if b else "—"
    pub = [e for b in saida["obras"].values() for e in b["emendas"] if e["autor"]]
    obras_pub = {e["id"].split(":")[0] for e in pub}
    L = [f"EMENDAS DE DEPUTADOS NAS OBRAS — {n_sp} obras de {UF} no ObrasGov · {'PUBLICAÇÃO LIGADA' if args.publicar else 'dry-run: publicação desligada (candidato em cache/)'}",
         "", "FUNIL",
         f"   empenhos de obras de {UF}: {n_emp} · obras com empenho: {len(com_emp)} ({p(len(com_emp), n_sp)})",
         "   código no empenho: " + " · ".join(f"{k} {v}" for k, v in cont_cod.most_common()),
         f"   pares obra × emenda com código de 12 dígitos: {sum(est['pares'].values())} · na CGU {est['pares']['na CGU']} · fora da CGU {est['pares']['código fora da CGU']}",
         f"   obras com emenda (código exato na CGU): {len(saida['obras'])}",
         "   pares por tipo: " + " · ".join(f"{k} {v}" for k, v in est["pares_por_tipo"].most_common()),
         "   individuais: " + " · ".join(f"{k} {v}" for k, v in est["individuais"].most_common()),
         "   retidos, pelo PRIMEIRO filtro que falha (só os de deputado do site): " + (" · ".join(f"{k} {v}" for k, v in est["funil"].most_common() if k != "publicável") or "nenhum"),
         "   cada filtro medido sozinho (todos os individuais, com ou sem deputado): " + (" · ".join(f"{k} {v}" for k, v in est["filtro_isolado"].most_common()) or "nenhum"),
         f"   PUBLICÁVEIS: {len(pub)} vínculos · {len(obras_pub)} obras · {len(saida['deputados'])} deputados "
         f"(das obras, {sum(1 for o in obras_pub if saida['obras'][o]['noSite'])} têm contrato federal e estão em obras.html)",
         "   pago do empenho no ObrasGov (só medido, não publicado), em todos os pares na CGU: " + " · ".join(f"{k} {v}" for k, v in est["valores"].items()),
         f"   pago do empenho no ObrasGov igual a zero entre os PUBLICÁVEIS: {est['pago_zero_publicaveis']['n']} de {len(pub)}",
         "", "FORA DESTA FASE (mesma regra, só contagem; nada é publicado)"]
    base = ("senador", "ex-deputado", "nenhum (sem pessoa única pela regra)")
    for quem in base + tuple(k for k in est["entrariam"] if k not in base):
        L.append(f"   {quem}: {est['entrariam'][quem]} vínculos · {len(est['entrariam_obras_' + quem])} obras · "
                 f"{len(est['entrariam_pessoas_' + quem])} pessoas")
    L.append("   (ex-deputado é aproximado: a lista de cada legislatura diz quem exerceu nela, não quando)")
    L += ["", f"ALIASES ({ALIASES.name}): {len(extra['aliases'])} na tabela, {len(extra['validos'])} aplicados nesta rodada"]
    for c, a in sorted(extra["aliases"].items()):
        d = extra["deps"].get(a["deputado"]) or {}
        res = Counter({k[1]: n for k, n in est["alias_resultado"].items() if k[0] == c})
        L.append(f"   código {c} → deputado {a['deputado']} ({d.get('nome', '?')}, {d.get('partido', '?')}-{d.get('uf', '?')}) · grafias "
                 f"{' / '.join(sorted(a['grafias']))} · {'aplicado' if c in extra['validos'] else 'NÃO aplicado'} · pares resolvidos "
                 f"{est['alias_pares'][c]}: " + (" · ".join(f"{k} {n}" for k, n in res.most_common()) or "nenhum"))
    for x in extra["avisos_alias"]:
        L.append(f"   aviso: {x}")
    L += ["", f"PUBLICAÇÃO ({'site/dados/emendas/' if args.publicar else 'candidato em cache/emendas/candidato/'}): "
          f"{extra['n_arquivos']} arquivos de deputado ({extra['kb_total']} kB; o maior {extra['kb_maior']} kB) + {INDICE} ({extra['kb_indice']} kB, "
          f"{extra['n_obras_site']} obras de obras.html com emenda)",
          f"   endereço do empenho no Portal da Transparência: o empenho do ObrasGov tem UG em {extra['com_ug']} e número em {extra['com_nr']} "
          f"dos {extra['n_emp_pub']} empenhos dos vínculos publicáveis; campo de gestão: "
          + (", ".join(extra["campos_gestao"]) if extra["campos_gestao"] else "nenhum no ObrasGov (fonte só em texto)")]
    L.append(f"   registros idênticos repetidos pela fonte num par obra × emenda (contados uma vez): {extra['repetidos']}")
    L += ["", "BAIXADO NESTA RODADA (o resto veio do cache)"]
    for b in BAIXADO:
        if b["de"] == "rede":
            mais = (f" · {b['paginas']} de {b['de_total']} páginas pedidas" if "paginas" in b else "") + (f" · {b['mb']} MB" if "mb" in b else "")
            L.append(f"   {b['fonte']}: baixado · {b['itens']} itens{mais}" + (f" · {b['segundos']} s" if "segundos" in b else ""))
        else:
            L.append(f"   {b['fonte']}: {b['de']} ({b['itens']} itens, baixado em {str(b.get('baixadoEm') or '?')[:16]})")
    L += ["", "CHECAGENS: " + ("ok" if not erros else f"{len(erros)} erro(s): " + "; ".join(erros[:8]))]
    return "\n".join(L)


# ---------------------------------------------------------------- testes

def testar():
    ok = 0

    def ve(c, m):
        nonlocal ok
        if not c:
            raise AssertionError(m)
        ok += 1
    ve(classifica_codigo("202428130002") == ("12 dígitos", "202428130002"), "12 dígitos")
    ve(classifica_codigo("202428130002.0")[1] == "202428130002", "'.0' no fim")
    ve(classifica_codigo("71250015")[1] is None, "8 dígitos não se completam")
    ve(classifica_codigo("0")[0] == "zero" and classifica_codigo(None)[0] == "sem código" and classifica_codigo("@")[0] == "outro formato", "outros formatos")
    ve(uf_categoria("SÃO PAULO") == "SP" and uf_categoria("Múltiplo") == "múltiplo" and uf_categoria("Sem informação") == "sem informação"
       and uf_categoria("RIO GRANDE DO SUL") == "outra UF" and uf_categoria("") == "sem informação", "UF da CGU por extenso")
    ve(em_exercicio({"57": [{"de": "2023-02-01", "ate": None}]}, "2024") and em_exercicio({"56": [{"de": "2019-02-01", "ate": "2023-01-31"}]}, "2024")
       and not em_exercicio({"55": [{"de": "2015-02-01", "ate": "2019-01-31"}]}, "2024"), "exercício no ano ou no anterior")
    # um arquivo da CGU de mentira e as obras
    cg = {"emendas": {"202400010001": ["0001", "FULANO DE TAL", "Emenda Individual - Transferências com Finalidade Definida", "2024", ["SÃO PAULO"], 5000.0, 3000.0],
                      "202400010002": ["0001", "FULANO DE TAL", "Emenda Individual", "2024", ["RIO GRANDE DO SUL"], 1.0, 0.0],
                      "202400010003": ["0001", "FULANO DE TAL", "Emenda Individual", "2024", ["Sem informação"], 1.0, 0.0],
                      "202400010004": ["0001", "FULANO DE TAL", "Emenda Individual", "2024", ["Múltiplo"], 1.0, 0.0],
                      "202400020001": ["0002", "CICLANO", "Emenda Individual", "2024", ["SÃO PAULO"], 5000.0, 3000.0],
                      "202400030001": ["0003", "COMISSAO DE SAUDE", "Emenda de Comissão", "2024", ["SÃO PAULO"], 5000.0, 3000.0],
                      "202400040001": ["0004", "BELTRANO SENADOR", "Emenda Individual", "2024", ["SÃO PAULO"], 5000.0, 3000.0],
                      "202400050001": ["0005", "EX DEPUTADO", "Emenda Individual", "2024", ["SÃO PAULO"], 1.0, 0.0]},
          "autorNomes": {"0001": ["FULANO DE TAL"], "0002": ["CICLANO"], "0003": ["COMISSAO DE SAUDE"], "0004": ["BELTRANO SENADOR"],
                         "0005": ["EX DEPUTADO"]},
          "nomeAutores": {"FULANO DE TAL": ["0001"], "CICLANO": ["0002", "0099"], "COMISSAO DE SAUDE": ["0003"],
                          "BELTRANO SENADOR": ["0004"], "EX DEPUTADO": ["0005"]}, "conflitos": []}
    deps = {10: {"id": 10, "nome": "Fulano de Tal", "partido": "XX", "uf": "SP", "mandatos": {"57": [{"de": "2023-02-01", "ate": None}]}},
            20: {"id": 20, "nome": "Ciclano", "partido": "YY", "uf": "SP", "mandatos": {"57": [{"de": "2023-02-01", "ate": None}]}}}
    autor_dep = {"0001": 10, "0002": 20}
    emp = lambda o, c, valor=1000.0, nr="2024NE000001", pago=0.0: {"id_projeto_investimento": o, "codigo_autor_emenda": c, "valor_empenho": valor,
                                                                   "pago": pago, "rppago": 0, "ug_emitente": 100001, "nr_empenho": nr}
    sp = {f"{i}.35-01": {"desc_nome": f"Obra {i}", "situacao": "Em execução", "sistema_resp": "Transferegov.br"} for i in range(1, 13)}
    empenhos = [emp("1.35-01", "202400010001", 500, pago=200), emp("1.35-01", "202400010001", 500, nr="2024NE000002"),   # publicável (2 empenhos)
                emp("2.35-01", "202400010002"),                       # outra UF
                emp("3.35-01", "202400010003"),                       # sem informação
                emp("4.35-01", "202400010004", 0),                    # múltiplo, mas valor zero
                emp("5.35-01", "202400010001", nr="2023NE000009"),    # ano diferente
                emp("6.35-01", "202400020001"),                       # nome com dois códigos
                emp("7.35-01", "202400030001"),                       # comissão: tipo, sem nome
                emp("8.35-01", "202400040001"),                       # senador: fora desta fase
                emp("9.35-01", "202400050001"),                       # ex-deputado: fora desta fase
                emp("10.35-01", "202400010004"),                      # múltiplo com valor: publicável, mas suprimido
                emp("11.35-01", "71250015"), emp("11.35-01", "0"), emp("X", "202400010001"),   # 8 dígitos, zero, obra de fora de SP
                emp("12.35-01", "202400010004", 100, pago=900)]      # publicável, mas o pago passa do empenhado
    pares, cont, com, _ = pares_das_obras(empenhos, sp)
    ve(("1.35-01", "202400010001") in pares and len(pares[("1.35-01", "202400010001")]) == 2 and not any(k[0] == "X" for k in pares), "pares por obra × emenda, só SP")
    ve(cont["8 dígitos (sem o ano: não se completa)"] == 1 and cont["zero"] == 1, "contagem dos formatos")
    senadores = {"BELTRANO SENADOR": [(99, {"57": [{"de": "2023-02-01", "ate": None}]})]}
    exdeps = {"EX DEPUTADO": {56: {77}}}
    vinc, est = avaliar(pares, cg, autor_dep, deps, {"10.35-01:202400010004": "teste"}, senadores, exdeps, {2024: [57], 2023: [56, 57]})
    por = {v["obra"]: v for v in vinc}
    ve(por["1.35-01"]["publicavel"] and por["1.35-01"]["empenhado"] == 1000 and len(por["1.35-01"]["empenhos"]) == 2, "publicável soma os empenhos da emenda na obra")
    ve(por["1.35-01"]["emendaEmpenhado"] == 5000 and por["1.35-01"]["emendaPago"] == 3000 and "pago" not in por["1.35-01"], "valores da emenda inteira vêm da CGU; o pago do ObrasGov não sai")
    ve(por["2.35-01"]["semAutor"] == "localidade_outra_uf" and not por["2.35-01"]["publicavel"], "(b) outra UF fica pendente")
    ve(por["3.35-01"]["semAutor"] == "localidade_sem_informacao", "(b) sem informação de UF fica de fora")
    ve(por["4.35-01"]["semAutor"] == "sem_valor_empenhado", "(c) valor empenhado zero")
    ve(por["5.35-01"]["semAutor"] == "ano_diferente", "ano da emenda ≠ ano do empenho")
    ve(por["6.35-01"]["semAutor"] == "codigo_nome_ambiguo", "(a) um nome com dois códigos")
    ve(por["7.35-01"]["semAutor"] == "tipo" and por["7.35-01"]["deputado"] is None, "comissão: só o tipo, sem pessoa")
    ve(por["8.35-01"]["semAutor"] == "fora_desta_fase" and est["entrariam"]["senador"] == 1, "senador conta, não entra")
    ve(por["9.35-01"]["semAutor"] == "fora_desta_fase" and est["entrariam"]["ex-deputado"] == 1, "ex-deputado conta, não entra")
    ve(por["10.35-01"]["semAutor"] == "suprimida_manualmente", "supressão retira o vínculo")
    ve(est["funil"]["publicável"] == 2, "dois publicáveis")
    ve(por["12.35-01"]["publicavel"] and est["valores"]["pago do empenho no ObrasGov maior que zero"] == 2, "o pago do ObrasGov só é medido")
    meta = {"_versao": VERSAO}
    saida = montar_saida(vinc, sp, {"1.35-01": ["Campinas"]}, deps, {"7.35-01"}, meta)
    ve(list(saida["deputados"]) == ["10"] and [x["obra"] for x in saida["deputados"]["10"]] == ["1.35-01", "12.35-01"]
       and saida["deputados"]["10"][0]["municipios"] == ["Campinas"], "perfil só com os publicáveis")
    ve(saida["obras"]["7.35-01"]["noSite"] and saida["obras"]["7.35-01"]["emendas"][0]["autor"] is None, "obra do site com emenda de comissão sem nome")
    ve(not checagens(saida, vinc), "checagens passam")
    ruim = json.loads(json.dumps(saida))
    ruim["obras"]["7.35-01"]["emendas"][0]["autor"] = {"id": 1, "nome": "X"}
    ve(any("emenda de comissão" in e for e in checagens(ruim, vinc)), "checagem: nome em emenda de comissão derruba")
    # ordem do perfil: ano e nome, nunca valor
    v2 = [dict(por["1.35-01"]), dict(por["1.35-01"], obra="2.35-01", id="2.35-01:202400010001", ano="2023", empenhado=9e9),
          dict(por["1.35-01"], obra="3.35-01", id="3.35-01:202400010001", empenhado=1.0)]
    s2 = montar_saida(v2, sp, {}, deps, set(), meta)
    ve([x["obra"] for x in s2["deputados"]["10"]] == ["1.35-01", "3.35-01", "2.35-01"], "perfil em ordem de ano e nome, não de valor")
    # supressões
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "s.json"
        f.write_text(json.dumps({"suprimir": ["1.35-01:202400010001", {"id": "2.35-01:202400010002", "motivo": "x"}]}), encoding="utf-8")
        ve(carregar_supressoes(f) == {"1.35-01:202400010001": "revisão manual", "2.35-01:202400010002": "x"}, "supressões lidas")
        f.write_text(json.dumps({"suprimir": ["1.35-01"]}), encoding="utf-8")
        try:
            carregar_supressoes(f)
            ve(False, "id mal formado deveria derrubar")
        except ValueError:
            ok += 1
        f.write_text("{", encoding="utf-8")
        try:
            carregar_supressoes(f)
            ve(False, "JSON inválido deveria derrubar")
        except ValueError:
            ok += 1
    ve(queda({"deputados": {}}, {"_versao": VERSAO, "deputados": {str(i): 1 for i in range(30)}}), "queda contra o índice publicado derruba")
    ve(not queda({"deputados": {}}, {"_versao": "outra", "deputados": {str(i): 1 for i in range(30)}}), "versão diferente não conta")

    # ---- tarefa 1: aliases (o mesmo código da CGU com duas grafias do mesmo deputado)
    cg["emendas"]["202400060001"] = ["0006", "FULANO ANTIGO", "Emenda Individual", "2024", ["SÃO PAULO"], 900.0, 100.0]
    cg["autorNomes"]["0006"] = ["FULANO ANTIGO", "FULANO NOVO"]
    cg["nomeAutores"].update({"FULANO ANTIGO": ["0006"], "FULANO NOVO": ["0006"]})
    deps[30] = {"id": 30, "nome": "Fulano Novo", "partido": "ZZ", "uf": "SP", "mandatos": {"57": [{"de": "2023-02-01", "ate": None}]}}
    autor_dep["0006"] = 30
    sp["13.35-01"] = {"desc_nome": "Estádio João Crescêncio", "situacao": "Paralisada", "sistema_resp": "Transferegov.br"}
    pares2, _, _, _ = pares_das_obras(empenhos + [emp("13.35-01", "202400060001")], sp)
    sup = {"10.35-01:202400010004": "teste"}
    sem, _ = avaliar(pares2, cg, autor_dep, deps, sup)
    com, est2 = avaliar(pares2, cg, autor_dep, deps, sup, aliases={"0006": 30})
    ve(next(v for v in sem if v["obra"] == "13.35-01")["semAutor"] == "codigo_nome_ambiguo", "sem alias: o código com duas grafias fica retido")
    v13 = next(v for v in com if v["obra"] == "13.35-01")
    ve(v13["publicavel"] and v13["alias"] == "0006" and est2["alias_pares"]["0006"] == 1
       and est2["alias_resultado"][("0006", "publicável")] == 1, "com alias: resolvido e publicável")
    ve(next(v for v in com if v["obra"] == "6.35-01")["semAutor"] == "codigo_nome_ambiguo", "com alias de outro código: o sem alias continua retido")
    nomes_cam = {30: {"FULANO ANTIGO", "FULANO NOVO"}}
    nd = lambda d: nomes_cam.get(d["id"], set())
    tabela = {"0006": {"deputado": 30, "grafias": {"FULANO ANTIGO", "FULANO NOVO"}}}
    ve(conferir_aliases(tabela, cg, autor_dep, deps, nd) == ({"0006": 30}, []), "alias conferido vale")
    ve(not conferir_aliases(tabela, cg, autor_dep, deps, lambda d: {"FULANO NOVO"})[0], "alias com grafia que a Câmara não registra não vale")
    cg3 = json.loads(json.dumps(cg))
    cg3["autorNomes"]["0006"].append("FULANO TERCEIRO")
    ve(not conferir_aliases(tabela, cg3, autor_dep, deps, nd)[0], "grafia nova na CGU, fora da tabela: o alias não vale")
    cg3 = json.loads(json.dumps(cg))
    cg3["nomeAutores"]["FULANO NOVO"] = ["0006", "0077"]
    ve(not conferir_aliases(tabela, cg3, autor_dep, deps, nd)[0], "grafia que também é de outro código: o alias não vale")
    ve(not conferir_aliases({"0006": {"deputado": 99, "grafias": tabela["0006"]["grafias"]}}, cg, {"0006": None}, deps, nd)[0],
       "deputado fora dos de hoje: o alias não vale")
    ve(not conferir_aliases(tabela, cg, {}, deps, nd)[0], "código que a regra do site não liga: o alias não vale")
    try:
        conferir_aliases({"0006": {"deputado": 10, "grafias": tabela["0006"]["grafias"]}}, cg, autor_dep, deps, nd)
        ve(False, "alias com id diferente do da regra do site deveria derrubar")
    except ValueError:
        ok += 1
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "a.json"
        f.write_text(json.dumps({"aliases": [{"codigoAutor": "0006", "deputado": 30, "grafias": ["Fulano Antigo", "FULANO NOVO"]}]}), encoding="utf-8")
        ve(carregar_aliases(f) == tabela, "aliases lidos (grafias normalizadas)")
        for ruim_ in ([{"codigoAutor": "0006", "deputado": 30, "grafias": ["SÓ UMA"]}], [{"codigoAutor": "x6", "deputado": 30, "grafias": ["A", "B"]}],
                      [{"codigoAutor": "0006", "deputado": True, "grafias": ["A", "B"]}],
                      [{"codigoAutor": "0006", "deputado": 30, "grafias": ["A", "B"]}, {"codigoAutor": "0006", "deputado": 30, "grafias": ["A", "B"]}]):
            f.write_text(json.dumps({"aliases": ruim_}), encoding="utf-8")
            try:
                carregar_aliases(f)
                ve(False, f"alias mal formado deveria derrubar: {ruim_}")
            except ValueError:
                ok += 1

    # ---- tarefa 2: um arquivo por deputado e um índice leve
    s3 = montar_saida(com, sp, {"1.35-01": ["Campinas"]}, deps, {"7.35-01"}, {**meta, "_fontes": {"empenhosBaixadosEm": "2026-10-24T10:00:00-03:00"}})
    ind, arqs = publicacao(s3, cg)
    ve(set(arqs) == {"10", "30"} and ind["deputados"] == {"10": 2, "30": 1}, "um arquivo por deputado com vínculo publicável")
    ve(list(ind["obrasDoSite"]) == ["7.35-01"] and ind["obrasDoSite"]["7.35-01"][0]["autor"] is None, "o índice só traz as obras de obras.html")
    ve(arqs["30"]["grafiasCgu"] == [{"codigo": "0006", "grafias": ["FULANO ANTIGO", "FULANO NOVO"]}] and "grafiasCgu" not in arqs["10"]
       and all("alias" not in x for a in arqs.values() for x in a["itens"]), "a grafia dupla da CGU aparece no arquivo do deputado")
    ve(arqs["10"]["_empenhosDe"] == "2026-10-24" and "_geradoEm" not in arqs["10"], "arquivo do deputado sem data de geração")
    ve(not checagens_publicacao(ind, arqs, s3), "publicação passa nas checagens (nome de obra com 'Crescêncio' não é palavra proibida)")

    # ---- tarefa 4: nenhum item mostra situação da obra, aditivo, estouro ou valor total por deputado
    ve(not any(k in CHAVES_PROIBIDAS for a in arqs.values() for x in a["itens"] for k in x)
       and not any(k in CHAVES_PROIBIDAS for lst in ind["obrasDoSite"].values() for e in lst for k in e), "itens sem chave proibida")
    for mexe, msg in ((lambda i, a: a["10"]["itens"][0].__setitem__("situacao", "Paralisada"), "situação da obra num item"),
                      (lambda i, a: a["10"].__setitem__("totalEmpenhado", 1.0), "valor somado por deputado"),
                      (lambda i, a: a["10"]["itens"][0].__setitem__("aditivos", []), "aditivo num item"),
                      (lambda i, a: i["obrasDoSite"]["7.35-01"][0].__setitem__("crescimento", 0.3), "crescimento do contrato no índice"),
                      (lambda i, a: a["30"].__setitem__("_texto", "O contrato teve estouro."), "texto fixo com 'estouro'")):
        i2, a2 = json.loads(json.dumps(ind)), json.loads(json.dumps(arqs))
        mexe(i2, a2)
        ve(checagens_publicacao(i2, a2, s3), f"checagem pega: {msg}")
    html_dep = (coleta.BASE / "site" / "deputado.html").read_text(encoding="utf-8")
    html_obr = (coleta.BASE / "site" / "obras.html").read_text(encoding="utf-8")
    m1 = re.search(r"async function emendasDoDeputado\(.*?\n}\r?\n", html_dep, re.S)
    m2 = re.search(r"const em=EMO.*?\} else if\(o\.emendas\.length\)", html_obr, re.S)
    ve(m1 and m2, "achei o código que desenha as emendas em deputado.html e em obras.html")
    for nome_, trecho in (("deputado.html", m1.group(0)), ("obras.html", m2.group(0))):
        achados = re.findall(r"situa[cç]|aditiv|estour|cresc|\btotal|reduce\(|\+=", trecho, re.I)
        ve(not achados, f"{nome_}: o desenho das emendas não mostra situação, aditivo, estouro nem soma ({achados})")

    # ---- a troca da pasta publicada: tudo ou nada
    with tempfile.TemporaryDirectory() as t:
        dest = Path(t) / "emendas"
        gravar_pasta(dest, ind, arqs)
        ve(sorted(p.name for p in dest.iterdir()) == ["10.json", "30.json", INDICE] and json.loads((dest / "30.json").read_text(encoding="utf-8"))
           == arqs["30"], "pasta gravada e relida igual")
        try:
            gravar_pasta(dest, ind, {**arqs, "40": {"itens": [object()]}})
            ve(False, "arquivo que não serializa deveria derrubar")
        except TypeError:
            ok += 1
        ve(sorted(p.name for p in dest.iterdir()) == ["10.json", "30.json", INDICE] and not (Path(t) / "emendas.novo").exists(),
           "falha no meio: a pasta publicada fica como estava")
        gravar_pasta(dest, {**ind, "deputados": {"10": 2}}, {"10": arqs["10"]})
        ve(sorted(p.name for p in dest.iterdir()) == ["10.json", INDICE], "deputado sem vínculo: o arquivo dele sai da pasta")
        os.replace(dest, Path(t) / "emendas.velho")                  # rodada que morreu entre os dois rename
        try:
            gravar_pasta(dest, ind, {**arqs, "40": {"itens": [object()]}})
        except TypeError:
            ok += 1
        ve(sorted(p.name for p in dest.iterdir()) == ["10.json", INDICE] and not (Path(t) / "emendas.velho").exists(),
           "troca interrompida: a próxima rodada devolve a pasta anterior antes de tudo (mesmo falhando depois)")
    ve(not validar_esquemas(ind, arqs), "publicação dentro dos esquemas de schemas/")
    i2 = json.loads(json.dumps(ind))
    i2["obrasDoSite"]["7.35-01"][0]["autor"] = {"id": 1, "nome": "X", "partido": None, "uf": None}
    ve(validar_esquemas(i2, arqs), "esquema: nome de pessoa em emenda de bancada/comissão fica de fora")

    # ---- operação: erro, página vazia ou truncada no download nunca vira "sem emenda", e nada é publicado
    import contextlib
    g = globals()
    nomes = ("CACHE_E", "PUBLICO", "CANDIDATO", "AUDITORIA", "RELATORIO", "INTERVALO")
    guardado, pedir_real, sessao_real = {n: g[n] for n in nomes}, obras.pedir, obras.sessao

    def pagina(pag, total, n=None, total_informado=None):
        tp = -(-total // POR_PAGINA)
        n = (POR_PAGINA if pag < tp else total - POR_PAGINA * (tp - 1)) if n is None else n
        t_ = total if total_informado is None else total_informado
        return {"data": [{"id_projeto_investimento": f"X{pag}.{i}", "codigo_autor_emenda": None, "nr_empenho": f"2024NE{pag:03d}{i:03d}",
                          "valor_empenho": 1.0, "ug_emitente": 100000} for i in range(n)],
                "total_items": t_, "total_pages": -(-t_ // POR_PAGINA), "page_number": pag, "page_size": POR_PAGINA}

    def falso(defeito=None, na=2, total=450, pedidas=None):
        def pedir(url, params=None, **kw):
            if url.endswith("/data-atualizacao"):
                return {"data_ultima_atualizacao": "2026-10-25T00:00:00"}
            pag = params["pagina"]
            if not url.endswith("/empenho"):
                return pagina(pag, 2)                                # obras de SP: uma página boa
            if pedidas is not None:
                pedidas.append(pag)
            if pag == na:
                if defeito == "erro":
                    raise RuntimeError("sem resposta depois de 6 tentativas")
                if defeito == "404":
                    return None
                if defeito == "json":
                    raise ValueError("Expecting ',' delimiter: line 1 column 90121 (char 90120)")
                if defeito == "sem_data":
                    return {"detail": "Internal Server Error"}
                if defeito == "vazia":
                    return pagina(pag, total, n=0)
                if defeito == "truncada":
                    return pagina(pag, total, n=150)
                if defeito == "mudou":
                    return pagina(pag, total, total_informado=total + 1)
            return pagina(pag, total)
        return pedir

    with tempfile.TemporaryDirectory() as t:
        b = Path(t)
        g.update(CACHE_E=b / "cache", PUBLICO=b / "site" / "emendas", CANDIDATO=b / "cache" / "candidato",
                 AUDITORIA=b / "cache" / "auditoria.json", RELATORIO=b / "cache" / "relatorio.txt", INTERVALO=0)
        try:
            pub = g["PUBLICO"]
            pub.mkdir(parents=True)
            (pub / INDICE).write_text('{"publicado": "na rodada anterior"}', encoding="utf-8")
            (pub / "10.json").write_text('{"deputado": 10, "itens": [1]}', encoding="utf-8")
            antes = {p.name: p.read_bytes() for p in pub.iterdir()}
            for defeito in ("erro", "404", "json", "sem_data", "vazia", "truncada", "mudou"):
                obras.pedir = falso(defeito)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        main(["--publicar"])
                    ve(False, f"{defeito}: a rodada deveria parar")
                except DownloadIncompleto as e:
                    ve("/empenho, página 2" in str(e), f"{defeito}: mensagem diz a lista e a página ({e})")
                ve({p.name: p.read_bytes() for p in pub.iterdir()} == antes, f"{defeito}: site/dados/emendas/ fica intacto")
                ve(not (g["CACHE_E"] / "empenhos.json").exists(), f"{defeito}: lista incompleta não vai para o cache")
                pasta = g["CACHE_E"] / "paginas" / "empenhos"
                ve((pasta / "p1.json").exists() != (defeito == "mudou"), f"{defeito}: a página boa fica para retomar (a fonte que mudou recomeça)")
            # retomada: falha na página 3, depois a rodada boa pede só a 3
            shutil.rmtree(g["CACHE_E"] / "paginas", ignore_errors=True)
            obras.pedir = falso("erro", na=3)
            try:
                lista("empenhos", "/empenho")
            except DownloadIncompleto:
                ok += 1
            pedidas = []
            obras.pedir = falso(pedidas=pedidas)
            itens_, _ = lista("empenhos", "/empenho")
            ve(len(itens_) == 450 and pedidas == [3] and BAIXADO[-1]["paginas"] == 1, f"retomada pede só o que faltou ({pedidas})")
            # ritmo: com páginas em paralelo, dois pedidos nunca começam a menos de INTERVALO um do outro
            shutil.rmtree(g["CACHE_E"] / "paginas", ignore_errors=True)
            (g["CACHE_E"] / "empenhos.json").unlink(missing_ok=True)
            inicios, pedir_bom = [], falso(total=1000)

            def cronometrado(url, params=None, **kw):
                inicios.append(time.monotonic())
                time.sleep(0.2)                                      # a resposta demora mais que o intervalo
                return pedir_bom(url, params, **kw)
            g["INTERVALO"], obras.pedir = 0.02, cronometrado
            t_ini = time.monotonic()
            ve(len(lista("empenhos", "/empenho")[0]) == 1000, "lista em paralelo: 5 páginas, 1.000 itens, na ordem")
            gaps = [b_ - a_ for a_, b_ in zip(sorted(inicios), sorted(inicios)[1:])]
            ve(len(inicios) == 5 and min(gaps) >= 0.019 and time.monotonic() - t_ini < 0.9,
               f"ritmo respeitado e páginas em voo ao mesmo tempo (uma por vez levaria 1,1 s; intervalos {[round(x, 3) for x in gaps]})")
            g["INTERVALO"] = 0
            # prazo da rodada: depois dele nenhuma página nova é pedida, e nada vira cache de lista
            (g["CACHE_E"] / "empenhos.json").unlink()
            shutil.rmtree(g["CACHE_E"] / "paginas", ignore_errors=True)
            obras.pedir, PRAZO[0] = falso(), time.monotonic() - 1
            try:
                lista("empenhos", "/empenho")
                ve(False, "prazo esgotado deveria parar")
            except DownloadIncompleto as e:
                ve("tempo da rodada esgotado" in str(e) and not (g["CACHE_E"] / "empenhos.json").exists(), "prazo esgotado: para sem lista")
            PRAZO[0] = None
            obras.pedir = falso()
            lista("empenhos", "/empenho")
            # lista 10% menor que a anterior: suspeita, para
            cam = g["CACHE_E"] / "empenhos.json"
            cam.write_text(json.dumps({"baixadoEm": "2000-01-01T00:00:00-03:00", "itens": [{}] * 1000}), encoding="utf-8")
            try:
                lista("empenhos", "/empenho")
                ve(False, "queda da fonte deveria parar")
            except DownloadIncompleto as e:
                ve("queda" in str(e) and len(json.loads(cam.read_text(encoding="utf-8"))["itens"]) == 1000,
                   "lista que encolheu mais de 10%: para, e o cache anterior fica")
            # Câmara (só contagem de ex-deputados): resposta vazia nunca é gravada como lista vazia
            obras.pedir = lambda url, params=None, **kw: None
            try:
                ex_deputados([57], set())
                ve(False, "lista da Câmara vazia deveria parar")
            except DownloadIncompleto:
                ve(not (g["CACHE_E"] / "deputados_leg57.json").exists(), "lista da Câmara sem resposta não vira cache vazio")

            # CGU: zip cortado nunca é lido pela metade
            cab = ["Código da Emenda", "Ano da Emenda", "Tipo de Emenda", "Código do Autor da Emenda", "Nome do Autor da Emenda", "UF",
                   "Valor Empenhado", "Valor Pago", "Valor Restos A Pagar Pagos"]

            def zip_cgu(n):
                s = ";".join(cab) + "\n" + "".join(f"2024{i:08d};2024;Emenda Individual;0001;FULANO;SÃO PAULO;1,00;0,00;0,00\n" for i in range(n))
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("EmendasParlamentares.csv", s.encode("latin-1"))
                return buf.getvalue()
            bom, cortado = zip_cgu(3000), zip_cgu(3000)[:4000]
            ve(len(ler_zip_cgu(bom)["emendas"]) == 3000, "zip bom lido inteiro")
            try:
                ler_zip_cgu(cortado)
                ve(False, "zip cortado deveria falhar")
            except (zipfile.BadZipFile, zlib.error, EOFError):
                ok += 1

            class Resp:
                def __init__(s, status=200, content=b"", headers=None):
                    s.status_code, s.content, s.headers = status, content, headers or {}

            class Sessao:
                def __init__(s, resp):
                    s.resp = resp

                def head(s, *a, **k):
                    return Resp(200, headers={"last-modified": "nova"})

                def get(s, *a, **k):
                    return s.resp
            ccgu = g["CACHE_E"] / "cgu.json"
            for resp, msg in ((Resp(200, cortado), "zip cortado"), (Resp(500), "HTTP 500")):
                obras.sessao = Sessao(resp)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        cgu()
                    ve(False, f"CGU {msg} sem cópia anterior deveria parar")
                except DownloadIncompleto:
                    ve(not ccgu.exists(), f"CGU {msg} sem cópia anterior: para e não grava nada")
            obras.sessao = Sessao(Resp(200, bom))
            ve(len(cgu()["emendas"]) == 3000 and ccgu.exists(), "CGU bom: gravado")
            obras.sessao = Sessao(Resp(500))                         # mesma versão: nem pede o zip
            ve(len(cgu()["emendas"]) == 3000 and json.loads((g["CACHE_E"] / "cgu_conferido.json").read_text(encoding="utf-8"))["versao"] == "nova",
               "mesma versão na CGU: usa o cache e anota a confirmação")
            c = json.loads(ccgu.read_text(encoding="utf-8"))
            c["_versao"] = "velha"
            ccgu.write_text(json.dumps(c), encoding="utf-8")
            obras.sessao = Sessao(Resp(200, cortado))
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                ve(len(cgu()["emendas"]) == 3000, "CGU cortado com cópia de hoje: usa a cópia completa, nunca a metade")
            ve("usando a cópia completa anterior, de 0 h (limite: 3 dias)" in log.getvalue(), f"o aviso diz a idade da cópia ({log.getvalue()!r})")
            # a idade conta da última confirmação da CGU: baixada há 10 dias, mas a CGU disse a mesma versão ontem → ainda vale
            antiga = lambda dias: (datetime.now(obras.BRASILIA) - timedelta(days=dias)).isoformat(timespec="seconds")
            c["_baixadoEm"] = antiga(10)
            ccgu.write_text(json.dumps(c), encoding="utf-8")
            conf = g["CACHE_E"] / "cgu_conferido.json"
            conf.write_text(json.dumps({"versao": "velha", "conferidoEm": antiga(1)}), encoding="utf-8")
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                ve(len(cgu()["emendas"]) == 3000 and "de 24 h" in log.getvalue(), "cópia confirmada ontem (baixada há 10 dias): vale, com aviso")
            # acima de 3 dias: para, e nada é publicado
            conf.write_text(json.dumps({"versao": "velha", "conferidoEm": antiga(4)}), encoding="utf-8")
            try:
                cgu()
                ve(False, "cópia da CGU com 4 dias deveria parar")
            except DownloadIncompleto as e:
                ve("4 dias, mais que o limite de 3 dias: nada é publicado" in str(e), f"cópia com 4 dias: para ({e})")
            conf.write_text(json.dumps({"versao": "outra", "conferidoEm": antiga(0)}), encoding="utf-8")
            try:
                cgu()
                ve(False, "confirmação de OUTRA versão não deveria rejuvenescer a cópia")
            except DownloadIncompleto:
                ok += 1
            obras.sessao = Sessao(Resp(200, zip_cgu(1000)))
            try:
                cgu()
                ve(False, "CGU que encolheu deveria parar")
            except DownloadIncompleto:
                ve(json.loads(ccgu.read_text(encoding="utf-8"))["_versao"] == "velha", "CGU 10% menor: para, e a cópia anterior fica")
        finally:
            g.update(guardado)
            obras.pedir, obras.sessao = pedir_real, sessao_real
    print(f"{ok} verificações passaram")


# ---------------------------------------------------------------- rodada

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="o padrão: só o candidato e o relatório em cache/emendas/")
    modo.add_argument("--publicar", action="store_true", help="troca site/dados/emendas/ pelo candidato (só se as checagens passarem)")
    ap.add_argument("--so-montar", action="store_true", help="sem rede: remonta do cache")
    ap.add_argument("--minutos", type=float, default=40, help="depois disto nenhuma página nova é pedida e a rodada para sem publicar "
                    "(padrão 40; a frio levou 12 min em 26/10/2026)")
    ap.add_argument("--teste", action="store_true", help="casos sintéticos, sem rede")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):          # nos testes a saída é redirecionada para um StringIO
        sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    CACHE_E.mkdir(parents=True, exist_ok=True)
    supressoes, aliases = carregar_supressoes(), carregar_aliases()

    del BAIXADO[:]
    PRAZO[0] = time.monotonic() + args.minutos * 60
    atualizado = None
    if not args.so_montar:
        try:                                         # só informativo: fora do ar, a rodada segue (as listas são conferidas uma a uma)
            atualizado = (obras.pedir(OG + "/data-atualizacao", tentativas=2) or {}).get("data_ultima_atualizacao")
        except (RuntimeError, ValueError) as e:
            aviso(f"ObrasGov /data-atualizacao: {e}")
    print(f"ObrasGov atualizado em {atualizado or '(não consultado)'}. Obras de {UF}…", flush=True)
    projetos, _ = lista(f"projetos_{UF}", "/projeto-investimento", args.so_montar, uf_principal=UF)
    sp = {p["id_projeto_investimento"]: p for p in projetos}
    print(f"   {len(sp)} obras; empenhos do país inteiro…", flush=True)
    empenhos, m_emp = lista("empenhos", "/empenho", args.so_montar)
    print(f"   {len(empenhos)} empenhos; municípios das obras de {UF}…", flush=True)
    geom, _ = lista(f"geometria_{UF}", "/geometria", args.so_montar, sg_uf=UF)
    geo = defaultdict(set)
    for g in geom:
        if g.get("no_municipio"):
            geo[g["id_projeto_investimento"]].add(g["no_municipio"])
    geo = {k: sorted(v) for k, v in geo.items()}

    cg = cgu(args.so_montar)
    autor_dep, fora_dep = obras.autores_deputados(cg["emendas"])
    deps = {d["id"]: d for d in json.loads(coleta.SAIDA.read_text(encoding="utf-8"))}
    validos, avisos_alias = conferir_aliases(aliases, cg, autor_dep, deps)
    for x in avisos_alias:
        aviso(x)
    base = json.loads(obras.SAIDA.read_text(encoding="utf-8")) if obras.SAIDA.exists() else {"obras": []}
    com_contrato = {o["id"] for o in base["obras"]}

    pares, cont_cod, com_emp, repetidos = pares_das_obras(empenhos, sp)
    # para a contagem de quem fica fora desta fase: senadores (os dois arquivos do site) e ex-deputados (listas por legislatura)
    senadores = defaultdict(list)
    for arq in ("senadores.json", "senadores_antigos.json"):
        sj = coleta.DADOS / arq
        if sj.exists():
            lst = json.loads(sj.read_text(encoding="utf-8"))
            for s in (lst if isinstance(lst, list) else lst.get("senadores", [])):
                senadores[coleta.nome_chave(s.get("nome") or "")].append((s["id"], s.get("mandatos")))
    legs = json.loads((coleta.DADOS / "legislaturas.json").read_text(encoding="utf-8"))
    anos = {int(cg["emendas"][c][3]) for _, c in pares if c in cg["emendas"] and cg["emendas"][c][3].isdigit()}
    leg_do_ano = {a: [L["id"] for L in legs if L["inicio"][:4] <= str(a) <= L["fim"][:4]] for a in anos | {a - 1 for a in anos}}
    try:                                             # só para a contagem de quem fica fora: sem a lista, não conta (e não publica nada dela)
        exdeps = ex_deputados(sorted({n for v in leg_do_ano.values() for n in v}), set(deps), args.so_montar)
    except DownloadIncompleto as e:
        aviso(f"{e}: ex-deputados não contados nesta rodada")
        exdeps = None

    vinc, est = avaliar(pares, cg, autor_dep, deps, supressoes, senadores, exdeps, leg_do_ano, aliases=validos)
    desconhecidas = sorted(set(supressoes) - {v["id"] for v in vinc})
    if desconhecidas:
        aviso(f"{len(desconhecidas)} supressão(ões) sem vínculo correspondente: {', '.join(desconhecidas[:5])}")
    meta = {"_versao": VERSAO, "_geradoEm": agora(), "_uf": UF, "_texto": TEXTO,
            "_fontes": {"obrasgov": atualizado, "empenhosBaixadosEm": m_emp.get("baixadoEm"), "cgu": cg.get("_versao")},
            "_regras": REGRAS, "_motivos": MOTIVOS, "_tipos": TIPOS, "_notaValores": NOTA_VALORES}
    saida = montar_saida(vinc, sp, geo, deps, com_contrato, meta)
    indice, arquivos = publicacao(saida, cg)
    publicado = coleta.ler_cache(PUBLICO / INDICE)
    erros = (checagens(saida, vinc) + checagens_publicacao(indice, arquivos, saida) + validar_esquemas(indice, arquivos)
             + queda(indice, publicado))
    gravar(AUDITORIA, saida)
    gravar_pasta(CANDIDATO, indice, arquivos)
    kb = {p.name: p.stat().st_size for p in CANDIDATO.iterdir()}
    emp_pub = [x for v in vinc if v["publicavel"] for x in v["empenhos"]]
    extra = {"aliases": aliases, "validos": validos, "avisos_alias": avisos_alias, "deps": deps,
             "n_arquivos": len(arquivos), "kb_indice": kb.get(INDICE, 0) // 1024,
             "kb_total": sum(n for k, n in kb.items() if k != INDICE) // 1024,
             "kb_maior": max((n for k, n in kb.items() if k != INDICE), default=0) // 1024, "n_obras_site": len(indice["obrasDoSite"]),
             "n_emp_pub": len(emp_pub), "com_ug": sum(1 for u, _ in emp_pub if re.fullmatch(r"\d{6}", str(u or ""))),
             "com_nr": sum(1 for _, n in emp_pub if re.fullmatch(r"\d{4}NE\d{6}", n or "")),
             "campos_gestao": sorted({k for e in empenhos for k in e if "gest" in k.lower()}), "repetidos": repetidos}
    texto = relatorio(est, cont_cod, len(sp), sum(cont_cod.values()), com_emp, saida, erros, args, extra)
    texto += f"\n\nAutores da CGU ligados a deputado do site: {len(autor_dep)} códigos; fora da regra: {dict(fora_dep)}"
    texto += f"\nAuditoria (todos os vínculos, inclusive os retidos): {AUDITORIA} ({AUDITORIA.stat().st_size // 1024} kB)"
    RELATORIO.write_text(texto + "\n", encoding="utf-8")
    print("\n" + texto)
    if erros:
        sys.exit(f"checagem falhou ({len(erros)}): nada publicado")
    if args.publicar:
        gravar_pasta(PUBLICO, indice, arquivos)
        print(f"site/dados/emendas/ publicado: {len(arquivos)} arquivos de deputado e {INDICE}.")


if __name__ == "__main__":
    main()
