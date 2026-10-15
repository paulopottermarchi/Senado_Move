"""
Câmara Aberta — quem vota junto com quem no Senado: os blocos que existem de fato no Plenário.

Uso:
    python scripts/blocos_senado.py   # depois de senado.py; lê cache/senado/, grava blocos_senado.json

O MESMO método de blocos.py (blocos.calcular): concordância entre cada par, agrupamento hierárquico por concordância
média sem olhar o partido, corte no maior salto, frequência com que cada senador votou como o líder do Governo
orientou, os 5 mais parecidos e os pares improváveis. Só o que vem de cada Casa muda:

  · Votos: as votações nominais ABERTAS do Plenário desde 1º/2/2023, lidas dos arquivos de voto de cada senador que
    senado.py já guarda em cache/senado/senadores/votos/ (os 81 de hoje e os que já saíram e votaram desde 2023). A
    mesma regra de senado.py: votação secreta fica fora, e só voto Sim e Não conta (abstenção, obstrução, "presente
    sem registrar voto", licença e missão não entram). Chave da votação: codigoSessaoVotacao.
  · Orientação do Governo: a do líder do Governo no Senado (/plenario/votacao/orientacaoBancada, via senado.orientacoes),
    ligada à votação pelo sequencialVotacao, como em senado.py.
  · Partido: o que o senador tinha NA DATA do voto, como o registro traz; o do período é aquele em que deu mais votos.
  · Mínimos menores que os da Câmara, porque há 183 votações nominais abertas (a Câmara tem 1.123): 50 votos Sim/Não
    por senador, 50 votações em comum por par, 30 votações com orientação do Governo para ter a porcentagem (o mesmo
    mínimo de senado.py). Medido: com 100 e 100 o resultado é o mesmo (2 blocos, os mesmos partidos), só com menos senadores.
  · NÃO há "o que cada bloco aprovou, por tema": a Câmara usa só a votação final de texto-base, em que Sim é aprovar a
    lei; o registro do Senado mistura destaque, emenda e requerimento, em que o Sim quer dizer outra coisa, e não há como
    separar a votação do texto-base com o que o Senado entrega. O arquivo grava `temas` vazio e a página esconde a seção.

O que isto NÃO é: posição ideológica (ver blocos.py). O campo `deputados` do JSON guarda os SENADORES, com o mesmo nome
nas duas Casas, para a página usar o mesmo código.
"""

import glob
import json
import os
import sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

import blocos
import coleta
import senado

SAIDA = coleta.DADOS / "blocos_senado.json"
MIN_VOTOS = 50       # votos Sim/Não nas votações nominais abertas para o senador entrar
MIN_COMUNS = 50      # votações em comum para um par contar
MIN_GOVERNO = senado.MIN_ORIENTACAO   # votações com orientação do Governo para ter a porcentagem (30)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cadastro = {}
    for caminho, atual in ((coleta.DADOS / "senadores.json", True), (coleta.DADOS / "senadores_antigos.json", False)):
        if caminho.exists():
            for s in json.loads(caminho.read_text(encoding="utf-8")):
                # "partido hoje" só existe para quem está em exercício: quem já saiu não tem partido "de hoje"
                cadastro.setdefault(s["id"], {"nome": s["nome"], "uf": s["uf"], "partido": s["partido"] if atual else None,
                                              "votos": s.get("votos") if atual else None})
    if sum(1 for c in cadastro.values() if c["partido"]) < 75:
        raise SystemExit("senadores.json com menos de 75 senadores em exercício — rodar senado.py antes.")

    ori_seq, ori_req, gov_seq = senado.orientacoes(False)
    print("Lendo os votos de cada senador (cache/senado/senadores/votos/)…", flush=True)
    bruto = []          # (data, votação, senador, voto, partido na data, sequencial)
    nomes_fora = {}
    for f in sorted(glob.glob(str(senado.CACHE_S / "votos" / "*.json"))):
        cod = int(os.path.basename(f)[:-5])
        for v in coleta.ler_cache(Path(f)) or []:
            if v.get("votacaoSecreta") in ("S", True) or (v.get("dataSessao") or "") < senado.INICIO_LEGISLATURA:
                continue
            for x in v.get("votos") or []:
                if str(x.get("codigoParlamentar")) != str(cod):
                    continue
                nomes_fora.setdefault(cod, {"nome": x.get("nomeParlamentar"), "uf": x.get("siglaUFParlamentar"), "partido": None})
                bruto.append((v.get("dataSessao"), v["codigoSessaoVotacao"], cod, x.get("siglaVotoParlamentar"),
                              x.get("siglaPartidoParlamentar"), senado.sequencial(v, ori_req)))
    info = {cod: cadastro.get(cod) or nomes_fora[cod] for cod in nomes_fora}
    sem_cadastro = [c for c in info if c not in cadastro]
    ordem = sorted({(d, k) for d, k, *_ in bruto})
    bit = {k: i for i, (_, k) in enumerate(ordem)}

    sim, nao = defaultdict(int), defaultdict(int)
    gov = defaultdict(lambda: [0, 0])     # [votações com orientação do Governo, votou como ele]
    siglas = defaultdict(Counter)         # partido NA DATA DO VOTO, voto a voto
    contagem = defaultdict(lambda: [0, 0])    # Sim e Não por senador, para conferir com senadores.json
    nominais, com_ori = set(), set()
    for data, k, cod, voto, partido, seq in bruto:
        if voto not in ("Sim", "Não"):
            continue
        j = bit[k]
        nominais.add(j)
        siglas[cod][partido] += 1
        if voto == "Sim":
            sim[cod] |= 1 << j
            contagem[cod][0] += 1
        else:
            nao[cod] |= 1 << j
            contagem[cod][1] += 1
        g = gov_seq.get(seq) if seq is not None else None
        if g in ("SIM", "NÃO"):
            gov[cod][0] += 1
            gov[cod][1] += (voto == "Sim") == (g == "SIM")
            com_ori.add(j)

    # Conferência ANTES de gravar: os Sim e Não de cada senador em exercício são os que senadores.json já publica (mesma fonte,
    # mesma regra); se divergirem, a página mostraria dois números diferentes para a mesma coisa.
    diverge = [c for c, i in cadastro.items() if i["votos"] and (contagem[c][0], contagem[c][1]) != (i["votos"]["sim"], i["votos"]["nao"])]
    if diverge:
        raise SystemExit(f"Sim/Não diferentes dos de senadores.json em {len(diverge)} senadores (ex.: {diverge[:3]}) — nada foi gravado.")

    ids = sorted(c for c in siglas if (sim[c] | nao[c]).bit_count() >= MIN_VOTOS)
    if len(ids) < 60:
        raise SystemExit(f"só {len(ids)} senadores com {MIN_VOTOS}+ votos Sim/Não — conferir os votos em cache.")
    regras = {"minVotos": MIN_VOTOS, "minComuns": MIN_COMUNS, "distOpostos": blocos.DIST_OPOSTOS, "parMin": blocos.PAR_MIN,
              "minGoverno": MIN_GOVERNO}
    parte, grupos, _, partido = blocos.calcular(ids, sim, nao, gov, siglas, info, regras)

    obj = {
        "_fonte": "Dados Abertos do Senado: votação nominal aberta do Plenário de cada senador (/votacao) e orientação de "
                  "bancada (/plenario/votacao/orientacaoBancada), votos Sim e Não; o campo `deputados` guarda os senadores",
        "_gerado": datetime.now().date().isoformat(),
        "casa": "senado",
        "votacoes": len(nominais),
        "votacoesComOrientacao": len(com_ori),
        **parte,
        "temas": [], "temasLeis": 0, "temasSemCategoria": 0,
    }
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA)

    print(f"\n{len(nominais)} votações nominais abertas do Plenário desde {senado.INICIO_LEGISLATURA} · {len(com_ori)} com orientação "
          f"Sim/Não do Governo · {len(ids)} senadores com {MIN_VOTOS}+ votos Sim/Não (de {len(siglas)} que votaram"
          + (f"; {len(sem_cadastro)} sem cadastro, nome tirado do voto" if sem_cadastro else "") + ")")
    c = parte["corte"]
    print(f"Corte: {c['blocos']} blocos — dentro, {c['concordanciaDentro']:.0%} ou mais de concordância; entre eles, {c['concordanciaEntre']:.0%}")
    for nb, b in enumerate(parte["blocos"]):
        print(f"   bloco {nb + 1}: {b['n']} senadores, votam com o Governo (mediana) "
              f"{b['medianaGoverno']:.0%} · " + ", ".join(f"{p} {q}" for p, q in list(b["partidos"].items())[:9]))
    print(f"Pares improváveis: {parte['improvaveisTotal']} · trocaram de partido no período (entre os de hoje): {parte['trocaramDePartido']}")
    for a, b, v, n in parte["improvaveis"][:5]:
        print(f"   {v:.1%} em {n}: {info[a]['nome']} ({partido[a]}) × {info[b]['nome']} ({partido[b]})")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")


if __name__ == "__main__":
    main()
