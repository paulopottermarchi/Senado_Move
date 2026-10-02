"""
Câmara Aberta — Etapa B (votos individuais, via arquivos anuais em bulk).

Uso:
    python scripts/votos.py              # usa o que já está em cache/bulk/
    python scripts/votos.py --atualizar  # baixa de novo os arquivos do ANO CORRENTE (votações
                                 # e autores); os anos fechados não mudam

Rodar DEPOIS de coleta.py: lê deputados.json, acrescenta `votos` a cada deputado
e grava proposicoes.json. Se coleta.py regravar deputados.json, rode este de novo.

Nenhuma chamada à API. Fontes (dadosabertos.camara.leg.br/arquivos, cache em disco):
  votacoes-{ano}             votação, órgão, descrição, `aprovacao`, placar oficial
  votacoesVotos-{ano}        voto de cada deputado, com o partido NA DATA DO VOTO
  votacoesProposicoes-{ano}  votação → proposição (número, ementa)
  proposicoesAutores-{ano}   autor da proposição — ano da PROPOSIÇÃO, não da votação

Só entram votações NOMINAIS (com voto individual registrado) e de TEXTO-BASE: a
mesma allowlist da Etapa A (coleta.e_texto_base), e por proposição só a votação
final (coleta.votacao_texto_base) — ex.: da PEC, o 2º turno.
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

import coleta  # a allowlist, o status e as faixas são os da Etapa A — nunca duplicar
from temas import CATEGORIAS, CURTO, classificar  # a mesma classificação da página por tema
import resumos  # passo 8: resumo do inteiro teor, gerado por resumos.py
import tramitacoes  # etapas da tramitação; tramitacoes.py baixa, aqui só se lê o cache


def _etapas(id_prop, titulo):
    """Etapas do histórico no cache; números do título ("PL 5809/2025 (Nº Anterior: PL 347/2003)")."""
    ev = coleta.ler_cache(tramitacoes.CACHE_T / f"{int(id_prop)}.json")
    if not ev:
        return None
    r = tramitacoes.etapas(ev, tramitacoes.numeros_do_titulo(titulo))   # atual e anterior, se renumerada
    return {k: v for k, v in r.items() if v}

ANOS = range(2023, 2027)  # legislatura atual
BULK = coleta.CACHE / "bulk"
URL = "https://dadosabertos.camara.leg.br/arquivos/{tipo}/csv/{tipo}-{ano}.csv"
PROPOSICOES = coleta.DADOS / "proposicoes.json"
FAIXAS = coleta.FAIXAS

# Valores de `voto` no CSV. Obstrução e "Artigo 17" (quem preside a sessão não
# vota) são presença registrada, mas não são voto: ficam fora de sim/não/abstenção
# e dos denominadores.
SIM, NAO, ABST, OBST, ART17 = "Sim", "Não", "Abstenção", "Obstrução", "Artigo 17"
VOTANTE = (SIM, NAO, ABST)


# ---------------------------------------------------------------- arquivos

def arquivo(tipo, ano, atualizar=False):
    caminho = BULK / f"{tipo}-{ano}.csv"
    # --atualizar só rebaixa o ano corrente: é o único arquivo que a Câmara ainda
    # altera. Rebaixar a legislatura inteira todo dia custaria ~200 MB por rodada.
    if caminho.exists() and not (atualizar and ano == datetime.now().year):
        return caminho
    print(f"   baixando {caminho.name}", flush=True)
    BULK.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    with coleta.sessao.get(URL.format(tipo=tipo, ano=ano), stream=True, timeout=300,
                           headers={"Accept": "*/*"}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
    os.replace(tmp, caminho)  # atômico: download interrompido não vira cache
    return caminho


def linhas(tipo, anos, atualizar=False):
    for ano in anos:
        with open(arquivo(tipo, ano, atualizar), encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")


def gravar_json(caminho, obj, um_por_linha=False):
    """um_por_linha: uma entrada da lista por linha, compacta. Com a lista de quem
    divergiu em cada lei, o formato indentado passava de 300 kB para quase 1 MB; assim
    o arquivo fica pequeno e o diff diário no git continua legível, votação por votação."""
    tmp = caminho.with_suffix(".tmp")
    texto = ("[\n" + ",\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":"))
                                for x in obj) + "\n]\n") if um_por_linha \
        else json.dumps(obj, ensure_ascii=False, indent=2)
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, caminho)


def instante(texto):
    return datetime.fromisoformat(texto) if texto else None


# ---------------------------------------------------------------- regras

def autor_principal(linhas_autores):
    """Primeiro proponente (ordemAssinatura mais baixa com proponente 1).
    Assinatura de apoiamento (proponente 0) não é autoria."""
    prop = [a for a in linhas_autores if a["proponente"] == "1"]
    return min(prop, key=lambda a: int(a["ordemAssinatura"] or 999999), default=None)


def partido_na_data(historico, quando):
    """Partido do deputado no registro de voto mais próximo de `quando`.
    `historico`: [(instante, sigla)] de todos os votos dele na legislatura."""
    if not historico or quando is None:
        return None
    return min(historico, key=lambda h: abs((h[0] - quando).total_seconds()))[1]


def media(valores):
    return round(sum(valores) / len(valores), 2) if valores else None


def e_nominal(v):
    """Votação com voto individual registrado, pelo placar oficial do próprio arquivo de
    votações. Serve para anos cujo arquivo de votos não é baixado; main() confere a
    regra a cada rodada contra os votos individuais da legislatura (medido: 1.125 de
    1.125 no Plenário, nenhuma exceção nas 42 mil votações de 2023–2026)."""
    return sum(int(v[k] or 0) for k in ("votosSim", "votosNao", "votosOutros")) > 0


_PLACAR_NO_TEXTO = re.compile(r"[\s.;,]*\bSim\b\s*:?\s*\d", re.I)


def linha_nominal(v):
    """[id, data, descrição sem o placar, sim, não] — o placar vai em números, à parte."""
    d = _PLACAR_NO_TEXTO.split(v["descricao"] or "", maxsplit=1)[0].strip()
    if d and d[-1] not in ".!?)":
        d += "."
    if len(d) > 200:
        d = d[:199].rsplit(" ", 1)[0] + "…"
    return [v["id"], v["data"], d, int(v["votosSim"] or 0), int(v["votosNao"] or 0)]


def calcular_votacao(v, votos_da_votacao, ideologia):
    """Placar, adesão por faixa e médias de uma votação nominal.
    Normaliza sempre pela bancada presente: nunca voto absoluto (a Câmara tem
    maioria de centro-direita, e em números absolutos tudo pareceria 'da direita')."""
    placar = Counter(r["voto"] for r in votos_da_votacao)
    sim_f, vot_f = Counter(), Counter()
    scores_sim, scores_nao = [], []
    sem_score = Counter()
    for r in votos_da_votacao:
        if r["voto"] not in VOTANTE:
            continue
        sigla = r["deputado_siglaPartido"]
        score = (ideologia.get(sigla) or {}).get("score")
        if score is None:
            sem_score[sigla] += 1  # fora das médias e do denominador
            continue
        faixa = coleta.classificar(score)
        vot_f[faixa] += 1
        if r["voto"] == SIM:
            sim_f[faixa] += 1
            scores_sim.append(score)
        elif r["voto"] == NAO:
            scores_nao.append(score)
    return {
        "sim": placar[SIM], "nao": placar[NAO], "abstencao": placar[ABST],
        "obstrucao": placar[OBST],
        "margem": abs(placar[SIM] - placar[NAO]),
        "scoreMedioSim": media(scores_sim),
        "scoreMedioNao": media(scores_nao),
        # parcela que votou SIM dentro de cada faixa; denominador = quem votou
        # (sim, não ou abstenção) naquela faixa. Faixa sem votante → null.
        "adesaoPorFaixa": {f: (round(sim_f[f] / vot_f[f], 3) if vot_f[f] else None)
                           for f in FAIXAS},
        "votantesPorFaixa": {f: vot_f[f] for f in FAIXAS},
        "excluidosSemScore": sum(sem_score.values()),
        # por partido, para o site declarar o viés (a União sozinha pesa ~11%)
        "excluidosPorPartido": dict(sem_score.most_common()),
        "_placar": placar,
    }


# ---------------------------------------------------------------- orientação da bancada

# "Votou diferente da orientação da própria bancada": o voto nominal do deputado contra a
# orientação REGISTRADA pela liderança dele (votacoesOrientacoes-{ano}, da Câmara). Registro,
# não estimativa — substitui a medida antiga, que inferia a "maioria do próprio partido".
#
# A Câmara registra a orientação por liderança: partido ("PL"), federação ("Fdr PT-PCdoB-PV") ou
# bloco ("Bl MdbPsdRepPode"). A própria bancada é a liderança MAIS ESTREITA que orientou e que
# contém o partido do deputado NA DATA DO VOTO: partido, senão federação, senão bloco. Medido
# (2023–2026, Plenário): 263 mil votos comparáveis; 49% contra orientação de bloco, 27% de
# federação, 24% do próprio partido — MDB, PSD, Republicanos, União e PP quase só orientam por bloco.
#
# Blocos: nem o arquivo nem a API dão o identificador do bloco; a sigla registrada é a abreviação
# dos partidos ("MdbPsdRepPode") e às vezes vem cortada ("UniPpFdrPsdbCid..."). A tabela abaixo
# lista, para cada sigla que aparece no Plenário, só os partidos VISÍVEIS nela: partido escondido
# no "..." não é atribuído ao bloco, e aquele voto não conta. Sigla nova (bloco formado depois)
# não conta até entrar na tabela — o relatório avisa.
NAO_BANCADA = {"Governo", "Oposição", "Maioria", "Minoria"}   # não são a bancada de ninguém
ORIENTA_PARTIDO = {"Solidaried": "SOLIDARIEDADE", "Republican": "REPUBLICANOS", "Podemos": "PODE"}
FEDERACOES = {"Fdr PT-PCdoB-PV": {"PT", "PCdoB", "PV"}, "Fdr PSOL-REDE": {"PSOL", "REDE"},
              "Fdr PSDB-CIDADAN": {"PSDB", "CIDADANIA"}, "Fdr PSDB-CIDADANIA": {"PSDB", "CIDADANIA"}}
BLOCOS = {
    "Bl MdbPsdRepPode": {"MDB", "PSD", "REPUBLICANOS", "PODE"},
    "Bl MdbPsdRepPodePsc": {"MDB", "PSD", "REPUBLICANOS", "PODE", "PSC"},
    "Bl UniPpFdrPsdbCid...": {"UNIÃO", "PP", "PSDB", "CIDADANIA"},
    "Bl UniPpPsd...": {"UNIÃO", "PP", "PSD"},
    "Bl AvanSolidPrd...": {"AVANTE", "SOLIDARIEDADE", "PRD"},
    "Bl PlUniPpPsd...": {"PL", "UNIÃO", "PP", "PSD"},
    "Bl PlFdrPtUniPp...": {"PL", "PT", "PCdoB", "PV", "UNIÃO", "PP"},
    "Bl PlFdrPTUniPp...": {"PL", "PT", "PCdoB", "PV", "UNIÃO", "PP"},
}
MIN_COMPARAVEIS = 50  # abaixo disso o número não é publicado
NIVEIS = ("partido", "federacao", "bloco")


def bancada(partido, orientacoes):
    """(nível, sigla registrada, orientação) da própria bancada, ou None."""
    for sigla, o in orientacoes.items():
        if ORIENTA_PARTIDO.get(sigla, sigla).upper() == partido.upper():
            return "partido", sigla, o
    for sigla, o in orientacoes.items():
        if partido in FEDERACOES.get(sigla, ()):
            return "federacao", sigla, o
    for sigla, o in orientacoes.items():
        if partido in BLOCOS.get(sigla, ()):
            return "bloco", sigla, o
    return None


def dif_orientacao(ids_votacoes, votos, orientacoes, listar=()):
    """Quantas vezes cada deputado votou diferente da orientação da própria bancada, nas
    votações nominais do Plenário.

    Mede frequência, não motivo: votar diferente da orientação pode ser convicção,
    compromisso com o estado ou acordo. O site não chama ninguém de fiel, infiel ou traidor.

    Regras (na dúvida, a votação não conta):
    - só voto Sim ou Não contra orientação Sim ou Não; orientação "Liberado", "Obstrução" ou em
      branco não conta, nem abstenção, obstrução e "Artigo 17" do deputado;
    - Governo, Oposição, Maioria e Minoria não são bancada de ninguém;
    - partido na data do voto (vem no próprio registro); sem bancada que orientou, não conta.

    `listar`: ids de votação (as 164 finais de texto-base) para as quais também se devolve QUEM
    votou diferente — a lista da página das leis. Mesma regra da estatística.

    Devolve também, por PARTIDO NA DATA DO VOTO, os mesmos totais (aba "Partidos": coesão da
    bancada). Não dá para somar o `difOrientacao` dos deputados atuais de um partido: quem trocou de
    partido levaria os votos do partido antigo para o novo.
    """
    listar = set(listar)
    por_partido = defaultdict(lambda: {"comparaveis": 0, "diferentes": 0, "niveis": Counter()})
    por_votacao = defaultdict(list)
    acum = defaultdict(lambda: {n: [0, 0] for n in NIVEIS})   # nível → [comparáveis, diferentes]
    motivos, sem_regra = Counter(), Counter()
    for idv in ids_votacoes:
        ori = {s: o for s, o in (orientacoes.get(idv) or {}).items() if s not in NAO_BANCADA}
        if not ori:
            motivos["votação sem orientação registrada"] += 1
            continue
        for s in ori:
            if (s.startswith("Bl") and s not in BLOCOS) or (s.startswith("Fdr") and s not in FEDERACOES):
                sem_regra[s] += 1
        for r in votos[idv]:
            if r["voto"] not in (SIM, NAO):
                continue
            achou = bancada(r["deputado_siglaPartido"], ori)
            if achou is None:
                motivos["partido sem bancada que orientou"] += 1
                continue
            nivel, sigla, o = achou
            if o not in (SIM, NAO):
                motivos[f"orientação {o or 'em branco'}"] += 1
                continue
            motivos["comparável"] += 1
            pp = por_partido[r["deputado_siglaPartido"]]
            pp["comparaveis"] += 1
            pp["niveis"][nivel] += 1
            pp["diferentes"] += r["voto"] != o
            dep = int(r["deputado_id"])
            acum[dep][nivel][0] += 1
            if r["voto"] != o:
                acum[dep][nivel][1] += 1
                if idv in listar:
                    # [id, nome, partido, uf, voto, orientação, bancada como registrada]
                    por_votacao[idv].append([dep, r["deputado_nome"], r["deputado_siglaPartido"],
                                             r["deputado_siglaUf"], r["voto"], o, sigla])
    for lista in por_votacao.values():
        lista.sort(key=lambda x: (x[6], x[2], x[1]))
    saida = {}
    for dep, a in acum.items():
        n = sum(v[0] for v in a.values())
        k = sum(v[1] for v in a.values())
        saida[dep] = {"comparaveis": n, "diferentes": k if n >= MIN_COMPARAVEIS else None,
                      "niveis": {nv: a[nv] for nv in NIVEIS if a[nv][0]},
                      "nota": None if n >= MIN_COMPARAVEIS else
                      f"menos de {MIN_COMPARAVEIS} votações em que a bancada orientou Sim ou Não"}
    return saida, por_votacao, motivos, sem_regra, por_partido


# ---------------------------------------------------------------- pipeline

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--atualizar", action="store_true",
                    help="baixar de novo os arquivos em lote do ano corrente")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        deputados = json.loads(coleta.SAIDA.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"{coleta.SAIDA.name} não existe — rode coleta.py antes.")
    ideologia = coleta.carregar_ideologia()

    print("Lendo votações da legislatura (cache/bulk/)…", flush=True)
    votacoes = {r["id"]: r for r in linhas("votacoes", ANOS, args.atualizar)}
    votos = defaultdict(list)
    historico = defaultdict(list)  # deputado → [(instante, partido)] para o autor
    for r in linhas("votacoesVotos", ANOS, args.atualizar):
        votos[r["idVotacao"]].append(r)
        historico[r["deputado_id"]].append((instante(r["dataHoraVoto"]),
                                            r["deputado_siglaPartido"]))
    rel = defaultdict(list)
    for r in linhas("votacoesProposicoes", ANOS, args.atualizar):
        rel[r["idVotacao"]].append(r)
    orientacoes = defaultdict(dict)   # votação → {liderança: orientação}
    for r in linhas("votacoesOrientacoes", ANOS, args.atualizar):
        orientacoes[r["idVotacao"]][r["siglaBancada"]] = r["orientacao"]

    # Por proposição (prefixo do id da votação = proposição onde foi registrada),
    # a votação final do texto-base — a mesma regra da Etapa A. Só entra se nominal:
    # votação simbólica não tem voto individual.
    grupos = defaultdict(list)
    for v in votacoes.values():
        grupos[v["id"].split("-")[0]].append(v)
    texto_base = [v for v in votacoes.values() if coleta.e_texto_base(v)]
    finais = [f for g in grupos.values() if (f := coleta.votacao_texto_base(g))]
    selecionadas = [v for v in finais if v["id"] in votos]

    # Proposição de cada votação: a do prefixo, se estiver na relação.
    def proposicao_de(v):
        linhas_rel = rel.get(v["id"], [])
        prefixo = v["id"].split("-")[0]
        return next((r for r in linhas_rel if r["proposicao_id"] == prefixo),
                    linhas_rel[0] if linhas_rel else None)

    # Uma votação final por proposição, mesmo se registrada sob prefixos diferentes.
    por_prop = {}
    for v in selecionadas:
        p = proposicao_de(v)
        chave = p["proposicao_id"] if p else v["id"].split("-")[0]
        if chave not in por_prop or v["dataHoraRegistro"] > por_prop[chave][0]["dataHoraRegistro"]:
            por_prop[chave] = (v, p)

    # Autores: proposicoesAutores é indexado pelo ano da proposição.
    anos_prop = sorted({int(p["proposicao_ano"]) for _, p in por_prop.values() if p})
    ids_prop = set(por_prop)
    autores = defaultdict(list)
    print(f"Lendo autores ({len(anos_prop)} anos de proposicoesAutores)…", flush=True)
    for r in linhas("proposicoesAutores", anos_prop, args.atualizar):
        if r["idProposicao"] in ids_prop:
            autores[r["idProposicao"]].append(r)

    # A regra de nominal dos anos anteriores (placar oficial > 0, sem o arquivo de votos)
    # é conferida a cada rodada contra os votos individuais da legislatura.
    violacoes = [f"{i}: placar oficial {'> 0 sem' if e_nominal(v) else '= 0 com'} votos individuais"
                 for i, v in votacoes.items() if e_nominal(v) != (i in votos)]

    # Todas as votações nominais do Plenário sobre cada proposição, desde o ano em que
    # ela foi apresentada: texto-base, emendas, destaques e requerimentos. O de urgência
    # é registrado no REQ, não na proposição — por isso a ligação vem também de
    # votacoesProposicoes (medido: 29 votações ligadas ao projeto e ao REQ, nenhuma a
    # dois projetos). Quantas vezes o Plenário parou para votar nome a nome é registro
    # de disputa, não nota de importância: o site não junta isso a nenhum outro número.
    anos_antes = range(min(anos_prop, default=ANOS.start), ANOS.start)
    print(f"Lendo votações de {anos_antes.start}–{anos_antes.stop - 1} "
          "(só para contar as nominais de cada proposição)…", flush=True)
    todas = {r["id"]: r for r in linhas("votacoes", anos_antes, args.atualizar)}
    todas.update(votacoes)
    ligadas = defaultdict(set)
    for i in todas:
        if i.split("-")[0] in ids_prop:
            ligadas[i.split("-")[0]].add(i)
    for r in linhas("votacoesProposicoes", anos_antes, args.atualizar):
        if r["proposicao_id"] in ids_prop:
            ligadas[r["proposicao_id"]].add(r["idVotacao"])
    for i, rs in rel.items():
        for r in rs:
            if r["proposicao_id"] in ids_prop:
                ligadas[r["proposicao_id"]].add(i)
    nominais_plen = {}
    for id_prop in ids_prop:
        vs = sorted((todas[i] for i in ligadas[id_prop] if i in todas
                     and todas[i]["siglaOrgao"] == "PLEN" and e_nominal(todas[i])),
                    key=lambda v: v["dataHoraRegistro"])
        nominais_plen[id_prop] = [linha_nominal(v) for v in vs]
        if por_prop[id_prop][0]["id"] not in {v["id"] for v in vs}:
            violacoes.append(f"{id_prop}: a votação final não está entre as nominais do Plenário")

    # ------------------------------------------------ por votação
    resumo_de = resumos.carregar()
    # autores senadores das votações de origem no Senado (senado.py); vazio se ainda não rodou
    senadores_autores = coleta.ler_cache(coleta.CACHE / "senado" / "senadores" / "autores_leis.json") or {}
    saida = []
    sem_score_total = Counter()
    motivos_autor = Counter()
    for id_prop, (v, p) in por_prop.items():
        c = calcular_votacao(v, votos[v["id"]], ideologia)
        placar = c.pop("_placar")
        sem_score_total.update(c["excluidosPorPartido"])

        # Sanidade: o placar contado voto a voto tem de bater com o oficial.
        if (str(placar[SIM]), str(placar[NAO])) != (v["votosSim"], v["votosNao"]):
            violacoes.append(f"{v['id']}: contado {placar[SIM]}×{placar[NAO]}, "
                             f"oficial {v['votosSim']}×{v['votosNao']}")
        desconhecidos = set(placar) - {SIM, NAO, ABST, OBST, ART17}
        if desconhecidos:
            violacoes.append(f"{v['id']}: valores de voto desconhecidos {desconhecidos}")

        # Resultado SÓ de `aprovacao`. Nunca de sim > não: PEC exige 308 e PLP 257.
        aprovacao = int(v["aprovacao"]) if v["aprovacao"] in ("0", "1") else None
        resultado = coleta.status_da_votacao({"aprovacao": aprovacao})

        # Autor e o score do partido dele NA DATA DA VOTAÇÃO (mesma base temporal
        # dos votantes). Autor não deputado ou sem voto na legislatura → null.
        a = autor_principal(autores.get(id_prop, []))
        autor_id = int(a["idDeputadoAutor"]) if a and a["idDeputadoAutor"] else None
        autor_partido = partido_na_data(historico.get(str(autor_id)),
                                        instante(v["dataHoraRegistro"])) if autor_id else None
        autor_score = (ideologia.get(autor_partido) or {}).get("score") if autor_partido else None
        # Autor senador (senado.py → cache/senado/autores_leis.json, ligado por identificador):
        # partido NA DATA DA VOTAÇÃO pelas filiações do Senado, score do mesmo ideologia.json.
        senador = senadores_autores.get(str(id_prop)) if a and not autor_id else None
        if senador:
            dia = (v["data"] or "")[:10]
            autor_partido = next((s for s, ini, fim in senador["filiacoes"]
                                  if (ini or "") <= dia and (fim is None or dia <= fim)), None)
            autor_score = (ideologia.get(autor_partido) or {}).get("score") if autor_partido else None
        if a is None:
            motivo = "autor não encontrado em proposicoesAutores"
        elif senador and autor_partido is None:
            motivo = "senador sem filiação registrada na data da votação"
        elif senador and autor_score is None:
            motivo = f"partido do autor sem score ({autor_partido})"
        elif senador:
            motivo = None
        elif autor_id is None:
            motivo = f"autor não é deputado ({a['tipoAutor']})"
        elif autor_partido is None:
            motivo = "autor sem voto registrado na legislatura"
        elif autor_score is None:
            motivo = f"partido do autor sem score ({autor_partido})"
        else:
            motivo = None
        motivos_autor[motivo or "com score"] += 1

        ementa = (p or {}).get("proposicao_ementa")
        saida.append({
            "idVotacao": v["id"],
            "idProposicao": int(id_prop),
            "numero": (p or {}).get("proposicao_titulo"),
            "titulo": coleta.titulo_curto(ementa),
            "resumo": resumo_de.get(int(id_prop)),  # passo 8: resumos.py (IA, do inteiro teor)
            # quanto tempo e por onde passou (tramitacoes.py; só se o histórico já estiver no cache)
            "tramitacao": _etapas(id_prop, (p or {}).get("proposicao_titulo")),
            "ementa": ementa,
            "data": v["data"],
            "autorId": autor_id,
            "autorSenador": senador["codigo"] if senador else None,   # código no Senado (senadores.html?id=)
            "autorNome": a["nomeAutor"] if a else None,
            "autorPartido": autor_partido,
            "autorScore": autor_score,
            "autorNota": motivo,
            **{k: c[k] for k in ("sim", "nao", "abstencao", "obstrucao", "margem")},
            "nominal": True,
            # todas as nominais do Plenário sobre a proposição: [id, data, descrição, sim, não]
            "nominaisPlenario": nominais_plen[id_prop],
            "aprovacao": aprovacao,
            "resultado": resultado,
            "scoreMedioSim": c["scoreMedioSim"],
            "scoreMedioNao": c["scoreMedioNao"],
            "apoioCruzado": (round(abs(autor_score - c["scoreMedioSim"]), 2)
                             if autor_score is not None and c["scoreMedioSim"] is not None
                             else None),
            "adesaoPorFaixa": c["adesaoPorFaixa"],
            "votantesPorFaixa": c["votantesPorFaixa"],
            "excluidosSemScore": c["excluidosSemScore"],
            "excluidosPorPartido": c["excluidosPorPartido"],
            "descricaoVotacao": v["descricao"],
            "urlCamara": "https://www.camara.leg.br/proposicoesWeb/"
                         f"fichadetramitacao?idProposicao={id_prop}",
        })

    if violacoes:
        print(f"\nCHECAGEM DE SANIDADE FALHOU — {len(violacoes)} problema(s). Nada foi gravado.")
        for x in violacoes:
            print("  -", x)
        sys.exit(2)

    # ------------------------------------------------ por deputado
    # Só sobre as votações selecionadas, e só o que está registrado. Ausência NÃO
    # se calcula por subtração: lista de votos não identifica quem faltou, e quem
    # assumiu no meio do mandato nem podia votar antes.
    ids_sel = [v["id"] for v, _ in por_prop.values()]
    por_dep = defaultdict(Counter)
    for idv in ids_sel:
        for r in votos[idv]:
            por_dep[int(r["deputado_id"])][r["voto"]] += 1
    for d in deputados:
        c = por_dep.get(d["id"], Counter())
        d["votos"] = {"chamadas": sum(c.values()), "sim": c[SIM], "nao": c[NAO],
                      "abstencao": c[ABST]}

    # Orientação da bancada: todas as votações nominais do Plenário, não só as 164 de texto-base.
    plen = [i for i in votos if (votacoes.get(i) or {}).get("siglaOrgao") == "PLEN"]
    difo, dif_por_votacao, motivos_ori, sem_regra, dif_partidos = dif_orientacao(plen, votos, orientacoes,
                                                                                 listar=ids_sel)
    # Para partidos.py (aba "Partidos"). Derivado e regerável: fica em cache/, não no site.
    coleta.gravar_cache(coleta.CACHE / "orientacao_partidos.json",
                        {"votacoes": len(plen), "partidos": dif_partidos})
    for d in deputados:
        d.pop("divergencia", None)   # medida antiga ("maioria do próprio partido"), substituída
        d["difOrientacao"] = difo.get(d["id"]) or {"comparaveis": 0, "diferentes": None, "niveis": {},
                                                   "nota": "sem voto Sim/Não contra orientação registrada no Plenário"}
    # Página das leis: em cada uma das 164, quem votou diferente da orientação da própria
    # bancada (mesma regra da estatística) e a categoria, pela classificação de temas.py.
    for x in saida:
        x.pop("divergentes", None)
        x["difOrientacao"] = dif_por_votacao.get(x["idVotacao"], [])
        x["temas"] = [[CURTO[CATEGORIAS[i]], termo, fonte]
                      for i, termo, fonte in classificar(x["ementa"] or "", "")]

    saida.sort(key=lambda x: x["data"], reverse=True)
    gravar_json(PROPOSICOES, saida, um_por_linha=True)
    gravar_json(coleta.SAIDA, deputados, um_por_linha=True)   # mesmo formato de coleta.py

    # ------------------------------------------------ relatório
    print(f"\nVotações na legislatura: {len(votacoes)} · de texto-base (allowlist): "
          f"{len(texto_base)} · nominais: {sum(v['id'] in votos for v in texto_base)}")
    print(f"Finais por proposição: {len(finais)} · nominais: {len(selecionadas)} · "
          f"gravadas: {len(saida)} (turnos anteriores e votações simbólicas ficam fora)")
    print("Checagem OK: placar voto a voto = placar oficial em todas.")
    print(f"Resultado (de `aprovacao`): {dict(Counter(x['resultado'] for x in saida))}")
    divergem = [x for x in saida if (x["sim"] > x["nao"]) != (x["aprovacao"] == 1)]
    print(f"Sim > Não mas não aprovada (ou o inverso): {len(divergem)}")
    for x in divergem:
        print(f"   {x['numero']} {x['sim']}×{x['nao']} → {x['resultado']}")
    votantes = sum(x["sim"] + x["nao"] + x["abstencao"] for x in saida)
    excl = sum(sem_score_total.values())
    print(f"Votos excluídos das médias por falta de score: {excl} de {votantes} "
          f"({excl / votantes:.1%}); só a UNIÃO: {sem_score_total['UNIÃO']} "
          f"({sem_score_total['UNIÃO'] / votantes:.1%}) · {dict(sem_score_total.most_common())}")
    print(f"Autor: {dict(motivos_autor.most_common())}")
    n_nom = sorted(len(x["nominaisPlenario"]) for x in saida)
    antes = sum(1 for x in saida if any(n[1] < f"{ANOS.start}" for n in x["nominaisPlenario"]))
    print(f"Votações nominais do Plenário por proposição (todas, não só a final): mediana "
          f"{n_nom[len(n_nom) // 2]} · máx {n_nom[-1]} · só a final: {n_nom.count(1)} · "
          f"com nominais antes de {ANOS.start}: {antes}")
    print(f"Voto × orientação da própria bancada ({len(plen)} votações nominais do Plenário): "
          f"{dict(motivos_ori.most_common())}")
    if sem_regra:
        aviso = (f"bancada sem regra em BLOCOS/FEDERACOES (votos dos partidos dela não contam): "
                 f"{dict(sem_regra.most_common())}")
        print(f"ATENÇÃO: {aviso}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning title=votos.py::{aviso}")
    t = sorted(d["difOrientacao"]["diferentes"] / d["difOrientacao"]["comparaveis"]
               for d in deputados if d["difOrientacao"]["diferentes"] is not None)
    if t:
        print(f"Votou diferente da orientação da própria bancada: {len(t)} deputados com número · "
              f"mediana {t[len(t)//2]:.1%} · p90 {t[int(len(t)*.9)]:.1%} · máx {t[-1]:.1%}")
    com = [d for d in deputados if d["votos"]["chamadas"]]
    print(f"\nDeputados com ao menos um voto registrado nessas votações: {len(com)} de "
          f"{len(deputados)}")
    print(f"{PROPOSICOES.name} ({len(saida)} votações) e {coleta.SAIDA.name} (+ votos) gravados.")

    disputadas = sorted(saida, key=lambda x: x["margem"])[:5]
    consensuais = sorted(saida, key=lambda x: -x["margem"])[:5]
    for nome, lista in (("Mais disputadas", disputadas), ("Mais consensuais", consensuais)):
        print(f"\n{nome}:")
        for x in lista:
            fmt = lambda s: "—" if s is None else f"{s:.2f}"
            print(f"   {x['numero']:<16} {x['sim']:>3}×{x['nao']:<3} margem {x['margem']:>3} · "
                  f"SIM {fmt(x['scoreMedioSim'])} / NÃO {fmt(x['scoreMedioNao'])} · "
                  f"{x['titulo'][:60]}")


if __name__ == "__main__":
    main()
