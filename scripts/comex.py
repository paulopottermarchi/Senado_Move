"""
Câmara Aberta — "Para onde o Brasil exporta e o que exporta" (site/dados/comex/*.json).

Uso:
    python scripts/comex.py                 # no máximo uma vez por semana: baixa, confere e grava
    python scripts/comex.py --forcar        # ignora o intervalo de uma semana
    python scripts/comex.py --so-montar     # não chama a API: remonta os JSON do cache bruto (cache/comex/)
    python scripts/comex.py --reaproveitar  # desenvolvimento: aceita o cache bruto de qualquer dia se a fonte e o último mês forem os mesmos
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
from contexto_trabalho import Aborta, MENOS, PROIBIDAS as PROIBIDAS_ECONOMIA, br, checar_textos as checar_proibidas

SAIDA = DADOS / "comex"
CACHE_C = CACHE / "comex"
API = "https://api-comexstat.mdic.gov.br"
SITE = "https://comexstat.mdic.gov.br/"
BRASILIA = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRASILIA).date()
VERSAO = "comex-1"

INTERVALO = 6.0            # segundos entre pedidos: a API (atrás do Cloudflare) devolve 429 ("tente novamente em 10 segundos") e, depois de muitos pedidos seguidos, continua devolvendo
ESPERA_429 = 15            # segundos de espera depois de um 429 (mais 5 por tentativa)
TENTATIVAS = 8
BLOCO_ANOS = 15            # anos por pedido mensal (180 linhas). ATENÇÃO: a API aplica o intervalo de meses a CADA ano do pedido (30 anos até 2026-09 = 30 × 9 = 270 linhas, não 357): bloco só com anos inteiros
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
            time.sleep(ESPERA_429 + 5 * n)
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


def consultar(ano, mes_fim, detalhes, mensal=False, fluxo="export"):
    """Exportação (ou importação, `fluxo="import"`), valor US$ FOB, de jan a `mes_fim` do ano, agrupada por `detalhes` (country, heading) ou só o total.
    Um ano por pedido: com mais de um, a API aplica o intervalo de meses a cada ano."""
    corpo = {"flow": fluxo, "monthDetail": mensal, "period": {"from": f"{ano}-01", "to": f"{ano}-{mes_fim:02d}"},
             "filters": [], "details": detalhes, "metrics": ["metricFOB"]}
    return pedir("POST", "/general", {"language": "pt"}, corpo)["list"]


def consultar_mensal(fluxo, ano_ini, ano_fim, mes_fim):
    """Total mensal (US$ FOB) de jan a `mes_fim` de CADA ano de `ano_ini` a `ano_fim` (a API repete o intervalo de meses em todos os anos). Cada linha traz `year` e `monthNumber`."""
    corpo = {"flow": fluxo, "monthDetail": True, "period": {"from": f"{ano_ini}-01", "to": f"{ano_fim}-{mes_fim:02d}"},
             "filters": [], "details": [], "metrics": ["metricFOB"]}
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


def cache_vale(c, atualizado, mes_fim, reaproveitar):
    """O cache bruto serve se foi baixado hoje (ou de qualquer dia, com --reaproveitar) e a fonte e o último mês são os mesmos."""
    return bool(c) and (reaproveitar or c.get("baixadoEm") == HOJE.isoformat()) and c.get("atualizadoEm") == atualizado and c.get("mesFinal") == mes_fim


def coletar_ano(ano, mes_fim, atualizado, reaproveitar=False):
    """Os dois pedidos de um ano que dependem do ano: valor por país e por posição SH4. Reaproveita o que foi baixado hoje (uma rodada interrompida
    retoma de onde parou), desde que a fonte e o último mês sejam os mesmos. Os totais mensais vêm de `coletar_mensais`, em blocos de anos."""
    arq = CACHE_C / "bruto" / f"{ano}.json"
    c = ler_cache(arq)
    if cache_vale(c, atualizado, mes_fim, reaproveitar):
        return c
    c = {"ano": ano, "mesFinal": mes_fim, "atualizadoEm": atualizado, "baixadoEm": HOJE.isoformat(),
         "paises": consultar(ano, mes_fim, ["country"]), "produtos": consultar(ano, mes_fim, ["heading"])}
    gravar_cache(arq, c)
    return c


def coletar_mensais(meta, reaproveitar=False):
    """O total mensal de exportação e de importação de todos os anos: os anos fechados (jan–dez) em blocos de BLOCO_ANOS anos e o ano corrente (jan até o último mês completo) à parte,
    porque a API repete o intervalo de meses em todos os anos do pedido. {baixadoEm, export: {ano: linhas}, import: {ano: linhas}}."""
    arq = CACHE_C / "bruto" / "mensais.json"
    c = ler_cache(arq)
    if cache_vale(c, meta["atualizadoEm"], meta["mesFinal"], reaproveitar):
        return c
    c = {"atualizadoEm": meta["atualizadoEm"], "mesFinal": meta["mesFinal"], "baixadoEm": HOJE.isoformat(), "export": {}, "import": {}}
    for fluxo in ("export", "import"):
        pedidos = [(ini, min(ini + BLOCO_ANOS - 1, meta["ano"] - 1), 12) for ini in range(meta["primeiroAno"], meta["ano"], BLOCO_ANOS)]
        pedidos.append((meta["ano"], meta["ano"], meta["mesFinal"]))
        for ini, fim, mes in pedidos:
            for x in consultar_mensal(fluxo, ini, fim, mes):
                c[fluxo].setdefault(str(int(x["year"])), []).append(x)
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


def mensal_de(lista, onde, meses, fechado=True):
    """Os valores mensais de uma resposta da API, na ordem dos meses, com as conferências: mês repetido, mês faltando (ano fechado = 12) e valor negativo."""
    por_mes = {}
    for x in lista:
        m = inteiro(x["monthNumber"], f"{onde} mensal")
        if m in por_mes:
            raise Aborta(f"{onde}: mês {m} repetido no total mensal")
        por_mes[m] = inteiro(x["metricFOB"], f"{onde} {m:02d}")
    if sorted(por_mes) != list(range(1, meses + 1)):
        raise Aborta(f"{onde}: meses do total mensal {sorted(por_mes)} (esperado 1 a {meses}{'; ano fechado' if fechado else ''})")
    if any(v < 0 for v in por_mes.values()):
        raise Aborta(f"{onde}: valor mensal negativo")
    return [por_mes[m] for m in range(1, meses + 1)]


def juntar_mensais(c, mensais, ano):
    """O bruto de um ano com os totais mensais de exportação e de importação que vieram em blocos de anos. `baixadoEm` é o do mais antigo dos dois pedaços."""
    exp, imp = mensais["export"].get(str(ano)), mensais["import"].get(str(ano))
    if not exp or not imp:
        raise Aborta(f"{ano}: sem total mensal de {'exportação' if not exp else 'importação'} na resposta da API (pedido em blocos truncado?)")
    return dict(c, mensal=exp, importacaoMensal=imp, baixadoEm=min(c["baixadoEm"], mensais["baixadoEm"]))


def montar_ano(c, fechado, anterior_mensal):
    """Os dois arquivos de um ano, conferidos. `anterior_mensal`: {mês: valor} do ano anterior (ou None)."""
    ano, mes_fim = c["ano"], c["mesFinal"]
    meses = 12 if fechado else mes_fim
    mensal = dict(enumerate(mensal_de(c["mensal"], f"{ano} exportação", meses, fechado), 1))
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
    if total <= 0:
        raise Aborta(f"{ano}: total negativo ou zero")
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


# ------------------------------------------------------------------------------------------------ balança comercial (exportação, importação e saldo)
DEFINICAO_SALDO = ("Saldo positivo (superávit) significa que o país vendeu ao exterior mais do que comprou, em bens. "
                   "Saldo negativo (déficit) significa o contrário.")
NOTA_BALANCA = ("Exportação e importação em valor FOB (a mercadoria no local de embarque, sem frete e seguro), em dólares correntes: sem corrigir pela inflação e "
                "sem converter para reais. O Comex Stat traz a importação também em valor CIF (com frete e seguro); aqui só entra o FOB, nos dois lados. "
                "O saldo é a exportação FOB menos a importação FOB. A balança é só de bens: a abertura comercial, mais abaixo na página, inclui serviços e vem de outra "
                "fonte (Contas Nacionais do IBGE), por isso os números não se comparam.")


def com_sinal(v):
    """−US$ 5,0 bilhões: o sinal vem antes da moeda."""
    return (MENOS if v < 0 else "") + dinheiro(abs(v))


def condicao_saldo(v):
    return "positivo" if v > 0 else "negativo" if v < 0 else "nulo"


def frase_balanca(item, ref):
    """A abertura do ano: valor exportado, valor importado e saldo, só como fato. `ref`: os mesmos meses do ano anterior (ou None)."""
    per, meses = item["rotulo"], item["meses"]
    x, m = sum(item["exportacao"]), sum(item["importacao"])
    s = x - m
    a = (f"Em {per}, o valor exportado foi de {dinheiro(x)} e o valor importado, de {dinheiro(m)} (os dois em valor FOB, em dólares correntes). "
         f"O saldo foi {condicao_saldo(s)}, de {com_sinal(s)}.")
    if not ref:
        return a
    xr, mr = sum(ref["exportacao"][:meses]), sum(ref["importacao"][:meses])
    sr = xr - mr
    quando = f"No mesmo período de {rotulo_periodo(ref['ano'], meses)}" if meses != 12 else f"Em {ref['ano']}"
    return a + f" {quando}, o saldo foi {condicao_saldo(sr)}, de {com_sinal(sr)} (exportação {dinheiro(xr)}, importação {dinheiro(mr)})."


def montar_balanca(brutos, saidas, meta, fonte):
    """A balança de todos os anos: exportação (a mesma do treemap), importação, saldo e a frase de cada ano. Conferida antes de ir para o arquivo."""
    itens = []
    for ano in sorted(brutos):
        d, _p = saidas[ano]
        meses = d["periodo"]["meses"]
        exp = [m["valor"] for m in d["mensal"]]
        imp = mensal_de(brutos[ano]["importacaoMensal"], f"{ano} importação", meses, d["fechado"])
        itens.append({"ano": ano, "fechado": d["fechado"], "meses": meses, "rotulo": d["periodo"]["rotulo"],
                      "exportacao": exp, "importacao": imp, "saldo": [e - i for e, i in zip(exp, imp)]})
    for i, it in enumerate(itens):
        it["frase"] = frase_balanca(it, itens[i - 1] if i else None)
    balanca = {"versao": VERSAO, "unidade": UNIDADE, "metrica": meta["metrica"], "definicao": DEFINICAO_SALDO, "nota": NOTA_BALANCA, "fonte": fonte, "anos": itens}
    conferir_balanca(balanca, saidas)
    return balanca


def conferir_balanca(balanca, saidas):
    """Abortam: exportação da balança diferente do total do treemap (por país e por produto) no mesmo período; saldo diferente de exportação menos importação;
    mês faltando; valor negativo; texto de julgamento."""
    for it in balanca["anos"]:
        ano, meses = it["ano"], it["meses"]
        exp, imp, saldo = it["exportacao"], it["importacao"], it["saldo"]
        if not (len(exp) == len(imp) == len(saldo) == meses):
            raise Aborta(f"{ano}: a balança tem {len(exp)} meses de exportação, {len(imp)} de importação e {len(saldo)} de saldo (esperado {meses})")
        if any(v < 0 for v in exp + imp):
            raise Aborta(f"{ano}: valor negativo na balança")
        if any(s != e - i for s, e, i in zip(saldo, exp, imp)):
            raise Aborta(f"{ano}: o saldo mensal não é a exportação menos a importação")
        d, p = saidas[ano]
        por_pais, por_produto = sum(i["valor"] for i in d["itens"]), sum(i["valor"] for i in p["itens"]) + p["residuo"]
        if not (sum(exp) == d["total"] == por_pais == por_produto):
            raise Aborta(f"{ano}: a exportação da balança ({sum(exp)}) difere do total do treemap ({d['total']}; por país {por_pais}; por produto {por_produto})")
        if it["fechado"] != (meses == 12):
            raise Aborta(f"{ano}: ano fechado com {meses} meses")
    checar_textos([it["frase"] for it in balanca["anos"]] + [balanca["definicao"], balanca["nota"]])


def montar_tudo(brutos, meta):
    """Todos os anos, conferidos de uma vez: ou tudo passa ou nada é gravado."""
    anos = sorted(brutos)
    if anos != list(range(anos[0], anos[-1] + 1)):
        raise Aborta(f"anos faltando na sequência: {anos}")
    if anos[0] != meta["primeiroAno"] or anos[-1] != meta["ano"]:
        raise Aborta(f"os anos coletados ({anos[0]}–{anos[-1]}) não são os que a fonte declara ({meta['primeiroAno']}–{meta['ano']})")
    saidas, indice_anos = {}, []
    anterior = None
    fonte = {"nome": "Comex Stat, Ministério do Desenvolvimento, Indústria, Comércio e Serviços (MDIC)", "url": SITE, "api": API + "/general",
             "documentacao": API + "/docs", "metrica": meta["metrica"]}
    for ano in anos:
        fechado = ano < meta["ano"] or meta["mesFinal"] == 12
        base, paises, produtos, residuo = montar_ano(brutos[ano], fechado, anterior)
        d = dict(base, modo="destino", itens=paises, fonte=fonte)
        p = dict(base, modo="produto", itens=produtos, residuo=residuo, nivelProduto="Posição do Sistema Harmonizado (SH4)", fonte=fonte)
        d["frase"], p["frase"] = frase("destino", base, paises), frase("produto", base, produtos)
        for a in (d, p):
            checar_textos(textos(a))      # a data em que a fonte atualizou fica só no índice: no arquivo do ano ela faria o git regravar os 60 arquivos todo mês
        saidas[ano] = (d, p)
        indice_anos.append({"ano": ano, "fechado": fechado, "meses": base["periodo"]["meses"], "rotulo": base["periodo"]["rotulo"], "total": base["total"]})
        anterior = {m["mes"]: m["valor"] for m in base["mensal"]}
    return saidas, indice_anos, montar_balanca(brutos, saidas, meta, fonte)


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


def gravar_se_mudou(caminho, obj, coletado, linhas=()):
    """Grava só se os números mudaram (o `coletadoEm` não conta): "novo", "mudou" ou None (igual ao que já estava)."""
    antigo = ler_cache(caminho)
    if antigo and sem_data(antigo) == sem_data(dict(obj, coletadoEm=antigo.get("coletadoEm"))):
        return None
    obj["coletadoEm"] = coletado
    gravar_json(caminho, obj, linhas=linhas)
    return "novo" if antigo is None else "mudou"


def gravar_tudo(saidas, indice_anos, balanca, meta, coletado):
    """Um arquivo por ano e modo, mais a balança. O `coletadoEm` de cada arquivo só muda quando os números mudam (o git não ganha 60 arquivos por semana);
    a data da última conferência com a fonte vai no índice. `coletado` é a data em que os dados saíram da API (a do cache bruto), não a de hoje:
    com --so-montar nada foi baixado, e a página não pode dizer "conferido hoje"."""
    resultados = [gravar_se_mudou(SAIDA / f"{modo}-{ano}.json", a, coletado, linhas=("itens",))
                  for ano, par in saidas.items() for modo, a in zip(("destino", "produto"), par)]
    resultados.append(gravar_se_mudou(SAIDA / "balanca.json", balanca, coletado, linhas=("anos",)))
    novos, mudou = resultados.count("novo"), resultados.count("mudou")
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


def imprimir_balanca(balanca, anos):
    milhar = lambda n: f"{n:,}".replace(",", ".")
    for it in balanca["anos"]:
        if it["ano"] in anos:
            x, m = sum(it["exportacao"]), sum(it["importacao"])
            print(f"\n=== balança {it['rotulo']}: exportação {milhar(x)} · importação {milhar(m)} · saldo {milhar(x - m)} (US$ FOB)")
            print(f"  frase: {it['frase']}")


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
    # balança comercial: exportação (a do treemap), importação, saldo e a frase de cada ano
    def com_importacao(ano, meses):
        b = bruto(ano, meses)
        b["importacaoMensal"] = [{"year": str(ano), "monthNumber": f"{m:02d}", "metricFOB": str(700 + m)} for m in range(1, meses + 1)]
        return b
    meta = {"primeiroAno": 2023, "ano": 2025, "mesFinal": 9, "metrica": "Valor US$ FOB", "atualizadoEm": "2026-01-01"}
    brutos = lambda: {2023: com_importacao(2023, 12), 2024: com_importacao(2024, 12), 2025: com_importacao(2025, 9)}
    saidas, _indice, bal = montar_tudo(brutos(), meta)
    a24, a25 = bal["anos"][1], bal["anos"][2]
    igual((a25["meses"], a25["fechado"], len(a25["saldo"]), a24["fechado"]), (9, False, 9, True), "o ano corrente da balança tem só os meses fechados")
    igual(a25["saldo"][0], 1001 - 701, "saldo mensal = exportação menos importação")
    igual("No mesmo período de jan–set/2024" in a25["frase"] and "Em jan–set/2025, o valor exportado" in a25["frase"], True, "a frase compara os mesmos meses")
    igual("Em 2023, o saldo" in a24["frase"], True, "ano fechado compara com o ano anterior inteiro")
    igual(com_sinal(-5_000_000_000), MENOS + "US$ 5,0 bilhões", "saldo negativo: o sinal antes da moeda")
    igual("O saldo foi negativo, de −US$" in frase_balanca({"ano": 2024, "meses": 12, "rotulo": "2024", "exportacao": [1] * 12, "importacao": [2_000_000_000] * 12}, None), True, "frase de saldo negativo")

    def aborta_balanca(msg, mexe):
        nonlocal ok
        b = brutos()
        mexe(b)
        try:
            montar_tudo(b, meta)
        except Aborta:
            ok += 1
            return
        raise AssertionError(f"deveria abortar: {msg}")
    aborta_balanca("importação sem um mês de ano fechado", lambda b: b[2024]["importacaoMensal"].pop(3))
    aborta_balanca("importação negativa", lambda b: b[2023]["importacaoMensal"][0].update(metricFOB="-5"))
    aborta_balanca("mês de importação repetido", lambda b: b[2024]["importacaoMensal"][1].update(monthNumber="01"))
    aborta_balanca("importação do ano corrente com mês a mais", lambda b: b[2025]["importacaoMensal"].append({"year": "2025", "monthNumber": "10", "metricFOB": "800"}))
    for msg, mexe in (("saldo diferente de exportação menos importação", lambda: bal["anos"][0]["saldo"].__setitem__(0, bal["anos"][0]["saldo"][0] + 1)),
                      ("exportação da balança diferente do total do treemap", lambda: bal["anos"][1]["exportacao"].__setitem__(0, bal["anos"][1]["exportacao"][0] + 1)),
                      ("mês faltando na balança", lambda: bal["anos"][0]["importacao"].pop())):
        original = json.loads(json.dumps(bal))
        mexe()
        try:
            conferir_balanca(bal, saidas)
        except Aborta:
            ok += 1
        else:
            raise AssertionError(f"deveria abortar: {msg}")
        bal.clear()
        bal.update(original)
    checar_textos([bal["definicao"], bal["nota"], a25["frase"]])
    ok += 1
    # os totais mensais chegam em blocos de anos e são juntados ao bruto de cada ano; bloco truncado (ano sem linhas) aborta
    mensais = {"baixadoEm": "2026-10-20", "export": {"2024": [{"year": "2024"}]}, "import": {"2024": [{"year": "2024"}], "2023": [{"year": "2023"}]}}
    j = juntar_mensais({"baixadoEm": "2026-10-19", "paises": [], "produtos": []}, mensais, 2024)
    igual((j["baixadoEm"], j["mensal"], j["importacaoMensal"]), ("2026-10-19", [{"year": "2024"}], [{"year": "2024"}]), "bruto do ano com os totais mensais")
    try:
        juntar_mensais({"baixadoEm": "2026-10-20"}, mensais, 2023)
        raise AssertionError("deveria abortar: ano sem exportação mensal na resposta em blocos")
    except Aborta:
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
    ap.add_argument("--reaproveitar", action="store_true", help="aceita o cache bruto de qualquer dia, desde que a fonte e o último mês sejam os mesmos (desenvolvimento)")
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
            mensais = coletar_mensais(meta, args.reaproveitar)
            print(f"  totais mensais de exportação e de importação: ok ({time.time() - t0:.0f}s)", flush=True)
            for ano in range(meta["primeiroAno"], meta["ano"] + 1):
                mes_fim = 12 if ano < meta["ano"] else meta["mesFinal"]
                brutos[ano] = juntar_mensais(coletar_ano(ano, mes_fim, meta["atualizadoEm"], args.reaproveitar), mensais, ano)
                print(f"  {ano}: ok ({time.time() - t0:.0f}s)", flush=True)
        else:
            meta = ler_cache(CACHE_C / "meta.json")
            if not meta:
                raise Aborta("--so-montar sem cache: rode comex.py antes")
            brutos = {}
            mensais = ler_cache(CACHE_C / "bruto" / "mensais.json")
            if not mensais:
                raise Aborta("--so-montar: falta o cache dos totais mensais (bruto/mensais.json); rode comex.py (--forcar) antes")
            for ano in range(meta["primeiroAno"], meta["ano"] + 1):
                c = ler_cache(CACHE_C / "bruto" / f"{ano}.json")
                if not c:
                    raise Aborta(f"--so-montar: falta o cache de {ano}")
                brutos[ano] = juntar_mensais(c, mensais, ano)
        saidas, indice_anos, balanca = montar_tudo(brutos, meta)
    except Aborta as e:
        print(f"ABORTADO (nada gravado; os arquivos anteriores ficam): {e}")
        sys.exit(1)
    if args.simular:
        print("--simular: conferido, nada gravado.")
    else:
        novos, mudou = gravar_tudo(saidas, indice_anos, balanca, meta, max(c["baixadoEm"] for c in brutos.values()))
        if rede:
            gravar_cache(CACHE_C / "estado.json", {"ultima": HOJE.isoformat()})
        print(f"Comex Stat: {len(indice_anos)} anos conferidos; {novos} arquivo(s) novo(s), {mudou} com números diferentes dos anteriores; índice atualizado.")
    if args.imprimir:
        fech = [a["ano"] for a in indice_anos if a["fechado"]]
        anos_impressos = [fech[-2], fech[-1]] + ([indice_anos[-1]["ano"]] if not indice_anos[-1]["fechado"] else [])
        imprimir(saidas, anos_impressos)
        imprimir_balanca(balanca, anos_impressos)


if __name__ == "__main__":
    main()
