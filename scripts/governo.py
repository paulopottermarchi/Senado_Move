"""
Câmara Aberta — alinhamento dos partidos ao Governo, governo a governo.

Uso:
    python scripts/governo.py            # depois de votos.py e blocos.py
    python scripts/governo.py --refazer  # ignora o cache derivado
    python scripts/governo.py --manter-votos   # não apaga os votos individuais de 2003–2022 depois de usá-los

Grava site/dados/partidos/governo.json, lido pela aba Partidos.

A MEDIDA é a de blocos.py ("Quem vota com quem"), sem mudar a regra: para cada deputado, a frequência com que
votou como o líder do Governo orientou (Sim ou Não) nas votações nominais do Plenário; o número do partido é a
mediana entre os deputados dele (mínimo de 3), pelo partido em que o deputado mais votou NO PERÍODO. Mesmos
mínimos: 100 votos Sim/Não no período para o deputado entrar, 50 votações com orientação para ter número.

O QUE MUDA é o recorte no tempo. Em vez de uma legislatura, o período é um GOVERNO (entradas/governos.json):
a 55ª legislatura teve Dilma e depois Temer, e uma mediana única misturaria dois governos — o PMDB mudou de lado
no meio. QUEM É O GOVERNO em cada votação não sai da tabela de governos, e sim do registro de orientação da
própria Câmara (bancada "GOV." ou "Governo"; é o líder do Governo do dia). A tabela só diz onde cortar o período e
qual é o partido do presidente, para marcar a coluna.

FONTES: os arquivos anuais em lote da Câmara — votacoes (órgão da votação), votacoesVotos (voto de cada deputado,
com o partido na data do voto) e votacoesOrientacoes (a bancada "GOV." ou "Governo"). Orientação só existe a
partir de 2003; antes disso não há medida (e os votos individuais de 2001–2002 são parciais).

SOBREVIVENTE: ao contrário de blocos.py, que só olha os 513 de hoje, aqui entra QUEM VOTOU — senão cada governo
antigo ficaria só com os deputados que ainda estão na Câmara. Por isso o governo atual difere até ~1,5 ponto do que
a página "Quem vota com quem" mostra por partido (suplentes que já saíram entram aqui). A conferência abaixo
reproduz o número do blocos.py exatamente quando se restringe a mesma população.

CACHE: derivado por ano em cache/governo/{ano}.json (pequeno). Os votos individuais de 2003–2022 pesam ~480 MB e
só este script os usa; depois de derivados, são apagados (--manter-votos para guardar).
"""

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date

import coleta
import partidos
import votos
from coleta import CACHE, DADOS, ENTRADAS, gravar_cache, ler_cache

SAIDA = DADOS / "partidos" / "governo.json"
DERIVADO = CACHE / "governo"
GOVERNOS = ENTRADAS / "governos.json"
VERSAO = "2026-10-02.1"          # mudar a cada alteração de regra deste script
ROTULOS_GOVERNO = ("GOV.", "Governo")   # a Câmara grafou a bancada de duas formas ao longo dos anos
MIN_VOTOS = 100                  # votos Sim/Não no período para o deputado entrar (blocos.MIN_VOTOS)
MIN_COMPARAVEIS = 50             # votações com orientação para o deputado ter número (blocos.py)
MIN_DEPUTADOS = 3                # deputados com número para o partido ter mediana (blocos.py)
MIN_VOTACOES_LEG = 20            # votações com orientação para dizer que um governo "tem dados" numa legislatura
PRIMEIRO_ANO = 2003              # votacoesOrientacoes começa aqui


def mediana(xs):
    return statistics.median(xs) if xs else None


def carregar_governos():
    g = json.loads(GOVERNOS.read_text(encoding="utf-8"))["governos"]
    ids = [x["id"] for x in g]
    assert len(set(ids)) == len(ids), "id de governo repetido"
    for a, b in zip(g, g[1:]):
        assert a["fim"] < b["inicio"], f"governos {a['id']} e {b['id']} se sobrepõem"
    return g


def governo_de(data, governos):
    for g in governos:
        if g["inicio"] <= data <= g["fim"]:
            return g["id"]
    return None


def md5(caminhos):
    h = hashlib.md5()
    for c in caminhos:
        with open(c, "rb") as f:
            for bloco in iter(lambda: f.read(1 << 20), b""):
                h.update(bloco)
    return h.hexdigest()


def processar_ano(ano, governos, legs, refazer, manter):
    """Por deputado e governo, os votos Sim/Não nominais do Plenário do ano, pelo partido NA DATA DO VOTO:
    {dep: {governo: {sigla: [votos, comparáveis, alinhados]}}}, mais a contagem de votações."""
    caminho = DERIVADO / f"{ano}.json"
    arqs = {t: votos.BULK / f"{t}-{ano}.csv" for t in ("votacoes", "votacoesOrientacoes", "votacoesVotos")}
    ficha = {"v": VERSAO, "g": [(g["id"], g["inicio"], g["fim"]) for g in governos]}
    c = None if refazer else ler_cache(caminho)
    # sem os CSV (apagados depois de derivar), vale o cache; com eles, ele tem de bater com o conteúdo de hoje
    if c and c.get("fp", {}).get("v") == ficha["v"] and c["fp"].get("g") == [list(x) for x in ficha["g"]]:
        if not all(a.exists() for a in arqs.values()) or c["fp"].get("md5") == md5(arqs.values()):
            return c

    for t in arqs:
        votos.arquivo(t, ano)
    plen = {r["id"]: r["dataHoraRegistro"][:10] for r in votos.linhas("votacoes", [ano]) if r["siglaOrgao"] == "PLEN"}
    orient = {}
    for r in votos.linhas("votacoesOrientacoes", [ano]):
        if r["siglaBancada"] in ROTULOS_GOVERNO and r["orientacao"] in (votos.SIM, votos.NAO):
            # a mesma votação nunca traz os dois rótulos com orientação diferente (conferido 2013–2022)
            assert orient.setdefault(r["idVotacao"], r["orientacao"]) == r["orientacao"], r["idVotacao"]

    dep = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0])))
    nominais, comparaveis, por_leg = defaultdict(set), defaultdict(set), defaultdict(set)
    for r in votos.linhas("votacoesVotos", [ano]):
        i, voto = r["idVotacao"], r["voto"]
        data = plen.get(i)
        if data is None or voto not in (votos.SIM, votos.NAO):
            continue
        gid = governo_de(data, governos)
        if gid is None:
            continue
        x = dep[r["deputado_id"]][gid][partidos.sigla_de(r["deputado_siglaPartido"], data)]
        x[0] += 1
        nominais[gid].add(i)
        o = orient.get(i)
        if o:
            x[1] += 1
            x[2] += voto == o
            comparaveis[gid].add(i)
            leg = coleta.legislatura_de(data, legs)
            if leg is not None:
                por_leg[f"{leg}|{gid}"].add(i)
    c = {"fp": {"v": VERSAO, "g": [list(x) for x in ficha["g"]],
                "md5": md5(arqs.values())},
         "dep": {d: {g: s for g, s in gs.items()} for d, gs in dep.items()},
         "nominais": {g: len(s) for g, s in nominais.items()},
         "comparaveis": {g: len(s) for g, s in comparaveis.items()},
         "porLeg": {k: len(s) for k, s in por_leg.items()}}
    gravar_cache(caminho, c)
    if ano < votos.ANOS.start and not manter:
        # só este script lê os votos individuais anteriores à legislatura atual; derivados, ocupam ~480 MB à toa
        arqs["votacoesVotos"].unlink(missing_ok=True)
    return c


def agregar(por_ano, governos):
    """Junta os anos: por governo e deputado, [votos, comparáveis, alinhados] e o partido de cada voto."""
    gd = {g["id"]: defaultdict(lambda: {"n": 0, "c": 0, "a": 0, "p": Counter()}) for g in governos}
    nominais, comparaveis, por_leg = Counter(), Counter(), Counter()
    for c in por_ano:
        for d, gs in c["dep"].items():
            for gid, siglas in gs.items():
                x = gd[gid][d]
                for s, (n, comp, ali) in siglas.items():
                    x["n"] += n
                    x["c"] += comp
                    x["a"] += ali
                    x["p"][s] += n
        nominais.update(c["nominais"])
        comparaveis.update(c["comparaveis"])
        por_leg.update(c["porLeg"])
    return gd, nominais, comparaveis, por_leg


def medianas(gd_g, so_estes=None):
    """{sigla: [mediana, deputados]} do governo, mais a mediana de todos. so_estes: restringe os deputados (só
    para a conferência contra blocos.py)."""
    por = defaultdict(list)
    todos = []
    for d, x in gd_g.items():
        if so_estes is not None and d not in so_estes:
            continue
        if x["n"] < MIN_VOTOS or x["c"] < MIN_COMPARAVEIS:
            continue
        v = x["a"] / x["c"]
        todos.append(v)
        s = x["p"].most_common(1)[0][0]
        if s != partidos.SEM_PARTIDO:
            por[s].append(v)
    return ({s: [round(mediana(v), 3), len(v)] for s, v in por.items() if len(v) >= MIN_DEPUTADOS},
            round(mediana(todos), 3) if todos else None, len(todos))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--refazer", action="store_true")
    ap.add_argument("--manter-votos", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    hoje = date.today()
    governos = carregar_governos()
    legs = [l for l in coleta.legislaturas() if l["id"] >= partidos.LEG_MIN]

    anos = range(max(PRIMEIRO_ANO, int(governos[0]["inicio"][:4])), hoje.year + 1)
    por_ano = []
    for ano in anos:
        print(f"   {ano}", end="", flush=True)
        por_ano.append(processar_ano(ano, governos, legs, args.refazer, args.manter_votos))
    print()
    gd, nominais, comparaveis, por_leg = agregar(por_ano, governos)

    saida_g, saida_p = [], defaultdict(dict)
    for g in governos:
        gid = g["id"]
        med, geral, n_dep = medianas(gd[gid])
        if not n_dep:
            continue
        for s, (m, nd) in med.items():
            saida_p[s][gid] = [m, nd]
        saida_g.append({**{k: g[k] for k in ("id", "rotulo", "presidente", "curto", "inicio", "partidoPresidente")},
                        "fim": min(g["fim"], hoje.isoformat()), "emCurso": g["fim"] >= hoje.isoformat(),
                        **({"nota": g["nota"]} if g.get("nota") else {}),
                        "votacoesNominais": nominais[gid], "votacoesComOrientacao": comparaveis[gid],
                        "deputados": n_dep, "mediana": geral})
    legislaturas = defaultdict(list)
    for chave, n in por_leg.items():
        leg, gid = chave.split("|")
        if n >= MIN_VOTACOES_LEG:
            legislaturas[leg].append(gid)
    ordem = {g["id"]: i for i, g in enumerate(governos)}
    legislaturas = {l: sorted(v, key=ordem.get) for l, v in sorted(legislaturas.items())}

    # ---- conferência: com a MESMA população do blocos.py (os 513 de hoje), o governo atual tem de dar o mesmo número
    blocos_json = DADOS / "blocos.json"
    aviso = None
    if blocos_json.exists() and (DADOS / "deputados.json").exists():
        hoje_ids = {str(d["id"]) for d in json.loads((DADOS / "deputados.json").read_text(encoding="utf-8"))}
        mesmo, _, _ = medianas(gd[governos[-1]["id"]], so_estes=hoje_ids)
        ref = json.loads(blocos_json.read_text(encoding="utf-8")).get("medianaGovernoPorPartido") or {}
        dif = {s: (mesmo.get(s, [None])[0], ref.get(s)) for s in set(mesmo) | set(ref) if mesmo.get(s, [None])[0] != ref.get(s)}
        aviso = dif
        print(f"Conferência com blocos.json (mesma população): "
              f"{'IGUAL' if not dif else 'DIFERENTE em ' + str(len(dif)) + ' partido(s): ' + str(dif)}")
        if dif:
            print("   (blocos.json pode ser de outro dia: rode blocos.py e este script em seguida)")

    obj = {
        "_fonte": "Dados Abertos da Câmara: votacoes, votacoesVotos e votacoesOrientacoes (bancada 'GOV.' ou 'Governo'), "
                  "votações nominais do Plenário, votos Sim e Não. Presidentes e partidos: entradas/governos.json.",
        "_gerado": hoje.isoformat(), "versao": VERSAO,
        "regras": {"minVotos": MIN_VOTOS, "minComparaveis": MIN_COMPARAVEIS, "minDeputados": MIN_DEPUTADOS,
                   "primeiroAno": PRIMEIRO_ANO},
        "governos": saida_g,
        "legislaturas": legislaturas,
        "partidos": {s: v for s, v in sorted(saida_p.items())},
    }
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA)

    print(f"\n{'governo':>10}{'nominais':>10}{'c/ Governo':>11}{'deputados':>10}{'partidos':>9}{'Câmara':>8}")
    for g in saida_g:
        print(f"{g['rotulo']:>10}{g['votacoesNominais']:>10}{g['votacoesComOrientacao']:>11}{g['deputados']:>10}"
              f"{sum(1 for v in saida_p.values() if g['id'] in v):>9}{g['mediana']:>8.1%}")
    print(f"Legislaturas com dados: {legislaturas}")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")
    return aviso


if __name__ == "__main__":
    main()
