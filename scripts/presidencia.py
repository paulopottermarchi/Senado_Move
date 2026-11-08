"""
Câmara Aberta — página "Presidente" (site/dados/presidencia.json): o mandato atual e as medidas provisórias editadas nele, com a situação
registrada pelo Congresso. (Os vetos entram num segundo passo.)

Uso:
    python scripts/presidencia.py                # --dry-run (o padrão): candidato, relatório e amostra em cache/presidencia/
    python scripts/presidencia.py --publicar     # grava site/dados/presidencia.json, só se TODAS as checagens passarem
    python scripts/presidencia.py --so-montar    # sem rede: remonta do cache
    python scripts/presidencia.py --teste        # casos sintéticos, sem rede
    --minutos N (padrão 10): passado o prazo, nenhum pedido novo é feito e a rodada para sem publicar

Fontes (Etapa 1, capturas/diagnostico_presidencia.md):
  * Mandato: entradas/governos.json (o governo cujo período contém hoje). O que o arquivo não tem fica "—" na página.
  * MPs: Dados Abertos do Senado Federal e do Congresso Nacional, GET /dadosabertos/processo?sigla=MPV&ano={ano} (situação, deliberação,
    lei gerada) e GET /dadosabertos/processo/{id} (a lei estruturada: número, data, código da norma), só para as aprovadas.
  * Conferência: API da Câmara, GET /api/v2/proposicoes?siglaTipo=MPV&ano={ano} (a mesma contagem por ano; dá também o id da ficha na Câmara).

Regras (o que a página promete):
  * A situação é a do campo oficial (`situacaoAtual`). Vazio: vale a deliberação oficial (`siglaTipoDeliberacao`). Os dois vazios: "—", com o
    motivo. Valor que este código não conhece: a rodada PARA (um estado novo da fonte nunca vira outro por palpite).
  * Perda de eficácia NÃO é rejeição: é o prazo constitucional (art. 62, § 3º) que terminou sem votação final.
  * Erro da fonte nunca vira "sem MPs" nem "em tramitação": lista vazia, resposta fora do formato, HTTP de erro, contagem que não bate com a
    Câmara ou queda/subida de mais de 10% contra o publicado param a rodada, e o arquivo publicado fica como estava.
  * MP "convertida" sem a lei registrada fica RETIDA (fora da lista e das contagens, listada com o motivo).
  * Nada de ranking, taxa, "derrota" ou "vitória": só contagens por situação, cada uma com a definição.
"""

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import coleta

VERSAO = "presidencia-1"
SAIDA = coleta.DADOS / "presidencia.json"
CACHE_P = coleta.CACHE / "presidencia"
CANDIDATO = CACHE_P / "candidato.json"
RELATORIO = CACHE_P / "relatorio.txt"
AMOSTRA = coleta.CACHE / "amostra_presidencia.csv"
GOVERNOS = coleta.ENTRADAS / "governos.json"
ESQUEMA = coleta.BASE / "schemas" / "presidencia.schema.json"
SENADO = "https://legis.senado.leg.br/dadosabertos"
CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
UA = "CamaraAberta/1.0 presidencia (https://github.com/paulopottermarchi/Senado_Move)"
BRASILIA = timezone(timedelta(hours=-3))
VALIDADE = timedelta(days=1)            # listas e MPs abertas
VALIDADE_FECHADA = timedelta(days=30)   # detalhe de MP já encerrada (a lei gerada não muda)
VARIACAO = 0.10                         # mais ou menos que isto de MPs contra o publicado: falha de fonte, a rodada para
INTERVALO = 0.5                         # segundos entre dois pedidos
PRAZO = [None]
URLS = {"congresso": "https://www.congressonacional.leg.br/materias/medidas-provisorias/-/mpv/{codigoMateria}",
        "api": SENADO + "/processo/{id}",
        "camara": "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={camaraId}",
        "lei": "https://legis.senado.leg.br/norma/{codigo}"}

# Categorias da página, na ordem em que aparecem; cada uma com a definição que vai ao lado do número.
SITUACOES = [
    ("convertida", "Convertida em lei",
     "O Congresso aprovou a MP, na íntegra ou na forma de um projeto de lei de conversão (com mudanças no texto), e ela virou lei. O número da lei é o registrado pelo Congresso."),
    ("aprovada_sem_lei", "Aprovada, sem lei registrada",
     "O Congresso aprovou a MP e a fonte ainda não registra a lei. A sanção cabe ao Presidente."),
    ("perdeu_eficacia", "Perdeu a eficácia",
     "O prazo para o Congresso votar a MP (60 dias, prorrogáveis por mais 60; Constituição, art. 62, § 3º) terminou sem votação final. Não é rejeição: "
     "a MP deixa de valer porque não foi votada a tempo."),
    ("revogada", "Revogada", "A MP foi revogada antes da conclusão da votação no Congresso, segundo o registro do Congresso."),
    ("rejeitada", "Rejeitada", "A Câmara ou o Senado votou e rejeitou a MP."),
    ("prejudicada", "Prejudicada", "O Congresso declarou a MP prejudicada (sem objeto), segundo o registro."),
    ("em_tramitacao", "Em tramitação",
     "A MP ainda está em análise no Congresso (comissão mista, Câmara ou Senado). A situação oficial de cada uma aparece na lista."),
    ("sem_situacao", "—", "A fonte não traz a situação nem a deliberação desta MP."),
]
CATEGORIAS = [c for c, _, _ in SITUACOES]
# situação oficial (situacaoAtual, como o Congresso grava) de MP encerrada → categoria
POR_SITUACAO = {"TRANSFORMADA EM NORMA JURÍDICA": "convertida", "TRANSFORMADA EM NORMA JURÍDICA COM VETO PARCIAL": "convertida",
                "SEM EFICÁCIA": "perdeu_eficacia", "REVOGADA": "revogada", "REJEITADA": "rejeitada", "REJEITADA A MEDIDA PROVISÓRIA": "rejeitada",
                "PREJUDICADA": "prejudicada", "REMETIDA À SANÇÃO": "aprovada_sem_lei", "APROVADA": "aprovada_sem_lei",
                "APROVADA A MEDIDA PROVISÓRIA": "aprovada_sem_lei", "APROVADO O PROJETO DE LEI DE CONVERSÃO": "aprovada_sem_lei"}
# deliberação oficial (siglaTipoDeliberacao, de /processo/tipos-decisao) → categoria; só vale quando a situação vem vazia
POR_DELIBERACAO = {"PERDA_EFICACIA": "perdeu_eficacia", "SEM_EFICACIA": "perdeu_eficacia", "REVOGADO": "revogada", "PREJUDICADO": "prejudicada",
                   "REJEITADO_PLENARIO": "rejeitada", "REJEITADO_PLENARIO_CD": "rejeitada",
                   "APROVADO_NA_INTEGRA": "aprovada_sem_lei", "APROVADO_PLV": "aprovada_sem_lei"}
# descrição oficial de cada deliberação que este código reconhece, copiada de GET /dadosabertos/processo/tipos-decisao (7/11/2026)
DELIBERACOES = {"PERDA_EFICACIA": "Perda de eficácia, em decorrência do término do prazo para sua votação no Congresso",
                "SEM_EFICACIA": "Sem eficácia (art. 48, II e XI, do RISF)", "REVOGADO": "Revogada", "PREJUDICADO": "Prejudicada",
                "REJEITADO_PLENARIO": "Rejeitada pelo Plenário", "REJEITADO_PLENARIO_CD": "Rejeitada pelo Plenário da Câmara dos Deputados",
                "APROVADO_NA_INTEGRA": "Aprovada na íntegra", "APROVADO_PLV": "Aprovada na forma de Projeto de Lei de Conversão"}
assert set(DELIBERACOES) == set(POR_DELIBERACAO)
PROIBIDAS = re.compile(r"derrot|vit[oó]ri|culp|governista|fracass|ranking", re.I)
TEXTO_PAGINA = coleta.BASE / "site" / "presidente.html"


class FonteFalhou(RuntimeError):
    """A fonte não respondeu como devia: nada é publicado."""


class Prazo(FonteFalhou):
    pass


def agora():
    return datetime.now(BRASILIA)


# ---------------------------------------------------------------- rede e cache

_sessao = None


def sessao():
    global _sessao
    if _sessao is None:
        _sessao = coleta.requests.Session()
        _sessao.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return _sessao


def pedir(url, params=None, tentativas=3):
    """JSON da fonte, ou FonteFalhou com o motivo da última tentativa (nunca devolve vazio no lugar de erro)."""
    ultimo = "sem resposta"
    for n in range(tentativas):
        if PRAZO[0] is not None and time.monotonic() > PRAZO[0]:
            raise Prazo(f"prazo da rodada esgotado antes de pedir {url}")
        time.sleep(INTERVALO)
        try:
            r = sessao().get(url, params=params, timeout=(10, 60))
        except (coleta.requests.ConnectionError, coleta.requests.Timeout) as e:
            ultimo = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(2 ** n)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ultimo = coleta.motivo_http(r)
            time.sleep(2 ** n)
            continue
        if r.status_code != 200:
            raise FonteFalhou(f"{url} {params or ''}: {coleta.motivo_http(r)}")
        try:
            return r.json()
        except ValueError:
            ultimo = f"HTTP 200 sem JSON ({r.headers.get('content-type') or 'sem tipo'})"
            time.sleep(2 ** n)
    raise FonteFalhou(f"{url} {params or ''}: falhou após {tentativas} tentativas ({ultimo})")


def cache_ler(nome, validade, so_montar):
    p = CACHE_P / nome
    c = coleta.ler_cache(p)
    if c is None:
        if so_montar:
            raise FonteFalhou(f"--so-montar sem {nome} no cache: rode sem --so-montar antes")
        return None
    if so_montar or agora() - datetime.fromisoformat(c["_baixadoEm"]) < validade:
        return c["dados"]
    return None


def cache_gravar(nome, dados):
    coleta.gravar_texto(CACHE_P / nome, json.dumps({"_baixadoEm": agora().isoformat(timespec="seconds"), "dados": dados}, ensure_ascii=False))


def lista_senado(ano, so_montar):
    """As MPs de um ano na fonte do Congresso, conferidas uma a uma (formato e identificação)."""
    nome = f"mpv_senado_{ano}.json"
    dados = cache_ler(nome, VALIDADE, so_montar)
    if dados is None:
        dados = pedir(SENADO + "/processo", {"sigla": "MPV", "ano": ano})
        conferir_lista_senado(dados, ano)
        cache_gravar(nome, dados)
    conferir_lista_senado(dados, ano)
    return dados


def conferir_lista_senado(dados, ano):
    if not isinstance(dados, list):
        raise FonteFalhou(f"Congresso, MPV {ano}: a resposta não é uma lista ({type(dados).__name__})")
    for m in dados:
        if not isinstance(m, dict) or not re.fullmatch(rf"MPV \d+/{ano}", str(m.get("identificacao") or "")) or not isinstance(m.get("id"), int) \
                or not isinstance(m.get("codigoMateria"), int) or m.get("tramitando") not in ("Sim", "Não"):
            raise FonteFalhou(f"Congresso, MPV {ano}: registro fora do formato: {json.dumps(m, ensure_ascii=False)[:200]}")
    ids = [m["identificacao"] for m in dados]
    if len(ids) != len(set(ids)):
        raise FonteFalhou(f"Congresso, MPV {ano}: MP repetida na lista ({[k for k, n in Counter(ids).items() if n > 1][:5]})")


def lista_camara(ano, so_montar):
    """{número: id} das MPs de um ano na Câmara, seguindo as páginas (link `next`)."""
    nome = f"mpv_camara_{ano}.json"
    dados = cache_ler(nome, VALIDADE, so_montar)
    if dados is None:
        dados, url, params = [], CAMARA + "/proposicoes", {"siglaTipo": "MPV", "ano": ano, "itens": 100, "ordem": "ASC", "ordenarPor": "id"}
        while url:
            j = pedir(url, params)
            if not isinstance(j, dict) or not isinstance(j.get("dados"), list):
                raise FonteFalhou(f"Câmara, MPV {ano}: resposta sem `dados`")
            dados += [{"id": x["id"], "numero": x["numero"], "ano": x["ano"]} for x in j["dados"]]
            url = next((l["href"] for l in j.get("links") or [] if l.get("rel") == "next"), None)
            params = None
        cache_gravar(nome, dados)
    return dados


def detalhe(m, so_montar):
    """O processo completo de uma MP (só para as aprovadas: é onde está a lei estruturada).
    Validade: o detalhe que JÁ traz a lei vale 30 dias (a lei registrada não muda); o que ainda não a traz (aprovada, aguardando a sanção) vale
    1 dia, para a MP passar a "convertida" na rodada seguinte ao registro da lei. Com 30 dias para todos, uma MP sancionada ficaria retida
    ("a fonte diz que virou lei, mas não registra a lei") até o cache vencer."""
    nome = f"detalhe/{m['id']}.json"
    d = cache_ler(nome, VALIDADE_FECHADA, so_montar)
    if d is not None and not so_montar and lei_do_detalhe(d) is None:
        d = cache_ler(nome, VALIDADE, so_montar)
    if d is None:
        d = pedir(SENADO + f"/processo/{m['id']}")
        if not isinstance(d, dict) or d.get("id") != m["id"]:
            raise FonteFalhou(f"Congresso, processo {m['id']} ({m['identificacao']}): detalhe fora do formato")
        cache_gravar(nome, d)
    return d


# ---------------------------------------------------------------- regras

def carregar_mandato(hoje, caminho=GOVERNOS):
    gov = json.loads(caminho.read_text(encoding="utf-8"))["governos"]
    atual = [g for g in gov if g["inicio"] <= hoje.isoformat() <= g["fim"]]
    if len(atual) != 1:
        raise FonteFalhou(f"entradas/governos.json: {len(atual)} governos contêm {hoje} (esperado 1)")
    g = atual[0]
    fim = None if g["fim"] >= "2099" else g["fim"]       # "2099-12-31" é o marcador de governo em curso, não uma data
    return {"id": g["id"], "rotulo": g["rotulo"], "presidente": g.get("presidente"), "inicio": g["inicio"], "fim": fim,
            "partido": [p["sigla"] for p in g.get("partidoPresidente") or []], "nota": g.get("nota"),
            "fonte": "entradas/governos.json (datas e partido conferidos no Wikidata, propriedades P39 e P102)"}


def anos_do_mandato(mandato, hoje):
    fim = min(hoje.year, int((mandato["fim"] or "9999")[:4]))
    return list(range(int(mandato["inicio"][:4]), fim + 1))


NORMA = re.compile(r"(Lei(?: Complementar)?) nº ([\d.]+),? de (\d{2})/(\d{2})/(\d{4})")


def lei_do_detalhe(d):
    """A lei gerada, do detalhe estruturado; None se o detalhe não a traz completa."""
    n = (d or {}).get("normaGerada")
    if not isinstance(n, dict) or not n.get("numero") or not n.get("dataAssinatura") or not n.get("codigo"):
        return None
    return {"numero": int(n["numero"]), "ano": int(str(n["dataAssinatura"])[:4]), "data": str(n["dataAssinatura"])[:10],
            "descricao": n.get("descricao") or f"Lei nº {n['numero']}", "codigo": int(n["codigo"])}


def classificar(m, d=None):
    """(categoria, situação oficial mostrada, motivo da retenção ou None, lei ou None). A lista é a única fonte da situação; o detalhe só
    dá a lei. Valor desconhecido levanta (a rodada para)."""
    sit = (m.get("situacaoAtual") or "").strip() or None
    deli = (m.get("siglaTipoDeliberacao") or "").strip() or None
    if m.get("tramitando") == "Sim":
        return "em_tramitacao", sit, None, None
    if sit:
        if sit not in POR_SITUACAO:
            raise FonteFalhou(f"{m['identificacao']}: situação nova na fonte ({sit!r}): incluir em POR_SITUACAO antes de publicar")
        cat = POR_SITUACAO[sit]
    elif deli:
        if deli not in POR_DELIBERACAO:
            raise FonteFalhou(f"{m['identificacao']}: deliberação nova na fonte ({deli!r}): incluir em POR_DELIBERACAO antes de publicar")
        cat = POR_DELIBERACAO[deli]
    else:
        return "sem_situacao", None, None, None
    lei = lei_do_detalhe(d) if d is not None else None
    texto_lei = m.get("normaGerada")
    if cat == "convertida":
        if not lei:
            return cat, sit, "a fonte diz que virou lei, mas não registra a lei (número, data e código da norma)", None
        mt = NORMA.search(str(texto_lei or ""))
        if not mt or int(mt[2].replace(".", "")) != lei["numero"]:
            return cat, sit, f"a lei da lista ({texto_lei!r}) não é a do detalhe (nº {lei['numero']})", None
        return cat, sit, None, lei
    if cat == "aprovada_sem_lei" and lei:              # aprovada e com a lei já registrada: é convertida
        return "convertida", sit, None, lei
    return cat, sit, None, None


def montar(mandato, por_ano_senado, por_ano_camara, detalhes):
    """O JSON da página e as retidas. `detalhes` = {id: processo} das aprovadas."""
    itens, retidas, por_ano = [], [], []
    camara_id = {(int(x["numero"]), int(x["ano"])): x["id"] for lst in por_ano_camara.values() for x in lst}
    for ano in sorted(por_ano_senado):
        lst = [m for m in por_ano_senado[ano] if mandato["inicio"] <= (m.get("dataApresentacao") or "")[:10] <= (mandato["fim"] or "9999")]
        # fonte × câmara: a contagem bruta do ano nas duas fontes (a conferência); noMandato: as do ano apresentadas dentro do mandato
        por_ano.append({"ano": ano, "fonte": len(por_ano_senado[ano]), "camara": len(por_ano_camara.get(ano, [])), "noMandato": len(lst)})
        for m in lst:
            num = int(m["identificacao"].split()[1].split("/")[0])
            cat, sit, motivo, lei = classificar(m, detalhes.get(m["id"]))
            if motivo:
                retidas.append({"identificacao": m["identificacao"], "motivo": motivo})
                continue
            item = {"id": m["id"], "codigoMateria": m["codigoMateria"], "numero": num, "ano": ano,
                    "ementa": (m.get("ementa") or "").strip() or None, "data": (m.get("dataApresentacao") or "")[:10] or None,
                    "situacao": cat, "situacaoFonte": sit, "deliberacaoFonte": (m.get("siglaTipoDeliberacao") or None),
                    "dataDeliberacao": (m.get("dataDeliberacao") or "")[:10] or None, "lei": lei,
                    "camaraId": camara_id.get((num, ano))}
            if item["deliberacaoFonte"] and item["deliberacaoFonte"] not in DELIBERACOES:
                raise FonteFalhou(f"{m['identificacao']}: deliberação nova na fonte ({item['deliberacaoFonte']!r}): incluir em DELIBERACOES antes de publicar")
            if cat == "sem_situacao":
                item["motivo"] = "a fonte não traz a situação nem a deliberação"
            elif not sit:
                item["motivo"] = "situação vazia na fonte: vale a deliberação oficial"
            itens.append(item)
    itens.sort(key=lambda x: (-x["ano"], -x["numero"]))
    contagem = Counter(x["situacao"] for x in itens)
    return {"_versao": VERSAO, "_geradoEm": agora().isoformat(timespec="minutes"),
            "_fontes": {"mps": {"nome": "Dados Abertos do Senado Federal e do Congresso Nacional", "url": SENADO + "/processo?sigla=MPV",
                                "documentacao": SENADO + "/v3/api-docs"},
                        "conferencia": {"nome": "Dados Abertos da Câmara dos Deputados", "url": CAMARA + "/proposicoes?siglaTipo=MPV"},
                        "mandato": mandato["fonte"]},
            "urls": URLS,
            "mandato": {k: v for k, v in mandato.items() if k != "fonte"},
            "mps": {"definicoes": [{"id": c, "rotulo": r, "definicao": t} for c, r, t in SITUACOES],
                    "deliberacoes": {k: v for k, v in DELIBERACOES.items() if any(x["deliberacaoFonte"] == k for x in itens)},
                    "total": len(itens), "contagem": {c: contagem.get(c, 0) for c in CATEGORIAS},
                    "porAno": por_ano, "retidas": retidas, "itens": itens}}


def checagens(saida):
    erros = []
    mps = saida["mps"]
    for a in mps["porAno"]:
        if a["fonte"] != a["camara"]:
            erros.append(f"MPV {a['ano']}: {a['fonte']} na fonte do Congresso e {a['camara']} na Câmara: contagem por ano não bate")
    soma = sum(mps["contagem"].values())
    if soma != mps["total"] or mps["total"] != len(mps["itens"]):
        erros.append(f"contagens por situação somam {soma}, o total é {mps['total']} e a lista tem {len(mps['itens'])}")
    if mps["total"] + len(mps["retidas"]) != sum(a["noMandato"] for a in mps["porAno"]):
        erros.append("itens + retidas não somam o total da fonte por ano")
    for x in mps["itens"]:
        if x["situacao"] == "convertida" and not x["lei"]:
            erros.append(f"MPV {x['numero']}/{x['ano']} convertida sem lei")
    ids = [(x["numero"], x["ano"]) for x in mps["itens"]]
    if len(ids) != len(set(ids)):
        erros.append("MP repetida na lista")
    # o ano corrente pode começar vazio; um ano FECHADO sem nenhuma MP é falha de fonte
    hoje = agora().date()
    for a in mps["porAno"]:
        if a["ano"] < hoje.year and a["fonte"] == 0:
            erros.append(f"MPV {a['ano']}: ano fechado sem nenhuma MP na fonte")
    erros += proibidos(saida)
    return erros


def proibidos(obj):
    """Nenhum texto da saída fala em derrota, vitória, culpa (nem ranking): a página só conta."""
    texto = json.dumps({k: v for k, v in obj.items() if k != "mps"} | {"definicoes": obj["mps"]["definicoes"]}, ensure_ascii=False)
    m = PROIBIDAS.search(texto)
    return [f"palavra proibida nos textos da saída: {m[0]!r}"] if m else []


def variacao(saida, publicado):
    """Mais de 10% de MPs a mais ou a menos contra o publicado (mesma versão e mesmo mandato): falha de fonte, a rodada para."""
    if not publicado or publicado.get("_versao") != VERSAO or (publicado.get("mandato") or {}).get("id") != saida["mandato"]["id"]:
        return []
    a, n = publicado["mps"]["total"], saida["mps"]["total"]
    if a >= 20 and abs(n - a) > a * VARIACAO:
        return [f"MPs: {a} no publicado e {n} agora (variação maior que {VARIACAO:.0%}): nada publicado"]
    return []


def validar_esquema(saida):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema não instalado: a saída não pode ser conferida contra schemas/presidencia.schema.json"]
    v = Draft202012Validator(json.loads(ESQUEMA.read_text(encoding="utf-8")))
    return [f"esquema: {'.'.join(map(str, e.absolute_path)) or '<raiz>'}: {e.message}" for e in v.iter_errors(saida)][:20]


def serializar(saida):
    """JSON compacto, uma MP por linha (o diff do git mostra só o que mudou)."""
    sep = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    mps = dict(saida["mps"])
    itens = mps.pop("itens")
    cab = {k: v for k, v in saida.items() if k != "mps"}
    return (sep(cab)[:-1] + ',\n"mps":' + sep(mps)[:-1] + ',\n"itens":[\n' + ",\n".join(sep(x) for x in itens) + "\n]}}\n")


def gravar(caminho, saida):
    texto = serializar(saida)
    json.loads(texto)
    coleta.gravar_texto(caminho, texto, newline="\n")


def amostra(saida, n=10, semente=20261107):
    """10 MPs para conferir à mão: pelo menos uma de cada situação presente, o resto sorteado (semente fixa)."""
    rnd = random.Random(semente)
    itens = saida["mps"]["itens"]
    escolha = []
    for c in CATEGORIAS:
        grupo = [x for x in itens if x["situacao"] == c]
        if grupo:
            escolha.append(rnd.choice(grupo))
    resto = [x for x in itens if x not in escolha]
    escolha += rnd.sample(resto, max(0, min(n - len(escolha), len(resto))))
    rot = {c: r for c, r, _ in SITUACOES}
    linhas = ["tipo;identificacao;situacao;situacao_fonte;deliberacao_fonte;lei;link_congresso;link_api;link_camara;confere"]
    for x in escolha[:n]:
        lei = f"{x['lei']['descricao']} ({URLS['lei'].format(codigo=x['lei']['codigo'])})" if x["lei"] else ""
        cam = URLS["camara"].format(camaraId=x["camaraId"]) if x["camaraId"] else ""
        linhas.append(";".join(str(v).replace(";", ",") for v in (
            "MP", f"MPV {x['numero']}/{x['ano']}", rot[x["situacao"]], x["situacaoFonte"] or "", x["deliberacaoFonte"] or "", lei,
            URLS["congresso"].format(codigoMateria=x["codigoMateria"]), URLS["api"].format(id=x["id"]), cam, "")))
    return "\n".join(linhas) + "\n"


def relatorio(saida, erros, args, segundos, pedidos):
    m = saida["mps"]
    rot = {c: r for c, r, _ in SITUACOES}
    l = [f"PRESIDENTE — {saida['mandato']['rotulo']} (desde {saida['mandato']['inicio']}) · {'publicação' if args.publicar else 'dry-run'} · "
         f"{segundos:.0f} s · {pedidos} pedidos à rede",
         f"MPs: {m['total']} publicáveis · retidas {len(m['retidas'])}",
         "por ano (Congresso × Câmara): " + " · ".join(f"{a['ano']}: {a['fonte']} × {a['camara']}" for a in m["porAno"]),
         "por situação: " + " · ".join(f"{rot[c]} {n}" for c, n in m["contagem"].items() if n or c in ("rejeitada",))]
    sem = [x for x in m["itens"] if x.get("motivo")]
    if sem:
        l.append("situação pela deliberação ou sem situação: " + ", ".join(f"MPV {x['numero']}/{x['ano']} ({rot[x['situacao']]})" for x in sem))
    for r in m["retidas"]:
        l.append(f"RETIDA {r['identificacao']}: {r['motivo']}")
    l += [f"ERRO: {e}" for e in erros] or ["checagens: todas passaram"]
    return "\n".join(l)


# ---------------------------------------------------------------- testes

def testar():
    import tempfile
    from pathlib import Path
    from unittest import mock
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1

    def mp(num, ano=2023, sit="SEM EFICÁCIA", deli="PERDA_EFICACIA", tram="Não", norma=None, data=None):
        return {"identificacao": f"MPV {num}/{ano}", "id": 1000 + num, "codigoMateria": 5000 + num, "tramitando": tram, "situacaoAtual": sit,
                "siglaTipoDeliberacao": deli, "normaGerada": norma, "dataApresentacao": data or f"{ano}-03-01", "ementa": f"Ementa {num}",
                "dataDeliberacao": f"{ano}-06-01"}

    def det(num, lei=15000, data="2024-03-06", codigo=40000000):
        return {"id": 1000 + num, "normaGerada": {"numero": lei, "dataAssinatura": data, "codigo": codigo, "descricao": f"Lei nº {lei} de 06/03/2024"}}

    # 1. as três MPs de 2023 sem situação: vale a deliberação oficial (perda de eficácia), com o motivo
    for n in (1169, 1179, 1204):
        cat, sit, motivo, lei = classificar(mp(n, sit=None))
        igual((cat, sit, motivo, lei), ("perdeu_eficacia", None, None, None), f"MPV {n}/2023 sem situação usa a deliberação")
    igual(classificar(mp(1, sit=None, deli=None))[0], "sem_situacao", "sem situação e sem deliberação: —")
    igual(classificar(mp(2, sit="MATÉRIA COM A RELATORIA", deli=None, tram="Sim"))[:2], ("em_tramitacao", "MATÉRIA COM A RELATORIA"),
          "tramitando: em tramitação, com a situação oficial")
    # 2. convertida exige a lei; a lei da lista tem de ser a do detalhe
    c = mp(3, sit="TRANSFORMADA EM NORMA JURÍDICA", deli="APROVADO_PLV", norma="Lei nº 15.000 de 06/03/2024")
    igual(classificar(c, det(3))[0], "convertida", "convertida com lei")
    igual(classificar(c, {"id": 1003, "normaGerada": None})[2] is not None, True, "convertida sem lei fica retida")
    igual(classificar(c, det(3, lei=15001))[2] is not None, True, "lei da lista diferente da do detalhe: retida")
    igual(classificar(mp(4, sit="REMETIDA À SANÇÃO", deli="APROVADO_PLV"), {"id": 1004})[0], "aprovada_sem_lei", "aprovada sem lei registrada")
    # 3. valor novo na fonte para a rodada
    for kw in ({"sit": "ARQUIVADA PELO FIM DO MUNDO"}, {"sit": None, "deli": "DELIBERACAO_NOVA"}):
        try:
            classificar(mp(5, **kw))
            igual(True, False, "valor desconhecido devia parar")
        except FonteFalhou:
            ok += 1
    # 4. montar + checagens: contagem por ano contra a Câmara, retida fora das contagens, soma por situação
    mand = {"id": "lula3", "rotulo": "Lula III", "presidente": "Lula", "inicio": "2023-01-01", "fim": None, "partido": ["PT"], "nota": None,
            "fonte": "teste"}
    sen = {2023: [mp(10), mp(11, sit=None), c | {"identificacao": "MPV 3/2023"}]}
    cam = {2023: [{"id": 9, "numero": "10", "ano": 2023}, {"id": 8, "numero": "11", "ano": 2023}, {"id": 7, "numero": "3", "ano": 2023}]}
    s = montar(mand, sen, cam, {1003: {"id": 1003, "normaGerada": None}})
    igual((s["mps"]["total"], len(s["mps"]["retidas"]), s["mps"]["contagem"]["perdeu_eficacia"]), (2, 1, 2), "retida sai da lista e das contagens")
    igual(checagens(s), [], "checagens passam com a retida declarada")
    igual(s["mps"]["itens"][0]["camaraId"] in (8, 9), True, "id da Câmara pelo número exato")
    s2 = montar(mand, sen, {2023: cam[2023][:2]}, {1003: det(3)})
    igual(any("contagem por ano não bate" in e for e in checagens(s2)), True, "Câmara com outro total: a rodada para")
    # 5. variação de mais de 10% contra o publicado
    pub = {"_versao": VERSAO, "mandato": {"id": "lula3"}, "mps": {"total": 100}}
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 89}}, pub) != [], True, "queda de 11%: para")
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 111}}, pub) != [], True, "subida de 11%: para")
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 105}}, pub), [], "5%: segue")
    igual(variacao({"mandato": {"id": "outro"}, "mps": {"total": 5}}, pub), [], "outro mandato: não compara")
    # 6. esquema sem campos extras
    s = montar(mand, sen, cam, {1003: det(3)})
    igual(validar_esquema(s), [], "a saída passa no esquema")
    extra = json.loads(serializar(s))
    extra["mps"]["itens"][0]["placar"] = 1
    igual(bool(validar_esquema(extra)), True, "campo extra é recusado pelo esquema")
    igual(json.loads(serializar(s)) == json.loads(json.dumps(s, ensure_ascii=False)), True, "a gravação uma por linha é o mesmo JSON")
    # 7. palavras proibidas: nos textos da saída e na página
    igual(proibidos(s), [], "textos da saída sem derrota/vitória/culpa")
    igual(bool(proibidos(s | {"_x": "uma vitória do governo"})), True, "palavra proibida é pega")
    if TEXTO_PAGINA.exists():                          # a página inteira: texto fixo, rótulos e os textos que o script dela escreve
        m = PROIBIDAS.search(TEXTO_PAGINA.read_text(encoding="utf-8"))
        igual(m and m[0], None, "presidente.html sem derrota, vitória ou culpa")
    # 8. mandato a partir de governos.json; 2099 é marcador, não data
    with tempfile.TemporaryDirectory() as t:
        g = Path(t) / "g.json"
        g.write_text(json.dumps({"governos": [{"id": "x", "rotulo": "X", "presidente": "P", "inicio": "2023-01-01", "fim": "2099-12-31",
                                              "partidoPresidente": [{"sigla": "AA"}]}]}), encoding="utf-8")
        mm = carregar_mandato(date(2026, 11, 7), g)
        igual((mm["fim"], mm["partido"]), (None, ["AA"]), "mandato em curso: fim desconhecido, partido da tabela")
        igual(anos_do_mandato(mm, date(2026, 11, 7)), [2023, 2024, 2025, 2026], "anos do mandato até hoje")
        try:
            carregar_mandato(date(2020, 1, 1), g)
            igual(True, False, "sem governo na data devia parar")
        except FonteFalhou:
            ok += 1
    # 9. erro da fonte nunca vira "sem MPs": a rodada inteira contra uma API simulada que falha; o publicado fica intacto
    with tempfile.TemporaryDirectory() as t:
        T = Path(t)
        pub = T / "presidencia.json"
        pub.write_text('{"intacto":1}', encoding="utf-8")
        respostas = {"lista_vazia": [], "formato": [{"identificacao": "X"}], "http": FonteFalhou("HTTP 503")}
        for nome, resp in respostas.items():
            def falso(url, params=None, tentativas=3, resp=resp):
                if isinstance(resp, Exception):
                    raise resp
                if "camara" in url:
                    return {"dados": [{"id": 1, "numero": 1, "ano": params["ano"]}], "links": []}
                return resp if params.get("ano") == 2024 else [mp(1, params["ano"])]
            with mock.patch(f"{__name__}.pedir", falso), mock.patch(f"{__name__}.CACHE_P", T / f"cache_{nome}"), mock.patch(f"{__name__}.SAIDA", pub), \
                    mock.patch(f"{__name__}.CANDIDATO", T / "cand.json"), mock.patch(f"{__name__}.RELATORIO", T / "rel.txt"), \
                    mock.patch(f"{__name__}.AMOSTRA", T / "amostra.csv"), mock.patch(f"{__name__}.INTERVALO", 0):
                saiu = None
                try:
                    main(["--publicar"], hoje=date(2025, 6, 1))
                except SystemExit as e:
                    saiu = e.code
                igual((saiu not in (None, 0), pub.read_text(encoding="utf-8")), (True, '{"intacto":1}'), f"fonte com erro ({nome}): para e o publicado fica")
                igual(nome != "http" or not (T / f"cache_{nome}" / "mpv_senado_2024.json").exists(), True, "resposta com erro não vai para o cache")
    # 10. aprovada sem lei registrada vira "convertida" na rodada seguinte ao registro da lei: duas rodadas inteiras (main) contra uma API
    #     simulada, com o relógio andando 25 h; o detalhe guardado sem a lei NÃO segura a MP por 30 dias
    with tempfile.TemporaryDirectory() as t:
        T = Path(t)
        dia = [datetime(2023, 6, 1, 8, 0, tzinfo=BRASILIA)]
        estado = {"lista": mp(4, sit="REMETIDA À SANÇÃO", deli="APROVADO_PLV"), "det": {"id": 1004, "normaGerada": None}}
        pedidos_det = []

        def falso(url, params=None, tentativas=3):
            if "camara" in url:
                return {"dados": [{"id": 7, "numero": "4", "ano": 2023}, {"id": 8, "numero": "10", "ano": 2023}], "links": []}
            if url.endswith("/processo"):
                return [estado["lista"], mp(10)]
            pedidos_det.append(url)
            return estado["det"]
        pub = T / "presidencia.json"
        patches = [mock.patch(f"{__name__}.pedir", falso), mock.patch(f"{__name__}.CACHE_P", T / "cache"), mock.patch(f"{__name__}.SAIDA", pub),
                   mock.patch(f"{__name__}.CANDIDATO", T / "cand.json"), mock.patch(f"{__name__}.RELATORIO", T / "rel.txt"),
                   mock.patch(f"{__name__}.AMOSTRA", T / "amostra.csv"), mock.patch(f"{__name__}.INTERVALO", 0),
                   mock.patch(f"{__name__}.agora", lambda: dia[0])]
        for x in patches:
            x.start()
        try:
            def rodada():
                main(["--publicar"], hoje=dia[0].date())
                return next(x for x in json.loads(pub.read_text(encoding="utf-8"))["mps"]["itens"] if x["numero"] == 4)
            a = rodada()
            igual((a["situacao"], a["lei"]), ("aprovada_sem_lei", None), "dia 1: aprovada, aguardando a sanção, sem lei")
            # dia 2 (25 h depois): a lista passa a dizer que virou lei e o detalhe traz a lei
            dia[0] += timedelta(hours=25)
            estado["lista"] = mp(4, sit="TRANSFORMADA EM NORMA JURÍDICA", deli="APROVADO_PLV", norma="Lei nº 15.000 de 06/03/2024")
            estado["det"] = det(4)
            b = rodada()
            igual((b["situacao"], (b["lei"] or {}).get("numero")), ("convertida", 15000), "dia 2: convertida, com a lei do detalhe")
            igual(len(pedidos_det), 2, "o detalhe sem lei foi pedido de novo no dia seguinte")
            # dia 3: a lei já está no detalhe guardado, que vale 30 dias: nenhum pedido novo de detalhe
            dia[0] += timedelta(hours=25)
            rodada()
            igual(len(pedidos_det), 2, "detalhe com a lei não é pedido de novo antes de 30 dias")
            # outro caminho da fonte: a lista ainda diz "remetida à sanção", mas o detalhe já traz a lei
            estado["lista"] = mp(4, sit="REMETIDA À SANÇÃO", deli="APROVADO_PLV")
            dia[0] += timedelta(hours=25)
            c2 = rodada()
            igual(c2["situacao"], "convertida", "lista atrasada e detalhe com a lei: convertida")
        finally:
            for x in patches:
                x.stop()
    print(f"{ok} verificações passaram")


# ---------------------------------------------------------------- rodada

def main(argv=None, hoje=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="o padrão: candidato, relatório e amostra em cache/")
    modo.add_argument("--publicar", action="store_true", help="grava site/dados/presidencia.json (só se as checagens passarem)")
    ap.add_argument("--so-montar", action="store_true", help="sem rede: remonta do cache")
    ap.add_argument("--minutos", type=float, default=10, help="prazo da rodada (padrão 10)")
    ap.add_argument("--teste", action="store_true", help="casos sintéticos, sem rede")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    t0, PRAZO[0] = time.monotonic(), time.monotonic() + args.minutos * 60
    hoje = hoje or agora().date()
    pedidos = [0]
    original = pedir

    def contando(*a, **k):
        pedidos[0] += 1
        return original(*a, **k)
    try:
        globals()["pedir"] = contando
        mandato = carregar_mandato(hoje)
        anos = anos_do_mandato(mandato, hoje)
        print(f"Presidente: {mandato['rotulo']} desde {mandato['inicio']}; MPs de {anos[0]} a {anos[-1]}…", flush=True)
        sen = {a: lista_senado(a, args.so_montar) for a in anos}
        cam = {a: lista_camara(a, args.so_montar) for a in anos}
        aprovadas = [m for a in anos for m in sen[a] if m.get("tramitando") == "Não" and (
            (m.get("situacaoAtual") or "") in {s for s, c in POR_SITUACAO.items() if c in ("convertida", "aprovada_sem_lei")}
            or (not m.get("situacaoAtual") and POR_DELIBERACAO.get(m.get("siglaTipoDeliberacao") or "") == "aprovada_sem_lei"))]
        detalhes = {m["id"]: detalhe(m, args.so_montar) for m in aprovadas}
        saida = montar(mandato, sen, cam, detalhes)
    except FonteFalhou as e:
        sys.exit(f"fonte com erro: {e}. Nada publicado; site/dados/presidencia.json fica como estava.")
    finally:
        globals()["pedir"] = original
    publicado = coleta.ler_cache(SAIDA)
    erros = checagens(saida) + variacao(saida, publicado) + validar_esquema(saida)
    gravar(CANDIDATO, saida)
    coleta.gravar_texto(AMOSTRA, amostra(saida), newline="\n")
    texto = relatorio(saida, erros, args, time.monotonic() - t0, pedidos[0])
    coleta.gravar_texto(RELATORIO, texto + "\n", newline="\n")
    print(texto)
    if erros:
        sys.exit(f"checagem falhou ({len(erros)}): nada publicado")
    if args.publicar:
        gravar(SAIDA, saida)
        print(f"site/dados/presidencia.json publicado: {saida['mps']['total']} MPs.")
    else:
        print(f"dry-run: candidato em {CANDIDATO}, amostra em {AMOSTRA}")


if __name__ == "__main__":
    main()
