"""
Câmara Aberta — aba "Partidos": proposições por partido, por legislatura, de 1991 até hoje.

Uso:
    python scripts/partidos.py              # depois de coleta.py, votos.py e blocos.py
    python scripts/partidos.py --refazer    # ignora o cache derivado e relê os arquivos em lote
Grava site/dados/partidos/indice.json e um arquivo por período (49 a 57 e "todas").

FONTE: os arquivos anuais em lote da Câmara, `proposicoes-{ano}` e `proposicoesAutores-{ano}` — de TODOS
os autores, não só dos 513 deputados de hoje. O cache da API só tem a carreira de quem está em exercício:
um período antigo ficaria com os sobreviventes (viés). Conferido em 2/10/2026: `siglaPartidoAutor` é o
partido NA DATA da apresentação, com a sigla da época (PFL em 2004, DEM em 2007, PMDB até 2017, MDB depois);
contra o histórico de exercício da API, nas 27 mil autorias da legislatura atual, 99,6% batem — a
diferença é quase toda sigla truncada ("REPUBLIC"), tratada em SIGLAS.

Quatro "partidos" diferentes, cada um no seu lugar (a página diz qual é qual):
  - PARTIDO NA DATA DA APRESENTAÇÃO: de quem é a proposição — tudo o que vem daqui;
  - PARTIDO HOJE (deputados.json): a bancada (só na legislatura atual);
  - PARTIDO DO PERÍODO (blocos.json): o alinhamento ao Governo (só desde 2023);
  - PARTIDO NA DATA DO VOTO (votos.py): a coesão (só desde 2023).

Regras:
  - Universo = PEC e PL de autoria (proponente 1) de DEPUTADOS. Executivo, Senado, comissões e
    lideranças não são partido: ficam de fora.
  - Proposição DISTINTA por partido: conta uma vez em cada partido que teve autor nela, por mais
    autores do partido que tenha; a de dois partidos conta em cada um (a soma das linhas passa do total
    de distintas; o JSON traz os dois números). NUNCA se somam os totais por deputado.
  - Período = legislatura da data de apresentação. "todas" = de 1991 em diante.
  - "Virou lei" = situação oficial (coleta.virou_lei), a de hoje: proposição velha tem mais tempo para virar lei
    do que a recente, e a página diz.
  - Por deputado, a unidade é o PAR (deputado, partido): quem trocou de partido conta em cada um, só com o
    que apresentou enquanto estava nele. Mediana da conversão e "sem lei": só quem apresentou algo no
    partido — não há lista de quem esteve no partido sem apresentar nada, e inventá-la seria estimar.
  - Maior autor = o par com mais proposições ÷ proposições distintas do partido (nunca passa de 100%).
  - Tema = a classificação de temas.py (a MESMA das páginas dos deputados), sobre ementa e indexação; multirrótulo
    (a proposição conta em cada categoria que tem), então as parcelas por tema somam mais de 100%.
    7 blocos e 27 categorias; é interpretação do site, com precisão não medida.
  - Amostra pequena = menos de 10 deputados autores NO PARTIDO, NO PERÍODO, NO TEMA escolhidos: aparece,
    marcada, fora da ordenação. A página aplica a regra sobre o recorte que o leitor está vendo.
  - Só na legislatura atual: bancada de hoje, alinhamento ao Governo, coesão e posição no espectro. Votos e
    orientações só estão carregados desde 2023, e as posições do BLS (wave 2021) são dos partidos de hoje:
    aplicá-las a "PFL" de 1995 seria anacronismo.

Cache derivado por ano (cache/partidos/{ano}.json): só relê o CSV se o arquivo mudou (md5) ou a regra de
tema mudou (temas.VERSAO). Arquivos de anos recentes são baixados de novo a cada 7 dias (a situação de uma
proposição de 2023 muda quando ela vira lei); os fechados, só se faltarem.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from statistics import median

import coleta
from coleta import CACHE, DADOS, ler_cache, gravar_cache, virou_lei
import temas

SAIDA = DADOS / "partidos"
BULK = CACHE / "bulk"
DERIVADO = CACHE / "partidos"
URL = "https://dadosabertos.camara.leg.br/arquivos/{tipo}/csv/{tipo}-{ano}.csv"
VERSAO = "2026-10-02.4"      # mudar a cada alteração de regra deste script
LEG_MIN = 49                 # 1991–1995: a primeira legislatura inteira depois da Constituição de 1988
MIN_AUTORES = 10             # abaixo disso: amostra pequena
MIN_COESAO = 200             # votos comparáveis para publicar a coesão de um partido
RECENTE_ANOS = 4             # anos recentes são rebaixados a cada 7 dias
VALIDADE_DIAS = 7
SEM_PARTIDO = "S.PART."

# Variantes de grafia do MESMO partido na fonte: sigla truncada em 8 letras, caixa e abreviação. Nada de
# fusão de sucessores (PMDB→MDB, PFL→DEM→UNIÃO ficam como eram na época) e as siglas com asterisco
# ("PL*", "PP**") ficam como vêm: o significado da marca não está documentado.
SIGLAS = {"REPUBLIC": "REPUBLICANOS", "SOLIDARI": "SOLIDARIEDADE", "SOLIDARIED": "SOLIDARIEDADE",
          "SD": "SOLIDARIEDADE", "SDD": "SOLIDARIEDADE", "PCDOB": "PCdoB", "PATRI": "PATRIOTA",
          "S.PART": SEM_PARTIDO, "S. PART.": SEM_PARTIDO}
INVALIDAS = {"", "www"}
# Sigla que foi de partidos DIFERENTES. A Câmara os distingue no próprio registro por legislatura com asterisco
# (/partidos?idLegislatura=): "PL*" nas legislaturas 49 a 52 é o Partido Liberal de 1985 a 2006, extinto na fusão
# que criou o PR; o "PL" de hoje é o PR renomeado em 2019. "PP**" é o Partido Progressista de 1993 a 1995,
# anterior ao PP de 2003. Os arquivos em lote trazem as duas fases como "PL" e "PP"; sem separar, a visão "Todas"
# somaria dois partidos numa linha (PL: 6.478 proposições, de partidos diferentes). {sigla: (data de corte, rótulo
# do período anterior)}.
ERAS = {"PL": ("2007-02-01", "PL*"), "PP": ("2003-02-01", "PP**")}


PARTICULAS = {"de", "da", "do", "das", "dos", "e", "di", "du"}


def nome_legivel(nome):
    """Os arquivos antigos trazem o nome em CAIXA ALTA ("NILSON GIBSON"); os novos, em caixa normal.
    Só mexe no que está todo em maiúsculas."""
    if not nome or nome != nome.upper():
        return nome
    partes = nome.lower().split()
    return " ".join(p if (i and p in PARTICULAS) else p.capitalize() for i, p in enumerate(partes))


def sigla_de(texto, data=""):
    s = (texto or "").strip()
    if s in INVALIDAS:
        return SEM_PARTIDO
    s = SIGLAS.get(s, s)
    corte = ERAS.get(s)
    if corte and data and data < corte[0]:
        return corte[1]
    return s


# ---------------------------------------------------------------- arquivos em lote

def obter(tipo, ano, ano_atual):
    """Caminho do arquivo anual; baixa se faltar, ou se for de ano recente e tiver mais de 7 dias."""
    caminho = BULK / f"{tipo}-{ano}.csv"
    recente = ano >= ano_atual - RECENTE_ANOS
    if caminho.exists():
        velho = recente and (time.time() - caminho.stat().st_mtime) > VALIDADE_DIAS * 86400
        if not velho:
            return caminho
    print(f"   baixando {caminho.name}", flush=True)
    BULK.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    try:
        with coleta.sessao.get(URL.format(tipo=tipo, ano=ano), stream=True, timeout=300,
                               headers={"Accept": "*/*"}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for bloco in r.iter_content(1 << 20):
                    f.write(bloco)
        os.replace(tmp, caminho)   # atômico: download interrompido não vira cache
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        if caminho.exists():       # falhou ao atualizar: segue com o que há
            print(f"   AVISO: não consegui atualizar {caminho.name} ({type(e).__name__}); uso o que há")
            return caminho
        raise
    return caminho


def md5(caminho):
    h = hashlib.md5()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def processar_ano(ano, ano_atual, refazer):
    """Proposições PEC e PL do ano com autoria de deputado, em forma compacta:
    [id, data, lei, [categorias], [[deputado, partido], …]]. Cacheado por ano."""
    f_prop = obter("proposicoes", ano, ano_atual)
    f_aut = obter("proposicoesAutores", ano, ano_atual)
    fp = {"v": VERSAO, "temas": temas.VERSAO, "prop": md5(f_prop), "aut": md5(f_aut)}
    c = None if refazer else ler_cache(DERIVADO / f"{ano}.json")
    if c and c.get("fp") == fp:
        return c

    props = {}
    with open(f_prop, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["siglaTipo"] not in coleta.TIPOS:
                continue
            cod = r["ultimoStatus_idSituacao"]
            lei = virou_lei({"statusProposicao": {
                "descricaoSituacao": r["ultimoStatus_descricaoSituacao"],
                "codSituacao": int(cod) if cod.isdigit() else None}})
            props[r["id"]] = ((r["dataApresentacao"] or "")[:10], int(lei), r["ementa"], r["keywords"])

    autores, nomes = defaultdict(list), defaultdict(Counter)
    with open(f_aut, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["proponente"] != "1" or not r["tipoAutor"].startswith("Deputado"):
                continue
            pid = r["idProposicao"]
            if pid not in props:
                continue
            dep = r["idDeputadoAutor"] or "n:" + r["nomeAutor"]
            autores[pid].append([dep, sigla_de(r["siglaPartidoAutor"], props[pid][0])])
            nomes[dep][r["nomeAutor"]] += 1

    saida, sem_data = [], 0
    for pid, pares in autores.items():
        data, lei, ementa, keywords = props[pid]
        if not data:
            sem_data += 1
            continue
        cats = sorted({i for i, _, _ in temas.classificar(ementa, keywords)})
        vistos, unicos = set(), []
        for p in pares:                       # o mesmo deputado listado duas vezes na mesma proposição
            if tuple(p) not in vistos:
                vistos.add(tuple(p))
                unicos.append(p)
        saida.append([int(pid), data, lei, cats, unicos])
    c = {"fp": fp, "props": saida, "semData": sem_data,
         "nomes": {d: nome_legivel(n.most_common(1)[0][0]) for d, n in nomes.items()}}
    gravar_cache(DERIVADO / f"{ano}.json", c)
    return c


# ---------------------------------------------------------------- agregação

def chaves_de_tema(cats):
    """Recortes em que a proposição entra: todos os temas, o bloco e a categoria de cada rótulo dela."""
    ks = {""}
    for i in cats:
        ks.add(f"c{i}")
        ks.add(f"b{BLOCO_DA_CATEGORIA[i]}")
    return ks


BLOCO_DA_CATEGORIA = {i: b for b, (_, cs) in enumerate(temas.BLOCOS) for i, c in enumerate(temas.CATEGORIAS) if c in cs}


def agregar(props):
    """{recorte: {"tot": [distintas, de mais de um partido, sem partido, repetições], "p": {sigla: acumulador}}}
    em uma passada: cada proposição alimenta o recorte "" e os do tema dela. Repetições = soma de (partidos − 1)
    por proposição: é o que faz a soma das linhas passar do total de distintas, e é conferido."""
    por = defaultdict(lambda: {"tot": [0, 0, 0, 0], "p": defaultdict(lambda: {"p": 0, "l": 0, "c": 0, "d": Counter(), "dl": Counter()})})
    for _, _, lei, cats, pares in props:
        partidos = defaultdict(set)
        for dep, sigla in pares:
            if sigla != SEM_PARTIDO:
                partidos[sigla].add(dep)
        for k in chaves_de_tema(cats):
            r = por[k]
            if not partidos:
                r["tot"][2] += 1
                continue
            r["tot"][0] += 1
            multi = len(partidos) > 1
            r["tot"][1] += multi
            r["tot"][3] += len(partidos) - 1
            for sigla, deps in partidos.items():
                x = r["p"][sigla]
                x["p"] += 1
                x["l"] += lei
                x["c"] += multi
                for d in deps:
                    x["d"][d] += 1
                    x["dl"][d] += lei
    return por


def linhas(rec, nomes):
    out = []
    for sigla, x in rec["p"].items():
        convs = [x["dl"][d] / n for d, n in x["d"].items()]
        top, n_top = x["d"].most_common(1)[0]
        out.append({"s": sigla, "p": x["p"], "l": x["l"], "c": x["c"], "a": len(x["d"]),
                    "m": round(median(convs), 5), "z": sum(1 for d in x["d"] if x["dl"][d] == 0),
                    "t": [top, nomes.get(top, "—"), n_top]})
    out.sort(key=lambda l: (-l["a"], -l["p"], l["s"]))
    return out


def conferir(por, periodo):
    """Soma das linhas = proposições distintas + repetições; maior autor nunca passa de 100%.
    Divergiu: aborta sem gravar."""
    for k, rec in por.items():
        soma = sum(x["p"] for x in rec["p"].values())
        if soma != rec["tot"][0] + rec["tot"][3]:
            sys.exit(f"CHECAGEM FALHOU em {periodo}/{k or 'todos'}: soma por partido {soma} ≠ "
                     f"distintas {rec['tot'][0]} + repetições {rec['tot'][3]}. Nada foi gravado.")
        for sigla, x in rec["p"].items():
            if x["d"].most_common(1)[0][1] > x["p"]:
                sys.exit(f"CHECAGEM FALHOU em {periodo}/{k or 'todos'}: maior autor de {sigla} passa de 100%.")


# ---------------------------------------------------------------- legislatura atual

def meta_atual(deputados, ideologia, coesao, governo):
    """Só da legislatura atual: o que depende de dados que só existem desde 2023 ou de hoje."""
    bancada = Counter(d["partido"] for d in deputados)
    meta = {}
    for sigla in set(bancada) | set(coesao) | set(governo):
        if sigla == SEM_PARTIDO:
            continue
        c = coesao.get(sigla)
        score = (ideologia.get(sigla) or {}).get("score")
        meta[sigla] = {
            "bancada": bancada.get(sigla, 0),
            "governo": governo.get(sigla),
            "coesao": ({"comparaveis": c["comparaveis"], "diferentes": c["diferentes"], "niveis": dict(c["niveis"])}
                       if c and c["comparaveis"] >= MIN_COESAO else None),
            "score": score,
        }
    return meta


def rotulo(leg):
    return f"{leg['id']}ª legislatura · {leg['inicio'][:4]} a {leg['fim'][:4]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--refazer", action="store_true", help="ignora o cache derivado")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.monotonic()
    hoje = date.today()
    legs = [l for l in coleta.legislaturas() if l["id"] >= LEG_MIN]
    atual = next((l for l in legs if l["inicio"] <= hoje.isoformat() <= l["fim"]), legs[-1])

    # ---------------------------------------------------------- 1. proposições, por ano
    todas, nomes, sem_data = [], {}, 0
    for ano in range(int(legs[0]["inicio"][:4]), hoje.year + 1):
        c = processar_ano(ano, hoje.year, args.refazer)
        todas += c["props"]
        nomes.update(c["nomes"])
        sem_data += c["semData"]
    print(f"{len(todas)} PEC e PL com autoria de deputado, de {legs[0]['inicio'][:4]} a {hoje.year} "
          f"({time.monotonic() - t0:.0f}s) · sem data: {sem_data}", flush=True)

    por_periodo = defaultdict(list)
    for p in todas:
        leg = coleta.legislatura_de(p[1], legs)
        if leg is not None:
            por_periodo[leg].append(p)
    por_periodo["todas"] = [p for ps in (por_periodo[l["id"]] for l in legs) for p in ps]

    # ---------------------------------------------------------- 2. só da legislatura atual
    deputados = json.loads((DADOS / "deputados.json").read_text(encoding="utf-8"))
    ideologia = coleta.carregar_ideologia()
    blocos_json = DADOS / "blocos.json"
    governo = (json.loads(blocos_json.read_text(encoding="utf-8")).get("medianaGovernoPorPartido") or {}) \
        if blocos_json.exists() else {}
    cache_coesao = ler_cache(CACHE / "orientacao_partidos.json") or {}
    if not cache_coesao:
        print("AVISO: cache/orientacao_partidos.json não existe — rode votos.py; a coesão fica sem número.")
    meta = meta_atual(deputados, ideologia, cache_coesao.get("partidos") or {}, governo)

    # ---------------------------------------------------------- 3. agregação e saída
    SAIDA.mkdir(parents=True, exist_ok=True)
    indice = {"_gerado": hoje.isoformat(), "versao": VERSAO, "legislaturaAtual": atual["id"],
              "minAutores": MIN_AUTORES, "minCoesao": MIN_COESAO,
              "blocos": [{"i": b, "nome": nome, "categorias": [
                  {"i": temas.CATEGORIAS.index(c), "nome": c, "curto": temas.CURTO[c]} for c in cs]}
                  for b, (nome, cs) in enumerate(temas.BLOCOS)],
              "periodos": []}
    resumo = []
    for pid in [l["id"] for l in reversed(legs)] + ["todas"]:
        props = por_periodo.get(pid, [])
        if not props:
            continue
        por = agregar(props)
        conferir(por, pid)
        leg = next((l for l in legs if l["id"] == pid), None)
        e_atual = pid == atual["id"]
        arq = {"id": pid, "atual": e_atual,
               "rotulo": rotulo(leg) if leg else f"Todas · {legs[0]['inicio'][:4]} a {hoje.year}",
               "inicio": leg["inicio"] if leg else legs[0]["inicio"], "fim": leg["fim"] if leg else hoje.isoformat(),
               "tot": {k: r["tot"][:3] for k, r in por.items()},
               "sel": {k: linhas(r, nomes) for k, r in por.items()}}
        if e_atual:
            arq["meta"] = meta
            arq["deputadosHoje"] = len(deputados)
        tmp = SAIDA / f"{pid}.tmp"
        tmp.write_text(json.dumps(arq, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, SAIDA / f"{pid}.json")
        sem_tema = sum(1 for p in props if not p[3])
        indice["periodos"].append({
            "id": pid, "rotulo": arq["rotulo"], "inicio": arq["inicio"], "fim": arq["fim"], "atual": e_atual,
            "proposicoes": por[""]["tot"][0], "deMaisDeUmPartido": por[""]["tot"][1],
            "semPartido": por[""]["tot"][2], "semTema": sem_tema,
            "partidos": len(arq["sel"][""]), "recursosDaLegislaturaAtual": e_atual})
        resumo.append((pid, por[""]["tot"], len(arq["sel"][""]), (SAIDA / f"{pid}.json").stat().st_size))
    tmp = SAIDA / "indice.tmp"
    tmp.write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SAIDA / "indice.json")

    # ---------------------------------------------------------- 4. relatório
    print(f"\n{'período':>8}{'distintas':>11}{'2+ partidos':>13}{'sem partido':>13}{'partidos':>10}{'kB':>7}")
    for pid, tot, np_, tam in resumo:
        print(f"{str(pid):>8}{tot[0]:>11}{tot[1]:>13}{tot[2]:>13}{np_:>10}{tam // 1024:>7}")
    print(f"\nLegislatura atual: {atual['id']} · {len(meta)} partidos com dados de bancada/votos")
    print(f"{len(list(SAIDA.glob('*.json')))} arquivos em {SAIDA} · {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
