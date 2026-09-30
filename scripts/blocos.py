"""
Câmara Aberta — quem vota junto com quem: os blocos que existem de fato no Plenário.

Uso:
    python scripts/blocos.py               # depois de votos.py; lê cache/bulk/, grava blocos.json
    python scripts/blocos.py --atualizar   # rebaixa as orientações do ano corrente (rodada diária)

Nenhuma chamada à API. Fontes: votacoes-{ano}, votacoesVotos-{ano} e
votacoesOrientacoes-{ano} (Dados Abertos da Câmara, os mesmos arquivos de votos.py).

O que calcula, só com votações nominais do Plenário e só votos Sim e Não:
  1. Concordância entre cada par de deputados: votos iguais ÷ votações em que os dois
     votaram Sim ou Não. Par com menos de MIN_COMUNS votações em comum não entra.
  2. Blocos: agrupamento hierárquico por concordância média, SEM usar o partido. O corte
     fica onde a concordância despenca (o maior salto entre fusões sucessivas) — é o dado
     que escolhe quantos blocos há, não o site. Medido em 2023–2026: dois blocos; dentro
     deles, 67% ou mais de concordância; entre eles, 48%.
  3. Para cada deputado: com que frequência votou como o líder do Governo orientou
     (votacoesOrientacoes, bancada "Governo", orientação Sim ou Não) e os 5 deputados com
     quem mais concorda.
  4. Pares improváveis: deputados de partidos a pelo menos DIST_OPOSTOS pontos de distância
     na escala do espectro, que concordam em pelo menos 90% das votações em comum.

O que isto NÃO é: posição ideológica. Medido: a posição de cada deputado no mapa de
similaridade tem correlação de 0,987 com a frequência com que ele segue a orientação do
Governo. No voto nominal brasileiro, o que separa os blocos é governo × oposição — é por
isso que o eixo do gráfico 1 vem de uma pesquisa (BLS), não de votos. Os dois são
mostrados, e nunca um no lugar do outro. Ver CLAUDE.md, "Quem vota com quem".
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import coleta
import votos

SAIDA = coleta.DADOS / "blocos.json"
MIN_VOTOS = 100      # votos Sim/Não no Plenário para o deputado entrar
MIN_COMUNS = 100     # votações em comum para um par contar
DIST_OPOSTOS = 4.0   # pontos na escala 1–10 para dois partidos serem "de lados opostos"
PAR_MIN = 0.90       # concordância mínima de um par improvável
N_PARECIDOS = 5


def mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--atualizar", action="store_true",
                    help="baixar de novo as orientações do ano corrente (os votos, votos.py já rebaixa)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    deputados = json.loads(coleta.SAIDA.read_text(encoding="utf-8"))
    info = {d["id"]: d for d in deputados}

    print("Lendo votações do Plenário (cache/bulk/)…", flush=True)
    vot = {r["id"]: r for r in votos.linhas("votacoes", votos.ANOS)}
    plen = sorted((i for i, v in vot.items() if v["siglaOrgao"] == "PLEN"),
                  key=lambda i: vot[i]["dataHoraRegistro"])
    bit = {v: k for k, v in enumerate(plen)}
    governo = {r["idVotacao"]: r["orientacao"]
               for r in votos.linhas("votacoesOrientacoes", votos.ANOS, args.atualizar)
               if r["siglaBancada"] == "Governo" and r["orientacao"] in (votos.SIM, votos.NAO)}

    # Votos como bits: um inteiro por deputado para Sim e outro para Não. A concordância
    # de um par é uma contagem de bits — 118 mil pares em menos de um segundo.
    sim, nao = defaultdict(int), defaultdict(int)
    gov = defaultdict(lambda: [0, 0])   # [votações com orientação, votou como o Governo]
    siglas = defaultdict(Counter)       # partido NA DATA DO VOTO, voto a voto
    nominais = set()
    for r in votos.linhas("votacoesVotos", votos.ANOS):
        j = bit.get(r["idVotacao"])
        if j is None or r["voto"] not in (votos.SIM, votos.NAO):
            continue
        nominais.add(j)
        d = int(r["deputado_id"])
        siglas[d][r["deputado_siglaPartido"]] += 1
        if r["voto"] == votos.SIM:
            sim[d] |= 1 << j
        else:
            nao[d] |= 1 << j
        o = governo.get(r["idVotacao"])
        if o:
            gov[d][0] += 1
            gov[d][1] += r["voto"] == o

    ids = [d["id"] for d in deputados if (sim[d["id"]] | nao[d["id"]]).bit_count() >= MIN_VOTOS]

    # Partido de cada deputado NO PERÍODO: aquele em que deu a maior parte dos votos. O de
    # hoje engana — medido: 155 dos deputados atuais trocaram de partido (janela de 2026),
    # e Túlio Gadêlha, hoje PSD, votou 837 vezes pela REDE e 52 pelo PSD. Composição dos
    # blocos, medianas por partido e pares improváveis usam o partido do período.
    ideologia = coleta.carregar_ideologia()
    partido = {d: siglas[d].most_common(1)[0][0] for d in ids}
    fatia = {d: siglas[d].most_common(1)[0][1] / sum(siglas[d].values()) for d in ids}
    score = lambda d: (ideologia.get(partido[d]) or {}).get("score")
    n = len(ids)
    ig = [[0] * n for _ in range(n)]
    cm = [[0] * n for _ in range(n)]
    for a in range(n):
        sa, na = sim[ids[a]], nao[ids[a]]
        va = sa | na
        for b in range(a + 1, n):
            sb, nb = sim[ids[b]], nao[ids[b]]
            c = (va & (sb | nb)).bit_count()
            if c >= MIN_COMUNS:
                ig[a][b] = ig[b][a] = ((sa & sb) | (na & nb)).bit_count()
                cm[a][b] = cm[b][a] = c

    # ------------------------------------------------ blocos (sem o partido)
    # Ligação média: concordância entre dois grupos = votos iguais ÷ votações em comum,
    # somados sobre todos os pares entre eles.
    grupos = {i: [i] for i in range(n)}
    g_ig = {(a, b): ig[a][b] for a in range(n) for b in range(a + 1, n)}
    g_cm = {(a, b): cm[a][b] for a in range(n) for b in range(a + 1, n)}
    fusoes, retratos = [], {}
    while len(grupos) > 1:
        v, a, b = max(((g_ig[k] / c, *k) for k, c in g_cm.items() if c), default=(None, 0, 0))
        if v is None:
            break
        fusoes.append(v)
        grupos[a] += grupos.pop(b)
        for k in list(grupos):
            if k != a:
                ka, kb = (min(k, a), max(k, a)), (min(k, b), max(k, b))
                g_ig[ka] += g_ig.pop(kb)
                g_cm[ka] += g_cm.pop(kb)
        del g_ig[(a, b)], g_cm[(a, b)]
        if len(grupos) <= 10:
            retratos[len(grupos)] = [list(g) for g in grupos.values()]
    # Corte no maior salto de concordância entre fusões sucessivas, entre 2 e 10 blocos.
    # fusoes[-k] é a última fusão feita até restarem k blocos (a menor concordância
    # dentro deles); fusoes[-k+1] é a seguinte, que juntaria dois deles.
    saltos = {k: fusoes[-k] - fusoes[-k + 1] for k in range(2, min(11, len(fusoes) + 1))}
    k = max(saltos, key=saltos.get)
    blocos = sorted(retratos[k], key=len, reverse=True)
    dentro, entre = fusoes[-k], fusoes[-k + 1]

    bloco_de = {ids[i]: nb for nb, g in enumerate(blocos) for i in g}
    pct = lambda d: gov[d][1] / gov[d][0] if gov[d][0] else None
    saida_blocos = []
    for nb, g in enumerate(blocos):
        ps = [pct(ids[i]) for i in g if pct(ids[i]) is not None]
        saida_blocos.append({
            "n": len(g),
            "partidos": dict(Counter(partido[ids[i]] for i in g).most_common()),
            "medianaGoverno": round(mediana(ps), 3) if ps else None,
        })

    # ------------------------------------------------ por deputado
    por_partido = defaultdict(list)
    for d in ids:
        if pct(d) is not None and gov[d][0] >= 50:
            por_partido[partido[d]].append(pct(d))
    saida_dep = {}
    for a, d in enumerate(ids):
        parecidos = sorted(((ig[a][b] / cm[a][b], cm[a][b], ids[b]) for b in range(n) if cm[a][b]),
                           reverse=True)[:N_PARECIDOS]
        saida_dep[str(d)] = {
            "bloco": bloco_de[d],
            "governo": [gov[d][1], gov[d][0]] if gov[d][0] >= 50 else None,
            "parecidos": [[b, round(v, 3), c] for v, c, b in parecidos],
        }
    medianas = {p: round(mediana(xs), 3) for p, xs in por_partido.items() if len(xs) >= 3}

    # ------------------------------------------------ pares improváveis
    # Só quem votou quase sempre (75%+) pelo mesmo partido: com troca no meio do período,
    # "partido oposto" deixa de ter sentido.
    improvaveis = []
    for a in range(n):
        for b in range(a + 1, n):
            sa, sb = score(ids[a]), score(ids[b])
            if (cm[a][b] < 2 * MIN_COMUNS or sa is None or sb is None or abs(sa - sb) < DIST_OPOSTOS
                    or fatia[ids[a]] < 0.75 or fatia[ids[b]] < 0.75):
                continue
            v = ig[a][b] / cm[a][b]
            if v >= PAR_MIN:
                improvaveis.append([ids[a], ids[b], round(v, 3), cm[a][b]])
    improvaveis.sort(key=lambda x: (-x[2], -x[3]))

    # ------------------------------------------------ o que cada bloco aprovou, por tema
    # Só as votações finais de texto-base (proposicoes.json), onde Sim = aprovar a lei.
    # Em requerimento e destaque o Sim quer dizer outra coisa (retirar de pauta, manter o
    # texto), e misturar tudo daria uma porcentagem sem sentido. Categorias = as da página
    # do deputado (temas.classificar, gravadas por votos.py em proposicoes.json).
    from temas import BLOCOS, CURTO
    de_curto = {v: k for k, v in CURTO.items()}
    leis =coleta.ler_cache(votos.PROPOSICOES) or []
    por_tema = defaultdict(lambda: {"leis": 0, "votos": [[0, 0] for _ in blocos], "divergem": 0})
    sem_tema = 0
    for lei in leis:
        j = bit.get(lei["idVotacao"])
        if j is None:
            continue
        cont = [[0, 0] for _ in blocos]
        for d in ids:
            if (sim[d] >> j) & 1:
                cont[bloco_de[d]][0] += 1
            elif (nao[d] >> j) & 1:
                cont[bloco_de[d]][1] += 1
        # maiorias opostas: cada bloco com 5+ votos Sim/Não e lados diferentes
        lados = [(s > n) if s + n >= 5 else None for s, n in cont]
        oposto = len(blocos) >= 2 and None not in lados[:2] and lados[0] != lados[1]
        cats = {de_curto.get(t[0]) for t in lei.get("temas") or []} - {None}
        if not cats:
            sem_tema += 1
        for c in cats:
            t = por_tema[c]
            t["leis"] += 1
            t["divergem"] += oposto
            for b in range(len(blocos)):
                t["votos"][b][0] += cont[b][0]
                t["votos"][b][1] += cont[b][1]
    temas_saida = []
    for nome_macro, cs in BLOCOS:
        for c in cs:
            if c not in por_tema:
                continue
            t = por_tema[c]
            temas_saida.append({
                "categoria": c, "grupo": nome_macro, "leis": t["leis"], "divergem": t["divergem"],
                "votos": t["votos"],
                "sim": [round(s / (s + n), 3) if s + n else None for s, n in t["votos"]],
            })

    # Sanidade: todo deputado com número está num bloco, e os blocos somam o universo.
    assert sum(len(g) for g in blocos) == n == len(saida_dep)

    obj = {
        "_fonte": "Dados Abertos da Câmara: votacoes, votacoesVotos e votacoesOrientacoes, "
                  "votações nominais do Plenário, votos Sim e Não",
        "_gerado": datetime.now().date().isoformat(),
        "votacoes": len(nominais),
        "votacoesComOrientacao": sum(1 for i in governo if vot.get(i, {}).get("siglaOrgao") == "PLEN"),
        "regras": {"minVotos": MIN_VOTOS, "minComuns": MIN_COMUNS, "distOpostos": DIST_OPOSTOS,
                   "parMin": PAR_MIN},
        "corte": {"blocos": k, "concordanciaDentro": round(dentro, 3), "concordanciaEntre": round(entre, 3)},
        "blocos": saida_blocos,
        "medianaGovernoPorPartido": medianas,
        # [nome, partido no período, UF, score desse partido, partido hoje]
        "nomes": {str(d): [info[d]["nome"], partido[d], info[d]["uf"], score(d), info[d]["partido"]]
                  for d in ids},
        "trocaramDePartido": sum(1 for d in ids if partido[d] != info[d]["partido"]),
        "deputados": saida_dep,
        "improvaveis": improvaveis[:60],
        "improvaveisTotal": len(improvaveis),
        "temas": temas_saida,
        "temasLeis": len(leis),
        "temasSemCategoria": sem_tema,
    }
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA)

    print(f"\n{len(nominais)} votações nominais do Plenário · {obj['votacoesComOrientacao']} com "
          f"orientação Sim/Não do Governo · {n} deputados com {MIN_VOTOS}+ votos Sim/Não")
    print(f"Corte: {k} blocos — dentro, {dentro:.0%} ou mais de concordância; entre eles, {entre:.0%}")
    for nb, b in enumerate(saida_blocos):
        print(f"   bloco {nb + 1}: {b['n']} deputados, votam com o Governo (mediana) "
              f"{b['medianaGoverno']:.0%} · " + ", ".join(f"{p} {q}" for p, q in list(b["partidos"].items())[:9]))
    print(f"Pares improváveis (partidos a {DIST_OPOSTOS:g}+ pontos, {PAR_MIN:.0%}+ de concordância): "
          f"{len(improvaveis)}")
    for a, b, v, c in improvaveis[:5]:
        print(f"   {v:.1%} em {c}: {info[a]['nome']} ({partido[a]}) × {info[b]['nome']} ({partido[b]})")
    print(f"Por tema ({len(leis)} leis; {sem_tema} sem categoria) — % de Sim de cada bloco, maiorias opostas:")
    for t in temas_saida:
        pcts = " × ".join("—" if v is None else f"{v:.0%}" for v in t["sim"])
        print(f"   {t['categoria']:<42} {t['leis']:>3} leis · {pcts:<11} · opostas em {t['divergem']}")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")


if __name__ == "__main__":
    main()
