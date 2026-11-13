"""
Câmara Aberta — página "Contexto econômico" (site/dados/contexto/*.json).

Uso:
    python scripts/contexto.py               # coleta (APIs, com cache), confere e grava os três JSON
    python scripts/contexto.py --so-montar   # não chama API nenhuma: remonta os JSON do cache (depois de resumos.py e objetivos.py)
    python scripts/contexto.py --so-mandatos # só o bloco 4 (consulta o Wikidata e grava mandatos.json): rápido, para testar a página

Quatro blocos, todos por API (nada de planilha, nada de tabela curada à mão onde há API):
  1. INDICADORES — IPCA (IBGE/SIDRA), meta de inflação e banda (JSON do site do Banco Central, conferido contra o
     SGS 13521 e contra o IPCA), PIB (IBGE/SIDRA), dívida bruta e líquida do Governo Geral (BCB/SGS), resultado primário
     e nominal do Governo Central (BCB/SGS 4639 e 4573, corrigidos pelo IPCA aqui; reserva: a API do Tesouro) e despesa
     do Governo Central (API de Séries Temporais do Tesouro, valores reais pelo IPCA).
  2. AGENDA — PEC e PLP com tema oficial "Economia" ou "Finanças Públicas e Orçamento" (/proposicoes?codTema=),
     em três abas: sancionadas/promulgadas, em tramitação (andamento nos últimos 365 dias) e paradas (mais de 365).
     PL ordinário só entra nas sancionadas (filtro). Data e número da norma vêm do despacho do evento "Transformação
     em Norma Jurídica" nas tramitações da API, conferidos com a data do DOU escrita no mesmo despacho.
  3. MARCOS — as emendas constitucionais e leis complementares (mesmo filtro de tema) para a linha do tempo.
  4. MANDATOS — o início de cada mandato presidencial (Wikidata, cargo "Presidente do Brasil"), só como referência de
     calendário nos gráficos: anotação com fato datado e fonte, nunca faixa nem cor por presidente, governo ou partido.

Regras que o script impõe (CLAUDE.md, "Contexto econômico"):
  * série longa: o máximo que cada fonte oferece, sem data inicial comum e SEM emendar metodologias diferentes;
  * nada é interpolado; frequência nativa; valores reais declaram deflator e mês-base; a série inteira é regravada;
  * nenhum dado é escrito vazio por cima de dado bom: se uma fonte falha ou encolhe, o bloco anterior fica e sai um aviso;
  * checagens que abortam sem gravar: buraco na série, unidade que mudou, valor fora do plausível, data no futuro,
    "sancionada" da janela exibida sem número ou data da norma.
Nenhuma ligação entre lei e indicador é feita aqui nem na página: o site não afirma causa.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import coleta
from coleta import API, CACHE, DADOS, ENTRADAS, Limitador, ler_cache, gravar_cache

import objetivos
import resumos
import wikipedia   # a mesma sessão com User-Agent identificado e espera entre pedidos (o Wikidata exige UA com contato)

SAIDA = DADOS / "contexto"
CACHE_C = CACHE / "contexto"
BRASILIA = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRASILIA).date()

TEMAS = {"40": "Economia", "70": "Finanças Públicas e Orçamento"}   # temas oficiais da Câmara (codTema)
TIPOS_AGENDA = ("PEC", "PLP")
ANO_INICIO = 1988
DIAS_PARADA = 365
JANELA_SANCIONADAS_DIAS = 365
# Situações (de /referencias/proposicoes/codSituacao) que encerram a tramitação na Câmara. Situação ausente na API
# NÃO entra aqui nem em "em tramitação": nunca se infere.
SIT_LEI, SIT_APENSADA, SIT_VETADA = 1140, 925, 937
SIT_ENCERRADAS = {923, 930, 931, 940, 941, 950, 1120, 1230, 1250, 1260, 1285, 1292}
SIT_EVENTO_NORMA = {"251", "1012"}   # "Transformação em Norma Jurídica" · "…com Veto Parcial"
TOLERANCIA_DOU = 3                   # dias entre o evento da Câmara e o DOU

URL_IBGE = "https://servicodados.ibge.gov.br/api/v3/agregados"     # reserva: só responde do Brasil (ver agregado_sidra)
URL_SIDRA = "https://apisidra.ibge.gov.br/values"                  # a fonte do SIDRA: responde de qualquer país
URL_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{}/dados"
URL_BCB_METAS = "https://www.bcb.gov.br/api/paginasite/sitebcb/controleinflacao/historicometas"
URL_BCB_12M = "https://www.bcb.gov.br/api/servico/sitebcb/meta-vs-inflacao-efetiva"
URL_TESOURO = "https://apiapex.tesouro.gov.br/aria/v1/series-temporais/custom/resultado-fiscal"
URL_CKAN_BCB = "https://dadosabertos.bcb.gov.br/api/3/action/package_search"
EXCECOES = ENTRADAS / "normas_excecoes.json"
LIM_OUTROS = Limitador(3)
LIM_TESOURO = Limitador(2)


class Aborta(Exception):
    """Checagem de sanidade falhou: nada é gravado."""


def aviso(txt):
    print(f"::warning title=contexto::{txt}" if os.environ.get("GITHUB_ACTIONS") else f"AVISO: {txt}", flush=True)


# ---------------------------------------------------------------- HTTP (fora da Câmara)

def texto_da_resposta(r, n=160):
    """O começo do corpo de uma resposta de erro, sem HTML: diz POR QUE a fonte falhou (ex.: "Application is not available", a página do servidor
    do Tesouro fora do ar em 7/11/2026). Só texto da própria fonte; nada de cabeçalho com dado pessoal."""
    t = re.sub(r"<(style|script)\b.*?</\1>", " ", r.text or "", flags=re.S | re.I)
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()
    return t[:n] + ("…" if len(t) > n else "")


def buscar(url, params=None, limitador=LIM_OUTROS, tentativas=5):
    ultimo = "sem resposta"
    for n in range(tentativas):
        limitador.esperar()
        try:
            r = coleta.sessao.get(url, params=params, timeout=90)
        except (coleta.requests.ConnectionError, coleta.requests.Timeout) as e:
            ultimo = f"{type(e).__name__}: {str(e)[:160]}"
            time.sleep(2 ** n)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ultimo = f"HTTP {r.status_code}" + (f", {texto_da_resposta(r)!r}" if r.text else "")
            time.sleep(2 ** n)
            continue
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:      # corpo vazio ou página de bloqueio no lugar do JSON (o SGS faz isso de vez em quando): repete
            ultimo = f"HTTP {r.status_code} sem JSON ({r.headers.get('content-type') or 'sem tipo'})" + (f", {texto_da_resposta(r)!r}" if r.text else ", corpo vazio")
            time.sleep(2 ** n)
            continue
    raise RuntimeError(f"falhou após {tentativas} tentativas ({ultimo}): {url} {params or ''}")


def mes_de(rotulo):
    """'202608' → '2026-08-01'."""
    return f"{rotulo[:4]}-{rotulo[4:6]}-01"


# ---------------------------------------------------------------- INDICADORES

_PERIODO_SIDRA = re.compile(r"^(Mês|Trimestre|Trimestre Móvel|Ano) \(Código\)$")


def agregado_sidra(tabela, variavel, classificacao=None):
    """Uma série do SIDRA para o Brasil: {"serie": {período: valor em texto}, "unidade", "variavel", "fonte"}. O período vem como a API de agregados o
    escreve (202608, 202602, 2025) e o valor sem conversão ("..." continua "..."): quem chama decide o que é dado.

    Fonte: apisidra.ibge.gov.br, que responde de qualquer país. A API de agregados (servicodados.ibge.gov.br) só responde do Brasil: medido em 23/10/2026
    (check-host.net), São Paulo em 0,3 s e tempo esgotado de Los Angeles, Frankfurt, Nuremberg, Paris, Zurique, Amsterdã e Londres. O runner do GitHub fica
    fora do país, e por isso os indicadores pararam em 21/10. A API de agregados fica de reserva (vale numa máquina no Brasil).
    `classificacao` no formato da API de agregados ("11255[90707]")."""
    caminho = f"{URL_SIDRA}/t/{tabela}/n1/all/v/{variavel}/p/all"
    if classificacao:
        m = re.fullmatch(r"(\d+)\[(\d+)\]", classificacao)
        if not m:
            raise ValueError(f"classificação em formato inesperado: {classificacao!r} (esperado 'NNNN[CCCC]')")
        caminho += f"/c{m[1]}/{m[2]}"
    try:
        return do_apisidra(buscar(caminho), tabela, variavel)
    except (RuntimeError, coleta.requests.HTTPError) as erro:
        print(f"   apisidra falhou na tabela {tabela} ({erro}); tentando a API de agregados", flush=True)
    params = {"localidades": "N1[all]", **({"classificacao": classificacao} if classificacao else {})}
    j = buscar(f"{URL_IBGE}/{tabela}/periodos/all/variaveis/{variavel}", params)
    r = j[0]["resultados"]
    if len(r) != 1:
        raise RuntimeError(f"a API de agregados devolveu {len(r)} recortes para a tabela {tabela} (esperado 1)")
    return {"serie": r[0]["series"][0]["serie"], "unidade": j[0]["unidade"], "variavel": j[0]["variavel"], "fonte": "servicodados"}


def do_apisidra(j, tabela, variavel):
    """A resposta do apisidra (a 1ª linha é o cabeçalho) no formato de agregado_sidra. Aborta se o formato mudar: período repetido (mais de um recorte),
    variável, unidade ou nome que mudam entre as linhas, cabeçalho sem a coluna do período."""
    if not isinstance(j, list) or len(j) < 2:
        raise RuntimeError(f"apisidra: resposta vazia ou fora do formato para a tabela {tabela}, variável {variavel}")
    cab, linhas = j[0], j[1:]
    kp = next((k for k, v in cab.items() if _PERIODO_SIDRA.match(str(v))), None)
    kv = next((k for k, v in cab.items() if v == "Variável (Código)"), None)
    kn = next((k for k, v in cab.items() if v == "Variável"), None)
    if not (kp and kv and kn and cab.get("MN") == "Unidade de Medida" and cab.get("V") == "Valor"):
        raise RuntimeError(f"apisidra: cabeçalho fora do formato na tabela {tabela}: {cab}")
    serie, unidades, nomes = {}, set(), set()
    for x in linhas:
        if str(x.get(kv)) != str(variavel):
            raise RuntimeError(f"apisidra: veio a variável {x.get(kv)} na tabela {tabela} (pedida {variavel})")
        if x[kp] in serie:
            raise RuntimeError(f"apisidra: período {x[kp]} repetido na tabela {tabela} (mais de um recorte)")
        serie[x[kp]] = x["V"]
        nomes.add(x[kn])
        if x["MN"]:
            unidades.add(x["MN"])
        else:                     # o apisidra deixa a unidade em branco nas linhas sem dado ("..."); valor de verdade sem unidade é formato novo
            try:
                float(x["V"])
            except ValueError:
                continue
            raise RuntimeError(f"apisidra: valor {x['V']} sem unidade no período {x[kp]} da tabela {tabela}")
    if len(unidades) != 1 or len(nomes) != 1:
        raise RuntimeError(f"apisidra: a tabela {tabela} veio com {len(unidades)} unidades e {len(nomes)} nomes de variável (esperado 1 de cada)")
    return {"serie": serie, "unidade": unidades.pop(), "variavel": nomes.pop(), "fonte": "apisidra"}


def sidra(tabela, variavel, classificacao=None, trimestral=False):
    """Série do SIDRA (ver agregado_sidra) com a unidade e o nome da variável que a própria fonte informa."""
    a = agregado_sidra(tabela, variavel, classificacao)
    serie = a["serie"]
    pontos = []
    for per in sorted(serie):
        try:
            v = float(serie[per])
        except ValueError:      # '...' no começo da série: sem dado, nunca zero
            continue
        if trimestral:
            q = int(per[4:6])
            pontos.append((f"{per[:4]}-{(q - 1) * 3 + 1:02d}-01", v))
        else:
            pontos.append((mes_de(per), v))
    return {"pontos": pontos, "unidade": a["unidade"], "nome": a["variavel"]}


def sgs(cod):
    """Série do SGS (API do Banco Central) + nome e unidade do conjunto de dados aberto (CKAN)."""
    j = buscar(URL_SGS.format(cod), {"formato": "json", "dataInicial": "01/01/1990",
                                     "dataFinal": HOJE.strftime("%d/%m/%Y")})
    pontos = [(f"{x['data'][6:]}-{x['data'][3:5]}-{x['data'][:2]}", float(x["valor"])) for x in j]
    nome, unidade = meta_sgs(cod)
    return {"pontos": pontos, "unidade": unidade, "nome": nome}


def meta_sgs(cod):
    """(nome, unidade) do conjunto de dados aberto do BC (CKAN); guardado 30 dias. Falha de rede = (None, None), com aviso."""
    cam = CACHE_C / "sgs_meta" / f"{cod}.json"
    c = ler_cache(cam)
    if c is not None and idade_dias(cam) < 30:
        return c["nome"], c["unidade"]
    try:
        busca = buscar(URL_CKAN_BCB, {"q": str(cod), "rows": 20})
        for p in busca["result"]["results"]:
            if str(p.get("codigo_sgs")) == str(cod):
                nome, unidade = p.get("title") or p.get("name"), p.get("unidade_medida")
                gravar_cache(cam, {"nome": nome, "unidade": unidade})
                return nome, unidade
    except Exception as e:
        aviso(f"nome da série SGS {cod} não lido do portal de dados abertos do BC ({type(e).__name__})")
        if c is not None:
            return c["nome"], c["unidade"]
    return None, None


def tesouro(codigo, real):
    """Série mensal da API de Séries Temporais do Tesouro (tema 10 = Resultado Fiscal do Governo Central).
    real=True: corrigida pelo IPCA, com referência no último mês da série (a própria API faz a conta)."""
    pontos, nome, pagina = [], None, 1
    while True:
        j = buscar(URL_TESOURO, {"tema": 10, "codigo_da_serie": codigo, "data_inicio": "01/1997",
                                 "correcao_ipca": "true" if real else "false", "pageSize": 1000, "page": pagina},
                   LIM_TESOURO)
        reg = j.get("registros") or []
        for x in reg:
            nome = x["nomeSerie"]
            pontos.append((x["data"][:10], float(x["valor"])))
        if len(reg) < 1000:
            break
        pagina += 1
    pontos.sort()
    return {"pontos": pontos, "unidade": "R$ milhões", "nome": nome}


def proximo_mes(d):
    a, m = int(d[:4]), int(d[5:7])
    return f"{a + (m == 12)}-{1 if m == 12 else m + 1:02d}-01"


def proximo_trimestre(d):
    a, m = int(d[:4]), int(d[5:7])
    return f"{a + (m >= 10)}-{(m + 2) % 12 + 1:02d}-01"


def checar_serie(nome, pontos, freq, minimo, maximo, unidade=None, esperada=None):
    """Buraco, valor fora do plausível, data no futuro e unidade diferente da esperada abortam."""
    if not pontos:
        raise Aborta(f"{nome}: série vazia")
    passo = proximo_mes if freq == "M" else proximo_trimestre
    for (a, _), (b, _) in zip(pontos, pontos[1:]):
        if passo(a) != b:
            raise Aborta(f"{nome}: buraco na série entre {a} e {b}")
    for d, v in pontos:
        if not minimo <= v <= maximo:
            raise Aborta(f"{nome}: valor fora do plausível em {d}: {v} (esperado entre {minimo} e {maximo})")
    if pontos[-1][0] > HOJE.isoformat():
        raise Aborta(f"{nome}: data no futuro ({pontos[-1][0]})")
    if esperada is not None and unidade != esperada:
        raise Aborta(f"{nome}: a unidade mudou — a fonte agora diz {unidade!r}, o esperado era {esperada!r}")


def soma_12m(pontos):
    """Soma móvel de 12 meses: [(mês, soma)] a partir do 12º mês."""
    out = []
    for i in range(11, len(pontos)):
        out.append((pontos[i][0], sum(v for _, v in pontos[i - 11:i + 1])))
    return out


def variacao_12m_da_soma(somas):
    """Crescimento % da soma de 12 meses sobre a soma de 12 meses anterior (mesma conta do PIB em 4 trimestres)."""
    d = dict(somas)
    out = []
    for mes, s in somas:
        a, m = int(mes[:4]), int(mes[5:7])
        ant = d.get(f"{a - 1}-{m:02d}-01")
        if ant:      # zero (ou ausente) não serve de base
            out.append((mes, (s / ant - 1) * 100))
    return out


def parse_metas(texto_json):
    """Tabela anual de metas, lida do JSON que alimenta a página oficial 'Histórico das metas para inflação'."""
    linhas, anterior = {}, None
    def limpa(h):
        h = re.sub(r"<br\s*/?>", "\n", h)
        return [re.sub(r"\s+", " ", t).strip() for t in re.sub(r"<[^>]+>", " ", h).replace("&amp;", "&").split("\n") if t.strip()]
    def num(s):
        return float(s.replace(",", "."))
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", texto_json, flags=re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
        if len(tds) not in (6, 8):
            continue
        cel = [limpa(t) for t in tds]
        try:
            ano = int(cel[0][0].rstrip("*"))
        except (ValueError, IndexError):
            continue
        if len(tds) == 8:
            normas = re.findall(r"exibenormativo\?tipo=[^&]+(?:&amp;|&)numero=(\d+)", tds[1])
            anterior = (normas, cel[2])
            c = cel[3:]
        else:
            c = cel[1:]
        if anterior is None:
            raise Aborta(f"BCB: a tabela de metas começa por uma linha sem norma (ano {ano})")
        normas, datas = anterior
        linhas[ano] = {"ano": ano, "normas": normas, "datas": datas, "metas": [num(x) for x in c[0]],
                       "tolerancias": [num(x) for x in c[1]], "efetiva": num(c[3][0]), "cartaAberta": c[4][0].lower().startswith("sim")}
    return linhas


# As três fontes dos indicadores. Cada uma é baixada, conferida e usada SOZINHA: se uma falha (fora do ar, como o Tesouro em 7/11/2026, que respondia
# 503 "Application is not available" até do Brasil, ou uma checagem que não passa), os blocos dela ficam com a última versão boa, com a data dessa
# coleta escrita na página, e os outros blocos atualizam. Antes, uma fonte fora do ar segurava o arquivo inteiro: de 21/10 a 7/11/2026 IPCA, PIB e
# dívida ficaram parados por causa do IBGE e, depois, do Tesouro.
GRUPOS = {
    "ibge": ("IBGE", ("ipca12", "ipcaMes", "pib4t", "pibTri")),
    "bcb": ("Banco Central", ("dbgg", "dlgg", "meta_sgs", "metas_pagina", "bcb12m")),
    # o resultado do Governo Central pelo Banco Central é uma fonte à parte: se falhar, não segura a dívida e as metas (nem o contrário)
    "bcb_fiscal": ("Banco Central (resultado fiscal)", ("primarioBC", "nominalBC")),
    "tesouro": ("Tesouro Nacional", ("despesa", "primario", "nominal")),
}
# As versões de cada bloco, em ordem de preferência: (fontes, construtor). O resultado primário e nominal vem do Banco Central desde 13/11/2026
# (a API do Tesouro estava fora do ar desde pelo menos 7/11); se o Banco Central faltar, entra a versão do Tesouro, o bloco INTEIRO na medida do
# Tesouro e com o nome dela na página, nunca uma série com metade de cada fonte. Ver escolher_versao().
BLOCO_GRUPOS = {"ipca": [("ibge", "bcb")], "pib": [("ibge",)], "despesa": [("tesouro", "ibge")], "divida": [("bcb",)],
                "resultado": [("bcb_fiscal", "ibge"), ("tesouro",)]}
# Dado da fonte preferida guardado no cache há até tantos dias vem antes da fonte de reserva fresca: o resultado é mensal, e a coleta de ontem do
# Banco Central quase sempre É a de hoje. Trocar de medida por um dia de fonte fora do ar faria o gráfico mudar de número e de volta.
DIAS_PREFERIDA = 7


def coletar_grupo(g):
    """As séries de uma fonte, da API. Qualquer falha levanta: quem chama decide o que fica no lugar."""
    if g == "ibge":
        return {"ipca12": sidra(1737, 2265), "ipcaMes": sidra(1737, 63),
                "pib4t": sidra(5932, 6562, "11255[90707]", trimestral=True), "pibTri": sidra(5932, 6564, "11255[90707]", trimestral=True)}
    if g == "bcb":
        return {"dbgg": sgs(13762), "dlgg": sgs(4536), "meta_sgs": sgs(13521),
                "metas_pagina": json.dumps(buscar(URL_BCB_METAS), ensure_ascii=False), "bcb12m": buscar(URL_BCB_12M)}
    if g == "bcb_fiscal":
        # NFSP sem desvalorização cambial, fluxo mensal corrente, Governo Federal e Banco Central (= Governo Central): 4639 primário, 4573 nominal
        return {"primarioBC": sgs(4639), "nominalBC": sgs(4573)}
    if g == "tesouro":
        return {"despesa": tesouro("10.03.1", True), "primario": tesouro("10.04.1", True), "nominal": tesouro("10.09.1", True)}
    raise ValueError(f"fonte desconhecida: {g}")


def coleta_por_grupo(br):
    """{fonte: data da coleta}, só das fontes que o cache tem inteiras. O cache de antes de 7/11/2026 tem uma data só (`coletadoEm`), que vale para todas."""
    datas = br.get("_coleta") or {}
    out = {}
    for g, (_, chaves) in GRUPOS.items():
        d = datas.get(g) or (None if datas else br.get("coletadoEm"))
        if d and all(k in br for k in chaves):
            out[g] = d
    return out


def montar_indicadores(forcar_rede, coletar=coletar_grupo):
    """Baixa (ou, com --so-montar, lê do cache) as séries de cada fonte. Devolve (brutos, falhas).
    Fonte que falha fica com a coleta anterior do cache, com a data dela; sem coleta anterior, os blocos dela vêm do arquivo publicado
    (construir_indicadores). O cache só guarda séries que vieram da fonte: nunca uma cópia do que foi publicado."""
    arq = CACHE_C / "indicadores_brutos.json"
    antes = ler_cache(arq) or {}
    datas_antes = coleta_por_grupo(antes)
    if not forcar_rede:
        if not datas_antes:
            raise Aborta("--so-montar sem cache de indicadores: rode contexto.py inteiro antes")
        return antes, []
    print("Indicadores: IPCA, PIB (IBGE), metas, dívida e resultado fiscal (Banco Central), despesa e resultado (Tesouro)…", flush=True)
    br, datas, falhas = {}, {}, []
    for g, (nome, chaves) in GRUPOS.items():
        try:
            br.update(coletar(g))
            datas[g] = HOJE.isoformat()
        except Exception as e:
            txt = str(e) if isinstance(e, Aborta) else f"{type(e).__name__}: {e}"
            if g in datas_antes:
                br.update({k: antes[k] for k in chaves})
                datas[g] = datas_antes[g]
                falhas.append((nome, f"{txt} (fica a coleta de {datas_antes[g]}, do cache)"))
            else:
                falhas.append((nome, f"{txt} (sem coleta anterior no cache)"))
    if datas:
        br["_coleta"] = datas
        br["coletadoEm"] = min(datas.values())
        gravar_cache(arq, br)
    return br, falhas


def series_de(br):
    """As séries do cache, com os pontos como tuplas (cópia: o cache não é alterado)."""
    return {k: dict(v, pontos=[tuple(p) for p in v["pontos"]]) for k, v in br.items() if isinstance(v, dict) and "pontos" in v}


def checar_ibge(S):
    checar_serie("IPCA 12 meses", S["ipca12"]["pontos"], "M", -10, 10000, S["ipca12"]["unidade"], "%")
    checar_serie("IPCA mensal", S["ipcaMes"]["pontos"], "M", -10, 100, S["ipcaMes"]["unidade"], "%")
    checar_serie("PIB 4 trimestres", S["pib4t"]["pontos"], "T", -15, 15, S["pib4t"]["unidade"], "%")
    checar_serie("PIB trimestre/trimestre", S["pibTri"]["pontos"], "T", -15, 15, S["pibTri"]["unidade"], "%")


def checar_divida(S):
    checar_serie("Dívida bruta (DBGG)", S["dbgg"]["pontos"], "M", 0, 200)
    checar_serie("Dívida líquida do Governo Geral", S["dlgg"]["pontos"], "M", 0, 200)
    if (S["dbgg"].get("unidade") or "Percentual") != "Percentual" or (S["dlgg"].get("unidade") or "Percentual") != "Percentual":
        raise Aborta("BCB: a unidade da dívida deixou de ser Percentual (% do PIB)")


def checar_tesouro(S):
    for k, nome in (("despesa", "Despesa total"), ("primario", "Resultado primário"), ("nominal", "Resultado nominal")):
        checar_serie(nome, S[k]["pontos"], "M", -1_500_000, 1_500_000)
    for k, esperado in (("despesa", "Despesa total"), ("primario", "Resultado Primário - Governo Central"),
                        ("nominal", "Resultado Nominal do Governo Central")):
        if S[k]["nome"] != esperado:
            raise Aborta(f"Tesouro: o nome da série mudou ({S[k]['nome']!r}, esperado {esperado!r})")


# O resultado do Governo Central pelo Banco Central: o nome e a unidade como o conjunto de dados aberto do BC os escreve (conferidos em 13/11/2026).
# NFSP = necessidades de financiamento: POSITIVO é déficit; a página usa negativo = déficit, então o sinal é trocado.
NOMES_BC = {"primarioBC": "NFSP sem desvalorização cambial - Fluxo mensal corrente - Resultado primário - Total - Governo Federal e Banco Central",
            "nominalBC": "NFSP sem desvalorização cambial - Fluxo mensal corrente - Resultado nominal - Total - Governo Federal e Banco Central"}
# O mesmo começo da série do Tesouro (a página nunca mostrou antes disso). O BC tem desde 1991, mas fluxos mensais de antes do Real não se corrigem
# pelo IPCA com sentido, e começar em outro mês mudaria o gráfico só por trocar de fonte.
INICIO_RESULTADO = "1997-01-01"
TOL_NOMINAL = 5.0      # R$ milhões por mês entre o nominal do BC e o do Tesouro (que republica o do BC): medido, 0,7 no máximo em 356 meses


def checar_bcb_fiscal(S):
    for k, nome in (("primarioBC", "Resultado primário (Banco Central)"), ("nominalBC", "Resultado nominal (Banco Central)")):
        checar_serie(nome, [p for p in S[k]["pontos"] if p[0] >= INICIO_RESULTADO], "M", -1_500_000, 1_500_000)
        # sem o portal de dados abertos do BC (fora do ar e sem cache) o nome vem vazio: aí vale a série pelo código, com aviso de meta_sgs
        if S[k].get("nome") is not None and S[k]["nome"] != NOMES_BC[k]:
            raise Aborta(f"Banco Central: o nome da série mudou ({S[k]['nome']!r}, esperado {NOMES_BC[k]!r})")
        if S[k].get("unidade") is not None and S[k]["unidade"] != "Milhões de reais":
            raise Aborta(f"Banco Central: a unidade de {nome} mudou ({S[k]['unidade']!r}, esperado 'Milhões de reais')")


def indice_ipca(ipca_mes):
    """Número-índice do IPCA encadeado das variações mensais do IBGE (tabela 1737, variável 63): {mês: índice}."""
    idx, v = {}, 1.0
    for d, var in ipca_mes:
        v *= 1 + var / 100
        idx[d] = v
    return idx


def a_precos_de(pontos, idx, base):
    """Valores correntes → reais de `base`, pelo IPCA: valor × índice(base) ÷ índice(mês). É a conta da API do Tesouro (correcao_ipca=true):
    conferido em 13/11/2026, o nominal do Tesouro em reais de ago/2026, desfeito com este índice, volta ao valor corrente do Banco Central com
    diferença máxima de R$ 0,7 milhão em 356 meses."""
    falta = [d for d, _ in pontos if d not in idx] + ([] if base in idx else [base])
    if falta:
        raise Aborta(f"o IPCA do IBGE ainda não tem {', '.join(sorted(set(falta))[:3])}: sem ele não há como corrigir o resultado do Banco Central")
    return [(d, v * idx[base] / idx[d]) for d, v in pontos]


def conferir_nominal_com_tesouro(S, idx, nom_bc):
    """O Tesouro republica o nominal do Banco Central (série 10.09.1, "fonte BCB"): quando há série do Tesouro (de hoje ou do cache), os dois têm de
    bater mês a mês em valores correntes. Divergência em muitos meses = sinal, deflator ou série errada: aborta. Em poucos meses = revisão do BC
    depois da coleta do Tesouro: só aviso. Devolve quantos meses foram comparados (0 sem série do Tesouro)."""
    if "nominal" not in S or not S["nominal"].get("pontos"):
        return 0
    tes = dict(S["nominal"]["pontos"])
    base_t = max(tes)
    if base_t not in idx:
        return 0
    comuns = [d for d in sorted(tes) if d in nom_bc and d in idx]
    difs = [(d, tes[d] * idx[d] / idx[base_t] - nom_bc[d]) for d in comuns]
    fora = [(d, x) for d, x in difs if abs(x) > TOL_NOMINAL]
    if len(fora) > 3:
        raise Aborta(f"o nominal do Banco Central e o do Tesouro (que republica o mesmo) divergem em {len(fora)} de {len(comuns)} meses "
                     f"(ex.: {fora[0][0][:7]}, R$ {fora[0][1]:,.1f} milhões): sinal, deflator ou série errada")
    if fora:
        aviso("o nominal do Banco Central difere do republicado pelo Tesouro em " + ", ".join(f"{d[:7]} (R$ {x:,.1f} mi)" for d, x in fora)
              + ": revisão do Banco Central depois da coleta do Tesouro?")
    return len(comuns)


def metas_conferidas(S, br):
    """Tabela de metas do BC conferida contra o IPCA do IBGE e o SGS 13521; devolve (metas, série mensal da meta e da banda, regime contínuo)."""
    ipca = dict(S["ipca12"]["pontos"])
    metas = parse_metas(br["metas_pagina"])
    if len(metas) < 26 or min(metas) != 1999:
        raise Aborta(f"BCB: a tabela de metas veio com {len(metas)} anos (esperado 26 ou mais, desde 1999)")
    # ---- IPCA em dezembro × 'inflação efetiva' da tabela oficial do BC (1999 em diante)
    for ano, l in metas.items():
        v = ipca.get(f"{ano}-12-01")
        if v is None or abs(v - l["efetiva"]) > 0.006:
            raise Aborta(f"IPCA de dez/{ano} ({v}) não bate com a inflação efetiva da tabela do BC ({l['efetiva']})")
    # ---- metas × SGS 13521 (a resolução mais recente de cada ano)
    sg = {int(d[:4]): v for d, v in S["meta_sgs"]["pontos"]}
    for ano, l in metas.items():
        if abs(sg.get(ano, -1) - l["metas"][0]) > 1e-9:
            raise Aborta(f"meta de {ano}: tabela do BC {l['metas'][0]} × SGS 13521 {sg.get(ano)}")
    # ---- últimos 12 meses da página do BC × SIDRA
    for x in br["bcb12m"].get("conteudo", []):
        d = x["DataReferencia"][:7] + "-01"
        if d in ipca and abs(ipca[d] - x["Inflacao12Meses"]) > 0.006:
            raise Aborta(f"IPCA 12 meses de {d[:7]}: SIDRA {ipca[d]} × BCB {x['Inflacao12Meses']}")
    # ---- regime vigente (meta contínua desde jan/2025): lido da página oficial, nunca suposto
    pag = br["metas_pagina"]
    if not (re.search(r"5\.141", pag) and re.search(r"3,00", pag)):
        raise Aborta("BCB: a página de metas não cita mais a Resolução CMN nº 5.141 e a meta de 3,00%")
    if not (abs(sg.get(2025, 0) - 3.0) < 1e-9 and abs(sg.get(2026, 0) - 3.0) < 1e-9):
        raise Aborta("SGS 13521: meta de 2025 e 2026 diferente de 3,00%")
    CONT = {"inicio": "2025-01-01", "meta": 3.0, "tolerancia": 1.5, "norma": "Resolução CMN nº 5.141", "data": "26/6/2024"}
    # série mensal da meta e da banda: regime do ano-calendário até dez/2024, meta contínua desde jan/2025
    meta_pts = []
    d = "1999-01-01"
    ultimo_mes = S["ipca12"]["pontos"][-1][0]
    while d <= ultimo_mes:
        ano = int(d[:4])
        if d >= CONT["inicio"]:
            m, t = CONT["meta"], CONT["tolerancia"]
        else:
            m, t = metas[ano]["metas"][0], metas[ano]["tolerancias"][0]
        meta_pts.append((d, m, round(m - t, 2), round(m + t, 2)))
        d = proximo_mes(d)
    return metas, meta_pts, CONT


def inicio_do_trimestre(mes_fim):
    """'2026-06-01' (junho = fim do 2º tri) → '2026-04-01' (início do trimestre), como o PIB é rotulado."""
    a, m = int(mes_fim[:4]), int(mes_fim[5:7])
    return f"{a}-{m - 2:02d}-01"


def pontos_json(pontos, casas=4):
    return [[d, round(v, casas)] for d, v in pontos]


def fato_variacao(pontos, unidade, meses=12, passo="M"):
    """Último valor e a variação em relação a 12 meses antes (ou 4 trimestres), como fato, sem julgamento."""
    ult = pontos[-1]
    n = meses if passo == "M" else meses // 3
    ref = pontos[-1 - n] if len(pontos) > n else None
    out = {"data": ult[0], "valor": round(ult[1], 4)}
    if ref:
        out["refData"] = ref[0]
        out["refValor"] = round(ref[1], 4)
        # valor em %: diferença em pontos percentuais; valor em R$: diferença em R$ (a variação % de um saldo negativo engana)
        out["variacao"] = round(ult[1] - ref[1], 4)
        out["variacaoUnidade"] = "p.p." if unidade in ("p.p.", "%") else unidade
    return out


def fonte_ibge(tab, var):
    return {"nome": f"IBGE — SIDRA, tabela {tab}", "url": f"https://sidra.ibge.gov.br/tabela/{tab}", "api": f"{URL_IBGE}/{tab}/periodos/all/variaveis/{var}"}


def fonte_sgs(cod):
    return {"nome": f"Banco Central — SGS {cod}", "url": f"https://dadosabertos.bcb.gov.br/dataset/{cod}", "api": URL_SGS.format(cod)}


FONTE_TES = {"nome": "Tesouro Nacional — API de Séries Temporais (Resultado do Tesouro Nacional, tema 10)",
             "url": "https://www.tesourotransparente.gov.br/temas/estatisticas-fiscais-e-planejamento/resultado-do-tesouro-nacional-rtn",
             "api": "https://apiapex.tesouro.gov.br/aria/v1/series-temporais/docs"}
ANOT_FGTS = {"data": "2012-03-01", "texto": "A partir de 1/3/2012 a despesa inclui recursos de complementação do FGTS e as despesas feitas com recursos dessa contribuição.",
             "fonte": "Resultado do Tesouro Nacional, nota 2 da série histórica", "url": FONTE_TES["url"]}


def bloco_ipca(S, br):
    checar_ibge(S)
    metas, meta_pts, CONT = metas_conferidas(S, br)
    return {
        "titulo": "Inflação (IPCA) e a meta",
        "definicao": "Variação do Índice Nacional de Preços ao Consumidor Amplo (IPCA) nos últimos 12 meses, medida pelo IBGE; "
                     "a meta e o intervalo de tolerância são fixados pelo Conselho Monetário Nacional.",
        "fontes": [fonte_ibge(1737, 2265),
                   {"nome": "Banco Central — Histórico das metas para inflação", "url": "https://www.bcb.gov.br/controleinflacao/historicometas",
                    "api": URL_BCB_METAS},
                   fonte_sgs(13521)],
        "referencia": S["ipca12"]["pontos"][-1][0], "unidade": "%",
        "series": [{"id": "ipca12", "nome": "IPCA em 12 meses", "unidade": "%", "freq": "M", "pontos": pontos_json(S["ipca12"]["pontos"], 2)}],
        "banda": {"nome": "Meta e intervalo de tolerância", "freq": "M",
                  "pontos": [[d, m, lo, hi] for d, m, lo, hi in meta_pts]},
        "ultimo": fato_variacao(S["ipca12"]["pontos"], "%"),
        "metaAtual": {"meta": CONT["meta"], "tolerancia": CONT["tolerancia"], "norma": CONT["norma"], "data": CONT["data"]},
        "anotacoes": [
            {"data": "2003-01-21", "texto": "Carta Aberta do Banco Central de 21/1/2003 ajustou as metas de 2003 e 2004 para 8,5% e 5,5%; "
             "a linha da meta mostra o valor da resolução do CMN.",
             "fonte": "Banco Central, Tabela de metas e resultados (nota 1)", "url": "https://www.bcb.gov.br/Pec/metas/TabelaMetaseResultados.pdf"},
            {"data": "2025-01-01", "texto": "Fim da meta por ano-calendário e início da meta contínua: 3,00% ±1,5 p.p. sobre a inflação em 12 meses, "
             "verificada todo mês (Resolução CMN nº 5.141, de 26/6/2024).",
             "fonte": "Banco Central, Metas para a inflação", "url": "https://www.bcb.gov.br/controleinflacao/metainflacao"}],
        "metasPorAno": [{"ano": a, "meta": l["metas"][0], "tolerancia": l["tolerancias"][0], "normas": l["normas"], "datas": l["datas"],
                         "efetiva": l["efetiva"], "cartaAberta": l["cartaAberta"]} for a, l in sorted(metas.items())],
    }


def bloco_pib(S, br):
    checar_ibge(S)
    pib = S["pib4t"]["pontos"]
    return {
        "titulo": "Crescimento do PIB",
        "definicao": "Crescimento do volume do PIB nos últimos quatro trimestres em relação aos quatro trimestres imediatamente anteriores, segundo o IBGE (Contas Nacionais Trimestrais).",
        "fontes": [fonte_ibge(5932, 6562)], "referencia": pib[-1][0], "unidade": "%",
        "series": [{"id": "pib4t", "nome": "PIB, acumulado em 4 trimestres", "unidade": "%", "freq": "T", "pontos": pontos_json(pib, 2)}],
        "ultimo": fato_variacao(pib, "%", 12, "T"),
        "trimestre": fato_variacao(S["pibTri"]["pontos"], "%", 3, "T") | {"nota": "variação sobre o trimestre anterior, com ajuste sazonal"},
        "anotacoes": [],
    }


def bloco_despesa(S, br):
    checar_tesouro(S)
    checar_ibge(S)
    pib = S["pib4t"]["pontos"]
    mes_base = S["despesa"]["pontos"][-1][0][:7]
    # soma de 12 meses (R$ do último mês da série) e crescimento real; mesmo critério do PIB (4 trimestres terminados no trimestre):
    # só os meses de fim de trimestre
    desp_cresc = variacao_12m_da_soma(soma_12m(S["despesa"]["pontos"]))
    desp_trim = [(inicio_do_trimestre(m), v) for m, v in desp_cresc if m[5:7] in ("03", "06", "09", "12")]
    janela = (max(desp_trim[0][0], pib[0][0]), min(desp_trim[-1][0], pib[-1][0]))
    return {
        "titulo": "Despesa do governo e crescimento do PIB",
        "definicao": "Despesa total do Governo Central (pagamento efetivo, Tesouro Nacional) corrigida pelo IPCA para reais de "
                     f"{mes_base[5:]}/{mes_base[:4]}: crescimento da soma de 12 meses sobre os 12 meses anteriores, ao lado do crescimento do PIB em 4 trimestres.",
        "fontes": [FONTE_TES, fonte_ibge(5932, 6562)], "referencia": desp_trim[-1][0], "unidade": "%",
        "deflator": {"indice": "IPCA", "mesBase": mes_base, "quem": "calculado pela própria API do Tesouro (correcao_ipca=true)"},
        "janelaComum": list(janela),
        "series": [{"id": "despesa", "nome": "Despesa total do Governo Central, real (soma de 12 meses)", "unidade": "%", "freq": "T",
                    "pontos": pontos_json([(d, v) for d, v in desp_trim if janela[0] <= d <= janela[1]], 2)},
                   {"id": "pib", "nome": "PIB, volume (acumulado em 4 trimestres)", "unidade": "%", "freq": "T",
                    "pontos": pontos_json([(d, v) for d, v in pib if janela[0] <= d <= janela[1]], 2)}],
        "ultimo": {"despesa": fato_variacao([(d, v) for d, v in desp_trim if d <= janela[1]], "%", 12, "T"),
                   "pib": fato_variacao([(d, v) for d, v in pib if d <= janela[1]], "%", 12, "T")},
        "anotacoes": [ANOT_FGTS],
    }


def bloco_divida(S, br):
    checar_divida(S)
    return {
        "titulo": "Dívida do governo",
        "definicao": "Dívida Bruta e Dívida Líquida do Governo Geral (Governo Federal, INSS e governos estaduais e municipais), em % do PIB, conforme o Banco Central.",
        "fontes": [fonte_sgs(13762), fonte_sgs(4536)],
        "referencia": min(S["dbgg"]["pontos"][-1][0], S["dlgg"]["pontos"][-1][0]), "unidade": "% do PIB",
        "series": [{"id": "dbgg", "nome": "Dívida bruta do Governo Geral (conceito do Banco Central)", "unidade": "% do PIB", "freq": "M",
                    "pontos": pontos_json(S["dbgg"]["pontos"], 2)},
                   {"id": "dlgg", "nome": "Dívida líquida do Governo Geral", "unidade": "% do PIB", "freq": "M",
                    "pontos": pontos_json(S["dlgg"]["pontos"], 2)}],
        "ultimo": {"dbgg": fato_variacao(S["dbgg"]["pontos"], "%"), "dlgg": fato_variacao(S["dlgg"]["pontos"], "%")},
        "anotacoes": [
            {"data": "2006-12-01", "texto": "A série da dívida bruta começa em dez/2006 (metodologia utilizada a partir de 2008, segundo o nome oficial no SGS); "
             "o Banco Central publica outra série da dívida bruta, no conceito do FMI, que não é emendada a esta.",
             "fonte": "Banco Central, SGS 13762; Estatísticas fiscais, Tabela 17", "url": "https://dadosabertos.bcb.gov.br/dataset/13762-divida-bruta-do-governo-geral--pib---metodologia-utilizada-a-partir-de-2008"}],
    }


def bloco_resultado_bc(S, br):
    """Versão preferida: as duas séries do Banco Central (abaixo da linha), corrigidas pelo IPCA por este site. Primário + juros = nominal fecha
    dentro da mesma apuração (na versão do Tesouro, o primário era de um método e o nominal de outro)."""
    checar_bcb_fiscal(S)
    checar_ibge(S)
    prim = [(d, -v) for d, v in S["primarioBC"]["pontos"] if d >= INICIO_RESULTADO]
    nom = [(d, -v) for d, v in S["nominalBC"]["pontos"] if d >= INICIO_RESULTADO]
    if [d for d, _ in prim] != [d for d, _ in nom]:
        raise Aborta("Banco Central: o primário e o nominal vieram com meses diferentes")
    idx = indice_ipca(S["ipcaMes"]["pontos"])
    base = prim[-1][0]
    conferidos = conferir_nominal_com_tesouro(S, idx, dict(nom))
    prim12 = soma_12m(a_precos_de(prim, idx, base))
    nom12 = soma_12m(a_precos_de(nom, idx, base))
    b = {
        "titulo": "Resultado primário e nominal do Governo Central",
        "definicao": "Resultado primário: receitas menos despesas do Governo Central (Tesouro Nacional, Previdência Social e Banco Central), sem contar os "
                     "juros da dívida. Resultado nominal: o primário mais os juros nominais. As duas séries são do Banco Central, apuradas 'abaixo da "
                     "linha', pela variação da dívida do governo. Negativo = déficit.",
        "medida": "bancoCentral",
        "legendas": {"primario": "Resultado primário (abaixo da linha, Banco Central)", "nominal": "Resultado nominal (abaixo da linha, Banco Central)"},
        "fontes": [fonte_sgs(4639), fonte_sgs(4573), fonte_ibge(1737, 63)], "referencia": prim12[-1][0], "unidade": "R$ milhões",
        "deflator": {"indice": "IPCA", "mesBase": base[:7], "quem": "conta deste site com o IPCA mensal do IBGE, a mesma da API do Tesouro"},
        "series": [{"id": "primario", "nome": "Resultado primário, soma de 12 meses", "unidade": "R$ milhões", "freq": "M", "pontos": pontos_json(prim12, 1)},
                   {"id": "nominal", "nome": "Resultado nominal, soma de 12 meses", "unidade": "R$ milhões", "freq": "M", "pontos": pontos_json(nom12, 1)}],
        "ultimo": {"primario": fato_variacao(prim12, "R$ milhões"), "nominal": fato_variacao(nom12, "R$ milhões")},
        "conferenciaTesouro": {"mesesNominal": conferidos},
        "anotacoes": [],
    }
    # o primário do Tesouro (acima da linha) no último mês que as duas fontes têm, em valores correntes: o leitor vê que as medidas diferem e quanto
    if conferidos and "primario" in S and S["primario"].get("pontos"):
        tes = dict(S["primario"]["pontos"])
        base_t = max(tes)
        comuns = sorted(d for d in tes if d in dict(prim) and d in idx and base_t in idx)
        if comuns:
            m = comuns[-1]
            b["outraMedida"] = {"mes": m[:7], "tesouro": round(tes[m] * idx[m] / idx[base_t], 1), "bancoCentral": round(dict(prim)[m], 1),
                                "nota": "resultado primário do mês, em reais correntes: o Tesouro apura 'acima da linha' (receitas menos despesas)"}
    return b


def bloco_resultado_tesouro(S, br):
    """Versão de reserva: a do Tesouro, como a página mostrava até 13/11/2026. Entra quando o Banco Central não tem dado recente (ver escolher_versao)."""
    checar_tesouro(S)
    mes_base = S["despesa"]["pontos"][-1][0][:7]
    prim12 = soma_12m(S["primario"]["pontos"])
    nom12 = soma_12m(S["nominal"]["pontos"])
    return {
        "titulo": "Resultado primário e nominal do Governo Central",
        "definicao": "Resultado primário: receitas menos despesas do Governo Central, sem contar os juros da dívida (apurado pelo Tesouro, 'acima da linha'). "
                     "Resultado nominal: o primário mais os juros nominais, apurado pelo Banco Central ('abaixo da linha') e publicado pelo Tesouro. "
                     "Negativo = déficit. Esta é a série do Tesouro, usada quando a do Banco Central não está disponível: o primário do Tesouro é medido "
                     "de outro jeito e pode diferir do que o Banco Central apura.",
        "medida": "tesouro",
        "legendas": {"primario": "Resultado primário (acima da linha, Tesouro)", "nominal": "Resultado nominal (abaixo da linha, Banco Central, publicado pelo Tesouro)"},
        "fontes": [FONTE_TES], "referencia": prim12[-1][0], "unidade": "R$ milhões",
        "deflator": {"indice": "IPCA", "mesBase": mes_base, "quem": "calculado pela própria API do Tesouro (correcao_ipca=true)"},
        "series": [{"id": "primario", "nome": "Resultado primário, soma de 12 meses", "unidade": "R$ milhões", "freq": "M", "pontos": pontos_json(prim12, 1)},
                   {"id": "nominal", "nome": "Resultado nominal, soma de 12 meses", "unidade": "R$ milhões", "freq": "M", "pontos": pontos_json(nom12, 1)}],
        "ultimo": {"primario": fato_variacao(prim12, "R$ milhões"), "nominal": fato_variacao(nom12, "R$ milhões")},
        "anotacoes": [ANOT_FGTS],
    }


# as listas seguem a ordem das versões de BLOCO_GRUPOS
CONSTRUTORES = {"ipca": [bloco_ipca], "pib": [bloco_pib], "despesa": [bloco_despesa], "divida": [bloco_divida],
                "resultado": [bloco_resultado_bc, bloco_resultado_tesouro]}


def escolher_versao(versoes, datas, hoje=None):
    """Os índices das versões de um bloco que têm todas as fontes (de hoje ou do cache), na ordem em que são tentadas.
    A idade de uma versão é a da coleta da sua fonte PRINCIPAL (a 1ª da lista: o IPCA que só corrige valores não conta).
      1. versões com a fonte principal de até DIAS_PREFERIDA dias, na ordem de preferência: a coleta de ontem do Banco Central vem antes do Tesouro
         de hoje (mesma medida, quase sempre os mesmos números);
      2. as mais velhas, da coleta mais recente para a mais antiga (empate: a preferida).
    Se a construção de uma falhar (checagem), tenta a seguinte; se nenhuma der, o bloco fica com a versão publicada."""
    hoje = hoje or HOJE
    ordem = []
    for i, grupos in enumerate(versoes):
        if not all(g in datas for g in grupos):
            continue
        d = date.fromisoformat(datas[grupos[0]])
        ordem.append(((0, i) if (hoje - d).days <= DIAS_PREFERIDA else (1, -d.toordinal(), i), i))
    return [i for _, i in sorted(ordem)]


def construir_indicadores(br, anterior=None):
    """Monta cada bloco da página a partir das fontes dele; devolve (blocos, falhas). Cada bloco leva `coletadoEm`, a data da coleta mais antiga
    entre as fontes que ele usa. Bloco cuja fonte faltou ou não passou nas checagens fica com a versão do arquivo publicado (`anterior`), com a data
    dela; sem versão publicada, sai da página ("Dados indisponíveis"). Nunca um bloco montado com metade das fontes."""
    S = series_de(br)
    datas = coleta_por_grupo(br)
    ant = (anterior or {}).get("blocos") or {}
    data_ant = (anterior or {}).get("coletadoEm") or ((anterior or {}).get("geradoEm") or "")[:10] or None
    B, falhas = {}, []
    for bid, versoes in BLOCO_GRUPOS.items():
        erros = []
        for i in escolher_versao(versoes, datas):
            try:
                b = CONSTRUTORES[bid][i](S, br)
            except Exception as e:      # checagem que não passa nesta versão: tenta a seguinte
                erros.append(str(e) if isinstance(e, Aborta) else f"{type(e).__name__}: {e}")
                continue
            b["coletadoEm"] = min(datas[g] for g in versoes[i])
            B[bid] = b
            if i > 0:       # a versão de reserva: diz por que a preferida não entrou
                reserva = ", ".join(GRUPOS[g][0] for g in versoes[i])
                principal = versoes[0][0]
                motivo = erros[0] if erros else (f"{GRUPOS[principal][0]}: última coleta em {datas[principal]}" if principal in datas
                                                 else f"{GRUPOS[principal][0]}: sem coleta nesta rodada nem no cache")
                aviso(f"bloco {bid}: entrou a versão de reserva ({reserva}). Motivo: {motivo}")
                if erros:       # uma checagem da versão preferida não passou: isso é falha, não só fonte fora do ar (que já foi contada)
                    falhas.append((bid, f"{erros[0]} (o bloco usou a versão de reserva: {reserva})"))
            break
        else:
            if erros:
                txt = erros[0]
            else:
                faltam = sorted({GRUPOS[g][0] for v in versoes for g in v if g not in datas})
                txt = " e ".join(faltam) + ": sem dados nesta rodada nem no cache"
            velho = ant.get(bid)
            if isinstance(velho, dict) and velho.get("series"):
                B[bid] = dict(velho, coletadoEm=velho.get("coletadoEm") or data_ant)
                falhas.append((bid, f"{txt} (o bloco fica com a versão publicada, coletada em {B[bid]['coletadoEm']})"))
            else:
                falhas.append((bid, f"{txt} (sem versão publicada: o bloco sai da página)"))
    B["ifi"] = {"titulo": "Projeções da Instituição Fiscal Independente",
                "definicao": "A IFI, órgão do Senado Federal, publica projeções para os indicadores fiscais; o site não as reproduz.",
                "url": "https://www12.senado.leg.br/ifi/publicacoes-1/relatorio-de-acompanhamento-fiscal"}
    return B, falhas


def frescos(B):
    """Quantos blocos foram montados nesta rodada com dado coletado hoje."""
    return sum(1 for b in B.values() if isinstance(b, dict) and b.get("series") and b.get("coletadoEm") == HOJE.isoformat())


def testar_resultado_bc():
    """A versão do Banco Central do resultado, com séries inventadas no formato das de verdade: sinal, correção pelo IPCA, conferência com o
    Tesouro e as recusas. Devolve quantas verificações passaram."""
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1

    def recusa(S, trecho, msg):
        nonlocal ok
        try:
            bloco_resultado_bc(S, {})
        except Aborta as e:
            assert trecho in str(e), f"{msg}: a mensagem não diz {trecho!r}: {e}"
            ok += 1
            return
        raise AssertionError(f"{msg}: deveria ter recusado")

    def meses(ini, fim):
        out, d = [], f"{ini}-01"
        while d <= f"{fim}-01":
            out.append(d)
            d = proximo_mes(d)
        return out

    M = meses("1996-01", "2026-09")
    tri = [d for d in meses("1996-01", "2026-06") if d[5:7] in ("01", "04", "07", "10")]
    bc_p = {d: 1000.0 + i for i, d in enumerate(meses("1991-01", "2026-09"))}       # NFSP: positivo = déficit
    bc_n = {d: 5000.0 + 3 * i for i, d in enumerate(meses("1991-01", "2026-09"))}
    idx = indice_ipca([(d, 0.5) for d in M])
    base_t = "2026-08-01"
    tes_meses = meses("1997-01", "2026-08")

    def S_(**troca):
        S = {"ipcaMes": {"pontos": [(d, 0.5) for d in M], "unidade": "%"}, "ipca12": {"pontos": [(d, 6.17) for d in M], "unidade": "%"},
             "pib4t": {"pontos": [(d, 2.0) for d in tri], "unidade": "%"}, "pibTri": {"pontos": [(d, 0.5) for d in tri], "unidade": "%"},
             "primarioBC": {"pontos": sorted(bc_p.items()), "nome": NOMES_BC["primarioBC"], "unidade": "Milhões de reais"},
             "nominalBC": {"pontos": sorted(bc_n.items()), "nome": NOMES_BC["nominalBC"], "unidade": "Milhões de reais"},
             # o Tesouro: o nominal é o do BC em reais de ago/2026; o primário é outra medida (50 milhões a menos de déficit por mês)
             "nominal": {"pontos": [(d, -bc_n[d] * idx[base_t] / idx[d]) for d in tes_meses]},
             "primario": {"pontos": [(d, (-bc_p[d] + 50) * idx[base_t] / idx[d]) for d in tes_meses]}}
        S.update(troca)
        return S

    b = bloco_resultado_bc(S_(), {})
    prim = dict(b["series"][0]["pontos"])
    esperado = sum(-bc_p[d] * idx["2026-09-01"] / idx[d] for d in meses("2025-10", "2026-09"))
    igual((b["medida"], min(prim), max(prim), b["deflator"]["mesBase"]), ("bancoCentral", "1997-12-01", "2026-09-01", "2026-09"),
          "a série começa em 1997 (soma de 12 meses desde dez/1997) e vai até o último mês do BC")
    igual(abs(prim["2026-09-01"] - round(esperado, 1)) < 1e-6 and esperado < 0, True, "sinal trocado (déficit negativo) e corrigido pelo IPCA para set/2026")
    igual(b["conferenciaTesouro"]["mesesNominal"], len(tes_meses), "o nominal confere com o do Tesouro mês a mês")
    igual((b["outraMedida"]["mes"], b["outraMedida"]["bancoCentral"], round(b["outraMedida"]["tesouro"] - b["outraMedida"]["bancoCentral"], 1)),
          ("2026-08", -bc_p["2026-08-01"], 50.0), "o primário do Tesouro no último mês comum, em valores correntes")
    sem_tes = S_()
    del sem_tes["nominal"], sem_tes["primario"]
    b2 = bloco_resultado_bc(sem_tes, {})
    igual((b2["conferenciaTesouro"]["mesesNominal"], "outraMedida" in b2), (0, False), "sem o Tesouro: sem conferência e sem comparação, sem quebrar")
    revisto = S_()
    revisto["nominal"] = {"pontos": [(d, v + (100 if d in ("2020-01-01", "2021-01-01") else 0)) for d, v in revisto["nominal"]["pontos"]]}
    igual(bloco_resultado_bc(revisto, {})["medida"], "bancoCentral", "dois meses revistos pelo BC depois da coleta do Tesouro: só aviso")
    recusa(S_(nominal={"pontos": [(d, -v) for d, v in S_()["nominal"]["pontos"]]}), "divergem", "Tesouro com o sinal oposto = série ou sinal errado")
    recusa(S_(ipcaMes={"pontos": [(d, 0.5) for d in M[:-1]], "unidade": "%"}), "IPCA do IBGE ainda não tem", "sem o IPCA do último mês não corrige")
    recusa(S_(primarioBC={"pontos": sorted(bc_p.items()), "nome": "NFSP com desvalorização cambial", "unidade": "Milhões de reais"}),
           "nome da série mudou", "nome diferente no portal do BC")
    recusa(S_(nominalBC={"pontos": sorted(bc_n.items()), "nome": NOMES_BC["nominalBC"], "unidade": "Bilhões de reais"}), "unidade", "unidade diferente")
    recusa(S_(primarioBC={"pontos": [p for p in sorted(bc_p.items()) if p[0] != "2010-05-01"], "nome": None, "unidade": None}), "buraco",
           "buraco na série do BC")
    igual(bloco_resultado_bc(S_(primarioBC={"pontos": sorted(bc_p.items()), "nome": None, "unidade": None}), {})["medida"], "bancoCentral",
          "sem o nome do portal do BC (fora do ar): vale a série pelo código")
    return ok


def testar():
    """python scripts/contexto.py --teste: a regra "cada fonte falha sozinha", com fontes e blocos falsos (sem rede)."""
    import tempfile
    from unittest import mock
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1
    hoje = HOJE.isoformat()
    falso = lambda nome: (lambda S, br: {"titulo": nome, "series": [{"id": nome, "pontos": [["2026-01-01", 1.0]]}]})
    # a versão preferida tem o nome do bloco; a de reserva, "(reserva)"
    construtores = {k: [falso(k if i == 0 else f"{k} (reserva)") for i in range(len(v))] for k, v in BLOCO_GRUPOS.items()}
    def coletor(fora):
        def c(g):
            if g in fora:
                raise RuntimeError(f"falhou após 5 tentativas (HTTP 503, 'Application is not available'): {g}")
            return {k: {"pontos": [["2026-01-01", 1.0]]} if k not in ("metas_pagina", "bcb12m") else "x" for k in GRUPOS[g][1]}
        return c
    def dia(n):
        return (HOJE - timedelta(days=n)).isoformat()
    def br_com(datas):
        """Um cache com as fontes de `datas` ({fonte: data da coleta}), sem passar pela rede."""
        return {k: {"pontos": [["2026-01-01", 1.0]]} if k not in ("metas_pagina", "bcb12m") else "x"
                for g in datas for k in GRUPOS[g][1]} | {"_coleta": datas, "coletadoEm": min(datas.values())}
    def quebra(S, br):
        raise Aborta("Banco Central: o nome da série mudou")
    with tempfile.TemporaryDirectory() as t, mock.patch.dict(CONSTRUTORES, construtores), mock.patch(f"{__name__}.CACHE_C", __import__("pathlib").Path(t)):
        # 1. Tesouro fora do ar e sem cache: IBGE e BC atualizam; a despesa vem do arquivo publicado, com a data dele; o resultado é do BC
        anterior = {"geradoEm": "2026-10-21T11:56-03:00", "coletadoEm": "2026-10-21",
                    "blocos": {k: {"titulo": f"{k} velho", "series": [{"id": k, "pontos": [["2025-01-01", 0.0]]}]} for k in BLOCO_GRUPOS}}
        br, f1 = montar_indicadores(True, coletor({"tesouro"}))
        igual([n for n, _ in f1], ["Tesouro Nacional"], "só o Tesouro falha na coleta")
        igual("Application is not available" in f1[0][1] and "sem coleta anterior" in f1[0][1], True, "o motivo vai na mensagem")
        igual(sorted(coleta_por_grupo(br)), ["bcb", "bcb_fiscal", "ibge"], "o cache guarda só o que veio da fonte")
        B, f2 = construir_indicadores(br, anterior)
        igual({k: B[k]["titulo"] for k in BLOCO_GRUPOS}, {"ipca": "ipca", "pib": "pib", "despesa": "despesa velho", "divida": "divida", "resultado": "resultado"},
              "a despesa (só do Tesouro) fica com a versão publicada; o resultado vem do Banco Central")
        igual({k: B[k]["coletadoEm"] for k in BLOCO_GRUPOS}, {"ipca": hoje, "pib": hoje, "despesa": "2026-10-21", "divida": hoje, "resultado": hoje},
              "cada bloco diz a data da própria coleta")
        igual(sorted(b for b, _ in f2), ["despesa"], "as falhas de bloco são contadas")
        igual(frescos(B), 4, "quatro blocos com dado de hoje")
        # 2. no dia seguinte o Tesouro volta: tudo atualiza
        br, f3 = montar_indicadores(True, coletor(set()))
        B, f4 = construir_indicadores(br, {"blocos": B})
        igual((f3, f4, frescos(B), B["resultado"]["titulo"]), ([], [], 5, "resultado"), "com as quatro fontes, os cinco blocos atualizam, o resultado pelo BC")
        # 3. depois o IBGE cai: fica a coleta do cache (de hoje, neste teste), sem tocar no que veio do BC e do Tesouro
        br, f5 = montar_indicadores(True, coletor({"ibge"}))
        igual(([n for n, _ in f5], "fica a coleta de" in f5[0][1]), (["IBGE"], True), "fonte que falha usa a coleta anterior do cache")
        igual(sorted(coleta_por_grupo(br)), ["bcb", "bcb_fiscal", "ibge", "tesouro"], "com o cache, as quatro fontes seguem disponíveis")
        # 4. --so-montar lê o cache e não chama a rede
        br2, f6 = montar_indicadores(False, coletor({"ibge", "bcb", "bcb_fiscal", "tesouro"}))
        igual((br2["_coleta"], f6), (br["_coleta"], []), "--so-montar usa o cache")
        # 5. checagem da versão do BC que não passa: entra a do Tesouro, e a falha é contada (não é só fonte fora do ar)
        with mock.patch.dict(CONSTRUTORES, {"resultado": [quebra, construtores["resultado"][1]]}):
            B2, f7 = construir_indicadores(br, {"blocos": B})
        igual((B2["resultado"]["titulo"], [b for b, _ in f7], "versão de reserva" in f7[0][1], frescos(B2)),
              ("resultado (reserva)", ["resultado"], True, 5), "checagem do BC que falha: o bloco usa o Tesouro e a falha aparece")
        # 6. as duas versões falham: fica o bloco publicado, com a data dele
        with mock.patch.dict(CONSTRUTORES, {"resultado": [quebra, quebra]}):
            B2, f7 = construir_indicadores(br, {"blocos": dict(B, resultado=dict(B["resultado"], titulo="resultado de ontem", coletadoEm="2026-11-06"))})
        igual((B2["resultado"]["titulo"], [b for b, _ in f7], frescos(B2)), ("resultado de ontem", ["resultado"], 4), "checagem que falha mantém o bloco anterior, com a data dele")
        # 7. sem versão publicada, o bloco sai (a página escreve "Dados indisponíveis") e nada é inventado
        with mock.patch.dict(CONSTRUTORES, {"resultado": [quebra, quebra]}):
            B3, f8 = construir_indicadores(br, None)
        igual(("resultado" in B3, "sai da página" in f8[0][1]), (False, True), "sem versão anterior, o bloco não aparece")
        # 8. o BC não respondeu hoje, mas a coleta de ontem está no cache: ela vem antes do Tesouro de hoje (mesma medida, sem trocar o gráfico)
        B4, f9 = construir_indicadores(br_com({"ibge": hoje, "bcb": hoje, "bcb_fiscal": dia(1), "tesouro": hoje}), None)
        igual((B4["resultado"]["titulo"], B4["resultado"]["coletadoEm"], f9), ("resultado", dia(1), []), "BC de ontem antes do Tesouro de hoje")
        # 9. o BC parado há 10 dias e o Tesouro de hoje: entra o Tesouro (o bloco inteiro na medida dele)
        B4, f9 = construir_indicadores(br_com({"ibge": hoje, "bcb": hoje, "bcb_fiscal": dia(10), "tesouro": hoje}), None)
        igual((B4["resultado"]["titulo"], B4["resultado"]["coletadoEm"], f9), ("resultado (reserva)", hoje, []), "BC velho: entra o Tesouro de hoje")
        # 10. as duas paradas: a coleta mais recente (aqui o BC, de 10 dias, contra o Tesouro de 30)
        B4, f9 = construir_indicadores(br_com({"ibge": hoje, "bcb": hoje, "bcb_fiscal": dia(10), "tesouro": dia(30)}), None)
        igual((B4["resultado"]["titulo"], B4["resultado"]["coletadoEm"]), ("resultado", dia(10)), "as duas velhas: a mais recente")
        # 11. sem nenhuma das duas: o bloco publicado, e a mensagem diz quais fontes faltam
        B4, f9 = construir_indicadores(br_com({"ibge": hoje, "bcb": hoje}), {"blocos": {"resultado": {"titulo": "publicado", "series": [{"id": "x"}],
                                                                                                      "coletadoEm": "2026-10-21"}}})
        igual((B4["resultado"]["titulo"], "Banco Central (resultado fiscal)" in dict(f9)["resultado"]), ("publicado", True), "sem as fontes: o bloco publicado")
    # a ordem das versões, por idade da fonte PRINCIPAL (o IPCA, que só corrige valores, não conta)
    V = [("bcb_fiscal", "ibge"), ("tesouro",)]
    for datas, esperado, msg in (({"bcb_fiscal": hoje, "ibge": hoje, "tesouro": hoje}, [0, 1], "as duas de hoje: a preferida primeiro"),
                                 ({"bcb_fiscal": dia(7), "ibge": hoje, "tesouro": hoje}, [0, 1], "a preferida de até 7 dias primeiro"),
                                 ({"bcb_fiscal": dia(8), "ibge": hoje, "tesouro": hoje}, [1, 0], "a preferida de 8 dias depois da reserva de hoje"),
                                 ({"bcb_fiscal": dia(8), "ibge": hoje, "tesouro": dia(9)}, [0, 1], "as duas velhas: a mais recente"),
                                 ({"bcb_fiscal": hoje, "ibge": dia(30), "tesouro": hoje}, [0, 1], "o IBGE parado não troca a medida"),
                                 ({"ibge": hoje, "tesouro": hoje}, [1], "sem o BC: só o Tesouro"),
                                 ({}, [], "sem fonte nenhuma")):
        igual(escolher_versao(V, datas), esperado, msg)
    # cache antigo, de uma data só: vale para as três fontes que ele tinha (o resultado do BC não estava lá)
    velho = {k: {"pontos": []} for g in ("ibge", "bcb", "tesouro") for k in GRUPOS[g][1]} | {"coletadoEm": "2026-10-23"}
    igual(coleta_por_grupo(velho), {"ibge": "2026-10-23", "bcb": "2026-10-23", "tesouro": "2026-10-23"}, "cache de antes de 7/11/2026")
    igual(coleta_por_grupo({"_coleta": {"ibge": "2026-11-07"}, "coletadoEm": "2026-11-07", "despesa": {}, "primario": {}, "nominal": {}}), {},
          "fonte com as séries mas sem data própria não conta (nem a do IBGE incompleto)")
    ok += testar_resultado_bc()

    class R:
        def __init__(self, texto):
            self.text = texto
    igual(texto_da_resposta(R("<html><style>body{}</style><h1>Application is not available</h1><p>The application</p></html>")),
          "Application is not available The application", "o motivo sai sem HTML")
    print(f"{ok} verificações passaram")


# ---------------------------------------------------------------- AGENDA

def idade_dias(caminho):
    try:
        return (time.time() - caminho.stat().st_mtime) / 86400
    except FileNotFoundError:
        return 1e9


def janelas(ini, fim, max_dias=85):
    """Intervalos de datas aceitos pela API: no mesmo ano e com até 3 meses de diferença."""
    out = []
    a = ini
    while a <= fim:
        b = min(fim, a + timedelta(days=max_dias), date(a.year, 12, 31))
        out.append((a, b))
        a = b + timedelta(days=1)
    return out


def listar_ids(tipo, tema, ano, forcar):
    cam = CACHE_C / "lista" / f"{tipo}-{tema}-{ano}.json"
    c = ler_cache(cam)
    if c is not None and not forcar and idade_dias(cam) < 30:
        return c
    itens = coleta.get_paginado(f"{API}/proposicoes", {"siglaTipo": tipo, "codTema": tema, "ano": ano, "itens": 100,
                                                      "ordem": "ASC", "ordenarPor": "id"})
    ids = [x["id"] for x in itens]
    gravar_cache(cam, ids)
    return ids


def ids_com_andamento(tipo, tema, ini, fim):
    out = set()
    for a, b in janelas(ini, fim):
        for x in coleta.get_paginado(f"{API}/proposicoes", {"siglaTipo": tipo, "codTema": tema, "dataInicio": a.isoformat(),
                                                           "dataFim": b.isoformat(), "itens": 100}):
            out.add(x["id"])
    return out


def detalhe(id_prop, refazer=False):
    cam = CACHE_C / "det" / f"{id_prop}.json"
    d = None if refazer else ler_cache(cam)
    if d is None:
        r = coleta.get(f"{API}/proposicoes/{id_prop}")
        if r is None:
            return None
        d = r["dados"]
        gravar_cache(cam, d)
    return d


PADRAO_NORMA = re.compile(r"(Emenda\s+Constitucional|Lei\s+Complementar|Lei\s+Ordin[áa]ria|Lei)\s*(?:n[ºo°.]?\s*)?([\d\.]+)\s*/\s*(\d{2,4})", re.I)
PADRAO_DOU = re.compile(r"DO[UF]C?\s+(\d{2})[\s/\.](\d{2})[\s/\.](\d{2,4})", re.I)


def ano4(a):
    a = int(a)
    return a if a >= 100 else (1900 + a if a >= 88 else 2000 + a)


def norma_da_tramitacao(tram):
    """Norma (tipo, número, ano, data do evento e do DOU) do evento 'Transformação em Norma Jurídica' mais recente das tramitações da API, ou None."""
    ev = [t for t in tram if str(t.get("codTipoTramitacao")) in SIT_EVENTO_NORMA or "norma jur" in (t.get("descricaoTramitacao") or "").lower()]
    if not ev:
        return None
    e = max(ev, key=lambda t: t["dataHora"])
    desp = e.get("despacho") or ""
    m = PADRAO_NORMA.search(desp)
    if not m:
        return None
    tipo_txt = re.sub(r"\s+", " ", m[1]).lower()
    sigla = "EC" if tipo_txt.startswith("emenda") else "LC" if "complementar" in tipo_txt else "LEI"
    nome = {"EC": "Emenda Constitucional", "LC": "Lei Complementar", "LEI": "Lei Ordinária"}[sigla]
    numero = int(m[2].replace(".", ""))
    ano = ano4(m[3])
    data = e["dataHora"][:10]
    dou = None
    md = PADRAO_DOU.search(desp)
    if md:
        try:
            dou = date(ano4(md[3]), int(md[2]), int(md[1])).isoformat()
        except ValueError:
            dou = None
    dif = (date.fromisoformat(dou) - date.fromisoformat(data)).days if dou else None
    return {"sigla": sigla, "tipo": nome, "numero": numero, "ano": ano, "rotulo": f"{sigla} {numero}/{ano}", "data": data, "dou": dou,
            "difDou": dif, "confere": dif is not None and 0 <= dif <= TOLERANCIA_DOU, "despacho": desp[:160]}


def tramitacao_da_norma(id_prop, dh):
    cam = CACHE_C / "tram" / f"{id_prop}.json"
    c = ler_cache(cam)
    if c is None or c.get("dh") != dh:
        r = coleta.get(f"{API}/proposicoes/{id_prop}/tramitacoes")
        c = {"dh": dh, "tram": (r or {}).get("dados") or []}
        gravar_cache(cam, c)
    return c["tram"]


def autores_de(id_prop):
    cam = CACHE_C / "autores" / f"{id_prop}.json"
    c = ler_cache(cam)
    if c is None:
        r = coleta.get(f"{API}/proposicoes/{id_prop}/autores")
        c = (r or {}).get("dados") or []
        gravar_cache(cam, c)
    return c


def estado_de(d):
    cod = (d.get("statusProposicao") or {}).get("codSituacao")
    if cod is None:
        return "sem_situacao"
    if cod == SIT_LEI:
        return "norma"
    if cod == SIT_VETADA or cod in SIT_ENCERRADAS:
        return "encerrada"
    if cod == SIT_APENSADA:
        return "apensada"
    return "tramitando"


def coletar_agenda(usar_rede, ultima):
    """Lista, baixa o detalhe e devolve {id: (detalhe, temas, tipo)} do universo, conforme a política de atualização."""
    universo = {}
    ano_final = HOJE.year
    if usar_rede:
        print("Agenda: listando PEC e PLP com tema oficial 40 ou 70 por ano de apresentação…", flush=True)
        for tipo in TIPOS_AGENDA:
            for ano in range(ANO_INICIO, ano_final + 1):
                for tema in TEMAS:
                    for i in listar_ids(tipo, tema, ano, forcar=ano >= ano_final - 1):
                        universo.setdefault(i, {"tipo": tipo, "temas": set()})["temas"].add(tema)
        gravar_cache(CACHE_C / "universo.json", {str(k): {"tipo": v["tipo"], "temas": sorted(v["temas"])} for k, v in universo.items()})
    else:
        u = ler_cache(CACHE_C / "universo.json")
        if not u:
            raise Aborta("--so-montar sem cache da agenda: rode contexto.py inteiro antes")
        universo = {int(k): {"tipo": v["tipo"], "temas": set(v["temas"])} for k, v in u.items()}

    # PL ordinário: só as que podem ter virado lei na janela (andamento nos últimos ~13 meses). `visto` = último dia em que
    # a API a mostrou com andamento; quem não anda há mais de ~15 meses sai da lista (não pode mais cair na janela).
    pl = ler_cache(CACHE_C / "pl_vistos.json") or {}
    if usar_rede:
        ini = date.fromisoformat(ultima) - timedelta(days=1) if (pl and ultima) else HOJE - timedelta(days=JANELA_SANCIONADAS_DIAS + 30)
        for tema in TEMAS:
            for i in ids_com_andamento("PL", tema, ini, HOJE):
                e = pl.setdefault(str(i), {"temas": []})
                if tema not in e["temas"]:
                    e["temas"].append(tema)
                e["visto"] = HOJE.isoformat()
        corte = (HOJE - timedelta(days=JANELA_SANCIONADAS_DIAS + 90)).isoformat()
        pl = {k: v for k, v in pl.items() if v.get("visto", HOJE.isoformat()) >= corte}
        gravar_cache(CACHE_C / "pl_vistos.json", pl)
    for k, v in pl.items():
        universo.setdefault(int(k), {"tipo": "PL", "temas": set()})["temas"].update(v["temas"])

    # detalhes: o que falta, o que andou desde a última rodada e o que está aberto e é velho (cada arquivo é lido uma vez)
    mudou = set()
    if usar_rede and ultima:
        a = date.fromisoformat(ultima) - timedelta(days=1)
        for tipo in TIPOS_AGENDA + ("PL",):
            for tema in TEMAS:
                mudou |= ids_com_andamento(tipo, tema, a, HOJE)
    dets, falta = {}, []
    for i in universo:
        cam = CACHE_C / "det" / f"{i}.json"
        d = ler_cache(cam)
        if d is None:
            falta.append((i, False))
        elif usar_rede and (i in mudou or (idade_dias(cam) > 7 and estado_de(d) in ("tramitando", "apensada", "sem_situacao"))):
            falta.append((i, True))
        if d is not None:
            dets[i] = d
    if usar_rede and falta:
        print(f"Agenda: {len(falta)} detalhes a baixar ({sum(1 for _, r in falta if r)} atualizações)…", flush=True)
        with ThreadPoolExecutor(coleta.WORKERS) as ex:
            for (i, _), d in zip(falta, ex.map(lambda x: detalhe(x[0], x[1]), falta)):
                if d is not None:
                    dets[i] = d
    return {i: (dets[i], v["temas"], v["tipo"]) for i, v in universo.items() if i in dets}


def carregar_excecoes():
    return (ler_cache(EXCECOES) or {}).get("normas", {})


def montar_agenda(universo, usar_rede):
    """Devolve (agenda, marcos, escopo_ia, relatorio)."""
    deputados = {d["id"]: d for d in (ler_cache(DADOS / "deputados.json") or [])}
    res = resumos.carregar()
    obj = objetivos.carregar()
    excecoes = carregar_excecoes()
    inicio_janela = (HOJE - timedelta(days=JANELA_SANCIONADAS_DIAS)).isoformat()
    sanc, tram, par = [], [], []
    marcos = []
    cont = Counter()
    sem_norma, divergentes = [], []   # sem_norma: (número, dia do último andamento)

    # tramitações das normas (EC/LC de PEC/PLP, e PL com andamento recente) — só quem está em situação de lei
    corte_pl = (HOJE - timedelta(days=JANELA_SANCIONADAS_DIAS + 120)).isoformat()
    normas_ids = [i for i, (d, _, tp) in universo.items() if estado_de(d) == "norma"
                  and (tp != "PL" or d["statusProposicao"]["dataHora"][:10] >= corte_pl)]
    if usar_rede:
        with ThreadPoolExecutor(coleta.WORKERS) as ex:
            tramitacoes = dict(zip(normas_ids, ex.map(
                lambda i: tramitacao_da_norma(i, universo[i][0]["statusProposicao"]["dataHora"]), normas_ids)))
    else:
        tramitacoes = {i: ((ler_cache(CACHE_C / "tram" / f"{i}.json") or {}).get("tram") or []) for i in normas_ids}

    def item_base(i, d, temas):
        st = d["statusProposicao"]
        ult = (st.get("dataHora") or "")[:10]
        return {"id": i, "numero": f"{d['siglaTipo']} {d['numero']}/{d['ano']}", "tipo": d["siglaTipo"],
                "ementa": (d.get("ementa") or "").strip(), "temas": [TEMAS[t] for t in sorted(temas)],
                "apresentacao": (d.get("dataApresentacao") or "")[:10],
                "situacao": st.get("descricaoSituacao"), "orgao": st.get("siglaOrgao"),
                "ultimoAndamento": ult,    # os dias sem andamento a página calcula (assim o diff diário só mostra quem andou)
                "url": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={i}"}

    for i, (d, temas, tp) in sorted(universo.items()):
        e = estado_de(d)
        cont[(tp, e)] += 1
        it = item_base(i, d, temas)
        if e == "norma":
            n = norma_da_tramitacao(tramitacoes.get(i) or [])
            exc = excecoes.get(it["numero"])
            if n is None and exc:
                n = {"sigla": exc["sigla"], "tipo": exc["tipo"], "numero": exc["numero"], "ano": exc["ano"],
                     "rotulo": f"{exc['sigla']} {exc['numero']}/{exc['ano']}", "data": exc["data"], "dou": None, "difDou": None,
                     "confere": True, "despacho": None, "excecao": {"fonte": exc["fonte"], "url": exc["url"]}}
            if n is None:
                sem_norma.append((it["numero"], it["ultimoAndamento"]))
                continue
            if n["difDou"] is not None and not n["confere"]:
                divergentes.append((it["numero"], n["rotulo"], n["data"], n["dou"]))
            it["norma"] = n
            if tp in ("PEC", "PLP"):
                marcos.append({"id": i, "data": n["data"], "rotulo": n["rotulo"], "sigla": n["sigla"], "numero": it["numero"],
                               "ementa": it["ementa"], "temas": it["temas"], "url": it["url"],
                               "dataConfere": n["confere"], "dou": n["dou"]})
            if n["data"] >= inicio_janela:
                sanc.append(it)
        elif tp == "PL":
            continue
        elif e == "tramitando":
            dias = (HOJE - date.fromisoformat(it["ultimoAndamento"])).days if it["ultimoAndamento"] else None
            (par if dias is not None and dias > DIAS_PARADA else tram).append(it)
        # apensadas, encerradas e sem situação: só contadas

    # sancionada na janela exibida sem número ou data da norma → aborta (regra do projeto)
    janela_sem = [n for n, ult in sem_norma if ult >= inicio_janela]
    if janela_sem:
        raise Aborta(f"sancionada(s) na janela sem número ou data da norma: {', '.join(janela_sem)}")

    # autoria, resumo e objetivo dos itens exibidos. Os autores vêm em paralelo (a 1ª rodada são mais de mil chamadas).
    exibidos = sanc + tram + par
    if usar_rede:
        with ThreadPoolExecutor(coleta.WORKERS) as ex:
            list(ex.map(autores_de, [it["id"] for it in exibidos]))

    def completar(it, com_texto):
        cam = CACHE_C / "autores" / f"{it['id']}.json"
        aut = (ler_cache(cam) or []) if cam.exists() else []
        principais = [a for a in aut if a.get("proponente") == 1] or aut[:1]
        pessoas = []
        for a in principais[:3]:
            m = re.search(r"/deputados/(\d+)", a.get("uri") or "")
            dep = deputados.get(int(m[1])) if m else None
            pessoas.append({"nome": a.get("nome"), "tipo": a.get("tipo"),
                            "partido": dep["partido"] if dep else None, "uf": dep["uf"] if dep else None})
        it["autoria"] = {"principais": pessoas, "total": len(aut)}
        if com_texto:
            it["resumo"] = res.get(it["id"])
            o = obj.get(it["id"])
            it["objetivo"] = {"trecho": o["o"], "fonte": o.get("f")} if o and o.get("o") else None
            it["objetivoStatus"] = "achado" if it["objetivo"] else ((o.get("motivo") or "não informado") if o else "pendente")

    for lista, com in ((sanc, True), (tram, True), (par, False)):
        for it in lista:
            completar(it, com)

    sanc.sort(key=lambda x: (x["norma"]["data"], x["id"]), reverse=True)
    tram.sort(key=lambda x: (x["ultimoAndamento"], x["id"]), reverse=True)
    par.sort(key=lambda x: (x["ultimoAndamento"], x["id"]), reverse=True)
    marcos.sort(key=lambda m: (m["data"], m["id"]))

    cont_tipo = lambda e: {tp: cont[(tp, e)] for tp in ("PEC", "PLP") if cont[(tp, e)]}   # PL só entra nas sancionadas
    agenda = {
        "janelaSancionadas": {"de": inicio_janela, "ate": HOJE.isoformat()},
        "contagens": {
            "sancionadasJanela": dict(Counter(x["tipo"] for x in sanc)),
            "sancionadasHistorico": {tp: cont[(tp, "norma")] for tp in ("PEC", "PLP")},
            "emTramitacao": dict(Counter(x["tipo"] for x in tram)),
            "paradas": dict(Counter(x["tipo"] for x in par)),
            "apensadas": cont_tipo("apensada"),
            "encerradas": cont_tipo("encerrada"),
            "semSituacao": cont_tipo("sem_situacao"),
            "universo": {tp: sum(v for (t, e), v in cont.items() if t == tp) for tp in ("PEC", "PLP")},
        },
        "normasSemNumero": [n for n, _ in sem_norma],
        "normasComDataDivergente": [{"proposicao": a, "norma": b, "dataEvento": c, "dataDou": d} for a, b, c, d in divergentes],
        "sancionadas": sanc, "emTramitacao": tram, "paradas": par,
    }
    escopo = [{"id": it["id"], "numero": it["numero"], "ementa": it["ementa"],
               "urlInteiroTeor": universo[it["id"]][0].get("urlInteiroTeor")} for it in sanc + tram]
    return agenda, marcos, escopo, {"semNorma": [n for n, _ in sem_norma], "divergentes": divergentes}


def contar_marcos(marcos):
    por_ano = Counter(int(m["data"][:4]) for m in marcos)
    return {a: por_ano.get(a, 0) for a in range(ANO_INICIO, HOJE.year + 1)}


# ---------------------------------------------------------------- MANDATOS PRESIDENCIAIS

URL_WIKIDATA = "https://query.wikidata.org/sparql"
CARGO_PRESIDENTE = "Q5176750"       # "Presidente do Brasil" no Wikidata: cada mandato é uma declaração P39 própria, com início e fim
MANDATOS_DESDE = "1980-01-01"       # a série mais antiga das páginas (IPCA) começa em dez/1980
ENTRADA_MANDATOS = ENTRADAS / "mandatos_presidenciais.json"
VALIDADE_MANDATOS = timedelta(days=7)   # um mandato novo só aparece em dia de posse: uma consulta por semana basta
MAX_ENTRE_MANDATOS = 100            # dias entre o fim de um mandato e o início do seguinte (Collor → Itamar: 88)
MAX_EM_EXERCICIO = 130              # dias da posse em exercício até a efetivação (Temer: 111)
MIN_MANDATOS_DESDE_1985 = 12        # Sarney, Collor, Itamar, FHC ×2, Lula ×2, Dilma ×2, Temer, Bolsonaro, Lula III
ROMANOS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
CONSULTA_MANDATOS = """SELECT ?pessoa ?nome ?inicio ?precisao ?fim WHERE {
  ?pessoa wdt:P31 wd:Q5 ; p:P39 ?st .
  ?st ps:P39 wd:%s ; pqv:P580 ?vi .
  ?vi wikibase:timeValue ?inicio ; wikibase:timePrecision ?precisao .
  OPTIONAL { ?st pq:P582 ?fim }
  OPTIONAL { ?pessoa rdfs:label ?pt . FILTER(LANG(?pt) = "pt") }
  OPTIONAL { ?pessoa rdfs:label ?en . FILTER(LANG(?en) = "en") }
  BIND(COALESCE(?pt, ?en) AS ?nome)
} ORDER BY ?inicio"""   # só pessoas (P31 = Q5): o Wikidata tem um item de eleição com P39 que não é presidente


def coletar_mandatos(rede):
    """Linhas do Wikidata ({item, nome, inicio, precisao, fim}); do cache se tem menos de 7 dias ou se não há rede."""
    caminho = CACHE_C / "mandatos_wikidata.json"
    cache = ler_cache(caminho)
    if cache:
        idade = datetime.now(BRASILIA) - datetime.fromisoformat(cache["consultadoEm"])
        if not rede or idade < VALIDADE_MANDATOS:
            return cache
    if not rede:
        raise Aborta("os mandatos presidenciais ainda não foram consultados (rode sem --so-montar)")
    resposta = wikipedia.pedir(URL_WIKIDATA, {"query": CONSULTA_MANDATOS % CARGO_PRESIDENTE, "format": "json"}, 1.0)
    if not resposta:
        raise Aborta("o Wikidata não respondeu à consulta dos mandatos presidenciais")
    vistos, linhas = set(), []
    for b in resposta["results"]["bindings"]:
        item, inicio = b["pessoa"]["value"].rsplit("/", 1)[1], b["inicio"]["value"][:10]
        if (item, inicio) in vistos:       # a pessoa com dois rótulos no mesmo idioma sairia duas vezes
            continue
        vistos.add((item, inicio))
        linhas.append({"item": item, "nome": b["nome"]["value"] if "nome" in b else item, "inicio": inicio,
                       "precisao": int(b["precisao"]["value"]), "fim": b["fim"]["value"][:10] if "fim" in b else None})
    bruto = {"consultadoEm": datetime.now(BRASILIA).isoformat(timespec="minutes"), "linhas": sorted(linhas, key=lambda l: l["inicio"])}
    gravar_cache(caminho, bruto)
    return bruto


def montar_mandatos(bruto):
    """Valida as linhas do Wikidata e devolve o que a página desenha. Qualquer divergência aborta: o arquivo anterior fica."""
    entrada = ler_cache(ENTRADA_MANDATOS) or {}
    curtos = {k: v["curto"] for k, v in entrada.get("nomes", {}).items()}
    em_exercicio = {e["item"]: e for e in entrada.get("posses_em_exercicio", [])}
    dia = date.fromisoformat
    linhas = [dict(l) for l in bruto["linhas"]]

    # o ordinal conta TODOS os mandatos da pessoa no cargo, antes de qualquer corte de data: "Lula I, II, III"
    por_pessoa = {}
    for l in linhas:
        por_pessoa.setdefault(l["item"], []).append(l)
    for rs in por_pessoa.values():
        for i, l in enumerate(sorted(rs, key=lambda x: x["inicio"]), 1):
            l["ordem"], l["de"] = i, len(rs)

    seq = [l for l in linhas if l["inicio"] >= "1979-01-01"]      # inclui Figueiredo, só para a conferência de continuidade
    if sum(l["inicio"] >= "1985-01-01" for l in seq) < MIN_MANDATOS_DESDE_1985:
        raise Aborta(f"mandatos: o Wikidata trouxe menos de {MIN_MANDATOS_DESDE_1985} mandatos desde 1985; nada gravado")
    for l in seq:
        if l["precisao"] != 11:
            raise Aborta(f"mandatos: a data de início de {l['nome']} ({l['inicio']}) não tem precisão de dia no Wikidata")
        if l["inicio"] > HOJE.isoformat():
            raise Aborta(f"mandatos: {l['nome']} começa no futuro ({l['inicio']})")
    for a, b in zip(seq, seq[1:]):
        if a["fim"] is None:
            raise Aborta(f"mandatos: o mandato de {a['nome']} que começa em {a['inicio']} está sem fim, e há outro depois")
        folga = (dia(b["inicio"]) - dia(a["fim"])).days
        if not -1 <= folga <= MAX_ENTRE_MANDATOS:
            raise Aborta(f"mandatos: {a['nome']} termina em {a['fim']} e {b['nome']} começa em {b['inicio']} ({folga} dias entre os dois)")

    # posse em exercício: vale só se o Wikidata traz EXATAMENTE a data de efetivação declarada em entradas/mandatos_presidenciais.json
    usadas = set()
    for l in seq:
        e = em_exercicio.get(l["item"])
        if not e or l["inicio"] != e["efetivadoEm"]:
            continue
        if not 0 < (dia(e["efetivadoEm"]) - dia(e["inicio"])).days <= MAX_EM_EXERCICIO:
            raise Aborta(f"mandatos: a posse em exercício de {l['nome']} ({e['inicio']}) está longe da efetivação ({e['efetivadoEm']})")
        l["efetivadoEm"], l["inicio"], l["nota"], l["fonteNota"] = l["inicio"], e["inicio"], e["nota"], e["fonte"]
        usadas.add(e["item"])
    if set(em_exercicio) - usadas:
        raise Aborta("mandatos: posse em exercício sem mandato igual no Wikidata (a data de efetivação mudou?): " + ", ".join(sorted(set(em_exercicio) - usadas)))
    seq.sort(key=lambda l: l["inicio"])
    if len({l["inicio"] for l in seq}) != len(seq):
        raise Aborta("mandatos: duas posses no mesmo dia")

    # a tabela de governos (que o governo.py usa) tem de bater com a lista: cada governo dela começa num mandato daqui
    comecos = {l["inicio"] for l in seq}
    for g in (ler_cache(ENTRADAS / "governos.json") or {}).get("governos", []):
        if g["inicio"] not in comecos:
            raise Aborta(f"mandatos: entradas/governos.json começa '{g['rotulo']}' em {g['inicio']}, e a lista do Wikidata não tem mandato nessa data")

    saida = []
    for l in seq:
        if l["inicio"] < MANDATOS_DESDE:
            continue
        curto = curtos.get(l["item"])
        if not curto:
            aviso(f"sem nome curto para {l['nome']} ({l['item']}) em entradas/mandatos_presidenciais.json: o rótulo sai com o nome inteiro")
            curto = l["nome"]
        o = {"rotulo": curto if l["de"] == 1 else f"{curto} {ROMANOS[l['ordem'] - 1]}", "presidente": l["nome"], "inicio": l["inicio"],
             "item": l["item"], "url": f"https://www.wikidata.org/wiki/{l['item']}"}
        if "efetivadoEm" in l:
            o.update(efetivadoEm=l["efetivadoEm"], nota=l["nota"], fonteNota=l["fonteNota"])
        saida.append(o)
    return {"consultadoEm": bruto["consultadoEm"],
            "fonte": {"nome": "Wikidata", "cargo": "Presidente do Brasil", "url": f"https://www.wikidata.org/wiki/{CARGO_PRESIDENTE}"},
            "regra": "Cada marca é o dia em que a pessoa assumiu a Presidência da República, inclusive em exercício. É só referência de calendário.",
            "mandatos": saida}


def gravar_mandatos(rede, gerado):
    """Consulta (ou lê do cache), valida e grava mandatos.json. A exceção sobe: quem chama decide como falhar."""
    m = montar_mandatos(coletar_mandatos(rede))
    gravar_json(SAIDA / "mandatos.json", {"geradoEm": gerado, "hoje": HOJE.isoformat(), **m}, linhas=("mandatos",))
    return m


# ---------------------------------------------------------------- saída

def gravar_json(caminho, obj, linhas=()):
    """Grava atômico. `linhas`: chaves cujas listas vão um item por linha (o diff diário do git mostra só o que mudou)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    partes = [f"{dump(k)}:{dump(v)}" for k, v in obj.items() if k not in linhas]
    partes += [f'{dump(k)}:[\n' + ",\n".join(dump(x) for x in obj[k]) + "\n]" for k in linhas if k in obj]
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text("{" + ",\n".join(partes) + "}\n", encoding="utf-8")
    os.replace(tmp, caminho)


def pontos_dos_indicadores(ind):
    return sum(len(s["pontos"]) for b in ind["blocos"].values() if isinstance(b, dict) for s in b.get("series", []))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--so-montar", action="store_true", help="não chama API: remonta os JSON do cache")
    ap.add_argument("--so-mandatos", action="store_true", help="só os mandatos presidenciais: consulta o Wikidata e grava mandatos.json")
    ap.add_argument("--teste", action="store_true", help="testes sem rede da regra 'cada fonte falha sozinha'")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    rede = not args.so_montar
    ultima = (ler_cache(CACHE_C / "estado.json") or {}).get("ultima")
    gerado = datetime.now(BRASILIA).isoformat(timespec="minutes")
    falhas = []   # cada bloco falha sozinho: o arquivo anterior do bloco que falhou fica, e o outro bloco segue

    def falhar(bloco, e):
        txt = e if isinstance(e, str) else str(e) if isinstance(e, Aborta) else f"{type(e).__name__}: {e}"
        falhas.append(f"{bloco}: {txt}")
        # no título da anotação do GitHub, vírgula e dois-pontos separam propriedades: sem o escape, "indicadores, Tesouro Nacional" virava
        # o título "contexto (indicadores" (rodada de 7/11/2026)
        titulo = bloco.replace("%", "%25").replace(",", "%2C").replace(":", "%3A")
        corpo = txt.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=contexto ({titulo})::{corpo}" if os.environ.get("GITHUB_ACTIONS") else f"FALHOU ({bloco}): {txt}", flush=True)

    def resumo_mandatos(m):
        ms = m["mandatos"]
        print(f"Mandatos presidenciais: {len(ms)}, de {ms[0]['rotulo']} ({ms[0]['inicio']}) a {ms[-1]['rotulo']} ({ms[-1]['inicio']}); consulta de {m['consultadoEm']}")

    if args.so_mandatos:      # atalho para testar a página: só o bloco dos mandatos
        try:
            resumo_mandatos(gravar_mandatos(True, gerado))
        except Exception as e:
            falhar("mandatos", e)
            sys.exit(1)
        return

    # ---- indicadores: cada fonte (IBGE, Banco Central, Tesouro) e cada bloco falham sozinhos (ver GRUPOS)
    B = indicadores = None
    try:
        anterior = ler_cache(SAIDA / "indicadores.json")
        br, falhas_fonte = montar_indicadores(rede)
        for nome, txt in falhas_fonte:
            falhar(f"indicadores, {nome}", txt)
        B, falhas_bloco = construir_indicadores(br, anterior)
        for bid, txt in falhas_bloco:
            falhar(f"indicadores, bloco {bid}", txt)
        datas = [b["coletadoEm"] for b in B.values() if isinstance(b, dict) and b.get("series") and b.get("coletadoEm")]
        # coletadoEm = a coleta MAIS ANTIGA entre os blocos publicados: é a data que frescor.py confere (um bloco parado não some na média)
        indicadores = {"geradoEm": gerado, "hoje": HOJE.isoformat(), "coletadoEm": min(datas) if datas else None, "blocos": B}
        if anterior and pontos_dos_indicadores(anterior) and pontos_dos_indicadores(indicadores) < 0.9 * pontos_dos_indicadores(anterior):
            raise Aborta(f"os indicadores encolheram de {pontos_dos_indicadores(anterior)} para {pontos_dos_indicadores(indicadores)} pontos; nada gravado")
        if rede and not frescos(B):
            raise Aborta("nenhum bloco com dado novo nesta rodada; o arquivo anterior fica como está")
    except Exception as e:      # Aborta (checagem) ou falha de rede/formato: nada é gravado deste bloco
        falhar("indicadores", e)
        indicadores = None

    # ---- agenda e marcos
    agenda = marcos = escopo = rel = por_ano = None
    try:
        universo = coletar_agenda(rede, ultima)
        agenda, marcos, escopo, rel = montar_agenda(universo, rede)
        por_ano = contar_marcos(marcos)
        ant = ler_cache(SAIDA / "agenda.json")
        if ant:
            n_ant = len(ant.get("paradas", [])) + len(ant.get("emTramitacao", []))
            if n_ant and len(agenda["paradas"]) + len(agenda["emTramitacao"]) < 0.8 * n_ant:
                raise Aborta("a agenda encolheu mais de 20%; nada gravado")
    except Exception as e:
        falhar("agenda", e)
        agenda = None

    # ---- mandatos presidenciais (referência de calendário nos gráficos; falha sozinho)
    try:
        resumo_mandatos(gravar_mandatos(rede, gerado))
    except Exception as e:
        falhar("mandatos", e)

    # ---- mais indicadores: trabalho, renda e setor externo (módulo irmão; falha sozinho e mantém o arquivo anterior)
    try:
        import contexto_trabalho
        t = contexto_trabalho.gerar(sys.modules[__name__], rede, gerado)
        print(f"Trabalho, renda e setor externo: {len(t['secoes'])} seções, {len(t['graficos'])} gráficos, {contexto_trabalho.pontos_do_bloco(t)} pontos")
    except Exception as e:
        falhar("trabalho", e)

    # ---- abertura comercial (Contas Nacionais; módulo irmão, também falha sozinho e mantém o arquivo anterior)
    try:
        import contexto_abertura
        ab = contexto_abertura.gerar(sys.modules[__name__], rede, gerado)
        print(f"Abertura comercial: {len(ab['secoes'])} seção, {contexto_abertura.pontos_do_bloco(ab)} pontos")
    except Exception as e:
        falhar("abertura", e)

    if indicadores:
        gravar_json(SAIDA / "indicadores.json", indicadores)
    if agenda:
        gravar_json(SAIDA / "agenda.json", {"geradoEm": gerado, "hoje": HOJE.isoformat(), **agenda},
                    linhas=("sancionadas", "emTramitacao", "paradas"))
        gravar_json(SAIDA / "marcos.json", {"geradoEm": gerado, "total": len(marcos), "porAno": por_ano, "marcos": marcos}, linhas=("marcos",))
        gravar_cache(CACHE_C / "escopo.json", escopo)
        for it in escopo:   # resumos.py procura a URL do inteiro teor aqui antes de pedir à API
            meta = CACHE / "teor" / "meta" / f"{it['id']}.json"
            if not meta.exists() and it["urlInteiroTeor"]:
                gravar_cache(meta, {"urlInteiroTeor": it["urlInteiroTeor"]})
    if rede and indicadores and agenda:   # a janela incremental só avança quando a rodada inteira deu certo
        gravar_cache(CACHE_C / "estado.json", {"ultima": HOJE.isoformat()})

    if indicadores:
        print("\nIndicadores (último período):", {k: b.get("referencia") for k, b in B.items() if isinstance(b, dict) and "series" in b})
    if agenda:
        print("Contagens da agenda:", json.dumps(agenda["contagens"], ensure_ascii=False))
        print(f"Marcos: {len(marcos)} (EC e LC com tema oficial 40 ou 70)")
        print("Marcos por ano:", " ".join(f"{a}:{n}" for a, n in por_ano.items()))
        print("Anos sem nenhum marco:", ", ".join(str(a) for a, n in por_ano.items() if n == 0) or "nenhum")
        if rel["semNorma"]:
            aviso(f"{len(rel['semNorma'])} norma(s) sem número no registro da Câmara e sem exceção em normas_excecoes.json: {', '.join(rel['semNorma'])}")
        if rel["divergentes"]:
            aviso(f"{len(rel['divergentes'])} norma(s) com data do evento diferente da data do DOU: " + "; ".join(f"{a} ({b}): evento {c_}, DOU {d}" for a, b, c_, d in rel["divergentes"]))
        print(f"Escopo de IA (sancionadas na janela + em tramitação): {len(escopo)} itens → cache/contexto/escopo.json")
    if falhas:
        sys.exit(1)


if __name__ == "__main__":
    main()
