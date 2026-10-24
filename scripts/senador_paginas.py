"""
Câmara Aberta — a página de cada senador (senador.html): as PEC e os PL de que é o primeiro autor, por tema.

Uso:
    python scripts/senador_paginas.py        # depois de senado.py (e de temas.py, que dá a taxonomia)

Lê só o cache/ do senado.py (autorias e detalhes de processo) e senadores.json / senadores_antigos.json. NÃO chama a API: um
detalhe que faltasse no cache aborta em vez de baixar. Grava senadores/{código}.json, um arquivo por senador (os 81 de hoje e os
que já saíram), para que a página baixe só o senador aberto, e senadores/resumo.json (a frase de cada um).

A LISTA É A MESMA DOS NÚMEROS: `senado.proposicoes_de` é a regra que o senado.py usa para contar (primeiro autor, PEC e PL, PLS conta
como PL, um projeto com várias fases conta uma vez). O script confere pecs, leis, apresentadas e viraramLei de cada senador contra
senadores.json e aborta sem gravar se divergirem — a página tem de somar o mesmo que o gráfico.

A CATEGORIA É INTERPRETAÇÃO DESTE SITE: as mesmas regras de palavras-chave que temas.py aplica às proposições dos deputados
(`temas.classificar`), sobre a EMENTA oficial do Senado, com o termo que disparou cada rótulo. Ao contrário da Câmara, o Senado não
entrega a indexação junto da lista de autoria, então não há segunda camada: proposição cuja ementa não casa com nenhuma regra fica
"sem categoria", nunca some. Precisão não medida, como na Câmara. Ver CLAUDE.md, "Página do senador".
"""

import os
import sys
import time
from collections import Counter
from datetime import date, timedelta

import coleta
import senado
import temas

SAIDA = coleta.DADOS / "senadores"
VOTOS_DIR = SAIDA / "votos"
VERSAO = "2026-10-15.2"           # mudar a cada alteração de regra deste script: vai para a página
LONGA = timedelta(days=36500)     # o cache vale sempre aqui: quem o renova é o senado.py, no começo da rodada
# Votações cuja matéria é uma proposição legislativa (tem ementa de lei, então dá para classificar por tema). Requerimento, mensagem, ofício etc.
# não têm: contam em "outras votações" (`o`), sem tema, e dizem isso.
SIGLAS_LEGISLATIVAS = {"PEC", "PL", "PLP", "PLS", "PLC", "PDL", "PRS", "MPV", "PLV", "PLN", "SCD"}
BLOCO_DE = {temas.CATEGORIAS.index(c): bi for bi, (_, cs) in enumerate(temas.BLOCOS) for c in cs}
VOTO = {"Sim": "sim", "Não": "nao", "Abstenção": "abstencao"}


def sem_api(*_a, **_k):
    raise RuntimeError("cache incompleto: este script não chama a API")


def registro(x, cats):
    """Uma proposição, só com o que a página mostra. `l` é a situação oficial de norma jurídica (a mesma regra da contagem)."""
    lei = (x.get("situacaoAtual") or "") in senado.LEI
    return {
        "m": x.get("codigoMateria"),                       # a ficha no Senado: .../materia/{m}
        "t": senado.tipo_de(x["identificacao"]),           # PEC ou PL (o PLS entra como PL)
        "i": x["identificacao"],                           # como o Senado numera: "PLS 123/2015", "PL 3560/2023"
        "d": (x.get("dataApresentacao") or "")[:10],
        "e": " ".join((x.get("ementa") or "").split()),
        "s": x.get("situacaoAtual") or "",
        "u": (x.get("dataSituacaoAtual") or "")[:10],      # desde quando a situação atual está registrada
        "tr": 1 if x.get("tramitando") == "Sim" else 0,
        "l": 1 if lei else 0,
        **({"nl": x["normaGerada"]} if lei and x.get("normaGerada") else {}),     # "Lei nº 14.785 de 27/12/2023"
        "c": cats,
    }


def votos_por_tema(cod):
    """Como o senador votou, POR TEMA: cada voto Sim, Não ou Abstenção conta no tema (ou nos temas) da matéria votada, pela mesma classificação da
    página por tema, sobre a ementa da matéria. É o mesmo conjunto de votações de `votos` em senadores.json (nominais abertas do Plenário desde
    1º/2/2023, com o voto do senador registrado) e a MESMA regra de senado.py; a unidade é a VOTAÇÃO, então uma matéria com vários turnos ou
    destaques conta em cada votação. Matéria que não é proposição legislativa (requerimento, mensagem…) vai em `o`; proposição cuja ementa não
    casa com regra nenhuma, em `s`. `b` = por bloco (a matéria conta uma vez no bloco), `c` = por categoria, na ordem de taxonomia.json.
    Devolve (votosTema, total de votações, por tipo de voto, lista, matérias): os dois primeiros para a conferência contra senadores.json; `lista` e
    `materias` para a página "quais votações" (uma linha por votação: sequencial, código da matéria, data, resultado, voto S/N/A, descrição)."""
    vt = coleta.ler_cache(senado.CACHE_S / "votos" / f"{cod}.json") or []
    novo = lambda: {"b": [0] * len(temas.BLOCOS), "c": [0] * len(temas.CATEGORIAS), "s": 0, "o": 0}
    por = {k: novo() for k in VOTO.values()}
    total, tipos = 0, Counter()
    lista, materias = [], {}
    for v in vt:
        if v.get("votacaoSecreta") in ("S", True) or (v.get("dataSessao") or "") < senado.INICIO_LEGISLATURA:
            continue
        for voto in v.get("votos") or []:
            if str(voto.get("codigoParlamentar")) != str(cod):
                continue
            total += 1
            k = VOTO.get(voto.get("siglaVotoParlamentar"))
            tipos[k or "outros"] += 1
            if k is None:
                continue                                 # licença, missão, presidindo… não é voto: o senado.py guarda à parte
            a = por[k]
            legislativa = (v.get("sigla") or "") in SIGLAS_LEGISLATIVAS
            cats = sorted({i for i, _, _ in temas.classificar(" ".join((v.get("ementa") or "").split()), "")}) if legislativa else None
            mat = str(v.get("codigoMateria"))
            materias.setdefault(mat, [v.get("identificacao"), " ".join((v.get("ementa") or "").split()), cats])
            lista.append([v.get("sequencialVotacao") or v.get("codigoSessaoVotacao"), mat, (v.get("dataSessao") or "")[:10],
                          v.get("resultadoVotacao"), {"sim": "S", "nao": "N", "abstencao": "A"}[k],
                          " ".join((v.get("descricaoVotacao") or "").split())])
            if not legislativa:
                a["o"] += 1
                continue
            for b in sorted({BLOCO_DE[i] for i in cats}):
                a["b"][b] += 1
            for i in cats:
                a["c"][i] += 1
            if not cats:
                a["s"] += 1
    lista.sort(key=lambda x: (x[2], x[0] or 0), reverse=True)
    return por, total, tipos, lista, materias


def contagem_da_lista(lista, materias):
    """Refaz as contagens por tema a partir da LISTA da página: tem de dar o mesmo que `votosTema` (conferência no main)."""
    por = {k: {"b": [0] * len(temas.BLOCOS), "c": [0] * len(temas.CATEGORIAS), "s": 0, "o": 0} for k in VOTO.values()}
    nome = {"S": "sim", "N": "nao", "A": "abstencao"}
    for _, mat, _, _, letra, _ in lista:
        a, cats = por[nome[letra]], materias[mat][2]
        if cats is None:
            a["o"] += 1
        elif not cats:
            a["s"] += 1
        else:
            for b in sorted({BLOCO_DE[i] for i in cats}):
                a["b"][b] += 1
            for i in cats:
                a["c"][i] += 1
    return por


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.monotonic()
    senado.pedir = sem_api                                  # cache ou nada

    atuais = coleta.ler_cache(senado.SAIDA) or []
    antigos = coleta.ler_cache(senado.ANTIGOS) or []
    if not atuais:
        sys.exit(f"Sem {senado.SAIDA.name}. Rode senado.py antes.")
    todos = [(s, False) for s in atuais] + [(s, True) for s in antigos]

    saidas, divergencias, sem_cache, pulados = {}, [], [], set()
    votos_total = Counter()
    listas = {}
    distintas, leis_sem_norma = {}, []
    for s, saiu in todos:
        cod = s["id"]
        if not (senado.CACHE_S / "autorias" / f"{cod}.json").exists():
            sem_cache.append(s["nome"])
            pulados.add(cod)
            continue
        try:
            _, meus = senado.proposicoes_de(str(cod), False, LONGA, LONGA)
        except RuntimeError as e:
            divergencias.append(f"{s['nome']}: {e}")
            continue
        props = []
        for x in meus:
            cats = temas.classificar(" ".join((x.get("ementa") or "").split()), "")
            r = registro(x, cats)
            if r["l"] and "nl" not in r:
                leis_sem_norma.append(f"{s['nome']}: {r['i']}")
            distintas[(cod, r["i"])] = cats
            props.append(r)
        props.sort(key=lambda p: (p["d"], p["m"] or 0), reverse=True)
        n_tipo = Counter(p["t"] for p in props)
        # sanidade: a página tem de somar o mesmo que o gráfico
        ref = (s["pecs"], s["leis"], s["apresentadas"], s["viraramLei"])
        obs = (n_tipo["PEC"], n_tipo["PL"], len(props), sum(p["l"] for p in props))
        if ref != obs:
            divergencias.append(f"{s['nome']}: PEC, PL, apresentadas, viraram lei = {obs} × {ref} em {senado.SAIDA.name}")
        saidas[cod] = {"id": cod, "pecs": n_tipo["PEC"], "leis": n_tipo["PL"], "viraramLei": sum(p["l"] for p in props),
                       "saiu": saiu, "props": props}
        # os votos por tema, só de quem tem votos em senadores.json; a conferência é contra o mesmo registro (total e por tipo de voto)
        vref = s.get("votos")
        if vref:
            if not (senado.CACHE_S / "votos" / f"{cod}.json").exists():
                divergencias.append(f"{s['nome']}: sem cache de votos (rode senado.py)")
                continue
            por, total, tipos, lista, materias = votos_por_tema(cod)
            if contagem_da_lista(lista, materias) != por:
                divergencias.append(f"{s['nome']}: a lista de votações não reproduz os votos por tema")
            listas[cod] = {"id": cod, "nome": s["nome"], "partido": s.get("partido"), "uf": s.get("uf"), "materias": materias, "votos": lista}
            esperado = (vref["votacoes"], vref["sim"], vref["nao"], vref["abstencao"])
            obtido = (total, tipos["sim"], tipos["nao"], tipos["abstencao"])
            if esperado != obtido:
                divergencias.append(f"{s['nome']}: votos (votações, Sim, Não, abstenção) = {obtido} × {esperado} em {senado.SAIDA.name}")
            saidas[cod]["votosTema"] = por
            votos_total["senadores"] += 1
            votos_total["votos"] += sum(tipos[k] for k in VOTO.values())
            votos_total["sem_tema"] += sum(a["s"] for a in por.values())
            votos_total["outras"] += sum(a["o"] for a in por.values())

    if divergencias:
        print("CHECAGEM FALHOU — nada foi gravado.")
        for d in divergencias[:40]:
            print("-", d)
        print("Provável causa: senadores.json de outra rodada do cache. Rode senado.py e depois este script.")
        sys.exit(2)
    # Sem cache de autoria (o cache do Actions foi zerado e o senado.py ainda não refez todos os que já saíram: ele para no limite de --minutos e
    # continua na rodada seguinte): a página dele FICA como estava, da última rodada que conseguiu montá-la, em vez de derrubar as dos outros.
    # O senadores_antigos.json também só é regravado com todos prontos, então os números da página continuam batendo com os do gráfico.
    if sem_cache:
        msg = (f"{len(sem_cache)} senador(es) sem cache de autoria: a página de cada um ficou como estava "
               f"(o senado.py refaz o cache nas próximas rodadas). Ex.: {', '.join(sem_cache[:6])}")
        print(f"::warning title=senador_paginas::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"AVISO: {msg}", flush=True)

    SAIDA.mkdir(exist_ok=True)
    validos = {f"{i}.json" for i in saidas} | {f"{i}.json" for i in pulados} | {"resumo.json"}
    for velho in SAIDA.glob("*.json"):
        if velho.name not in validos:
            velho.unlink()                                  # senador que saiu da lista
    mudaram, frases = 0, {}
    for cod, obj in saidas.items():
        # Quem está em exercício: a frase da legislatura atual, como a dos deputados. Quem já saiu não tem "esta legislatura".
        obj["frase"] = (temas.frase(obj["props"], desde="", quando="Na carreira") if obj["saiu"]
                        else temas.frase(obj["props"]))
        obj["versao"] = VERSAO
        frases[cod] = obj["frase"]
        mudaram += temas.gravar_se_mudou(SAIDA / f"{cod}.json", obj)
    for cod in pulados:                                      # a frase de quem ficou como estava: a do arquivo dele, se existe
        antigo = coleta.ler_cache(SAIDA / f"{cod}.json")
        if antigo and antigo.get("frase"):
            frases[cod] = antigo["frase"]
    temas.gravar_se_mudou(SAIDA / "resumo.json", {"inicioLegislatura": temas.INICIO_LEGISLATURA, "versao": VERSAO,
                                                  "temasVersao": temas.VERSAO, "geradoEm": date.today().isoformat(),
                                                  "frases": frases})
    # senadores/votos/{código}.json: cada votação em que votou Sim, Não ou se absteve, com a matéria, a descrição e o voto (página "quais votações").
    # Só os arquivos de quem tem votos; o que sobrou de quem saiu da lista é apagado.
    VOTOS_DIR.mkdir(exist_ok=True)
    for velho in VOTOS_DIR.glob("*.json"):
        if velho.stem not in {str(c) for c in listas} | {str(c) for c in pulados}:
            velho.unlink()
    for cod, obj in listas.items():
        temas.gravar_se_mudou(VOTOS_DIR / f"{cod}.json", {**obj, "versao": VERSAO})

    total = len(distintas)
    sem_tema = sum(1 for c in distintas.values() if not c)
    rot = Counter(i for c in distintas.values() for i, _, _ in c)
    tam = sum(p.stat().st_size for p in SAIDA.glob("*.json"))
    maior = max(SAIDA.glob("*.json"), key=lambda p: p.stat().st_size)
    print(f"Checagem de sanidade OK: PEC, PL, apresentadas e viraram lei batem com {senado.SAIDA.name} e {senado.ANTIGOS.name}.")
    print(f"{len(saidas)} senadores ({sum(1 for o in saidas.values() if not o['saiu'])} em exercício, "
          f"{sum(1 for o in saidas.values() if o['saiu'])} que já saíram) · {total} proposições · "
          f"{sem_tema} sem categoria ({sem_tema / max(total, 1):.1%}) · {sum(rot.values()) / max(total - sem_tema, 1):.2f} categorias por proposição com tema")
    if votos_total["votos"]:
        print(f"Votos por tema ({votos_total['senadores']} senadores): {votos_total['votos']} votos Sim, Não ou abstenção, confere com {senado.SAIDA.name} · "
              f"em matéria sem proposição legislativa: {votos_total['outras']} ({votos_total['outras'] / votos_total['votos']:.1%}) · "
              f"proposição sem categoria: {votos_total['sem_tema']} ({votos_total['sem_tema'] / votos_total['votos']:.1%})")
    print(f"Viraram lei: {sum(o['viraramLei'] for o in saidas.values())} (somas por senador); "
          f"sem o número da norma no registro: {len(leis_sem_norma)}" + (f" ({'; '.join(leis_sem_norma[:4])}…)" if leis_sem_norma else ""))
    print(f"{mudaram} arquivos regravados · {len(list(SAIDA.glob('*.json')))} em {SAIDA.name}/ ({tam // 1024} kB; o maior, "
          f"{maior.name}, {maior.stat().st_size // 1024} kB) · {time.monotonic() - t0:.0f} s")


if __name__ == "__main__":
    main()
