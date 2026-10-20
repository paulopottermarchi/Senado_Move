"""
Câmara Aberta — "Para onde o Brasil exporta e o que exporta" (site/dados/comex/*.json).

Uso:
    python scripts/comex.py                 # no máximo uma vez por semana: baixa, confere e grava
    python scripts/comex.py --forcar        # ignora o intervalo de uma semana
    python scripts/comex.py --so-montar     # não chama a API: remonta os JSON do cache bruto (cache/comex/)
    python scripts/comex.py --simular       # baixa e confere, mas não grava nada
    python scripts/comex.py --imprimir      # além de gravar, imprime totais, os 10 maiores e as frases de dois anos
    python scripts/comex.py --teste         # testes sintéticos, sem rede

Fonte: Comex Stat (MDIC), API pública https://api-comexstat.mdic.gov.br (documentação em /docs e /docs/doc.yaml).
Dados gerais de exportação de 1997 até o último mês completo, em valor US$ FOB (dólares correntes: nem deflacionado nem em reais).
Dois modos, por ano: por DESTINO (país, nome da tabela oficial) e por PRODUTO (posição do Sistema Harmonizado, SH4, com o nome da
tabela oficial). Nenhuma categoria é criada aqui: cada item é um país ou uma posição SH4 como o MDIC os registra.

Regras (CLAUDE.md, "Exportações"):
  * "valor exportado (US$ FOB)", nunca "lucro", "ganho" ou "receita"; sem julgamento ("dependência", "risco"…) e sem ligar a lei, governo ou partido;
  * ano corrente = só os meses fechados, com o período escrito (jan–set/2026); a comparação com outro ano usa os MESMOS meses;
  * cada rodada baixa de novo TODOS os anos (a fonte revisa o passado); nada é gravado vazio por cima de dado bom;
  * checagens que ABORTAM sem gravar: total por país ≠ total do período; soma por produto fora da tolerância do total; valor negativo;
    mês faltando (ano fechado) ou a mais; unidade da métrica diferente; ano faltando na sequência; palavra de julgamento no texto.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coleta
from coleta import CACHE, DADOS, ler_cache, gravar_cache
from contexto_trabalho import Aborta, PROIBIDAS as PROIBIDAS_ECONOMIA, br, checar_textos as checar_proibidas

SAIDA = DADOS / "comex"
CACHE_C = CACHE / "comex"
API = "https://api-comexstat.mdic.gov.br"
SITE = "https://comexstat.mdic.gov.br/"
BRASILIA = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRASILIA).date()
VERSAO = "comex-1"

INTERVALO = 3.0            # segundos entre pedidos: a API devolve 429 ("tente novamente em 10 segundos") depois de poucos pedidos seguidos
ESPERA_429 = 12            # segundos de espera depois de um 429
TENTATIVAS = 8
SEMANAL = 7                # dias entre rodadas: os dados mudam por mês, então uma consulta por semana basta
TOLERANCIA_SH4 = 1e-5      # a soma por posição SH4 pode ficar abaixo do total (a consulta por NCM soma o total exato, a por SH4 fica um pouco abaixo; a fonte não explica): ver `residuo`
MIN_PAISES, MIN_PRODUTOS = 100, 500
MES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
UNIDADE = "US$ FOB (dólares correntes)"

# Texto proibido (além do que já vale para a página de economia): nada de julgamento sobre quem compra
PROIBIDAS = PROIBIDAS_ECONOMIA + [r"depend[êe]ncia", r"depende", r"\bref[ée](?:m|ns)\b",r"\bperigo", r"fr[áa]gil", r"fragilidade", r"\blucro", r"\bganho",
                                  r"receita", r"\bdominan", r"concentra[çc][ãa]o excessiva"]


# ------------------------------------------------------------------------------------------------ HTTP
_ultimo = [0.0]


def pedir(metodo, caminho, params=None, corpo=None):
    """Um pedido à API com espera entre pedidos, repetição em 429, 5xx, falha de rede e corpo que não é JSON.
    Devolve o campo `data` (a API embrulha tudo em {data, success, message})."""
    for n in range(TENTATIVAS):
        espera = _ultimo[0] + INTERVALO - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        _ultimo[0] = time.monotonic()
        try:
            r = coleta.sessao.request(metodo, API + caminho, params=params, json=corpo, timeout=180)
        except (coleta.requests.ConnectionError, coleta.requests.Timeout):
            time.sleep(2 ** n)
            continue
        if r.status_code == 429:
            time.sleep(ESPERA_429 * (n + 1))
            continue
        if r.status_code >= 500:
            time.sleep(2 ** n)
            continue
        if r.status_code >= 400:
            raise Aborta(f"a API recusou o pedido ({r.status_code}): {caminho} {json.dumps(corpo, ensure_ascii=False)[:200]} → {r.text[:200]}")
        try:
            j = r.json()
        except ValueError:
            time.sleep(2 ** n)
            continue
        if not j.get("success", True):
            raise Aborta(f"a API respondeu success=false: {caminho} {j.get('message')}")
        return j["data"]
    raise Aborta(f"a API não respondeu depois de {TENTATIVAS} tentativas: {caminho}")


def consultar(ano, mes_fim, detalhes, mensal=False):
    """Exportação, valor US$ FOB, de jan a `mes_fim` do ano, agrupada por `detalhes` (country, heading) ou só o total."""
    corpo = {"flow": "export", "monthDetail": mensal, "period": {"from": f"{ano}-01", "to": f"{ano}-{mes_fim:02d}"},
             "filters": [], "details": detalhes, "metrics": ["metricFOB"]}
    return pedir("POST", "/general", {"language": "pt"}, corpo)["list"]


# ------------------------------------------------------------------------------------------------ coleta
def meta_da_fonte():
    """Datas e unidade da fonte: última atualização (ano e último mês completo), primeiro e último ano, e o nome da métrica."""
    up = pedir("GET", "/general/dates/updated")
    anos = pedir("GET", "/general/dates/years")
    mets = pedir("GET", "/general/metrics", {"language": "pt"})["list"]
    fob = next((m for m in mets if m["id"] == "metricFOB"), None)
    if not fob or fob["text"] != "Valor US$ FOB":
        raise Aborta(f"a métrica metricFOB mudou na fonte: {fob!r} (esperado 'Valor US$ FOB')")
    return {"atualizadoEm": up["updated"], "ano": int(up["year"]), "mesFinal": int(up["monthNumber"]),
            "primeiroAno": int(anos["min"]), "ultimoAno": int(anos["max"]), "metrica": fob["text"]}


def coletar_ano(ano, mes_fim, atualizado):
    """Os três pedidos de um ano: total por mês, por país e por posição SH4. Reaproveita o que foi baixado hoje
    (uma rodada interrompida retoma de onde parou), desde que a fonte e o último mês sejam os mesmos."""
    arq = CACHE_C / "bruto" / f"{ano}.json"
    c = ler_cache(arq)
    if c and c.get("baixadoEm") == HOJE.isoformat() and c.get("atualizadoEm") == atualizado and c.get("mesFinal") == mes_fim:
        return c
    c = {"ano": ano, "mesFinal": mes_fim, "atualizadoEm": atualizado, "baixadoEm": HOJE.isoformat(),
         "mensal": consultar(ano, mes_fim, [], mensal=True),
         "paises": consultar(ano, mes_fim, ["country"]),
         "produtos": consultar(ano, mes_fim, ["heading"])}
    gravar_cache(arq, c)
    return c


# ------------------------------------------------------------------------------------------------ montagem e checagens
def rotulo_periodo(ano, meses):
    return str(ano) if meses == 12 else f"jan–{MES[meses - 1]}/{ano}" if meses > 1 else f"jan/{ano}"


def dinheiro(v):
    """US$ 348,3 bilhões · US$ 523 milhões (valor em dólares)."""
    if abs(v) >= 1e9:
        return f"US$ {br(v / 1e9, 1)} bilhões"
    return f"US$ {br(v / 1e6, 0)} milhões"


def pct(v, total):
    return f"{br(v / total * 100, 1)}%"


def inteiro(x, onde):
    try:
        return int(x)
    except (TypeError, ValueError):
        raise Aborta(f"{onde}: valor que não é inteiro: {x!r}")


def montar_ano(c, fechado, anterior_mensal):
    """Os dois arquivos de um ano, conferidos. `anterior_mensal`: {mês: valor} do ano anterior (ou None)."""
    ano, mes_fim = c["ano"], c["mesFinal"]
    meses = 12 if fechado else mes_fim
    mensal = {}
    for x in c["mensal"]:
        m = inteiro(x["monthNumber"], f"{ano} mensal")
        if m in mensal:
            raise Aborta(f"{ano}: mês {m} repetido no total mensal")
        mensal[m] = inteiro(x["metricFOB"], f"{ano}-{m:02d}")
    if sorted(mensal) != list(range(1, meses + 1)):
        raise Aborta(f"{ano}: meses do total mensal {sorted(mensal)} (esperado 1 a {meses}{'; ano fechado' if fechado else ''})")
    total = sum(mensal.values())
    # países
    paises = [{"nome": x["country"], "valor": inteiro(x["metricFOB"], f"{ano} {x['country']}")} for x in c["paises"]]
    nomes = [p["nome"] for p in paises]
    if len(set(nomes)) != len(nomes):
        raise Aborta(f"{ano}: país repetido na tabela por país")
    if len(paises) < MIN_PAISES:
        raise Aborta(f"{ano}: só {len(paises)} países (esperado {MIN_PAISES} ou mais)")
    soma_p = sum(p["valor"] for p in paises)
    if soma_p != total:
        raise Aborta(f"{ano}: o total por país ({soma_p}) não bate com o total do período ({total}); diferença {total - soma_p}")
    # produtos (SH4)
    produtos = [{"codigo": str(x["headingCode"]), "nome": x["heading"], "valor": inteiro(x["metricFOB"], f"{ano} SH4 {x['headingCode']}")}
                for x in c["produtos"]]
    codigos = [p["codigo"] for p in produtos]
    if len(set(codigos)) != len(codigos):
        raise Aborta(f"{ano}: posição SH4 repetida")
    if len(produtos) < MIN_PRODUTOS:
        raise Aborta(f"{ano}: só {len(produtos)} posições SH4 (esperado {MIN_PRODUTOS} ou mais)")
    soma_h = sum(p["valor"] for p in produtos)
    residuo = total - soma_h
    if residuo < 0 or residuo > TOLERANCIA_SH4 * total:
        raise Aborta(f"{ano}: a soma por posição SH4 ({soma_h}) difere do total ({total}) em {residuo} (tolerância {TOLERANCIA_SH4 * total:.0f})")
    for lista, que in ((paises, "país"), (produtos, "produto")):
        neg = [i["nome"] for i in lista if i["valor"] < 0]
        if neg:
            raise Aborta(f"{ano}: valor negativo em {que}: {neg[:3]}")
    if total <= 0 or any(v < 0 for v in mensal.values()):
        raise Aborta(f"{ano}: total ou mês negativo/zero")
    paises.sort(key=lambda p: (-p["valor"], p["nome"]))
    produtos.sort(key=lambda p: (-p["valor"], p["codigo"]))
    # comparação com os MESMOS meses do ano anterior (ano fechado: o ano anterior inteiro)
    comp = None
    if anterior_mensal and all(m in anterior_mensal for m in range(1, meses + 1)):
        t0 = sum(anterior_mensal[m] for m in range(1, meses + 1))
        comp = {"ano": ano - 1, "meses": meses, "periodo": rotulo_periodo(ano - 1, meses), "total": t0, "variacaoPct": round((total / t0 - 1) * 100, 2)}
    per = {"de": f"{ano}-01", "ate": f"{ano}-{meses:02d}", "meses": meses, "rotulo": rotulo_periodo(ano, meses)}
    base = {"ano": ano, "fechado": fechado, "periodo": per, "unidade": UNIDADE, "total": total,
            "mensal": [{"mes": m, "valor": mensal[m]} for m in sorted(mensal)], "comparacao": comp}
    return base, paises, produtos, residuo


def frase(modo, base, itens):
    """A abertura de cada ano e modo: só fato (valor e participação), nenhuma conclusão. Sem preposição antes do nome: os nomes da tabela do
    MDIC não trazem artigo ("China", "Estados Unidos"), e inventar o gênero de cada um seria uma tabela nossa."""
    per, total = base["periodo"]["rotulo"], base["total"]
    p1, p2, p3 = itens[0], itens[1], itens[2]
    quem = "o destino registrado com maior valor exportado" if modo == "destino" else "o produto de maior valor exportado"
    a = (f"Em {per}, {quem} foi {p1['nome']}, com {pct(p1['valor'], total)} do valor exportado "
         f"({dinheiro(p1['valor'])}), seguido de {p2['nome']}, com {pct(p2['valor'], total)}, e {p3['nome']}, com {pct(p3['valor'], total)}.")
    b = f" O total foi de {dinheiro(total)}, em dólares correntes (valor FOB)"
    comp = base["comparacao"]
    if comp:
        mesmo = "no mesmo período de" if base["periodo"]["meses"] != 12 else "em"
        b += (f"; {mesmo} {comp['periodo'] if mesmo.startswith('no') else comp['ano']}, de {dinheiro(comp['total'])} "
              f"(variação de {br(comp['variacaoPct'], 1, sinal=True)}%).")
    else:
        b += "."
    return a + b


def textos(arquivo):
    return [arquivo["frase"], arquivo["periodo"]["rotulo"], arquivo["unidade"]] + [i["nome"] for i in arquivo["itens"]]


def checar_textos(lista):
    checar_proibidas(lista, PROIBIDAS)


def montar_tudo(brutos, meta):
    """Todos os anos, conferidos de uma vez: ou tudo passa ou nada é gravado."""
    anos = sorted(brutos)
    if anos != list(range(anos[0], anos[-1] + 1)):
        raise Aborta(f"anos faltando na sequência: {anos}")
    if anos[0] != meta["primeiroAno"] or anos[-1] != meta["ano"]:
        raise Aborta(f"os anos coletados ({anos[0]}–{anos[-1]}) não são os que a fonte declara ({meta['primeiroAno']}–{meta['ano']})")
    saidas, indice_anos = {}, []
    anterior = None
    for ano in anos:
        fechado = ano < meta["ano"] or meta["mesFinal"] == 12
        base, paises, produtos, residuo = montar_ano(brutos[ano], fechado, anterior)
        fonte = {"nome": "Comex Stat, Ministério do Desenvolvimento, Indústria, Comércio e Serviços (MDIC)", "url": SITE, "api": API + "/general",
                 "documentacao": API + "/docs", "metrica": meta["metrica"]}
        d = dict(base, modo="destino", itens=paises, fonte=fonte)
        p = dict(base, modo="produto", itens=produtos, residuo=residuo, nivelProduto="Posição do Sistema Harmonizado (SH4)", fonte=fonte)
        d["frase"], p["frase"] = frase("destino", base, paises), frase("produto", base, produtos)
        for a in (d, p):
            checar_textos(textos(a))      # a data em que a fonte atualizou fica só no índice: no arquivo do ano ela faria o git regravar os 60 arquivos todo mês
        saidas[ano] = (d, p)
        indice_anos.append({"ano": ano, "fechado": fechado, "meses": base["periodo"]["meses"], "rotulo": base["periodo"]["rotulo"], "total": base["total"]})
        anterior = {m["mes"]: m["valor"] for m in base["mensal"]}
    return saidas, indice_anos


# ------------------------------------------------------------------------------------------------ gravação
def gravar_json(caminho, obj, linhas=()):
    """Escrita atômica; `linhas`: chaves cujas listas saem um item por linha (diff diário legível no git)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    partes = [f"{dump(k)}:{dump(v)}" for k, v in obj.items() if k not in linhas]
    partes += [f'{dump(k)}:[\n' + ",\n".join(dump(x) for x in obj[k]) + "\n]" for k in linhas if k in obj]
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text("{" + ",\n".join(partes) + "}\n", encoding="utf-8")
    os.replace(tmp, caminho)


def sem_data(obj):
    return {k: v for k, v in obj.items() if k != "coletadoEm"}


def gravar_tudo(saidas, indice_anos, meta, coletado):
    """Um arquivo por ano e modo. O `coletadoEm` de cada arquivo só muda quando os números mudam (o git não ganha 60 arquivos por semana);
    a data da última conferência com a fonte vai no índice. `coletado` é a data em que os dados saíram da API (a do cache bruto), não a de hoje:
    com --so-montar nada foi baixado, e a página não pode dizer "conferido hoje"."""
    mudou = novos = 0
    for ano, (d, p) in saidas.items():
        for modo, a in (("destino", d), ("produto", p)):
            caminho = SAIDA / f"{modo}-{ano}.json"
            antigo = ler_cache(caminho)
            if antigo and sem_data(antigo) == sem_data(dict(a, coletadoEm=antigo.get("coletadoEm"))):
                continue
            a["coletadoEm"] = coletado
            gravar_json(caminho, a, linhas=("itens",))
            novos += antigo is None
            mudou += antigo is not None
    fechados = [x["ano"] for x in indice_anos if x["fechado"]]
    indice = {"versao": VERSAO, "verificadoEm": coletado, "atualizadoEmFonte": meta["atualizadoEm"], "primeiroAno": indice_anos[0]["ano"],
              "ultimoAno": indice_anos[-1]["ano"], "anoPadrao": fechados[-1], "anos": indice_anos, "unidade": UNIDADE,
              "limiarDemais": 0.005,
              "fonte": {"nome": "Comex Stat (MDIC)", "url": SITE, "api": API + "/general", "documentacao": API + "/docs"},
              "historico": "Os dados gerais do Comex Stat começam em 1997. Os de 1989 a 1996 usam outra metodologia e a nomenclatura NBM, e não entram."}
    gravar_json(SAIDA / "indice.json", indice, linhas=("anos",))
    return novos, mudou


def pode_rodar(forcar):
    est = ler_cache(CACHE_C / "estado.json") or {}
    ultima = est.get("ultima")
    if forcar or not ultima:
        return True, None
    dias = (HOJE - date.fromisoformat(ultima)).days
    return dias >= SEMANAL, dias


def imprimir(saidas, anos):
    milhar = lambda n: f"{n:,}".replace(",", ".")        # só o NÚMERO: o nome do produto traz vírgula
    for ano in anos:
        d, p = saidas[ano]
        print()
        print(f"=== {d['periodo']['rotulo']} — total {milhar(d['total'])} {UNIDADE} ({'ano fechado' if d['fechado'] else 'ano corrente: só meses fechados'})")
        for nome, a in (("DESTINO", d), ("PRODUTO (SH4)", p)):
            print(f"  10 maiores — {nome}  ({len(a['itens'])} itens)")
            for i, it in enumerate(a["itens"][:10], 1):
                print(f"   {i:>2}. {it['nome'][:78]:<78} {milhar(it['valor']):>16}  {it['valor'] / a['total'] * 100:5.2f}%")
            print(f"  frase: {a['frase']}")
        print(f"  residuo SH4 (total − soma dos produtos): {milhar(p['residuo'])}")


# ------------------------------------------------------------------------------------------------ testes
def testar():
    ok = 0

    def igual(a, b, m):
        nonlocal ok
        assert a == b, f"{m}: {a!r} != {b!r}"
        ok += 1

    igual(dinheiro(348_278_462_500), "US$ 348,3 bilhões", "bilhões")
    igual(dinheiro(523_400_000), "US$ 523 milhões", "milhões")
    igual(rotulo_periodo(2025, 12), "2025", "ano inteiro")
    igual(rotulo_periodo(2026, 9), "jan–set/2026", "ano corrente")

    def bruto(ano, meses, meses_cheios=None):
        mm = [{"year": str(ano), "monthNumber": f"{m:02d}", "metricFOB": str(1000 + m)} for m in (meses_cheios or range(1, meses + 1))]
        tot = sum(int(x["metricFOB"]) for x in mm)
        paises = [{"year": str(ano), "country": f"País {i}", "metricFOB": str(tot // 150 + (1 if i < tot % 150 else 0))} for i in range(150)]
        prods = [{"year": str(ano), "headingCode": f"{1000 + i}", "heading": f"Produto {i}", "metricFOB": str(tot // 600 + (1 if i < tot % 600 else 0))} for i in range(600)]
        return {"ano": ano, "mesFinal": meses, "mensal": mm, "paises": paises, "produtos": prods}
    base, pa, pr, res = montar_ano(bruto(2024, 12), True, None)
    igual((base["total"], res, len(pa), len(pr)), (sum(1000 + m for m in range(1, 13)), 0, 150, 600), "ano fechado montado")
    # o ano seguinte compara com os MESMOS meses
    b2, pa2, pr2, _ = montar_ano(bruto(2025, 9), False, {m["mes"]: m["valor"] for m in base["mensal"]})
    igual((b2["comparacao"]["meses"], b2["comparacao"]["periodo"]), (9, "jan–set/2024"), "comparação usa os mesmos meses")
    igual(b2["comparacao"]["total"], sum(1000 + m for m in range(1, 10)), "total do mesmo período do ano anterior, não do ano inteiro")
    f = frase("destino", b2, pa2)
    igual("no mesmo período de jan–set/2024" in f, True, "a frase diz que a comparação é do mesmo período")
    checar_textos(textos(dict(frase=f, periodo=b2["periodo"], unidade=UNIDADE, itens=pa2)))
    ok += 1

    def deve_abortar(msg, **mexe):
        nonlocal ok
        b = bruto(2024, 12)
        for k, v in mexe.items():
            v(b)
        try:
            montar_ano(b, True, None)
        except Aborta:
            ok += 1
            return
        raise AssertionError(f"deveria abortar: {msg}")
    deve_abortar("mês faltando em ano fechado", m=lambda b: b["mensal"].pop(5))
    deve_abortar("total por país diferente", p=lambda b: b["paises"][0].update(metricFOB=str(int(b["paises"][0]["metricFOB"]) + 1)))
    deve_abortar("soma por produto fora da tolerância", p=lambda b: b["produtos"].pop(0))
    deve_abortar("valor negativo", p=lambda b: (b["paises"][0].update(metricFOB="-5"), b["paises"][1].update(metricFOB=str(int(b["paises"][1]["metricFOB"]) + 5 + int(b["paises"][0]["metricFOB"])))))
    deve_abortar("país repetido", p=lambda b: b["paises"][1].update(country=b["paises"][0]["country"]))
    for palavra in ("A dependência do mercado", "risco de concentração", "um destino vulnerável", "lucro das empresas", "refém de um só comprador"):
        try:
            checar_textos([palavra])
            raise AssertionError(f"deveria abortar: {palavra}")
        except Aborta:
            ok += 1
    checar_textos(["Em 2025, o destino registrado com maior valor exportado foi China, com 28,7% do valor exportado."])
    ok += 1
    # o texto fixo da página
    html = (Path(__file__).resolve().parent.parent / "site" / "contexto.html")
    if html.exists():
        t = html.read_text(encoding="utf-8")
        if 'id="exportacoes"' in t:
            ini = t.index('id="exportacoes"')
            bloco = t[ini:t.index('id="indicadores"', ini)]
            js = t[t.index("/* ===================================================================== exportações"):t.index("/* ===================================================================== início")]
            trechos = [a or b for a, b in re.findall(r"`([^`]*)`|'([^'\n]{12,})'", js)]
            estatico = re.sub(r"\$\{[^}]*\}", " ", re.sub(r"<[^>]+>", " ", bloco) + " " + " ".join(trechos))
            igual(len(estatico) > 1500, True, "o texto fixo da página foi lido")
            checar_textos([estatico])
            ok += 1
    print(f"{ok} verificações passaram")


# ------------------------------------------------------------------------------------------------ principal
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so-montar", action="store_true")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--imprimir", action="store_true")
    ap.add_argument("--teste", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    rede = not args.so_montar
    if rede:
        pode, dias = pode_rodar(args.forcar)
        if not pode:
            print(f"Comex Stat: última coleta há {dias} dia(s); os dados mudam por mês e a rodada é semanal. Nada a fazer (use --forcar).")
            return
    try:
        if rede:
            meta = meta_da_fonte()
            print(f"Comex Stat: dados de {meta['primeiroAno']} a {meta['ano']}, último mês completo {meta['mesFinal']:02d}/{meta['ano']}, "
                  f"atualizado pela fonte em {meta['atualizadoEm']}", flush=True)
            gravar_cache(CACHE_C / "meta.json", meta)
            brutos = {}
            t0 = time.time()
            for ano in range(meta["primeiroAno"], meta["ano"] + 1):
                mes_fim = 12 if ano < meta["ano"] else meta["mesFinal"]
                brutos[ano] = coletar_ano(ano, mes_fim, meta["atualizadoEm"])
                print(f"  {ano}: ok ({time.time() - t0:.0f}s)", flush=True)
        else:
            meta = ler_cache(CACHE_C / "meta.json")
            if not meta:
                raise Aborta("--so-montar sem cache: rode comex.py antes")
            brutos = {}
            for ano in range(meta["primeiroAno"], meta["ano"] + 1):
                c = ler_cache(CACHE_C / "bruto" / f"{ano}.json")
                if not c:
                    raise Aborta(f"--so-montar: falta o cache de {ano}")
                brutos[ano] = c
        saidas, indice_anos = montar_tudo(brutos, meta)
    except Aborta as e:
        print(f"ABORTADO (nada gravado; os arquivos anteriores ficam): {e}")
        sys.exit(1)
    if args.simular:
        print("--simular: conferido, nada gravado.")
    else:
        novos, mudou = gravar_tudo(saidas, indice_anos, meta, max(c["baixadoEm"] for c in brutos.values()))
        if rede:
            gravar_cache(CACHE_C / "estado.json", {"ultima": HOJE.isoformat()})
        print(f"Comex Stat: {len(indice_anos)} anos conferidos; {novos} arquivo(s) novo(s), {mudou} com números diferentes dos anteriores; índice atualizado.")
    if args.imprimir:
        fech = [a["ano"] for a in indice_anos if a["fechado"]]
        imprimir(saidas, [fech[-2], fech[-1]] + ([indice_anos[-1]["ano"]] if not indice_anos[-1]["fechado"] else []))


if __name__ == "__main__":
    main()
