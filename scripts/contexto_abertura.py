"""
Câmara Aberta — "Abertura comercial" da página de economia (bloco "Mais indicadores"): (exportações + importações de bens e serviços) ÷ PIB.

Uso (chamado por contexto.py, que passa o seu próprio módulo; também roda sozinho):
    python scripts/contexto_abertura.py [--so-montar] [--imprimir] [--teste]

Grava site/dados/contexto/abertura.json. Fonte: IBGE, Contas Nacionais Trimestrais (SIDRA, tabela 1846, "Valores a preços correntes", variável 585, R$ milhões,
trimestral desde 1996-T1). Exportação e importação de bens e serviços e PIB, os três a preços correntes e em REAIS: a mesma moeda no numerador e no denominador,
para o câmbio não distorcer a proporção. Nada é deflacionado e nada é convertido.

- Cada ponto é a SOMA DOS QUATRO TRIMESTRES terminados naquele trimestre, no numerador e no denominador (como o IBGE apresenta o PIB acumulado em 4 trimestres):
  tira a sazonalidade das exportações (safra) sem interpolar nada. A série começa no 4º trimestre de 1996, o primeiro com quatro trimestres de dado.
- NÃO é a balança comercial do MDIC (comex.py): aquela é só de bens, em dólares; esta inclui serviços e é em reais. A página diz isso.
- Checagens que ABORTAM (o arquivo anterior fica): série vazia ou com buraco, unidade ou nome da variável diferente, valor negativo, data no futuro, abertura fora de
  uma faixa plausível, a identidade das contas (PIB = consumo + governo + FBCF + estoques + exportação − importação) que não fecha, encolhimento, palavra de julgamento.
- A frase de abertura é gerada dos números aqui, nunca escrita à mão; sem "mais aberta", "menos aberta", "fechada", "protecionismo", "liberalização" ou qualquer frase
  que sugira que mais ou menos é melhor.
"""

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contexto_trabalho import (Aborta, PROIBIDAS as PROIBIDAS_ECONOMIA, br, checar, checar_textos as checar_proibidas, deslocar, fato,
                               fonte_sidra, pts, rotulo, sidra_serie, trecho_extremos, variacao_txt)

VERSAO = "abertura-1"
LIMITE_ENCOLHIMENTO = 0.9
TAB, VAR, CLS = 1846, 585, 11255
FAIXA_ABERTURA = (8.0, 60.0)          # % do PIB: fora disso a série mudou de sentido (sinal, unidade, definição)
FAIXA_COMPONENTE = (2.0, 40.0)
TOLERANCIA_IDENTIDADE = 10            # R$ milhões: a identidade das contas é exata; o IBGE arredonda cada componente ao milhão

# categoria da classificação 11255 ("Setores e subsetores") → nome na série
CATEGORIAS = OrderedDict([("pib", 90707), ("exp", 93407), ("imp", 93408), ("consumo", 93404), ("governo", 93405), ("fbcf", 93406), ("estoques", 102880)])
FAIXA_VALOR = {"pib": (100_000, 20_000_000), "exp": (5_000, 5_000_000), "imp": (5_000, 5_000_000), "consumo": (50_000, 20_000_000),
               "governo": (10_000, 5_000_000), "fbcf": (10_000, 5_000_000), "estoques": (-1_000_000, 1_000_000)}
SERIES = OrderedDict((k, dict(tab=TAB, var=VAR, cls=f"{CLS}[{c}]", freq="T", unid="Milhões de Reais", nome_contem="preços correntes",
                              minimo=FAIXA_VALOR[k][0], maximo=FAIXA_VALOR[k][1])) for k, c in CATEGORIAS.items())
# conferência extra, só reportada: o PIB da tabela 2072 (contas econômicas trimestrais, o mesmo que o bloco da renda nacional usa)
CONFERENCIA_PIB = dict(tab=2072, var=933, freq="T", unid="Milhões de Reais", minimo=100_000, maximo=20_000_000)

PROIBIDAS = PROIBIDAS_ECONOMIA + [r"mais aberta", r"menos aberta", r"\bfechada", r"\bfechado", r"protecionis", r"liberaliza", r"\babertura excessiva",
                                  r"dependên", r"\bdepende"]

DEFINICAO = "Mostra quanto do PIB corresponde ao comércio com o exterior."
NOTA_FONTE = ("Abertura comercial = (exportações de bens e serviços + importações de bens e serviços) ÷ PIB, tudo a preços correntes e em reais (Contas Nacionais Trimestrais do IBGE, "
              "tabela 1846). A mesma moeda no numerador e no denominador evita que o câmbio distorça a proporção. Cada ponto soma os quatro trimestres terminados no trimestre indicado, "
              "no numerador e no denominador; a série começa no 4º trimestre de 1996.")
NOTA_BALANCA = ("Isto não é a balança comercial do MDIC (seção das exportações, no alto da página): aquela é só de bens, em dólares e por mês; esta inclui serviços, é em reais e "
                "trimestral, e vem de outra fonte. Os números não se comparam.")


# ------------------------------------------------------------------------------------------------ coleta e montagem
def montar_brutos(C, rede):
    arq = C.CACHE_C / "abertura_brutos.json"
    if not rede:
        b = C.ler_cache(arq)
        if not b:
            raise Aborta("--so-montar sem cache da abertura comercial: rode contexto.py inteiro antes")
        return b
    print("Abertura comercial: SIDRA (IBGE), Contas Nacionais Trimestrais…", flush=True)
    out = {"sidra": {nome: sidra_serie(C, nome, e) for nome, e in SERIES.items()}, "coletadoEm": C.HOJE.isoformat()}
    out["sidra"]["pib2072"] = sidra_serie(C, "pib2072", CONFERENCIA_PIB)
    C.gravar_cache(arq, out)
    return out


def variacao_frase(atual, antes):
    """"variação de −2,8 p.p." (diferença do valor atual para o de um ano antes), ou "sem variação"; o rótulo evita ler o sinal como se fosse do valor de um ano antes."""
    v = variacao_txt(atual, antes, "pp")
    return v if v == "sem variação" else f"variação de {v}"


def acumulado_4tri(pontos):
    """[(data do último trimestre, soma dos quatro trimestres)]. Os trimestres são consecutivos (checar() já recusou buraco)."""
    return [(pontos[i][0], sum(v for _, v in pontos[i - 3:i + 1])) for i in range(3, len(pontos))]


def construir(C, brutos, gerado):
    S = {k: dict(v, pontos=[tuple(p) for p in v["pontos"]]) for k, v in brutos["sidra"].items()}
    hoje = C.HOJE.isoformat()
    for nome, e in list(SERIES.items()) + [("pib2072", CONFERENCIA_PIB)]:
        checar(nome, S[nome], e, e["freq"], hoje)
        if S[nome].get("ausentes"):
            raise Aborta(f"{nome}: a fonte marca como indisponível {S[nome]['ausentes'][:4]}")
    datas = {k: [d for d, _ in S[k]["pontos"]] for k in SERIES}
    if len({tuple(v) for v in datas.values()}) != 1:
        raise Aborta("as séries das Contas Nacionais não têm os mesmos trimestres: " + ", ".join(f"{k} {v[0]}→{v[-1]}" for k, v in datas.items()))
    D = {k: dict(S[k]["pontos"]) for k in SERIES}
    # identidade das contas: PIB = consumo das famílias + consumo do governo + FBCF + variação de estoques + exportação − importação (a importação vem positiva)
    maior = max(abs(D["pib"][d] - (D["consumo"][d] + D["governo"][d] + D["fbcf"][d] + D["estoques"][d] + D["exp"][d] - D["imp"][d])) for d in D["pib"])
    if maior > TOLERANCIA_IDENTIDADE:
        raise Aborta(f"as contas não fecham: PIB difere de consumo + governo + FBCF + estoques + exportação − importação em até R$ {maior:.0f} milhões")
    d2072 = dict(S["pib2072"]["pontos"])
    dif_pib = max((abs(v - d2072[d]) for d, v in D["pib"].items() if d in d2072), default=None)

    pib4, exp4, imp4 = (dict(acumulado_4tri(S[k]["pontos"])) for k in ("pib", "exp", "imp"))
    datas4 = sorted(pib4)
    if not datas4:
        raise Aborta("poucos trimestres para somar quatro")
    razao = {k: [(d, num[d] / pib4[d] * 100) for d in datas4] for k, num in (("exp", exp4), ("imp", imp4))}
    razao["soma"] = [(d, (exp4[d] + imp4[d]) / pib4[d] * 100) for d in datas4]
    for k, lista in razao.items():
        lo, hi = FAIXA_ABERTURA if k == "soma" else FAIXA_COMPONENTE
        for d, v in lista:
            if not lo <= v <= hi:
                raise Aborta(f"abertura fora da faixa plausível ({lo}% a {hi}%): {k} = {v:.2f}% em {d}")

    # ---- a seção
    f = fato(razao["soma"], "T", "tri")
    pc = lambda v: f"{br(v, 1)}%"
    comp_u = {k: dict(razao[k])[f["data"]] for k in ("exp", "imp")}
    frase = (f"A soma das exportações e das importações de bens e serviços correspondeu a {pc(f['valor'])} do PIB nos quatro trimestres até o {f['rotulo']} "
             f"(exportações, {pc(comp_u['exp'])}; importações, {pc(comp_u['imp'])}). "
             + (f"Um ano antes (quatro trimestres até o {f['antes']['rotulo']}), foi de {pc(f['antes']['valor'])} ({variacao_frase(f['valor'], f['antes']['valor'])}). " if "antes" in f else "")
             + trecho_extremos(f, pc))
    ult = f["data"]
    secao = {"id": "abertura", "n": 8, "titulo": "Quanto do PIB corresponde ao comércio com o exterior?", "grafico": "g_abertura", "avancada": True, "fato": f, "frase": frase,
             "definicao": DEFINICAO, "notaFixa": NOTA_BALANCA, "notaMetodo": NOTA_FONTE,
             "decomposicao": {"data": ult, "pib4": pib4[ult], "exportacoes4": exp4[ult], "importacoes4": imp4[ult], "unidade": "R$ milhões, soma de quatro trimestres"},
             "fontes": [fonte_sidra(C, TAB, VAR)]}
    nomes = {"exp": "Exportações de bens e serviços", "imp": "Importações de bens e serviços", "soma": "Soma (exportações + importações)"}
    grafico = {"titulo": "Exportações e importações de bens e serviços, em % do PIB (soma de quatro trimestres)", "rotData": "tri", "fmt": "pct", "zero": True,
               "rotulosDiretos": False, "marcarPontos": False, "notas": ["abertura"],
               "series": [{"id": k, "nome": nomes[k], "unidade": "% do PIB", "freq": "T", "pontos": pts(razao[k], 1.0, 3)} for k in ("exp", "imp", "soma")]}
    notas = {"abertura": {"curta": "Bens e serviços, a preços correntes e em reais; cada ponto soma os quatro trimestres até o trimestre indicado. Não é a balança comercial do MDIC.",
                          "texto": NOTA_FONTE + " " + NOTA_BALANCA, "fontes": []}}
    return {"versao": VERSAO, "geradoEm": gerado, "hoje": hoje, "coletadoEm": brutos.get("coletadoEm"), "secoes": [secao], "graficos": {"g_abertura": grafico}, "notas": notas,
            "conferencias": {"identidadeDasContas": {"texto": "PIB = consumo das famílias + consumo do governo + FBCF + variação de estoques + exportação − importação (diferença máxima, R$ milhões)",
                                                      "maximo": round(maior, 1), "trimestres": len(D["pib"])},
                             "pibEntreTabelas": {"texto": "PIB da tabela 1846 menos PIB da tabela 2072 (diferença máxima, R$ milhões; só reportada)", "maximo": dif_pib}}}


def pontos_do_bloco(o):
    return sum(len(s["pontos"]) for g in o["graficos"].values() for s in g["series"])


def textos(o):
    t = []
    for s in o["secoes"]:
        t += [s["titulo"], s["frase"], s["definicao"], s.get("notaFixa", ""), s.get("notaMetodo", "")]
    t += [g["titulo"] for g in o["graficos"].values()] + [s["nome"] for g in o["graficos"].values() for s in g["series"]]
    t += [n["texto"] for n in o["notas"].values()] + [n["curta"] for n in o["notas"].values()]
    return [x for x in t if x]


def checar_textos(txts):
    checar_proibidas(txts, PROIBIDAS)


def gerar(C, rede, gerado):
    """Baixa, confere, monta e grava abertura.json. Aborta (e mantém o arquivo anterior) em qualquer checagem."""
    o = construir(C, montar_brutos(C, rede), gerado)
    checar_textos(textos(o))
    destino = C.SAIDA / "abertura.json"
    ant = C.ler_cache(destino)
    if ant and ant.get("graficos") and pontos_do_bloco(o) < LIMITE_ENCOLHIMENTO * pontos_do_bloco(ant):
        raise Aborta(f"o bloco da abertura encolheu de {pontos_do_bloco(ant)} para {pontos_do_bloco(o)} pontos; nada gravado")
    C.gravar_json(destino, o, linhas=("secoes",))
    return o


def imprimir(o):
    print("\nSÉRIES (último valor)")
    for s in o["graficos"]["g_abertura"]["series"]:
        u = s["pontos"][-1]
        print(f"  {s['nome']:<40} {rotulo(s['pontos'][0][0], 'tri')} → {rotulo(u[0], 'tri')}  último = {u[1]}%")
    print("\nFRASE\n  " + o["secoes"][0]["frase"] + "\n  (" + o["secoes"][0]["definicao"] + ")")
    print("\nCONFERÊNCIAS", json.dumps(o["conferencias"], ensure_ascii=False))


# ------------------------------------------------------------------------------------------------ testes
def testar():
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1

    def aborta(fn, msg):
        nonlocal ok
        try:
            fn()
        except Aborta:
            ok += 1
            return
        raise AssertionError(f"deveria abortar: {msg}")

    p = [(deslocar("1996-01-01", 3 * i), float(10 + i)) for i in range(8)]
    ac = acumulado_4tri(p)
    igual(len(ac), 5, "quatro trimestres: perde os três primeiros")
    igual(ac[0], ("1996-10-01", 10 + 11 + 12 + 13.0), "soma dos quatro primeiros")
    igual(ac[-1][1], 14 + 15 + 16 + 17.0, "soma dos quatro últimos")
    igual(rotulo("1996-10-01", "tri"), "4º tri/1996", "rótulo do trimestre")

    class FakeC:                       # as Contas Nacionais de brinquedo: o PIB fecha com a identidade
        HOJE = type("H", (), {"isoformat": staticmethod(lambda: "2026-10-20")})()
        URL_IBGE = "https://servicodados.ibge.gov.br/api/v3/agregados"
        CACHE_C = Path("/nao/existe")
        SAIDA = Path("/nao/existe")

    def reg(pontos):
        return {"pontos": pontos, "ausentes": [], "unidade": "Milhões de Reais", "nome": "Valores a preços correntes", "tab": TAB, "var": VAR, "freq": "T"}

    def bruto(troca=None, exp=170_000, imp=120_000):
        """Doze trimestres de brinquedo em que o PIB fecha com a identidade das contas; a exportação sobe 1.000 por trimestre e o PIB com ela."""
        datas = [deslocar("2020-01-01", 3 * i) for i in range(12)]
        serie = lambda f: [(d, float(f(i))) for i, d in enumerate(datas)]
        governo, fbcf, estoques = 200_000, 150_000, 0
        s = {"governo": reg(serie(lambda i: governo)), "fbcf": reg(serie(lambda i: fbcf)), "estoques": reg(serie(lambda i: estoques)),
             "exp": reg(serie(lambda i: exp + 1000 * i)), "imp": reg(serie(lambda i: imp)), "pib": reg(serie(lambda i: 1_000_000 + 1000 * i))}
        s["consumo"] = reg(serie(lambda i: 1_000_000 - governo - fbcf - estoques - exp + imp))      # o que falta para a identidade fechar
        s["pib2072"] = reg(list(s["pib"]["pontos"]))
        if troca:
            troca(s)
        return {"sidra": s, "coletadoEm": "2026-10-20"}
    # a soma é (170 + 120) ÷ 1.000 = cerca de 29% do PIB
    o = construir(FakeC, bruto(), "2026-10-20T00:00-03:00")
    igual(o["secoes"][0]["definicao"], DEFINICAO, "a definição fixa")
    igual(o["conferencias"]["identidadeDasContas"]["maximo"], 0.0, "a identidade fecha")
    checar_textos(textos(o))
    ok += 1
    aborta(lambda: construir(FakeC, bruto(lambda s: s["imp"].update(pontos=[(d, -v) for d, v in s["imp"]["pontos"]])), "x"), "importação negativa")
    aborta(lambda: construir(FakeC, bruto(exp=400_000, imp=350_000), "x"), "abertura fora da faixa (75% do PIB)")
    aborta(lambda: construir(FakeC, bruto(lambda s: s["consumo"].update(pontos=[(d, v + 500) for d, v in s["consumo"]["pontos"]])), "x"), "a identidade não fecha")
    aborta(lambda: construir(FakeC, bruto(lambda s: s["exp"].update(unidade="Mil Reais")), "x"), "unidade diferente")
    aborta(lambda: construir(FakeC, bruto(lambda s: s["exp"].update(pontos=s["exp"]["pontos"][:5] + s["exp"]["pontos"][6:])), "x"), "buraco na série")
    aborta(lambda: construir(FakeC, bruto(lambda s: s["exp"].update(pontos=s["exp"]["pontos"] + [("2030-01-01", 500.0)])), "x"), "data no futuro")
    for palavra in ("A economia ficou mais aberta", "um país menos aberto, mais fechada", "o protecionismo", "a liberalização do comércio", "dependência do comércio"):
        aborta(lambda palavra=palavra: checar_textos([palavra]), palavra)
    # o texto fixo da página
    html = (Path(__file__).resolve().parent.parent / "site" / "contexto.html").read_text(encoding="utf-8")
    js = html[html.index("/* ===================================================================== mais indicadores"):html.index("/* ===================================================================== início")]
    trechos = [a or b for a, b in re.findall(r"`([^`]*)`|'([^'\n]{12,})'", js)]
    estatico = re.sub(r"\$\{[^}]*\}", " ", " ".join(trechos))
    igual(len(estatico) > 1000, True, "o texto fixo do bloco foi lido")
    checar_textos([estatico])
    ok += 1
    print(f"{ok} verificações passaram")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--so-montar", action="store_true", help="não chama API: remonta o JSON do cache")
    ap.add_argument("--imprimir", action="store_true", help="imprime o último valor de cada série e a frase gerada")
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
    print(f"abertura.json gravado: {len(o['secoes'])} seção, {len(o['graficos'])} gráfico, {pontos_do_bloco(o)} pontos")
    if args.imprimir:
        imprimir(o)


if __name__ == "__main__":
    main()
