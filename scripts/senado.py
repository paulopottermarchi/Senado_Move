"""
Câmara Aberta — senadores: o que cada um propôs, o que virou lei, o que relatou e como votou.

Uso:
    python scripts/senado.py              # os 81 senadores em exercício; grava senadores.json
    python scripts/senado.py --atualizar  # rebaixa autorias, relatorias e votos (o detalhe dos processos fica)

Fonte: Dados Abertos do Senado (legis.senado.leg.br/dadosabertos), sem chave. Conferido em
29/9/2026 contra a API (a especificação está em /dadosabertos/v3/api-docs):
  - /senador/{codigo}/autorias, /relatorias e /votacoes estão marcados DEPRECATED; os atuais são
    /processo?codigoParlamentarAutor=, /processo/relatoria?codigoParlamentar= e
    /votacao?codigoParlamentar=.
  - /votacao por senador traz, em cada votação, só o voto dele (lista "votos" com 1 item). Nas
    votações NOMINAIS os totais vêm nulos (183 de 183 na amostra) — contados aqui; só as SECRETAS
    trazem total, e nelas o voto individual é só "Votou".
  - /processo?codigoParlamentarAutor= devolve também coautoria (PEC exige 27 assinaturas): autor =
    ordem 1 em autoriaIniciativa do detalhe /processo/{id}, pelo CÓDIGO do parlamentar. Com um
    autor só na lista, é ele (a consulta foi pelo código dele). Medido numa amostra de 8: 63% das
    PEC e PL têm mais de um autor.

Regras (as da Câmara, sem reimplementar o que não muda):
  - Apresentadas = PEC + PL de autoria (ordem 1). Virou lei = situação "TRANSFORMADA EM NORMA
    JURÍDICA" (com ou sem veto parcial). Nunca inferir.
  - Espectro = score do PARTIDO atual em ideologia.json (média das respostas da wave 2021 do BLS,
    calculada pelo site) — o mesmo número vale para deputado e senador do mesmo partido.
  - Relatoria = dado de contexto, nunca score. Contam processos distintos (PEC, PL, PLP, MPV, PDL,
    PRS) em que foi relator; e quantos terminaram com a matéria deliberada. Designações repetidas
    (redistribuição, ad hoc, troca de comissão) não inflam o número.
  - Votos: votações nominais abertas do Plenário desde 1º/2/2023. O Senado registra todo senador
    em cada votação e, quando não votou, o motivo oficial (medido: atividade parlamentar 919,
    presente sem registrar voto 957, licença saúde 306, missão 222, presidindo 183, não compareceu
    79). A ficha mostra "votou em N" e o motivo registrado do resto — sem taxa, sem ranking.
  - Votou diferente da orientação da própria bancada: /plenario/votacao/orientacaoBancada/{ini}/{fim}
    (não DEPRECATED; conferido em 30/9/2026) traz, por votação, a orientação REGISTRADA de cada
    partido (SIM, NÃO, LIVRE). Compara o voto Sim/Não do senador com a orientação SIM/NÃO do partido
    dele NA DATA DO VOTO (o registro do voto traz a sigla). LIVRE (liberada), obstrução, partido que
    não orientou e as lideranças que não são bancada do senador (Governo, Oposição, Maioria, Minoria,
    Bancada Feminina) não contam. Registro, não estimativa. Nunca "traidor" ou "infiel".
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

import requests

import coleta

API = "https://legis.senado.leg.br/dadosabertos"
CACHE_S = coleta.CACHE / "senado" / "senadores"
SAIDA = coleta.DADOS / "senadores.json"
TIPOS = ("PEC", "PL")
TIPOS_RELATORIA = ("PEC", "PL", "PLP", "MPV", "PDL", "PRS", "PLN")
LEI = ("TRANSFORMADA EM NORMA JURÍDICA", "TRANSFORMADA EM NORMA JURÍDICA COM VETO PARCIAL")
INICIO_LEGISLATURA = "2023-02-01"
DELIBERADA = ("Deliberação da matéria", "Matéria deliberada no plenário")
VALIDADE = timedelta(days=7)
# Orientação: o registro escreve alguns partidos por extenso; o voto do senador traz a sigla.
ORIENTA_ALIAS = {"Podemos": "PODE", "Republica": "REPUBLICANOS", "Republicanos": "REPUBLICANOS",
                 "Progressistas": "PP"}
NAO_BANCADA = {"Governo", "Oposição", "Maioria", "Minoria", "Banc Fem", "B.Feminina"}
MIN_ORIENTACAO = 30   # votações comparáveis para publicar o número (medido: mediana de 100 por senador)
# Requerimento cujo voto por senador vem sem sequencialVotacao: liga pelo número e ano do requerimento
# e pela data, que precisam apontar uma única votação do registro de orientações.
REQUERIMENTO = re.compile(r"Requerimento\s+(?:n[ºo°.]*\s*)?([\d.]+)\s*,?\s+de\s+(\d{4})", re.I)

sessao = requests.Session()
sessao.headers.update({"User-Agent": "CamaraAberta/1.0 (https://github.com/paulopottermarchi/Senado_Move)",
                       "Accept": "application/json"})
_ultima = [0.0]


def pedir(caminho, **params):
    for n in range(6):
        espera = 0.4 - (time.monotonic() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.monotonic()
        try:
            r = sessao.get(API + caminho, params=params, timeout=120)
        except requests.RequestException:
            time.sleep(5 * (n + 1))
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 200:
            return r.json()
        time.sleep(max(int(r.headers.get("retry-after") or 0), 5 * (n + 1)))
    raise RuntimeError(f"sem resposta: {caminho} {params}")


def em_cache(nome, atualizar, validade=VALIDADE):
    caminho = CACHE_S / nome
    if atualizar or not caminho.exists():
        return caminho, None
    if datetime.now() - datetime.fromtimestamp(caminho.stat().st_mtime) > validade:
        return caminho, None
    return caminho, coleta.ler_cache(caminho)


def buscar(nome, caminho_api, atualizar, validade=VALIDADE, **params):
    caminho, c = em_cache(nome, atualizar, validade)
    if c is None:
        c = pedir(caminho_api, **params)
        coleta.gravar_cache(caminho, c if c is not None else [])
    return c or []


def orientacoes(atualizar):
    """Orientação registrada de cada partido em cada votação do Plenário desde 2023.
    Devolve {sequencialVotacao: {sigla: 'SIM'|'NÃO'|'LIVRE'|…}} e o índice
    {(data, número do requerimento, ano): [sequenciais]} para o voto que vem sem sequencial."""
    hoje = datetime.now()
    por_seq, por_req = {}, {}
    for ano in range(int(INICIO_LEGISLATURA[:4]), hoje.year + 1):
        ini = INICIO_LEGISLATURA.replace("-", "") if ano == int(INICIO_LEGISLATURA[:4]) else f"{ano}0101"
        fim = f"{ano}1231" if ano < hoje.year else hoje.strftime("%Y%m%d")
        # ano fechado não muda — salvo o anterior, relido por um tempo para pegar o fim do ano
        validade = timedelta(days=1) if ano >= hoje.year - 1 else timedelta(days=3650)
        d = buscar(f"orientacoes/{ano}.json", f"/plenario/votacao/orientacaoBancada/{ini}/{fim}",
                   atualizar, validade)
        for v in (d.get("votacoes") or []) if isinstance(d, dict) else []:
            por_seq[v["sequencialVotacao"]] = {
                ORIENTA_ALIAS.get(o.get("partido"), o.get("partido")): o.get("voto")
                for o in v.get("orientacoesLideranca") or [] if o.get("partido") not in NAO_BANCADA}
            if v.get("siglaTipoMateria") == "RQS":
                chave = ((v.get("dataInicioVotacao") or "")[:10], v.get("numeroMateria"), v.get("anoMateria"))
                por_req.setdefault(chave, []).append(v["sequencialVotacao"])
    return por_seq, por_req


def sequencial(v, por_req):
    if v.get("sequencialVotacao") is not None:
        return v["sequencialVotacao"]
    m = REQUERIMENTO.search(v.get("descricaoVotacao") or "")
    if not m:
        return None
    achados = por_req.get((v.get("dataSessao"), int(m.group(1).replace(".", "")), int(m.group(2))), [])
    return achados[0] if len(achados) == 1 else None


def autor_principal(cod, p, atualizar):
    """É o primeiro autor? Um autor só na lista: sim (a consulta foi pelo código dele). Vários: o de
    ordem 1 no detalhe do processo, pelo código."""
    if len(re.findall(r"\bSenador(?:a)?\s", p.get("autoria") or "")) <= 1 and "," not in (p.get("autoria") or ""):
        return True
    det = buscar(f"processo/{p['id']}.json", f"/processo/{p['id']}", False, timedelta(days=30))
    ai = (det or {}).get("autoriaIniciativa") if isinstance(det, dict) else None
    primeiro = min(ai or [], key=lambda a: a.get("ordem") or 99, default=None)
    return bool(primeiro) and str(primeiro.get("codigoParlamentar")) == str(cod)


AUTORES_LEIS = CACHE_S / "autores_leis.json"


def autores_de_leis(atualizar=False):
    """Das 164 votações (proposicoes.json), as de autoria do Senado ganham o senador autor, por
    identificador: numeração única Câmara–Senado desde 2019 → processo do Senado com a mesma
    identificação (casa iniciadora) → autor de ORDEM 1 pelo código. O nome que a Câmara grava
    ("Senado Federal - Flávio Arns") só confere; se não bater, fica de fora. Grava as filiações com
    datas: o votos.py pega o partido NA DATA DA VOTAÇÃO, como faz com os deputados."""
    P = json.loads((coleta.DADOS / "proposicoes.json").read_text(encoding="utf-8"))
    saida, conta = {}, Counter()
    for p in P:
        nome_camara = p.get("autorNome") or ""
        if not nome_camara.startswith("Senado Federal - "):
            continue
        m = re.match(r"(PEC|PLP|PL|PDL)\s+(\d+)/(\d{4})", p.get("numero") or "")
        if not m:
            conta["número não reconhecido"] += 1
            continue
        sigla, num, ano = m[1], m[2], m[3]
        lst = buscar(f"busca/{sigla}_{num}_{ano}.json", "/processo", atualizar, timedelta(days=30),
                     sigla=sigla, numero=num, ano=ano)
        sf = [x for x in lst if x.get("casaIdentificadora") == "SF"
              and re.sub(r"\s*\(.*?\)", "", x.get("identificacao") or "").strip() == f"{sigla} {num}/{ano}"]
        ini = [x for x in sf if x.get("objetivo") == "Iniciadora"] or sf
        if not ini:
            conta["processo do Senado não achado pelo número (anterior a 2019?)"] += 1
            continue
        det = buscar(f"processo/{ini[0]['id']}.json", f"/processo/{ini[0]['id']}", False, timedelta(days=30))
        ai = (det or {}).get("autoriaIniciativa") if isinstance(det, dict) else None
        primeiro = min(ai or [], key=lambda a: a.get("ordem") or 99, default=None)
        if not primeiro or primeiro.get("siglaTipo") != "SENADOR" or not primeiro.get("codigoParlamentar"):
            conta["autor de ordem 1 não é senador"] += 1
            continue
        if coleta.nome_chave(primeiro.get("autor")) != coleta.nome_chave(nome_camara.split(" - ", 1)[1]):
            conta["nome registrado na Câmara não confere"] += 1
            continue
        cod = primeiro["codigoParlamentar"]
        f = buscar(f"filiacoes/{cod}.json", f"/senador/{cod}/filiacoes", atualizar, timedelta(days=30))
        fs = (((f or {}).get("FiliacaoParlamentar") or {}).get("Parlamentar") or {}).get("Filiacoes", {}).get("Filiacao") or []
        fs = fs if isinstance(fs, list) else [fs]
        saida[str(p["idProposicao"])] = {
            "codigo": int(cod), "nome": primeiro.get("autor"),
            "filiacoes": [[(x.get("Partido") or {}).get("SiglaPartido"), x.get("DataFiliacao"), x.get("DataDesfiliacao")]
                          for x in fs]}
        conta["ligado"] += 1
    coleta.gravar_cache(AUTORES_LEIS, saida)
    print(f"Autores do Senado nas votações da Câmara: {dict(conta)}")
    return saida


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--atualizar", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    lista = (pedir("/senador/lista/atual") or {}).get("ListaParlamentarEmExercicio", {}) \
        .get("Parlamentares", {}).get("Parlamentar", [])
    if len(lista) < 75:
        raise SystemExit(f"Lista de senadores com {len(lista)} nomes — conferir a API antes de gravar.")
    ideologia = {k: v for k, v in json.loads(coleta.IDEOLOGIA.read_text(encoding="utf-8")).items()
                 if not k.startswith("_")}
    print(f"{len(lista)} senadores em exercício", flush=True)
    ori_seq, ori_req = orientacoes(args.atualizar)
    print(f"Orientações de bancada registradas: {len(ori_seq)} votações "
          f"({sum(1 for o in ori_seq.values() if o)} com orientação de algum partido)", flush=True)
    motivos_ori = Counter()

    saida, votacoes = [], {}
    for n, p in enumerate(lista, 1):
        idp, man = p["IdentificacaoParlamentar"], p.get("Mandato") or {}
        cod = idp["CodigoParlamentar"]
        if n % 10 == 0:
            print(f"   {n}/{len(lista)}", flush=True)

        procs = buscar(f"autorias/{cod}.json", "/processo", args.atualizar, codigoParlamentarAutor=cod)
        candidatos = [x for x in procs if (x.get("identificacao") or "").split(" ")[0] in TIPOS
                      and autor_principal(cod, x, args.atualizar)]
        # Um projeto, várias fases: o Senado registra a volta da Câmara como outro processo
        # ("PL 1958/2021 (Substitutivo-CD)", "(fase 2)", "(Emenda-CD)") — medido: 127 números
        # repetidos. Conta uma vez pelo número; vale o registro com a lei, se alguma fase virou lei.
        por_numero = {}
        for x in candidatos:
            base = re.sub(r"\s*\(.*?\)", "", x["identificacao"]).strip()
            atual = por_numero.get(base)
            if atual is None or ((x.get("situacaoAtual") or "") in LEI and (atual.get("situacaoAtual") or "") not in LEI):
                por_numero[base] = {**x, "identificacao": base} if atual is None else {**x, "identificacao": base,
                                                                                       "dataApresentacao": atual.get("dataApresentacao")}
        meus = list(por_numero.values())
        n_tipo = Counter(x["identificacao"].split(" ")[0] for x in meus)
        leis = [x for x in meus if (x.get("situacaoAtual") or "") in LEI]
        nesta = [x for x in meus if (x.get("dataApresentacao") or "") >= INICIO_LEGISLATURA]

        rel = buscar(f"relatorias/{cod}.json", "/processo/relatoria", args.atualizar, codigoParlamentar=cod)
        # "SUG 5/2022", "PL 2036/2023": só proposições legislativas (sugestão, requerimento, ofício não)
        rel = [r for r in rel if (r.get("identificacaoProcesso") or " ").split(" ")[0] in TIPOS_RELATORIA]
        processos = {r.get("idProcesso") for r in rel}
        deliberadas = {r.get("idProcesso") for r in rel if r.get("descricaoTipoEncerramento") in DELIBERADA}

        vt = buscar(f"votos/{cod}.json", "/votacao", args.atualizar, codigoParlamentar=cod)
        conta, dif = Counter(), Counter()
        for v in vt:
            if v.get("votacaoSecreta") in ("S", True) or (v.get("dataSessao") or "") < INICIO_LEGISLATURA:
                continue
            for voto in v.get("votos") or []:
                if str(voto.get("codigoParlamentar")) != str(cod):
                    continue
                # O Senado registra TODO senador em cada votação, com o motivo quando não votou
                # (atividade parlamentar, licença, missão, presidindo, não compareceu): guardado como
                # vem, sem taxa — "votou em N" com o motivo oficial do resto.
                sig = voto.get("siglaVotoParlamentar")
                conta["votacoes"] += 1
                conta[{"Sim": "sim", "Não": "nao", "Abstenção": "abstencao"}.get(sig, f"reg:{sig}")] += 1
                votacoes.setdefault(v["codigoSessaoVotacao"], {"data": v.get("dataSessao"),
                                                               "identificacao": v.get("identificacao"),
                                                               "votos": {}})["votos"][cod] = [sig, voto.get("siglaPartidoParlamentar")]
                # Orientação da própria bancada: o partido dele NA DATA DO VOTO, como o registro traz.
                if sig not in ("Sim", "Não"):
                    continue
                seq = sequencial(v, ori_req)
                ori = ori_seq.get(seq) if seq is not None else None
                if ori is None:
                    motivos_ori["votação sem registro de orientação"] += 1
                    continue
                o = ori.get(voto.get("siglaPartidoParlamentar"))
                if o not in ("SIM", "NÃO"):
                    motivos_ori["partido não orientou" if o is None else f"orientação {o}"] += 1
                    continue
                motivos_ori["comparável"] += 1
                dif["comparaveis"] += 1
                dif["diferentes"] += (sig == "Sim") != (o == "SIM")

        partido = idp.get("SiglaPartidoParlamentar")
        ide = ideologia.get(partido) or {}
        score = ide.get("score")
        saida.append({
            "id": int(cod), "nome": idp.get("NomeParlamentar"), "partido": partido,
            "uf": idp.get("UfParlamentar"), "urlFoto": idp.get("UrlFotoParlamentar"),
            "url": idp.get("UrlPaginaParlamentar"), "participacao": man.get("DescricaoParticipacao"),
            "mandato": [(man.get("PrimeiraLegislaturaDoMandato") or {}).get("DataInicio"),
                        (man.get("SegundaLegislaturaDoMandato") or man.get("PrimeiraLegislaturaDoMandato") or {}).get("DataFim")],
            "score": score, "ideologia": coleta.classificar(score) if score is not None else None,
            "ideX": round((score - 1) / 9, 4) if score is not None else None,
            "scoreNota": None if score is not None else (ide.get("nota") or "partido ausente de ideologia.json"),
            "pecs": n_tipo["PEC"], "leis": n_tipo["PL"], "apresentadas": len(meus), "viraramLei": len(leis),
            "nestaLegislatura": {"apresentadas": len(nesta),
                                 "viraramLei": sum(1 for x in nesta if (x.get("situacaoAtual") or "") in LEI)},
            "relatorias": {"processos": len(processos), "deliberadas": len(deliberadas), "designacoes": len(rel)},
            "votos": ({"votacoes": conta["votacoes"], "sim": conta["sim"], "nao": conta["nao"],
                       "abstencao": conta["abstencao"],
                       "outros": {k[4:]: v for k, v in conta.items() if k.startswith("reg:")}}
                      if conta else None),
            "difOrientacao": ({"comparaveis": dif["comparaveis"], "diferentes": dif["diferentes"]}
                              if dif["comparaveis"] >= MIN_ORIENTACAO else
                              {"comparaveis": dif["comparaveis"], "diferentes": None,
                               "nota": f"menos de {MIN_ORIENTACAO} votações em que o partido orientou Sim ou Não"
                                       if dif["comparaveis"] else "o partido não orientou Sim ou Não em nenhuma votação em que votou"}),
            "leisLista": [[x["identificacao"], (x.get("ementa") or "")[:220], x.get("dataApresentacao"),
                           x.get("situacaoAtual"), x.get("codigoMateria")] for x in sorted(leis, key=lambda x: x.get("dataApresentacao") or "", reverse=True)[:12]],
        })

    saida.sort(key=lambda s: s["nome"])
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text("[\n" + ",\n".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) for s in saida) + "\n]\n",
                   encoding="utf-8")
    os.replace(tmp, SAIDA)

    com = [s for s in saida if s["score"] is not None]
    print(f"\n{len(saida)} senadores · com score: {len(com)} · sem: "
          f"{Counter(s['partido'] for s in saida if s['score'] is None)}")
    print(f"PEC+PL de autoria: mediana {sorted(s['apresentadas'] for s in saida)[len(saida) // 2]} · "
          f"viraram lei: {sum(s['viraramLei'] for s in saida)} (somas por senador)")
    rp = sorted(s["relatorias"]["processos"] for s in saida)
    print(f"Relatorias (processos distintos): mediana {rp[len(rp) // 2]} · máx {rp[-1]}")
    print(f"Votações nominais do Plenário desde {INICIO_LEGISLATURA} com voto de senador atual: {len(votacoes)}")
    print(f"Voto × orientação da própria bancada: {dict(motivos_ori.most_common())}")
    tx = sorted(s["difOrientacao"]["diferentes"] / s["difOrientacao"]["comparaveis"]
                for s in saida if s["difOrientacao"]["diferentes"] is not None)
    if tx:
        print(f"Votou diferente da orientação da própria bancada: {len(tx)} senadores com número · "
              f"mediana {tx[len(tx) // 2]:.1%} · p90 {tx[int(len(tx) * .9)]:.1%} · máx {tx[-1]:.1%}")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")
    autores_de_leis(args.atualizar)


if __name__ == "__main__":
    main()
