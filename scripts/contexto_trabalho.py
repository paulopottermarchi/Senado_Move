"""
Câmara Aberta — bloco "Mais indicadores (trabalho, renda e setor externo)" da página de economia.

Uso (chamado por contexto.py, que passa o seu próprio módulo; também roda sozinho):
    python scripts/contexto_trabalho.py [--so-montar] [--imprimir] [--teste]

Grava site/dados/contexto/trabalho.json. Tudo por API oficial: IBGE (SIDRA, API de agregados) e Banco Central (SGS).
Regras (as mesmas da página de economia, em CLAUDE.md): sem semáforo, sem índice composto, sem seta nem cor que julgue,
sem causalidade, sem governo ou partido, dado indisponível = "Dados indisponíveis", nunca estimado nem interpolado.

- Cada série começa onde a fonte começa e mantém a frequência nativa (trimestre móvel, trimestral, anual, mensal).
- A série inteira é baixada de novo a cada execução (o IBGE revisa o passado, e os valores reais mudam de base todo mês).
- Checagens que ABORTAM (o arquivo anterior fica): lacuna na série (fora das lacunas oficiais conhecidas), unidade ou
  base diferente da esperada, valor fora da faixa plausível, data no futuro, série vazia, encolhimento, texto com
  palavra de julgamento. A identidade massa ≈ ocupados × rendimento médio só é REPORTADA.
- Os textos de abertura de cada seção são gerados dos números aqui, nunca escritos à mão.
"""

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MENOS = "−"
VERSAO = "trabalho-1"

# ------------------------------------------------------------------------------------------------ séries
# freq: M = mensal (trimestre móvel no SIDRA; mensal no SGS) · T = trimestral · A = anual
# `lacunas`: períodos que a própria fonte publica como "..." (dado não disponível). Qualquer outra lacuna aborta.
SIDRA = OrderedDict([
    ("rend_real", dict(tab=6390, var=5933, freq="M", unid="Reais", minimo=500, maximo=20_000)),
    ("rend_nom", dict(tab=6390, var=5929, freq="M", unid="Reais", minimo=300, maximo=20_000)),
    ("ocupados", dict(tab=6323, var=4090, cls="888[47946]", freq="M", unid="Mil pessoas", minimo=50_000, maximo=200_000)),
    ("massa_real", dict(tab=6392, var=6293, freq="M", unid="Milhões de Reais", minimo=50_000, maximo=2_000_000)),
    ("massa_nom", dict(tab=6392, var=6288, freq="M", unid="Milhões de Reais", minimo=50_000, maximo=2_000_000)),
    ("desocupacao", dict(tab=6381, var=4099, freq="M", unid="%", minimo=1, maximo=30)),
    ("informalidade", dict(tab=8513, var=12466, freq="M", unid="%", minimo=20, maximo=60)),
    ("subutilizacao", dict(tab=6441, var=4118, freq="M", unid="%", minimo=5, maximo=40,
                           lacunas={"2015-10-01", "2015-11-01"})),
    ("renda_pc", dict(tab=7533, var=10816, cls="1019[49243]", freq="A", unid="Reais", minimo=500, maximo=10_000,
                      nome_contem="a preços médios do último ano")),
    ("renda_p50", dict(tab=7526, var=10838, cls="1019[49264]", freq="A", unid="Reais", minimo=300, maximo=8_000,
                       nome_contem="a preços médios do último ano")),
    ("gini", dict(tab=7435, var=10681, freq="A", unid="Índice", minimo=0.2, maximo=0.8)),
    ("rndb", dict(tab=2072, var=938, freq="T", unid="Milhões de Reais", minimo=100_000, maximo=20_000_000)),
    ("pib_corrente", dict(tab=2072, var=933, freq="T", unid="Milhões de Reais", minimo=100_000, maximo=20_000_000)),
    ("rnb", dict(tab=2072, var=936, freq="T", unid="Milhões de Reais", minimo=100_000, maximo=20_000_000)),
    ("salarios_ext", dict(tab=2072, var=934, freq="T", unid="Milhões de Reais", minimo=-100_000, maximo=100_000)),
    ("rendas_prop_ext", dict(tab=2072, var=935, freq="T", unid="Milhões de Reais", minimo=-1_000_000, maximo=1_000_000)),
    ("transf_ext", dict(tab=2072, var=937, freq="T", unid="Milhões de Reais", minimo=-100_000, maximo=100_000)),
])
SGS = OrderedDict([
    ("tc12", dict(cod=24419, unid="Milhões de dólares americanos", minimo=-200_000, maximo=100_000)),
    ("tc12pib", dict(cod=23079, unid="Percentual", minimo=-12, maximo=8)),
    ("tc_mes", dict(cod=22701, unid="Milhões de dólares americanos", minimo=-30_000, maximo=15_000)),
    ("bens", dict(cod=22707, unid="Milhões de dólares americanos", minimo=-15_000, maximo=40_000)),
    ("servicos", dict(cod=22719, unid="Milhões de dólares americanos", minimo=-15_000, maximo=5_000)),
    ("renda_prim", dict(cod=22800, unid="Milhões de dólares americanos", minimo=-20_000, maximo=5_000)),
    ("renda_sec", dict(cod=22838, unid="Milhões de dólares americanos", minimo=-2_000, maximo=5_000)),
])

# Palavras de julgamento que não podem aparecer em nenhum texto desta parte da página (CLAUDE.md, "o que a página recusa").
PROIBIDAS = [r"\bbom\b", r"\bboa\b", r"\bruim\b", r"\bruins\b", r"\brisco\b", r"\briscos\b", r"vulnerabilidade", r"vulner[áa]vel",
             r"honesta", r"enganos[ao]", r"\binfla\b", r"\bmelhor\b", r"\bpior\b", r"\bmelhora\b", r"\bpiora\b", r"\bmelhorou\b",
             r"\bpiorou\b", r"preocupante", r"alarmante", r"chega ao bolso", r"ponto fraco", r"quase n[ãa]o avan[çc]a"]

LIMITE_ENCOLHIMENTO = 0.9     # menos de 90% dos pontos da rodada anterior = a fonte encolheu: nada é gravado


class Aborta(Exception):
    """Checagem de sanidade falhou: nada é gravado e o arquivo anterior fica."""


# ------------------------------------------------------------------------------------------------ datas e números
def deslocar(d, meses):
    a, m = int(d[:4]), int(d[5:7])
    t = a * 12 + (m - 1) + meses
    return f"{t // 12}-{t % 12 + 1:02d}-01"


def passo_de(freq):
    return {"M": 1, "T": 3, "A": 12}[freq]


def rotulo(d, tipo):
    """tm: trimestre móvel terminado no mês de d · tri: trimestre civil · mes · ano."""
    a, m = int(d[:4]), int(d[5:7])
    if tipo == "tm":
        d0 = deslocar(d, -2)
        a0, m0 = int(d0[:4]), int(d0[5:7])
        return f"{MES[m0 - 1]}–{MES[m - 1]}/{a}" if a0 == a else f"{MES[m0 - 1]}/{a0}–{MES[m - 1]}/{a}"
    if tipo == "tri":
        return f"{(m - 1) // 3 + 1}º tri/{a}"
    if tipo == "mes":
        return f"{MES[m - 1]}/{a}"
    return str(a)


def br(v, casas=1, sinal=False):
    """Número em português: vírgula decimal, ponto de milhar e sinal de menos de verdade."""
    s = f"{abs(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if v < 0 and float(f"{abs(v):.{casas}f}") != 0:
        return MENOS + s
    return ("+" + s) if sinal and float(f"{abs(v):.{casas}f}") != 0 else s


def dinheiro(v, unidade, casas):
    """R$ 3.777 · −US$ 63,0 bilhões: o sinal vem antes da moeda."""
    return f"{MENOS if v < 0 else ''}{unidade} {br(abs(v), casas)}"


def fato(pontos, freq, tipo):
    """Último valor, o de um ano antes e os extremos da série, só como fato. `pontos`: [(data, valor)] em ordem."""
    ult = pontos[-1]
    ref = dict(pontos).get(deslocar(ult[0], -12))
    vals = [v for _, v in pontos]
    vmax, vmin = max(vals), min(vals)
    pmax = [d for d, v in pontos if v == vmax]
    pmin = [d for d, v in pontos if v == vmin]
    f = {"data": ult[0], "rotulo": rotulo(ult[0], tipo), "valor": ult[1],
         "inicio": pontos[0][0], "inicioRotulo": rotulo(pontos[0][0], tipo),
         "max": {"data": pmax[0], "rotulo": rotulo(pmax[0], tipo), "valor": vmax, "n": len(pmax)},
         "min": {"data": pmin[0], "rotulo": rotulo(pmin[0], tipo), "valor": vmin, "n": len(pmin)}}
    f["extremo"] = ("maior" if ult[1] == vmax else "menor" if ult[1] == vmin else None)
    f["extremoUnico"] = (len(pmax) == 1 if f["extremo"] == "maior" else len(pmin) == 1 if f["extremo"] == "menor" else None)
    if ref is not None:
        f["antes"] = {"data": deslocar(ult[0], -12), "rotulo": rotulo(deslocar(ult[0], -12), tipo), "valor": ref}
    return f


def trecho_extremos(f, fmtv, sujeito=None):
    """'É o maior valor da série, que começa em …' ou, se não for, os dois extremos com o período de cada um."""
    abre = f"{sujeito} é" if sujeito else "É"
    if f["extremo"] == "maior" and f["extremoUnico"]:
        return f"{abre} o maior valor da série, que começa em {f['inicioRotulo']}."
    if f["extremo"] == "menor" and f["extremoUnico"]:
        return f"{abre} o menor valor da série, que começa em {f['inicioRotulo']}."
    if f["extremo"]:
        outro = f["max"] if f["extremo"] == "maior" else f["min"]
        return f"{'O valor atual' if not sujeito else sujeito} iguala o {f['extremo']} valor da série, que começa em {f['inicioRotulo']} ({fmtv(outro['valor'])})."
    return (f"Na série, que começa em {f['inicioRotulo']}, o menor valor foi {fmtv(f['min']['valor'])} ({f['min']['rotulo']}) "
            f"e o maior, {fmtv(f['max']['valor'])} ({f['max']['rotulo']}).")


def variacao_txt(atual, antes, tipo_var):
    """tipo_var: 'pct' (variação % de um nível) ou 'pp' (diferença em pontos percentuais de uma taxa)."""
    if tipo_var == "pp":
        d = atual - antes
        return f"{br(d, 1, sinal=True)} p.p." if abs(d) >= 0.05 else "sem variação"
    d = (atual / antes - 1) * 100
    return f"{br(d, 1, sinal=True)}%"


# ------------------------------------------------------------------------------------------------ coleta
def periodo_para_data(chave, freq):
    if freq == "M":
        return f"{chave[:4]}-{chave[4:6]}-01"
    if freq == "T":
        q = int(chave[4:6])
        return f"{chave[:4]}-{(q - 1) * 3 + 1:02d}-01"
    return f"{chave[:4]}-01-01"


def sidra_serie(C, nome, e):
    """Série do SIDRA (API de agregados do IBGE) com os "..." registrados à parte: 'sem dado' nunca vira zero."""
    params = {"localidades": "N1[all]"}
    if e.get("cls"):
        params["classificacao"] = e["cls"]
    j = C.buscar(f"{C.URL_IBGE}/{e['tab']}/periodos/all/variaveis/{e['var']}", params)
    r = j[0]["resultados"]
    if len(r) != 1:
        raise Aborta(f"{nome}: o SIDRA devolveu {len(r)} recortes para a tabela {e['tab']} (esperado 1)")
    serie = r[0]["series"][0]["serie"]
    pontos, ausentes = [], []
    for chave in sorted(serie):
        data = periodo_para_data(chave, e["freq"])
        try:
            pontos.append((data, float(serie[chave])))
        except ValueError:
            ausentes.append(data)
    return {"pontos": pontos, "ausentes": ausentes, "unidade": j[0]["unidade"], "nome": j[0]["variavel"],
            "tab": e["tab"], "var": e["var"], "freq": e["freq"]}


def montar_brutos(C, rede):
    arq = C.CACHE_C / "trabalho_brutos.json"
    if not rede:
        br_ = C.ler_cache(arq)
        if not br_:
            raise Aborta("--so-montar sem cache do bloco de trabalho: rode contexto.py inteiro antes")
        return br_
    print("Trabalho, renda e setor externo: SIDRA (IBGE) e SGS (Banco Central)…", flush=True)
    out = {"sidra": {}, "sgs": {}, "coletadoEm": C.HOJE.isoformat()}
    for nome, e in SIDRA.items():
        out["sidra"][nome] = sidra_serie(C, nome, e)
    for nome, e in SGS.items():
        s = C.sgs(e["cod"])
        out["sgs"][nome] = {"pontos": s["pontos"], "ausentes": [], "unidade": s["unidade"], "nome": s["nome"], "cod": e["cod"], "freq": "M"}
    C.gravar_cache(arq, out)
    return out


# ------------------------------------------------------------------------------------------------ checagens
def checar(nome, s, e, freq, hoje):
    """Lacuna, valor fora do plausível, data no futuro, unidade e nome diferentes do esperado: aborta."""
    pontos = [tuple(p) for p in s["pontos"]]
    if not pontos:
        raise Aborta(f"{nome}: série vazia")
    if e.get("unid") and s["unidade"] and s["unidade"] != e["unid"]:
        raise Aborta(f"{nome}: a unidade mudou — a fonte agora diz {s['unidade']!r}, o esperado era {e['unid']!r}")
    if e.get("nome_contem") and e["nome_contem"] not in s["nome"]:
        raise Aborta(f"{nome}: a base mudou — o nome da variável não traz mais {e['nome_contem']!r} ({s['nome']!r})")
    passo = passo_de(freq)
    lacunas = set(e.get("lacunas", ()))
    faltando = []
    for (a, _), (b, _) in zip(pontos, pontos[1:]):
        d = deslocar(a, passo)
        while d != b:
            if d > b:
                raise Aborta(f"{nome}: datas fora de ordem entre {a} e {b}")
            faltando.append(d)
            d = deslocar(d, passo)
    # os "..." no meio da série são lacunas da fonte; só valem se estiverem na lista das conhecidas
    novas = [d for d in faltando if d not in lacunas]
    if novas:
        raise Aborta(f"{nome}: buraco na série em {', '.join(novas[:4])} (lacunas oficiais conhecidas: {sorted(lacunas) or 'nenhuma'})")
    for d, v in pontos:
        if not e["minimo"] <= v <= e["maximo"]:
            raise Aborta(f"{nome}: valor fora do plausível em {d}: {v} (esperado entre {e['minimo']} e {e['maximo']})")
    if pontos[-1][0] > hoje:
        raise Aborta(f"{nome}: data no futuro ({pontos[-1][0]})")
    return faltando


def somar_12m(pontos):
    out = []
    for i in range(11, len(pontos)):
        out.append((pontos[i][0], sum(v for _, v in pontos[i - 11:i + 1])))
    return out


# ------------------------------------------------------------------------------------------------ montagem
def fonte_sidra(C, tab, var):
    return {"nome": f"IBGE — SIDRA, tabela {tab}", "url": f"https://sidra.ibge.gov.br/tabela/{tab}",
            "api": f"{C.URL_IBGE}/{tab}/periodos/all/variaveis/{var}"}


def fonte_sgs(C, cod):
    return {"nome": f"Banco Central — SGS {cod}", "url": f"https://dadosabertos.bcb.gov.br/dataset/{cod}", "api": C.URL_SGS.format(cod)}


NOTA_PNAD = ("A PNAD Contínua é a série vigente do IBGE: foi reponderada a partir da divulgação de 30/11/2021 (calibração por sexo e idade, "
             "Nota Técnica 03/2021) e de novo a partir de 31/7/2025 (população do Censo 2022, Nota Técnica 02/2025); os trimestres móveis desde 2012 "
             "já estão na ponderação atual. Cada ponto é um trimestre móvel (a média de três meses), identificado pelo último mês; pontos vizinhos "
             "compartilham dois meses de pesquisa, então não são independentes. Sem interpolação.")
URL_NT03 = "https://www.ibge.gov.br/novo-portal-destaques/31932-nota-tecnica-sobre-a-divulgacao-da-reponderacao-da-pnad-continua-em-2021.html"
URL_NT02 = "https://biblioteca.ibge.gov.br/visualizacao/livros/liv102194.pdf"
URL_DEFLATOR = "https://biblioteca.ibge.gov.br/visualizacao/livros/liv101813.pdf"
URL_BPM6 = "https://www.bcb.gov.br/ftp/infecon/nm1bpm6p.pdf"
URL_PRODUTIVIDADE = "https://ibre.fgv.br/observatorio-produtividade"


def pts(lista, escala=1.0, casas=4):
    return [[d, round(v * escala, casas)] for d, v in lista]


def construir(C, br_, gerado):
    S = {k: dict(v, pontos=[tuple(p) for p in v["pontos"]]) for k, v in {**br_["sidra"], **br_["sgs"]}.items()}
    hoje = C.HOJE.isoformat()
    todas = {**SIDRA, **SGS}
    conf = {}
    lacunas_vistas = {}
    for nome, e in todas.items():
        freq = e.get("freq", "M")
        faltando = checar(nome, S[nome], e, freq, hoje)
        if faltando:
            lacunas_vistas[nome] = faltando
        # "..." publicados pela fonte: têm de ser exatamente as lacunas conhecidas (ou menos, se a fonte passou a publicar)
        conhecidas = set(e.get("lacunas", ()))
        inicio = S[nome]["pontos"][0][0]
        inesperados = sorted(d for d in set(S[nome].get("ausentes", [])) if d > inicio and d not in conhecidas)
        if inesperados:
            raise Aborta(f"{nome}: a fonte marca como indisponível {inesperados[:4]}, fora das lacunas oficiais conhecidas")

    # ---- base do rendimento real: o IBGE expressa o real a preços do ÚLTIMO trimestre móvel, então o último ponto real = nominal
    for real, nom, rot in (("rend_real", "rend_nom", "rendimento"), ("massa_real", "massa_nom", "massa de rendimento")):
        if S[real]["pontos"][-1] != S[nom]["pontos"][-1]:
            raise Aborta(f"{rot}: no último período o valor real ({S[real]['pontos'][-1]}) deixou de ser igual ao nominal "
                         f"({S[nom]['pontos'][-1]}): a base dos valores reais mudou")
    ult_tm = S["rend_real"]["pontos"][-1][0]
    base_tm = rotulo(ult_tm, "tm")
    ult_ano = S["renda_pc"]["pontos"][-1][0][:4]

    # ---- as datas dos indicadores do mesmo grupo têm de coincidir no fim
    for a, b in (("rend_real", "massa_real"), ("rend_real", "ocupados"), ("rend_real", "desocupacao")):
        if S[a]["pontos"][-1][0] != S[b]["pontos"][-1][0]:
            conf.setdefault("avisos", []).append(f"{a} termina em {S[a]['pontos'][-1][0]} e {b} em {S[b]['pontos'][-1][0]}")

    # ---- identidade aproximada: massa ≈ ocupados × rendimento médio (só REPORTADA)
    oc, rm, ms = dict(S["ocupados"]["pontos"]), dict(S["rend_real"]["pontos"]), dict(S["massa_real"]["pontos"])
    difs = [((oc[d] * 1000 * rm[d] / 1e6) / ms[d] - 1) * 100 for d in sorted(ms) if d in oc and d in rm]
    conf["massaIdentidade"] = {
        "texto": "massa de rendimento ≈ pessoas ocupadas × rendimento médio (estimado menos publicado, em % da massa publicada)",
        "minimo": round(min(difs), 2), "maximo": round(max(difs), 2), "ultimo": round(difs[-1], 2), "pontos": len(difs),
        "nota": "As pessoas ocupadas incluem quem não tem rendimento de trabalho, então a conta feita com o total de ocupados fica acima da massa publicada."}

    # ---- decomposição das transações correntes: os quatro componentes somam o saldo (aborta se a conta não fechar)
    comp = {k: dict(S[k]["pontos"]) for k in ("bens", "servicos", "renda_prim", "renda_sec", "tc_mes")}
    maior = 0.0
    for d, v in comp["tc_mes"].items():
        soma = sum(comp[k].get(d, float("nan")) for k in ("bens", "servicos", "renda_prim", "renda_sec"))
        if soma != soma:
            raise Aborta(f"transações correntes: falta componente em {d}")
        maior = max(maior, abs(soma - v))
    if maior > 10:
        raise Aborta(f"transações correntes: os quatro componentes não fecham com o saldo (diferença de {maior:.1f} milhões de dólares)")
    c12 = {k: somar_12m(sorted(comp[k].items())) for k in ("bens", "servicos", "renda_prim", "renda_sec", "tc_mes")}
    tc12 = dict(S["tc12"]["pontos"])
    dif12 = max(abs(v - tc12[d]) for d, v in c12["tc_mes"] if d in tc12)
    if dif12 > 5:
        raise Aborta(f"transações correntes: o acumulado em 12 meses do BC difere da soma dos saldos mensais em {dif12:.1f} milhões de dólares")
    conf["transacoesIdentidade"] = {"componentesMenosSaldoMensal": round(maior, 2), "acumulado12mMenosSomaMensal": round(dif12, 2),
                                    "texto": "bens + serviços + renda primária + renda secundária = saldo das transações correntes (diferença máxima, em milhões de dólares)"}
    # a série em % do PIB começa mais cedo do que o acumulado em US$: as duas partem do mesmo mês (o acumulado de 12 meses só existe a partir do 12º)
    ini_tc = S["tc12"]["pontos"][0][0]
    pib_pct = [(d, v) for d, v in S["tc12pib"]["pontos"] if d >= ini_tc]

    # ---- RNDB: PIB + salários + rendas de propriedade (líquidos do exterior) = RNB; + outras transferências = RNDB
    q = {k: dict(S[k]["pontos"]) for k in ("pib_corrente", "salarios_ext", "rendas_prop_ext", "rnb", "transf_ext", "rndb")}
    dm = 0.0
    for d, v in q["rndb"].items():
        dm = max(dm, abs(q["pib_corrente"][d] + q["salarios_ext"][d] + q["rendas_prop_ext"][d] + q["transf_ext"][d] - v))
    if dm > 100:
        raise Aborta(f"RNDB: PIB + salários + rendas de propriedade + transferências não fecham com a RNDB (diferença de {dm:.0f} milhões)")
    conf["rndbIdentidade"] = {"texto": "PIB + salários líquidos do exterior + rendas de propriedade líquidas + outras transferências correntes = RNDB (diferença máxima, R$ milhões)",
                              "maximo": round(dm, 1)}

    # ---- frases e fatos ----------------------------------------------------------------------------------------------------
    sec = []

    def pt(k):
        return S[k]["pontos"]

    # 1. rendimento médio real habitual
    f = fato(pt("rend_real"), "M", "tm")
    fm = lambda v: dinheiro(v, "R$", 0)
    frase = (f"O rendimento médio real habitual foi de {fm(f['valor'])} por mês no trimestre {f['rotulo']}. "
             + (f"Doze meses antes ({f['antes']['rotulo']}), era de {fm(f['antes']['valor'])} ({variacao_txt(f['valor'], f['antes']['valor'], 'pct')}). " if "antes" in f else "")
             + trecho_extremos(f, fm))
    sec.append({"id": "rendimento", "n": 1, "titulo": "O salário médio compra mais ou menos que antes?", "grafico": "g_rend", "fato": f, "frase": frase,
                "definicao": "Rendimento que as pessoas ocupadas de 14 anos ou mais normalmente recebem por mês, somando todos os trabalhos (habitual, não o efetivamente recebido no mês).",
                "real": f"“Real” significa já descontada a inflação: os valores de todos os períodos estão em reais do trimestre mais recente ({base_tm}), corrigidos pelo IPCA.",
                "fontes": [fonte_sidra(C, 6390, 5933)]})
    # 2. ocupados
    po = [(d, v / 1000) for d, v in pt("ocupados")]
    f = fato(po, "M", "tm")
    fm = lambda v: f"{br(v, 1)} milhões"
    frase = (f"Havia {br(f['valor'], 1)} milhões de pessoas ocupadas no trimestre {f['rotulo']}. "
             + (f"Doze meses antes ({f['antes']['rotulo']}), eram {br(f['antes']['valor'], 1)} milhões ({variacao_txt(f['valor'], f['antes']['valor'], 'pct')}). " if "antes" in f else "")
             + trecho_extremos(f, fm))
    sec.append({"id": "ocupados", "n": 2, "titulo": "Quantas pessoas estão trabalhando?", "grafico": "g_ocup", "fato": f, "frase": frase,
                "definicao": "Pessoas de 14 anos ou mais que exerceram trabalho na semana de referência, remunerado ou não, ou estavam temporariamente afastadas dele.",
                "fontes": [fonte_sidra(C, 6323, 4090)]})
    # 3. massa
    pm = [(d, v / 1000) for d, v in pt("massa_real")]
    f = fato(pm, "M", "tm")
    fm = lambda v: dinheiro(v, "R$", 1) + " bilhões"
    frase = (f"A renda do trabalho de todas as pessoas ocupadas somou {fm(f['valor'])} por mês no trimestre {f['rotulo']}. "
             + (f"Doze meses antes ({f['antes']['rotulo']}), somava {fm(f['antes']['valor'])} ({variacao_txt(f['valor'], f['antes']['valor'], 'pct')}). " if "antes" in f else "")
             + trecho_extremos(f, fm))
    sec.append({"id": "massa", "n": 3, "titulo": "Quanto o conjunto dos trabalhadores recebe?", "grafico": "g_massa", "fato": f, "frase": frase,
                "definicao": "Soma dos rendimentos habituais de todos os trabalhos das pessoas ocupadas, em reais do trimestre mais recente: é só renda do trabalho, "
                             "não inclui aluguel, benefícios (como aposentadorias e programas sociais) nem outras fontes.",
                "fontes": [fonte_sidra(C, 6392, 6293)]})
    # 4 a 6. taxas
    def taxa(nome, id_, n, titulo, definicao, rotulo_taxa, tab, var, extra=None):
        f = fato(pt(nome), "M", "tm")
        fm = lambda v: f"{br(v, 1)}%"
        frase = (f"A {rotulo_taxa} foi de {fm(f['valor'])} no trimestre {f['rotulo']}. "
                 + (f"Doze meses antes ({f['antes']['rotulo']}), era de {fm(f['antes']['valor'])} ({variacao_txt(f['valor'], f['antes']['valor'], 'pp')}). " if "antes" in f else "")
                 + trecho_extremos(f, fm))
        d = {"id": id_, "n": n, "titulo": titulo, "grafico": "g_taxas", "grupo": "taxas", "fato": f, "frase": frase, "definicao": definicao,
             "fontes": [fonte_sidra(C, tab, var)]}
        if extra:
            d.update(extra)
        return d
    sec.append(taxa("desocupacao", "desocupacao", 4, "Quantas pessoas procuram trabalho e não acham?",
                    "Pessoas de 14 anos ou mais sem trabalho na semana de referência que tomaram providência para conseguir um e estavam disponíveis, como percentual da força de trabalho (ocupadas mais desocupadas).",
                    "taxa de desocupação", 6381, 4099))
    sec.append(taxa("informalidade", "informalidade", 5, "Que tipo de trabalho as pessoas têm?",
                    "Percentual das pessoas ocupadas em trabalho que o IBGE classifica como informal: empregado do setor privado ou doméstico sem carteira assinada, empregador ou conta própria sem registro no CNPJ e trabalhador familiar auxiliar.",
                    "taxa de informalidade", 8513, 12466,
                    {"inicioNota": f"A série da informalidade começa em {rotulo(pt('informalidade')[0][0], 'tm')}; antes disso o IBGE não a publica."}))
    sec.append(taxa("subutilizacao", "subutilizacao", 6, "Quem queria trabalhar mais e não consegue?",
                    "Percentual formado por pessoas desocupadas, subocupadas por insuficiência de horas trabalhadas e na força de trabalho potencial, em relação à força de trabalho ampliada.",
                    "taxa composta de subutilização da força de trabalho", 6441, 4118,
                    {"lacunaNota": "O IBGE não publica esta taxa para os trimestres móveis terminados em out/2015 e nov/2015 (dado não disponível na fonte): a linha do gráfico fica interrompida nesses dois meses."}))
    # 7. renda per capita e Gini
    fr = fato(pt("renda_pc"), "A", "ano"); fp = fato(pt("renda_p50"), "A", "ano"); fg = fato(pt("gini"), "A", "ano")
    reais = lambda v: dinheiro(v, "R$", 0)
    gi = lambda v: br(v, 3)
    partes = [f"Em {fr['rotulo']}, a renda domiciliar per capita média foi de {reais(fr['valor'])} por mês e a mediana (P50), de {reais(fp['valor'])}, em reais de {ult_ano}."]
    if "antes" in fr and "antes" in fp:
        partes.append(f"Um ano antes ({fr['antes']['rotulo']}), eram {reais(fr['antes']['valor'])} e {reais(fp['antes']['valor'])}, também em reais de {ult_ano}.")
    partes.append(trecho_extremos(fr, reais, "A média"))
    partes.append(f"O índice de Gini foi de {gi(fg['valor'])}" + (f", contra {gi(fg['antes']['valor'])} um ano antes" if "antes" in fg else "") + ". "
                  + trecho_extremos(fg, gi, "O Gini"))
    sec.append({"id": "renda", "n": 7, "titulo": "Qual a renda por pessoa da casa, e o quanto ela é desigual?", "grafico": "g_renda", "grafico2": "g_gini",
                "fato": {"renda": fr, "p50": fp, "gini": fg}, "frase": " ".join(partes),
                "definicao": "Renda domiciliar per capita: o rendimento de todas as fontes dos moradores do domicílio dividido pelo número de moradores; "
                             "a mediana é o valor que divide as pessoas ao meio (o IBGE a publica como o limite superior do P50) e o Gini mede a concentração, de 0 (igualdade perfeita) a 1 (desigualdade máxima).",
                "fontes": [fonte_sidra(C, 7533, 10816), fonte_sidra(C, 7526, 10838), fonte_sidra(C, 7435, 10681)]})
    # 8. transações correntes
    ptc = [(d, v / 1000) for d, v in pt("tc12")]
    f = fato(ptc, "M", "mes")
    fb = lambda v: dinheiro(v, "US$", 1) + " bilhões"
    fpib = dict(pib_pct)
    pct = lambda d: f"{br(fpib[d], 2)}%"
    ultimo_c = {k: c12[k][-1][1] / 1000 for k in ("bens", "servicos", "renda_prim", "renda_sec")}
    ant_d = f["antes"]["data"] if "antes" in f else None
    frase = (f"Nos 12 meses até {f['rotulo']}, o saldo das transações correntes foi de {fb(f['valor'])} ({pct(f['data'])} do PIB). "
             + (f"Um ano antes ({f['antes']['rotulo']}), foi de {fb(f['antes']['valor'])} ({pct(ant_d)} do PIB). " if ant_d in fpib else "")
             + f"Desse saldo, os bens somaram {fb(ultimo_c['bens'])}, os serviços {fb(ultimo_c['servicos'])}, a renda primária {fb(ultimo_c['renda_prim'])} e a renda secundária {fb(ultimo_c['renda_sec'])}. "
             + trecho_extremos(f, fb))
    sec.append({"id": "externo", "n": 10, "titulo": "O país pagou ao exterior mais ou menos do que recebeu?", "grafico": "g_tc", "grafico2": "g_tcpib", "avancada": True,
                "fato": f, "frase": frase,
                "definicao": "Déficit significa que o país pagou ao exterior mais do que recebeu no período; é coberto por entrada de capital.",
                "notaFixa": "A proporção do PIB usa o PIB em dólares; variações cambiais alteram a proporção.",
                "decomposicao": {k: round(c12[k][-1][1], 1) for k in c12 if k != "tc_mes"} | {"saldo": round(c12["tc_mes"][-1][1], 1), "data": c12["tc_mes"][-1][0]},
                "fontes": [fonte_sgs(C, 24419), fonte_sgs(C, 23079), fonte_sgs(C, 22707), fonte_sgs(C, 22719), fonte_sgs(C, 22800), fonte_sgs(C, 22838)]})
    # 9. RNDB (nominal)
    prn = [(d, v / 1e6) for d, v in pt("rndb")]
    f = fato(prn, "T", "tri")
    trilh = lambda v: dinheiro(v, "R$", 2) + " trilhões"
    pib_u = pt("pib_corrente")[-1][1] / 1e6
    frase = (f"No {f['rotulo']}, a renda nacional disponível bruta foi de {trilh(f['valor'])}, em valores correntes (nominais, sem desconto da inflação). "
             + (f"No {f['antes']['rotulo']}, foi de {trilh(f['antes']['valor'])}. " if "antes" in f else "")
             + f"O PIB corrente do {f['rotulo']} foi de {trilh(pib_u)}.")
    sec.append({"id": "rndb", "n": 11, "titulo": "Qual a renda disponível dos moradores do país?", "grafico": "g_rndb", "avancada": True, "nominal": True,
                "fato": f, "frase": frase,
                "definicao": "A renda nacional disponível bruta é a renda disponível dos residentes do país; difere do PIB pelas rendas recebidas e enviadas ao exterior e pelas transferências.",
                "notaNominal": "Esta série é nominal: o IBGE não publica a renda nacional disponível bruta em termos reais no SIDRA, e este site não a deflaciona por conta própria. "
                               "Por isso a abertura não compara o valor com o máximo da série (valores nominais sobem com a inflação). Na versão em volume, que não é publicada aqui, "
                               "a renda também incorpora o efeito dos termos de troca; esta série nominal não tem esse ajuste.",
                "decomposicao": {"pib": S["pib_corrente"]["pontos"][-1][1], "salarios": S["salarios_ext"]["pontos"][-1][1], "rendasPropriedade": S["rendas_prop_ext"]["pontos"][-1][1],
                                 "rnb": S["rnb"]["pontos"][-1][1], "transferencias": S["transf_ext"]["pontos"][-1][1], "rndb": S["rndb"]["pontos"][-1][1],
                                 "data": S["rndb"]["pontos"][-1][0]},
                "fontes": [fonte_sidra(C, 2072, 938)]})

    # ---- gráficos: o que a página desenha (frequência nativa, sem interpolar) ---------------------------------------------
    def serie(id_, nome, unidade, freq, lista, casas=4):
        return {"id": id_, "nome": nome, "unidade": unidade, "freq": freq, "pontos": pts(lista, 1.0, casas)}
    G = OrderedDict()
    G["g_rend"] = {"titulo": "Rendimento médio real habitual, todos os trabalhos", "rotData": "tm", "fmt": "reais", "zero": True,
                   "series": [serie("rend_real", "Rendimento médio real habitual", "R$", "M", pt("rend_real"), 0)]}
    G["g_ocup"] = {"titulo": "Pessoas ocupadas", "rotData": "tm", "fmt": "milhoes", "zero": True,
                   "series": [serie("ocupados", "Pessoas ocupadas", "milhões de pessoas", "M", po, 3)]}
    G["g_massa"] = {"titulo": "Massa de rendimento real habitual do trabalho", "rotData": "tm", "fmt": "bilhoes", "zero": True,
                    "series": [serie("massa", "Massa de rendimento do trabalho", "R$ bilhões por mês", "M", pm, 2)]}
    G["g_taxas"] = {"titulo": "Desocupação, informalidade e subutilização", "rotData": "tm", "fmt": "pct", "zero": True,
                    "series": [serie("desocupacao", "Desocupação", "%", "M", pt("desocupacao"), 2), serie("informalidade", "Informalidade", "%", "M", pt("informalidade"), 2),
                               serie("subutilizacao", "Subutilização", "%", "M", pt("subutilizacao"), 2)]}
    G["g_renda"] = {"titulo": "Renda domiciliar per capita: média e mediana (P50)", "rotData": "ano", "fmt": "reais", "zero": True,
                    "series": [serie("renda_pc", "Média", "R$ por mês", "A", pt("renda_pc"), 0), serie("renda_p50", "Mediana (P50)", "R$ por mês", "A", pt("renda_p50"), 0)]}
    G["g_gini"] = {"titulo": "Índice de Gini do rendimento domiciliar per capita", "rotData": "ano", "fmt": "gini", "zero": False,
                   "series": [serie("gini", "Gini", "índice de 0 a 1", "A", pt("gini"), 3)]}
    G["g_tc"] = {"titulo": "Transações correntes, acumulado em 12 meses, e os quatro componentes", "rotData": "mes", "fmt": "usbi", "zero": True,
                 "series": [serie("tc12", "Saldo das transações correntes", "US$ bilhões", "M", ptc, 3)]
                 + [serie(k, nome_, "US$ bilhões", "M", [(d, v / 1000) for d, v in c12[k]], 3)
                    for k, nome_ in (("bens", "Bens"), ("servicos", "Serviços"), ("renda_prim", "Renda primária"), ("renda_sec", "Renda secundária"))]}
    G["g_tcpib"] = {"titulo": "Transações correntes, acumulado em 12 meses, em % do PIB", "rotData": "mes", "fmt": "pct2", "zero": True,
                    "series": [serie("tc12pib", "Saldo em % do PIB", "% do PIB", "M", pib_pct, 2)]}
    G["g_rndb"] = {"titulo": "Renda nacional disponível bruta e PIB, valores correntes", "rotData": "tri", "fmt": "trilhoes", "zero": True,
                   "series": [serie("rndb", "Renda nacional disponível bruta", "R$ trilhões", "T", prn, 4),
                              serie("pib", "PIB", "R$ trilhões", "T", [(d, v / 1e6) for d, v in pt("pib_corrente")], 4)]}

    # quais notas de método valem para cada gráfico e como ele se desenha (só opções de leiaute: nenhum número)
    for k, ns in {"g_rend": ["pnad", "real"], "g_ocup": ["pnad"], "g_massa": ["pnad", "real"], "g_taxas": ["pnad"], "g_renda": ["anual"],
                  "g_gini": ["anual"], "g_tc": ["bpm6"], "g_tcpib": ["bpm6"], "g_rndb": ["rndb"]}.items():
        G[k]["notas"] = ns
        G[k]["rotulosDiretos"] = len(G[k]["series"]) > 1          # série única: sem rótulo no fim da linha
        G[k]["marcarPontos"] = G[k]["series"][0]["freq"] == "A"    # séries anuais: um ponto por ano

    notas = OrderedDict()
    notas["pnad"] = {"curta": "Série vigente da PNAD Contínua, reponderada a partir de nov/2021 e de 31/7/2025. Cada ponto é um trimestre móvel (média de três meses), identificado pelo último mês.",
                     "texto": NOTA_PNAD, "fontes": [{"nome": "IBGE, Nota Técnica 03/2021 (reponderação, nov/2021)", "url": URL_NT03},
                                                      {"nome": "IBGE, Nota Técnica 02/2025 (reponderação, 31/7/2025)", "url": URL_NT02}]}
    notas["real"] = {"curta": f"Valores reais: em reais de {base_tm}, corrigidos pelo IPCA; a base muda a cada divulgação e a série inteira é refeita.",
                     "texto": f"Valores reais: IPCA (índice regional, ponderado), em reais do último trimestre móvel divulgado ({base_tm}). A base muda a cada divulgação e o IBGE refaz a série inteira, "
                              "então comparar valores de execuções diferentes não vale; por isso a página compara sempre dentro da mesma série baixada. Conferido nos dados: no último período o valor real é igual ao nominal.",
                     "fontes": [{"nome": "IBGE, Nota Técnica sobre o deflacionamento dos rendimentos dos trimestres móveis", "url": URL_DEFLATOR}]}
    notas["anual"] = {"curta": f"Valores anuais em reais de {ult_ano} (IPCA), comparáveis entre os anos. O IBGE reponderou parte dos temas anuais só em 2026: veja a camada avançada.",
                      "texto": f"Renda per capita e Gini são anuais (PNAD Contínua, rendimento de todas as fontes), em reais de {ult_ano} (preços médios do último ano, IPCA), comparáveis entre os anos. "
                               "O IBGE informa que os temas anuais divulgados antes de 31/7/2025 seriam reponderados no ciclo de 2026; a fonte não permite saber ano a ano qual ponderação cada valor usa. "
                               "A mediana não tem tabela própria: é o limite superior da classe P50, publicado pelo IBGE.",
                      "fontes": [{"nome": "IBGE, Nota Técnica 02/2025", "url": URL_NT02}]}
    notas["bpm6"] = {"curta": "Séries do Banco Central na organização do manual BPM6 (adotado em abril de 2015). A proporção do PIB usa o PIB em dólares; variações cambiais alteram a proporção.",
                     "texto": "O Banco Central divulga o balanço de pagamentos pelo manual BPM6 desde abril de 2015 (antes, BPM5), o que reorganizou as transações correntes em bens, serviços, renda primária e renda secundária; "
                              "as séries do SGS já usam essa organização desde 1995. A proporção do PIB usa o PIB em dólares; variações cambiais alteram a proporção. O acumulado em 12 meses em dólares só existe a partir de dez/1995, e o gráfico em % do PIB parte do mesmo mês.",
                     "fontes": [{"nome": "Banco Central, adoção do BPM6", "url": URL_BPM6}]}
    notas["rndb"] = {"curta": "Valores correntes (nominais): sem desconto da inflação. O IBGE não publica esta renda em termos reais no SIDRA, e este site não a deflaciona.",
                     "texto": "PIB + salários líquidos recebidos do exterior + rendas de propriedade líquidas = renda nacional bruta; + outras transferências correntes líquidas = renda nacional disponível bruta. Contas Econômicas Trimestrais do IBGE, valores correntes.",
                     "fontes": []}

    out = {"versao": VERSAO, "geradoEm": gerado, "hoje": hoje, "coletadoEm": br_.get("coletadoEm"),
           "baseReal": {"indice": "IPCA", "trimestreMovel": ult_tm, "rotulo": base_tm, "anual": ult_ano},
           "secoes": sec, "graficos": G, "notas": notas, "conferencias": conf,
           "lacunasDaFonte": lacunas_vistas,
           "paraSaberMais": {"titulo": "Produtividade",
                             "texto": "Este site não calcula produtividade. O Observatório da Produtividade Regis Bonelli, do FGV IBRE, publica indicadores de produtividade do trabalho e da produtividade total dos fatores.",
                             "link": {"nome": "Observatório da Produtividade Regis Bonelli (FGV IBRE)", "url": URL_PRODUTIVIDADE}}}
    return out


def pontos_do_bloco(o):
    return sum(len(s["pontos"]) for g in o["graficos"].values() for s in g["series"])


def textos(o):
    """Todo texto que a página mostra a partir deste arquivo (para a checagem de palavras de julgamento)."""
    t = []
    for s in o["secoes"]:
        t += [s["titulo"], s["frase"], s["definicao"], s.get("real", ""), s.get("notaFixa", ""), s.get("notaNominal", ""),
              s.get("inicioNota", ""), s.get("lacunaNota", "")]
    t += [g["titulo"] for g in o["graficos"].values()]
    t += [s["nome"] for g in o["graficos"].values() for s in g["series"]]
    t += [n["texto"] for n in o["notas"].values()] + [n.get("curta", "") for n in o["notas"].values()]
    t += [o["paraSaberMais"]["texto"], o["paraSaberMais"]["titulo"]]
    return [x for x in t if x]


def checar_textos(txts, proibidas=PROIBIDAS):
    for t in txts:
        for p in proibidas:
            m = re.search(p, t, re.I)
            if m:
                raise Aborta(f"palavra de julgamento ({m.group(0)!r}) em: {t[:90]}…")


def gerar(C, rede, gerado):
    """Baixa, confere, monta e grava trabalho.json. Aborta (e mantém o arquivo anterior) em qualquer checagem."""
    br_ = montar_brutos(C, rede)
    o = construir(C, br_, gerado)
    checar_textos(textos(o))
    destino = C.SAIDA / "trabalho.json"
    ant = C.ler_cache(destino)
    if ant and ant.get("graficos") and pontos_do_bloco(o) < LIMITE_ENCOLHIMENTO * pontos_do_bloco(ant):
        raise Aborta(f"o bloco de trabalho encolheu de {pontos_do_bloco(ant)} para {pontos_do_bloco(o)} pontos; nada gravado")
    C.gravar_json(destino, o, linhas=("secoes",))
    return o


def imprimir(o):
    print("\nSÉRIES (último valor)")
    for g in o["graficos"].values():
        for s in g["series"]:
            u = s["pontos"][-1]
            print(f"  {s['nome']:<42} {s['pontos'][0][0]} → {u[0]}  último = {u[1]}  [{s['unidade']}]")
    print("\nFRASES")
    for s in o["secoes"]:
        print(f"\n{s['n']}. {s['titulo']}\n   {s['frase']}\n   ({s['definicao']})")
        if s.get("real"):
            print("   " + s["real"])
    print("\nCONFERÊNCIAS", json.dumps(o["conferencias"], ensure_ascii=False))


# ------------------------------------------------------------------------------------------------ testes
def testar():
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1
    igual(br(1234.5, 1), "1.234,5", "milhar e vírgula")
    igual(br(-0.04, 1), "0,0", "zero negativo arredondado não leva sinal")
    igual(br(-63.0376, 1), MENOS + "63,0", "sinal de menos")
    igual(dinheiro(-63.0, "US$", 1), MENOS + "US$ 63,0", "sinal antes da moeda")
    igual(deslocar("2026-01-01", -12), "2025-01-01", "desloca 12 meses")
    igual(deslocar("2026-03-01", -2), "2026-01-01", "desloca 2 meses")
    igual(rotulo("2026-08-01", "tm"), "jun–ago/2026", "trimestre móvel")
    igual(rotulo("2026-02-01", "tm"), "dez/2025–fev/2026", "trimestre móvel que cruza o ano")
    igual(rotulo("2026-04-01", "tri"), "2º tri/2026", "trimestre")
    p = [(deslocar("2025-01-01", i), float(v)) for i, v in enumerate([5, 9, 3, 7, 7, 6, 8, 4, 6, 7, 5, 6, 6, 7])]
    f = fato(p, "M", "mes")
    igual((f["extremo"], f["antes"]["valor"]), (None, p[1][1]), "fato: 12 meses antes")
    igual(f["max"]["valor"], 9.0, "fato: máximo")
    igual(trecho_extremos(fato([(deslocar("2025-01-01", i), float(i)) for i in range(14)], "M", "mes"), lambda v: str(v)).startswith("É o maior valor da série"), True, "extremo: maior")
    igual(trecho_extremos(fato([(deslocar("2025-01-01", i), float(-i)) for i in range(14)], "M", "mes"), lambda v: str(v)).startswith("É o menor valor da série"), True, "extremo: menor")
    igual(variacao_txt(5.3, 5.6, "pp"), MENOS + "0,3 p.p.", "variação em p.p.")
    igual(variacao_txt(3777, 3642, "pct"), "+3,7%", "variação %")
    # lacuna: a oficial passa, a nova aborta
    e = dict(unid="%", minimo=0, maximo=100, lacunas={"2015-10-01"})
    base = [("2015-09-01", 5.0), ("2015-12-01", 6.0)]
    try:
        checar("x", {"pontos": base, "unidade": "%", "nome": "x"}, e, "M", "2026-10-19")
        raise SystemExit("deveria abortar: 2015-11 não é lacuna conhecida")
    except Aborta:
        ok += 1
    r = checar("x", {"pontos": [("2015-09-01", 5.0), ("2015-12-01", 6.0)], "unidade": "%", "nome": "x"}, dict(e, lacunas={"2015-10-01", "2015-11-01"}), "M", "2026-10-19")
    igual(r, ["2015-10-01", "2015-11-01"], "lacunas oficiais passam e ficam registradas")
    for pontos, esperado in (([("2030-01-01", 1.0)], "futuro"), ([("2025-01-01", 500.0)], "plausível"), ([], "vazia")):
        try:
            checar("x", {"pontos": pontos, "unidade": "%", "nome": "x"}, dict(unid="%", minimo=0, maximo=100), "M", "2026-10-19")
            raise SystemExit(f"deveria abortar: {esperado}")
        except Aborta:
            ok += 1
    try:
        checar("x", {"pontos": [("2025-01-01", 5.0)], "unidade": "Mil pessoas", "nome": "x"}, dict(unid="%", minimo=0, maximo=100), "M", "2026-10-19")
        raise SystemExit("deveria abortar: unidade")
    except Aborta:
        ok += 1
    try:
        checar_textos(["Foi um ano bom para o emprego"])
        raise SystemExit("deveria abortar: palavra de julgamento")
    except Aborta:
        ok += 1
    checar_textos(["O salário médio compra mais ou menos que antes?", "A renda disponível dos residentes."])
    ok += 1
    # o texto fixo da página (o bloco recolhido e o JS dele) também não pode ter palavra de julgamento
    html = (Path(__file__).resolve().parent.parent / "site" / "contexto.html").read_text(encoding="utf-8")
    ini = html.index('<section class="painel" id="indicadores"')
    bloco = html[ini:html.index('id="ifiBloco"', ini)]       # as duas páginas dos números oficiais (botão, títulos e textos fixos)
    js = html[html.index("/* ===================================================================== mais indicadores"):html.index("/* ===================================================================== início")]
    trechos = [a or b for a, b in re.findall(r"`([^`]*)`|'([^'\n]{12,})'", js)]
    estatico = re.sub(r"<[^>]+>", " ", bloco) + " " + " ".join(trechos)
    estatico = re.sub(r"\$\{[^}]*\}", " ", estatico)
    igual(len(estatico) > 3000, True, "o texto fixo da página foi lido (não está vazio)")
    checar_textos([estatico])
    ok += 1
    print(f"{ok} verificações passaram")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--so-montar", action="store_true", help="não chama API: remonta o JSON do cache")
    ap.add_argument("--imprimir", action="store_true", help="imprime o último valor de cada série e as frases geradas")
    ap.add_argument("--teste", action="store_true", help="testes sintéticos, sem rede")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    import contexto as C
    from datetime import datetime
    gerado = datetime.now(C.BRASILIA).isoformat(timespec="minutes")
    try:
        o = gerar(C, not args.so_montar, gerado)
    except Aborta as e:
        print(f"ABORTADO (nada gravado): {e}")
        sys.exit(1)
    print(f"trabalho.json gravado: {len(o['secoes'])} seções, {len(o['graficos'])} gráficos, {pontos_do_bloco(o)} pontos")
    if args.imprimir:
        imprimir(o)


if __name__ == "__main__":
    main()
