"""
Câmara Aberta — página "Contexto econômico" (site/dados/contexto/*.json).

Uso:
    python scripts/contexto.py               # coleta (APIs, com cache), confere e grava os três JSON
    python scripts/contexto.py --so-montar   # não chama API nenhuma: remonta os JSON do cache (depois de resumos.py e objetivos.py)
    python scripts/contexto.py --so-mandatos # só o bloco 4 (consulta o Wikidata e grava mandatos.json): rápido, para testar a página

Quatro blocos, todos por API (nada de planilha, nada de tabela curada à mão onde há API):
  1. INDICADORES — IPCA (IBGE/SIDRA), meta de inflação e banda (JSON do site do Banco Central, conferido contra o
     SGS 13521 e contra o IPCA), PIB (IBGE/SIDRA), dívida bruta e líquida do Governo Geral (BCB/SGS) e despesa,
     resultado primário e nominal do Governo Central (API de Séries Temporais do Tesouro, valores reais pelo IPCA).
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

def buscar(url, params=None, limitador=LIM_OUTROS, tentativas=5):
    for n in range(tentativas):
        limitador.esperar()
        try:
            r = coleta.sessao.get(url, params=params, timeout=90)
        except (coleta.requests.ConnectionError, coleta.requests.Timeout):
            time.sleep(2 ** n)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** n)
            continue
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:      # corpo vazio ou página de bloqueio no lugar do JSON (o SGS faz isso de vez em quando): repete
            time.sleep(2 ** n)
            continue
    raise RuntimeError(f"falhou após {tentativas} tentativas: {url} {params or ''}")


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


def montar_indicadores(forcar_rede):
    """Baixa (ou lê do cache) as séries, confere e devolve o bloco `indicadores`."""
    arq = CACHE_C / "indicadores_brutos.json"
    if forcar_rede:
        br = {}
        print("Indicadores: IPCA, PIB (IBGE), metas (BCB), dívida (BCB), despesa e resultado (Tesouro)…", flush=True)
        br["ipca12"] = sidra(1737, 2265)
        br["ipcaMes"] = sidra(1737, 63)
        br["pib4t"] = sidra(5932, 6562, "11255[90707]", trimestral=True)
        br["pibTri"] = sidra(5932, 6564, "11255[90707]", trimestral=True)
        br["dbgg"] = sgs(13762)
        br["dlgg"] = sgs(4536)
        br["meta_sgs"] = sgs(13521)
        br["despesa"] = tesouro("10.03.1", True)
        br["primario"] = tesouro("10.04.1", True)
        br["nominal"] = tesouro("10.09.1", True)
        j = buscar(URL_BCB_METAS)
        br["metas_pagina"] = json.dumps(j, ensure_ascii=False)
        br["bcb12m"] = buscar(URL_BCB_12M)
        br["coletadoEm"] = HOJE.isoformat()
        gravar_cache(arq, br)
    else:
        br = ler_cache(arq)
        if not br:
            raise Aborta("--so-montar sem cache de indicadores: rode contexto.py inteiro antes")
    return br


def derivar_indicadores(br):
    """Confere as fontes entre si e monta as séries que a página desenha."""
    S = {k: v for k, v in br.items() if isinstance(v, dict) and "pontos" in v}
    for k in S:
        S[k]["pontos"] = [tuple(p) for p in S[k]["pontos"]]
    # ---- unidades e faixas plausíveis (aborta se a fonte mudou de sentido)
    checar_serie("IPCA 12 meses", S["ipca12"]["pontos"], "M", -10, 10000, S["ipca12"]["unidade"], "%")
    checar_serie("IPCA mensal", S["ipcaMes"]["pontos"], "M", -10, 100, S["ipcaMes"]["unidade"], "%")
    checar_serie("PIB 4 trimestres", S["pib4t"]["pontos"], "T", -15, 15, S["pib4t"]["unidade"], "%")
    checar_serie("PIB trimestre/trimestre", S["pibTri"]["pontos"], "T", -15, 15, S["pibTri"]["unidade"], "%")
    checar_serie("Dívida bruta (DBGG)", S["dbgg"]["pontos"], "M", 0, 200)
    checar_serie("Dívida líquida do Governo Geral", S["dlgg"]["pontos"], "M", 0, 200)
    for k, nome in (("despesa", "Despesa total"), ("primario", "Resultado primário"), ("nominal", "Resultado nominal")):
        checar_serie(nome, S[k]["pontos"], "M", -1_500_000, 1_500_000)
    for k, esperado in (("despesa", "Despesa total"), ("primario", "Resultado Primário - Governo Central"),
                        ("nominal", "Resultado Nominal do Governo Central")):
        if S[k]["nome"] != esperado:
            raise Aborta(f"Tesouro: o nome da série mudou ({S[k]['nome']!r}, esperado {esperado!r})")
    if (S["dbgg"].get("unidade") or "Percentual") != "Percentual" or (S["dlgg"].get("unidade") or "Percentual") != "Percentual":
        raise Aborta("BCB: a unidade da dívida deixou de ser Percentual (% do PIB)")
    # ---- IPCA em dezembro × 'inflação efetiva' da tabela oficial do BC (1999 em diante)
    ipca = dict(S["ipca12"]["pontos"])
    metas = parse_metas(br["metas_pagina"])
    if len(metas) < 26 or min(metas) != 1999:
        raise Aborta(f"BCB: a tabela de metas veio com {len(metas)} anos (esperado 26 ou mais, desde 1999)")
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
    # ---- derivados do Tesouro: soma de 12 meses (R$ do último mês da série) e crescimento real da despesa
    base = S["despesa"]["pontos"][-1][0]
    desp12 = soma_12m(S["despesa"]["pontos"])
    prim12 = soma_12m(S["primario"]["pontos"])
    nom12 = soma_12m(S["nominal"]["pontos"])
    desp_cresc = variacao_12m_da_soma(desp12)
    # mesmo critério do PIB (4 trimestres terminados no trimestre): só os meses de fim de trimestre
    desp_trim = [(inicio_do_trimestre(m), v) for m, v in desp_cresc if m[5:7] in ("03", "06", "09", "12")]
    janela_comum = (max(desp_trim[0][0], S["pib4t"]["pontos"][0][0]),
                    min(desp_trim[-1][0], S["pib4t"]["pontos"][-1][0]))
    return S, metas, meta_pts, CONT, base, desp12, prim12, nom12, desp_trim, janela_comum


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


def construir_indicadores(br):
    S, metas, meta_pts, CONT, base, desp12, prim12, nom12, desp_trim, janela = derivar_indicadores(br)
    mes_base = base[:7]
    fonte_ibge = lambda tab, var: {"nome": f"IBGE — SIDRA, tabela {tab}", "url": f"https://sidra.ibge.gov.br/tabela/{tab}",
                                         "api": f"{URL_IBGE}/{tab}/periodos/all/variaveis/{var}"}
    fonte_sgs = lambda cod: {"nome": f"Banco Central — SGS {cod}", "url": f"https://dadosabertos.bcb.gov.br/dataset/{cod}",
                                   "api": URL_SGS.format(cod)}
    fonte_tes = {"nome": "Tesouro Nacional — API de Séries Temporais (Resultado do Tesouro Nacional, tema 10)",
                 "url": "https://www.tesourotransparente.gov.br/temas/estatisticas-fiscais-e-planejamento/resultado-do-tesouro-nacional-rtn",
                 "api": "https://apiapex.tesouro.gov.br/aria/v1/series-temporais/docs"}
    pib_ult = S["pib4t"]["pontos"]
    B = {}
    B["ipca"] = {
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
    B["pib"] = {
        "titulo": "Crescimento do PIB",
        "definicao": "Crescimento do volume do PIB nos últimos quatro trimestres em relação aos quatro trimestres imediatamente anteriores, segundo o IBGE (Contas Nacionais Trimestrais).",
        "fontes": [fonte_ibge(5932, 6562)], "referencia": pib_ult[-1][0], "unidade": "%",
        "series": [{"id": "pib4t", "nome": "PIB, acumulado em 4 trimestres", "unidade": "%", "freq": "T", "pontos": pontos_json(pib_ult, 2)}],
        "ultimo": fato_variacao(pib_ult, "%", 12, "T"),
        "trimestre": fato_variacao(S["pibTri"]["pontos"], "%", 3, "T") | {"nota": "variação sobre o trimestre anterior, com ajuste sazonal"},
        "anotacoes": [],
    }
    B["despesa"] = {
        "titulo": "Despesa do governo e crescimento do PIB",
        "definicao": "Despesa total do Governo Central (pagamento efetivo, Tesouro Nacional) corrigida pelo IPCA para reais de "
                     f"{mes_base[5:]}/{mes_base[:4]}: crescimento da soma de 12 meses sobre os 12 meses anteriores, ao lado do crescimento do PIB em 4 trimestres.",
        "fontes": [fonte_tes, fonte_ibge(5932, 6562)], "referencia": desp_trim[-1][0], "unidade": "%",
        "deflator": {"indice": "IPCA", "mesBase": mes_base, "quem": "calculado pela própria API do Tesouro (correcao_ipca=true)"},
        "janelaComum": list(janela),
        "series": [{"id": "despesa", "nome": "Despesa total do Governo Central, real (soma de 12 meses)", "unidade": "%", "freq": "T",
                    "pontos": pontos_json([(d, v) for d, v in desp_trim if janela[0] <= d <= janela[1]], 2)},
                   {"id": "pib", "nome": "PIB, volume (acumulado em 4 trimestres)", "unidade": "%", "freq": "T",
                    "pontos": pontos_json([(d, v) for d, v in pib_ult if janela[0] <= d <= janela[1]], 2)}],
        "ultimo": {"despesa": fato_variacao([(d, v) for d, v in desp_trim if d <= janela[1]], "%", 12, "T"),
                   "pib": fato_variacao([(d, v) for d, v in pib_ult if d <= janela[1]], "%", 12, "T")},
        "anotacoes": [
            {"data": "2012-03-01", "texto": "A partir de 1/3/2012 a despesa inclui recursos de complementação do FGTS e as despesas feitas com recursos dessa contribuição.",
             "fonte": "Resultado do Tesouro Nacional, nota 2 da série histórica", "url": fonte_tes["url"]}],
    }
    B["divida"] = {
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
    B["resultado"] = {
        "titulo": "Resultado primário e nominal do Governo Central",
        "definicao": "Resultado primário: receitas menos despesas do Governo Central, sem contar os juros da dívida (apurado pelo Tesouro, 'acima da linha'). "
                     "Resultado nominal: o primário mais os juros nominais, apurado pelo Banco Central ('abaixo da linha'). Negativo = déficit.",
        "fontes": [fonte_tes], "referencia": prim12[-1][0], "unidade": "R$ milhões",
        "deflator": {"indice": "IPCA", "mesBase": mes_base, "quem": "calculado pela própria API do Tesouro (correcao_ipca=true)"},
        "series": [{"id": "primario", "nome": "Resultado primário, soma de 12 meses", "unidade": "R$ milhões", "freq": "M", "pontos": pontos_json(prim12, 1)},
                   {"id": "nominal", "nome": "Resultado nominal, soma de 12 meses", "unidade": "R$ milhões", "freq": "M", "pontos": pontos_json(nom12, 1)}],
        "ultimo": {"primario": fato_variacao(prim12, "R$ milhões"), "nominal": fato_variacao(nom12, "R$ milhões")},
        "anotacoes": [
            {"data": "2012-03-01", "texto": "A partir de 1/3/2012 a despesa inclui recursos de complementação do FGTS e as despesas feitas com recursos dessa contribuição.",
             "fonte": "Resultado do Tesouro Nacional, nota 2 da série histórica", "url": fonte_tes["url"]}],
    }
    B["ifi"] = {"titulo": "Projeções da Instituição Fiscal Independente",
                "definicao": "A IFI, órgão do Senado Federal, publica projeções para os indicadores fiscais; o site não as reproduz.",
                "url": "https://www12.senado.leg.br/ifi/publicacoes-1/relatorio-de-acompanhamento-fiscal"}
    return B


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
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    rede = not args.so_montar
    ultima = (ler_cache(CACHE_C / "estado.json") or {}).get("ultima")
    gerado = datetime.now(BRASILIA).isoformat(timespec="minutes")
    falhas = []   # cada bloco falha sozinho: o arquivo anterior do bloco que falhou fica, e o outro bloco segue

    def falhar(bloco, e):
        txt = str(e) if isinstance(e, Aborta) else f"{type(e).__name__}: {e}"
        falhas.append(f"{bloco}: {txt}")
        print(f"::error title=contexto ({bloco})::{txt}" if os.environ.get("GITHUB_ACTIONS") else f"FALHOU ({bloco}): {txt}", flush=True)

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

    # ---- indicadores
    B = indicadores = None
    try:
        br = montar_indicadores(rede)
        B = construir_indicadores(br)
        indicadores = {"geradoEm": gerado, "hoje": HOJE.isoformat(), "coletadoEm": br.get("coletadoEm"), "blocos": B}
        anterior = ler_cache(SAIDA / "indicadores.json")
        if anterior and pontos_dos_indicadores(anterior) and pontos_dos_indicadores(indicadores) < 0.9 * pontos_dos_indicadores(anterior):
            raise Aborta(f"os indicadores encolheram de {pontos_dos_indicadores(anterior)} para {pontos_dos_indicadores(indicadores)} pontos; nada gravado")
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
