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
  - Apresentadas = PEC + PL de autoria (ordem 1; o PLS, Projeto de Lei do Senado até 2018, conta como PL). Virou lei = situação "TRANSFORMADA EM NORMA
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
  - Alinhamento ao Governo: o MESMO registro de orientação traz a do líder do Governo no Senado (partido "Governo",
    SIM, NÃO ou LIVRE; 137 das 424 votações com orientação registrada desde 2023 têm SIM ou NÃO, medido em 5/10/2026). Parcela dos votos Sim ou Não do senador,
    nas votações com orientação SIM ou NÃO do Governo, em que ele votou como o Governo orientou. Mesma medida do
    blocos.py na Câmara. Mede proximidade com o governo do momento, não ideologia nem mérito; mínimo de 30 votações.
  - Filtro de legislatura (da 52ª, 2003, em diante): `mandatos` = períodos EM EXERCÍCIO de cada senador de hoje em cada
    legislatura (/senador/{codigo}/mandatos, campo Exercicios: titular ou suplente que de fato exerceu) e `porLegislatura` =
    PEC e PL de autoria (ordem 1) e relatorias pela legislatura da data de apresentação (da designação, nas relatorias).
    Só os senadores de hoje: quem já saiu não está aqui (como no filtro de mandato dos deputados); a soma por legislatura
    é conferida contra o total da carreira e o script aborta se não bater. Votos e orientação existem só desde 2023.
  - Senadores que JÁ SAÍRAM (5/10/2026): `senadores_antigos.json`, com todo senador que exerceu o mandato em alguma legislatura
    da 52ª em diante (/senador/lista/legislatura/{n}, só quem tem `Exercicios`: titular ou suplente que de fato exerceu) e não
    está entre os de hoje — 309 de 402 (12 só exerceram antes de 2003). Mesmos números dos de hoje, calculados do mesmo jeito. A coleta é longa (~35 min a frio):
    `--minutos` limita o tempo por rodada, o cache guarda o que já foi, e o arquivo só é regravado quando TODOS estão prontos.
  - Suplentes e saída (5/10/2026): `mandatosDetalhe` = cada mandato que alcança a 52ª legislatura em diante, com os períodos em
    exercício (data de início, data de fim e a causa do fim — término do mandato, renúncia, licença, retorno do titular, falecimento,
    cassação…), os SUPLENTES (1º e 2º, com os períodos em que cada um assumiu; vazio = não assumiu) e, quando o senador foi
    suplente, de quem. `saida` = a data e a causa do fim do último período em exercício, ou nulo se ainda está no cargo. Tudo de
    /senador/{codigo}/mandatos, o mesmo registro de `Exercicios`; suplente que nunca exerceu não está na lista de senadores de
    nenhuma legislatura (`roster`), e é o que permite dizer "não assumiu".
  - `posicao`: o partido de cada senador NA LEGISLATURA (o que ele tinha ao entrar em exercício nela, pelas filiações) e a posição
    desse partido no BLS (2021). O Senado grava a filiação pelo código do partido, e o código acompanha as renomeações (MDB,
    REPUBLICANOS, CIDADANIA, SOLIDARIEDADE aparecem com a sigla de hoje em filiações antigas); as exceções estão em
    `entradas/partidos_sucessao.json` (PPB→PP; PL só vale desde que o PR voltou a se chamar PL). Partido sem posição na pesquisa
    (PFL/DEM, PTB, PSL, PSC…) fica sem posição: nunca estimada.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from statistics import median

import requests

import coleta

API = "https://legis.senado.leg.br/dadosabertos"
CACHE_S = coleta.CACHE / "senado" / "senadores"
SAIDA = coleta.DADOS / "senadores.json"
ANTIGOS = coleta.DADOS / "senadores_antigos.json"
TIPOS = ("PEC", "PL", "PLS")      # PLS = Projeto de Lei do Senado, o nome do PL no Senado até 2018 (numeração única desde 2019)


def tipo_de(identificacao):
    """"PEC" ou "PL" (o PLS entra como PL: é o mesmo tipo de proposição, só com outro nome até 2018)."""
    sigla = (identificacao or "").split(" ")[0]
    return "PL" if sigla == "PLS" else sigla
TIPOS_RELATORIA = ("PEC", "PL", "PLP", "MPV", "PDL", "PRS", "PLN")
LEI = ("TRANSFORMADA EM NORMA JURÍDICA", "TRANSFORMADA EM NORMA JURÍDICA COM VETO PARCIAL")
INICIO_LEGISLATURA = "2023-02-01"
DELIBERADA = ("Deliberação da matéria", "Matéria deliberada no plenário")
VALIDADE = timedelta(days=7)
# Orientação: o registro escreve alguns partidos por extenso; o voto do senador traz a sigla.
ORIENTA_ALIAS = {"Podemos": "PODE", "Republica": "REPUBLICANOS", "Republicanos": "REPUBLICANOS",
                 "Progressistas": "PP"}
NAO_BANCADA = {"Governo", "Oposição", "Maioria", "Minoria", "Banc Fem", "B.Feminina"}
LEG_MIN = 52           # 52ª legislatura = 2003–2007: o filtro de legislatura começa aqui (a Câmara só registra exercício desde então)
SUCESSAO = coleta.ENTRADAS / "partidos_sucessao.json"
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
    Devolve {sequencialVotacao: {sigla: 'SIM'|'NÃO'|'LIVRE'|…}}, o índice
    {(data, número do requerimento, ano): [sequenciais]} para o voto que vem sem sequencial e
    {sequencialVotacao: orientação do líder do Governo}."""
    hoje = datetime.now()
    por_seq, por_req, gov_seq = {}, {}, {}
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
            # o líder do Governo não é bancada de ninguém (fica fora de por_seq), mas a orientação dele é a medida do alinhamento
            for o in v.get("orientacoesLideranca") or []:
                if o.get("partido") == "Governo":
                    gov_seq[v["sequencialVotacao"]] = o.get("voto")
            if v.get("siglaTipoMateria") == "RQS":
                chave = ((v.get("dataInicioVotacao") or "")[:10], v.get("numeroMateria"), v.get("anoMateria"))
                por_req.setdefault(chave, []).append(v["sequencialVotacao"])
    return por_seq, por_req, gov_seq


def sequencial(v, por_req):
    if v.get("sequencialVotacao") is not None:
        return v["sequencialVotacao"]
    m = REQUERIMENTO.search(v.get("descricaoVotacao") or "")
    if not m:
        return None
    achados = por_req.get((v.get("dataSessao"), int(m.group(1).replace(".", "")), int(m.group(2))), [])
    return achados[0] if len(achados) == 1 else None


def lista_de(x):
    """A API devolve objeto quando há um item e lista quando há vários."""
    return x if isinstance(x, list) else ([] if x in (None, "") else [x])


def mandatos_de(cod, atualizar):
    """Os mandatos do senador (registro do Senado), em lista."""
    d = buscar(f"mandatos/{cod}.json", f"/senador/{cod}/mandatos", atualizar, timedelta(days=30))
    return lista_de((((d or {}).get("MandatoParlamentar") or {}).get("Parlamentar") or {}).get("Mandatos", {}).get("Mandato"))


def exercicios(cod, legs, atualizar):
    """{"57": [{"de", "ate", "como"}]}: em que períodos o senador esteve EM EXERCÍCIO, por legislatura, da LEG_MIN em diante.
    Fonte: Exercicios de cada mandato em /senador/{codigo}/mandatos (titular ou suplente que de fato exerceu; suplente que
    nunca assumiu não tem Exercicios). Cada exercício é cortado nas datas da legislatura; período aberto na legislatura em
    curso fica com "ate": null."""
    ms = mandatos_de(cod, atualizar)
    hoje = datetime.now().strftime("%Y-%m-%d")
    saida = {}
    for m in ms:
        for e in lista_de((m.get("Exercicios") or {}).get("Exercicio")):
            ini, fim = (e.get("DataInicio") or "")[:10], (e.get("DataFim") or "")[:10] or None
            if not ini:
                continue
            for l in legs:
                if l["id"] < LEG_MIN or ini > l["fim"] or (fim and fim < l["inicio"]):
                    continue
                de, ate = max(ini, l["inicio"]), min(fim or "9999-12-31", l["fim"])
                saida.setdefault(str(l["id"]), []).append(
                    {"de": de, "ate": None if ate >= hoje else ate, "como": m.get("DescricaoParticipacao")})
    return saida


def por_legislatura(meus, leis, rel, legs):
    """Os números de autoria e relatoria separados pela legislatura da DATA DE APRESENTAÇÃO (da designação, na relatoria):
    "virou lei na 55ª" = apresentada na 55ª e hoje é lei, como no filtro de mandato dos deputados. Confere a soma contra o
    total e devolve só da LEG_MIN em diante (o resto segue na carreira inteira)."""
    leg_de = lambda data: coleta.legislatura_de(data, legs)       # noqa: E731
    ident_lei = {id(x) for x in leis}
    conta = {}
    for x in meus:
        c = conta.setdefault(leg_de(x.get("dataApresentacao")), Counter())
        tipo = tipo_de(x["identificacao"])
        c["apresentadas"] += 1
        c["pecs"] += tipo == "PEC"
        c["leis"] += tipo == "PL"
        c["viraramLei"] += id(x) in ident_lei
    rels = {}
    for r in rel:
        l = leg_de((r.get("dataDesignacao") or "")[:10])
        g = rels.setdefault(l, {"processos": set(), "deliberadas": set(), "designacoes": 0})
        g["processos"].add(r.get("idProcesso"))
        g["designacoes"] += 1
        if r.get("descricaoTipoEncerramento") in DELIBERADA:
            g["deliberadas"].add(r.get("idProcesso"))
    if sum(c["apresentadas"] for c in conta.values()) != len(meus) or sum(c["viraramLei"] for c in conta.values()) != len(leis):
        raise SystemExit("a soma por legislatura não bate com o total da carreira")
    saida = {}
    for l in sorted({k for k in list(conta) + list(rels) if k is not None and k >= LEG_MIN}):
        c, g = conta.get(l, Counter()), rels.get(l, {"processos": (), "deliberadas": (), "designacoes": 0})
        saida[str(l)] = {"pecs": c["pecs"], "leis": c["leis"], "apresentadas": c["apresentadas"], "viraramLei": c["viraramLei"],
                         "relatorias": {"processos": len(g["processos"]), "deliberadas": len(g["deliberadas"]),
                                        "designacoes": g["designacoes"]}}
    return saida, sum(c["apresentadas"] for k, c in conta.items() if k is None)


def periodos(m):
    """[[início, fim ou None, causa]] dos exercícios de um mandato, em ordem. A causa é o texto do Senado ("Término do mandato",
    "Renúncia", "Retorno do titular", "Licença saúde"…); None enquanto o período está aberto."""
    saida = []
    for e in lista_de((m.get("Exercicios") or {}).get("Exercicio")):
        if e.get("DataInicio"):
            saida.append([e["DataInicio"][:10], (e.get("DataFim") or "")[:10] or None,
                          (e.get("DescricaoCausaAfastamento") or "").strip() or None])
    return sorted(saida, key=lambda x: (x[0], x[1] or "9999"))


def mandatos_detalhe(cod, legs, ros, atualizar):
    """Os mandatos do senador que alcançam a LEG_MIN em diante, com exercícios, suplentes e (se foi suplente) o titular."""
    inicio_min = next(l["inicio"] for l in legs if l["id"] == LEG_MIN)
    saida = []
    for m in mandatos_de(cod, atualizar):
        dados = m.get("SegundaLegislaturaDoMandato") or m.get("PrimeiraLegislaturaDoMandato") or {}
        fim = (dados.get("DataFim") or "")[:10]
        if not fim or fim < inicio_min:
            continue
        reg = {"id": m.get("CodigoMandato"), "uf": m.get("UfParlamentar"), "como": m.get("DescricaoParticipacao"),
               "de": ((m.get("PrimeiraLegislaturaDoMandato") or {}).get("DataInicio") or "")[:10] or None, "ate": fim,
               "exercicios": periodos(m)}
        if m.get("Titular"):
            reg["titular"] = [int(m["Titular"]["CodigoParlamentar"]), m["Titular"].get("NomeParlamentar")]
        if m.get("DescricaoParticipacao") == "Titular":
            sups = []
            for sp in lista_de((m.get("Suplentes") or {}).get("Suplente")):
                scod = int(sp["CodigoParlamentar"])
                # assumiu = o mesmo mandato no registro do suplente, com Exercicios; quem nunca exerceu não está em nenhuma lista
                assumiu = []
                if scod in ros:
                    for ms in mandatos_de(scod, atualizar):
                        if ms.get("CodigoMandato") == m.get("CodigoMandato"):
                            assumiu = periodos(ms)
                sups.append([(sp.get("DescricaoParticipacao") or "")[:2], scod, sp.get("NomeParlamentar"), assumiu])
            reg["suplentes"] = sups
        saida.append(reg)
    return sorted(saida, key=lambda r: r["de"] or "")


def saida_do_senado(mand):
    """{data, causa} do fim do último período em exercício; None se o último está aberto (ainda no cargo) ou não há período."""
    todos = [e for r in mand for e in r["exercicios"]]
    if not todos:
        return None
    ultimo = max(todos, key=lambda e: (e[1] or "9999-12-31", e[0]))
    return None if ultimo[1] is None else {"data": ultimo[1], "causa": ultimo[2]}


def filiacoes(cod, atualizar):
    """[(sigla, data da filiação, data da desfiliação ou None)] — o mesmo cache de autores_de_leis()."""
    f = buscar(f"filiacoes/{cod}.json", f"/senador/{cod}/filiacoes", atualizar, timedelta(days=30))
    fs = lista_de((((f or {}).get("FiliacaoParlamentar") or {}).get("Parlamentar") or {}).get("Filiacoes", {}).get("Filiacao"))
    return [((x.get("Partido") or {}).get("SiglaPartido"), (x.get("DataFiliacao") or "")[:10], (x.get("DataDesfiliacao") or "")[:10] or None)
            for x in fs]


def partido_em(fils, data):
    """O partido em que estava filiado na data (a filiação mais recente que a cobre), ou None."""
    ativas = [f for f in fils if f[1] and f[1] <= data and (not f[2] or f[2] >= data)]
    return max(ativas, key=lambda f: f[1])[0] if ativas else None


def nota_sem_posicao(sigla, ide):
    """Por que o partido não tem posição: a nota do ideologia.json ou, se nem consta, que a pesquisa não o mediu."""
    if ide.get("nota"):
        return ide["nota"]
    return "sem partido" if sigla in (None, "", "S/Partido") else f"{sigla} não está entre os partidos medidos pela pesquisa"


def posicao_do_partido(sigla, data, ideologia, sucessao):
    """{partido, score, ideX, ideologia, nota}: a posição do partido no BLS (2021), pelo MESMO partido sob outro nome quando a
    tabela de sucessão diz que é. Sem posição na pesquisa, ou sigla que não é o partido de hoje naquela data: sem posição."""
    if not sigla:
        return {"partido": None, "score": None, "ideX": None, "ideologia": None, "nota": "sem filiação registrada na data"}
    regra = sucessao.get(sigla) or {}
    if regra.get("desde") and data < regra["desde"]:
        return {"partido": sigla, "score": None, "ideX": None, "ideologia": None,
                "nota": f"{sigla} antes de {regra['desde'][8:10]}/{regra['desde'][5:7]}/{regra['desde'][:4]} não é o partido que a pesquisa mediu (era outro partido com essa sigla ou com outro nome)"}
    chave = regra.get("para", sigla)
    ide = ideologia.get(chave) or {}
    sc = ide.get("score")
    return {"partido": sigla, "score": sc, "ideX": round((sc - 1) / 9, 4) if sc is not None else None,
            "ideologia": coleta.classificar(sc) if sc is not None else None,
            "nota": None if sc is not None else nota_sem_posicao(sigla, ide)}


def posicoes(cod, legs, exerc, so_autoria, ideologia, sucessao, atualizar):
    """{legislatura: posição do partido que ele tinha ao entrar em exercício nela}, da LEG_MIN em diante."""
    fils = filiacoes(cod, atualizar)
    inicio = {str(l["id"]): l["inicio"] for l in legs}
    saida = {}
    for leg in sorted(set(exerc) | set(so_autoria), key=int):
        data = exerc[leg][0]["de"] if leg in exerc else inicio[leg]
        saida[leg] = posicao_do_partido(partido_em(fils, data), data, ideologia, sucessao)
    return saida


def roster(legs, atualizar):
    """{código: nome} de quem exerceu o mandato em alguma legislatura da LEG_MIN em diante (titular ou suplente que de fato
    exerceu: o mandato tem Exercicios). Uma chamada por legislatura; as encerradas não mudam."""
    saida = {}
    for l in legs:
        if l["id"] < LEG_MIN:
            continue
        validade = timedelta(days=7) if l["id"] == legs[-1]["id"] else timedelta(days=3650)
        d = buscar(f"legislatura/{l['id']}.json", f"/senador/lista/legislatura/{l['id']}", atualizar, validade)
        ps = lista_de(((d or {}).get("ListaParlamentarLegislatura") or {}).get("Parlamentares", {}).get("Parlamentar"))
        for pl in ps:
            if any(m.get("Exercicios") for m in lista_de((pl.get("Mandatos") or {}).get("Mandato"))):
                ident = pl["IdentificacaoParlamentar"]
                saida[int(ident["CodigoParlamentar"])] = ident.get("NomeParlamentar")
    return saida


def identidade_do_ex(cod, atualizar):
    """(idp, man) de quem já saiu, no formato que /senador/lista/atual dá aos de hoje: identificação do /senador/{codigo} e o
    último mandato com exercício (de onde vêm UF, participação e datas)."""
    d = buscar(f"detalhe/{cod}.json", f"/senador/{cod}", atualizar, timedelta(days=30))
    idp = dict((((d or {}).get("DetalheParlamentar") or {}).get("Parlamentar") or {}).get("IdentificacaoParlamentar") or {})
    ms = [m for m in mandatos_de(cod, atualizar) if m.get("Exercicios")]
    man = max(ms, key=lambda m: max((e.get("DataInicio") or "") for e in lista_de(m["Exercicios"].get("Exercicio"))), default={})
    idp["UfParlamentar"] = man.get("UfParlamentar")
    idp.setdefault("CodigoParlamentar", str(cod))
    return idp, man


def autor_principal(cod, p, atualizar):
    """É o primeiro autor? Um autor só no texto, sem mais ninguém: sim (a consulta foi pelo código dele). Vários — ou
    "Senador X e outros.", em que X é o PRIMEIRO e quem consultou pode ser só um dos que assinaram —: o de ordem 1 no
    detalhe do processo, pelo código. Medido em 5/10/2026: tratar "e outros" como autor único atribuía ao senador
    consultado PECs e PLs de outro senador (mais de um em cada cinco itens)."""
    autoria = p.get("autoria") or ""
    if len(re.findall(r"\bSenador(?:a)?\s", autoria)) <= 1 and "," not in autoria and "outros" not in autoria.lower():
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
    ap.add_argument("--minutos", type=float, default=25,
                    help="tempo máximo na coleta dos senadores que já saíram; a rodada seguinte continua de onde parou")
    ap.add_argument("--sem-antigos", action="store_true", help="só os senadores de hoje")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    lista = (pedir("/senador/lista/atual") or {}).get("ListaParlamentarEmExercicio", {}) \
        .get("Parlamentares", {}).get("Parlamentar", [])
    if len(lista) < 75:
        raise SystemExit(f"Lista de senadores com {len(lista)} nomes — conferir a API antes de gravar.")
    ideologia = {k: v for k, v in json.loads(coleta.IDEOLOGIA.read_text(encoding="utf-8")).items()
                 if not k.startswith("_")}
    sucessao = {k: v for k, v in json.loads(SUCESSAO.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    print(f"{len(lista)} senadores em exercício", flush=True)
    ori_seq, ori_req, gov_seq = orientacoes(args.atualizar)
    print(f"Orientações de bancada registradas: {len(ori_seq)} votações "
          f"({sum(1 for o in ori_seq.values() if o)} com orientação de algum partido; "
          f"{sum(1 for g in gov_seq.values() if g in ('SIM', 'NÃO'))} com orientação SIM ou NÃO do Governo)", flush=True)
    motivos_ori = Counter()
    legs = coleta.legislaturas()
    sem_leg = [0]
    votacoes = {}
    atual_leg = str(legs[-1]["id"])
    ros = roster(legs, args.atualizar)       # quem exerceu desde a LEG_MIN: os de hoje, os que saíram e os suplentes que assumiram

    def coletar(cod, idp, man, validade, com_votos):
        """Um senador, do mesmo jeito para quem está e para quem já saiu. `validade`: o cache de quem saiu vale 30 dias (o que
        muda, ali, é só um projeto antigo virar lei); `com_votos`: só quem exerceu desde 2023 tem voto para ler."""
        procs = buscar(f"autorias/{cod}.json", "/processo", args.atualizar, validade, codigoParlamentarAutor=cod)
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
        n_tipo = Counter(tipo_de(x["identificacao"]) for x in meus)
        leis = [x for x in meus if (x.get("situacaoAtual") or "") in LEI]
        nesta = [x for x in meus if (x.get("dataApresentacao") or "") >= INICIO_LEGISLATURA]

        rel = buscar(f"relatorias/{cod}.json", "/processo/relatoria", args.atualizar, validade, codigoParlamentar=cod)
        # "SUG 5/2022", "PL 2036/2023": só proposições legislativas (sugestão, requerimento, ofício não)
        rel = [r for r in rel if (r.get("identificacaoProcesso") or " ").split(" ")[0] in TIPOS_RELATORIA]
        processos = {r.get("idProcesso") for r in rel}
        deliberadas = {r.get("idProcesso") for r in rel if r.get("descricaoTipoEncerramento") in DELIBERADA}

        vt = buscar(f"votos/{cod}.json", "/votacao", args.atualizar, validade, codigoParlamentar=cod) if com_votos else []
        conta, dif, gov = Counter(), Counter(), Counter()
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
                g = gov_seq.get(seq) if seq is not None else None
                if g in ("SIM", "NÃO"):           # orientação LIVRE ou ausente do líder do Governo não conta
                    gov["comparaveis"] += 1
                    gov["alinhados"] += (sig == "Sim") == (g == "SIM")
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

        por_leg, fora = por_legislatura(meus, leis, rel, legs)
        sem_leg[0] += fora
        # O registro de mandatos do Senado às vezes não traz o exercício de suplente (Wilder Morais: senador de 2012 a 2019,
        # mas /mandatos só mostra o mandato de 2023). Quando há PEC ou PL apresentado COMO SENADOR numa legislatura sem exercício
        # registrado, isso entra à parte, como indício — nunca inventando um período. "Como senador" = o texto da autoria
        # nomeia senador: projeto da Câmara de quem era deputado ("Câmara dos Deputados") não vale.
        exerc = exercicios(cod, legs, args.atualizar)
        mand_det = mandatos_detalhe(cod, legs, ros, args.atualizar)
        como_senador = {coleta.legislatura_de(x.get("dataApresentacao"), legs) for x in candidatos
                        if re.search(r"\bSenador(?:a)?\s", x.get("autoria") or "")}
        so_autoria = sorted(str(l) for l in como_senador if l is not None and l >= LEG_MIN and str(l) not in exerc)
        partido = idp.get("SiglaPartidoParlamentar")
        ide = ideologia.get(partido) or {}
        score = ide.get("score")
        return {
            "id": int(cod), "nome": idp.get("NomeParlamentar"), "partido": partido,
            "uf": idp.get("UfParlamentar"), "urlFoto": idp.get("UrlFotoParlamentar"),
            "url": idp.get("UrlPaginaParlamentar"), "participacao": man.get("DescricaoParticipacao"),
            "mandato": [(man.get("PrimeiraLegislaturaDoMandato") or {}).get("DataInicio"),
                        (man.get("SegundaLegislaturaDoMandato") or man.get("PrimeiraLegislaturaDoMandato") or {}).get("DataFim")],
            "score": score, "ideologia": coleta.classificar(score) if score is not None else None,
            "ideX": round((score - 1) / 9, 4) if score is not None else None,
            "scoreNota": None if score is not None else nota_sem_posicao(partido, ide),
            "pecs": n_tipo["PEC"], "leis": n_tipo["PL"], "apresentadas": len(meus), "viraramLei": len(leis),
            "mandatos": exerc, "exercicioPorAutoria": so_autoria, "porLegislatura": por_leg,
            "posicao": posicoes(cod, legs, exerc, so_autoria, ideologia, sucessao, args.atualizar),
            "mandatosDetalhe": mand_det, "saida": saida_do_senado(mand_det),
            "nestaLegislatura": {"apresentadas": len(nesta),
                                 "viraramLei": sum(1 for x in nesta if (x.get("situacaoAtual") or "") in LEI)},
            "relatorias": {"processos": len(processos), "deliberadas": len(deliberadas), "designacoes": len(rel)},
            "votos": ({"votacoes": conta["votacoes"], "sim": conta["sim"], "nao": conta["nao"],
                       "abstencao": conta["abstencao"],
                       "outros": {k[4:]: v for k, v in conta.items() if k.startswith("reg:")}}
                      if conta else None),
            "difOrientacao": (None if not com_votos else {"comparaveis": dif["comparaveis"], "diferentes": dif["diferentes"]}
                              if dif["comparaveis"] >= MIN_ORIENTACAO else
                              {"comparaveis": dif["comparaveis"], "diferentes": None,
                               "nota": f"menos de {MIN_ORIENTACAO} votações em que o partido orientou Sim ou Não"
                                       if dif["comparaveis"] else "o partido não orientou Sim ou Não em nenhuma votação em que votou"}),
            "governo": (None if not com_votos else {"comparaveis": gov["comparaveis"], "alinhados": gov["alinhados"]}
                        if gov["comparaveis"] >= MIN_ORIENTACAO else
                        {"comparaveis": gov["comparaveis"], "alinhados": None,
                         "nota": f"menos de {MIN_ORIENTACAO} votações em que o líder do Governo orientou Sim ou Não"
                                 if gov["comparaveis"] else "o líder do Governo não orientou Sim ou Não em nenhuma votação em que votou"}),
            "leisLista": [[x["identificacao"], (x.get("ementa") or "")[:220], x.get("dataApresentacao"),
                           x.get("situacaoAtual"), x.get("codigoMateria")] for x in sorted(leis, key=lambda x: x.get("dataApresentacao") or "", reverse=True)[:12]],
        }

    saida = []
    for n, p in enumerate(lista, 1):
        if n % 10 == 0:
            print(f"   {n}/{len(lista)}", flush=True)
        saida.append(coletar(p["IdentificacaoParlamentar"]["CodigoParlamentar"], p["IdentificacaoParlamentar"],
                             p.get("Mandato") or {}, VALIDADE, True))

    # checagem ANTES de gravar: os 81 de hoje têm de estar em exercício na legislatura atual
    por = Counter(l for s_ in saida for l in s_["mandatos"])
    n_indicio = sum(len(s_["exercicioPorAutoria"]) for s_ in saida)
    if por[atual_leg] < len(saida) * 0.95:
        raise SystemExit(f"só {por[atual_leg]} de {len(saida)} senadores com exercício na {atual_leg}ª legislatura: conferir /mandatos")
    gravar(SAIDA, sorted(saida, key=lambda s: s["nome"]))

    com = [s for s in saida if s["score"] is not None]
    print(f"\n{len(saida)} senadores · com score: {len(com)} · sem: "
          f"{Counter(s['partido'] for s in saida if s['score'] is None)}")
    print(f"PEC+PL de autoria: mediana {median(s['apresentadas'] for s in saida)} · "
          f"viraram lei: {sum(s['viraramLei'] for s in saida)} (somas por senador)")
    rp = sorted(s["relatorias"]["processos"] for s in saida)
    print(f"Relatorias (processos distintos): mediana {median(rp)} · máx {rp[-1]}")
    print(f"Votações nominais do Plenário desde {INICIO_LEGISLATURA} com voto de senador atual: {len(votacoes)}")
    print(f"Voto × orientação da própria bancada: {dict(motivos_ori.most_common())}")
    tx = sorted(s["difOrientacao"]["diferentes"] / s["difOrientacao"]["comparaveis"]
                for s in saida if s["difOrientacao"]["diferentes"] is not None)
    if tx:
        print(f"Votou diferente da orientação da própria bancada: {len(tx)} senadores com número · "
              f"mediana {median(tx):.1%} · p90 {tx[int(len(tx) * .9)]:.1%} · máx {tx[-1]:.1%}")
    print(f"{SAIDA.name} gravado ({SAIDA.stat().st_size // 1024} kB).")
    print("Em exercício por legislatura (entre os senadores de hoje): "
          + " · ".join(f"{l}ª {por[l]}" for l in sorted(por, key=int)) + (f" · proposições sem legislatura: {sem_leg[0]}" if sem_leg[0] else "")
          + f" · legislaturas só com indício por autoria: {n_indicio}")

    if not args.sem_antigos:
        antigos_de_ontem(args, legs, saida, coletar, atual_leg, ros)
    autores_de_leis(args.atualizar)


def gravar(caminho, registros):
    """Um senador por linha, como os outros arquivos; escrita atômica."""
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in registros) + "\n]\n",
                   encoding="utf-8")
    os.replace(tmp, caminho)


def antigos_de_ontem(args, legs, atuais, coletar, atual_leg, ros):
    """Quem já saiu: todo senador que exerceu o mandato desde a LEG_MIN e não está entre os de hoje. O arquivo só é regravado
    quando todos estão prontos — um arquivo com metade dos senadores de uma legislatura seria um número errado, não um parcial."""
    hoje = {s["id"] for s in atuais}
    cods = sorted(c for c in ros if c not in hoje)
    print(f"\nSenadores que já saíram (exerceram desde a {LEG_MIN}ª legislatura): {len(cods)} de {len(ros)}", flush=True)
    t0, feitos, antes_de_2003 = time.monotonic(), [], 0
    for i, cod in enumerate(cods, 1):
        if (time.monotonic() - t0) / 60 > args.minutos:
            print(f"   tempo da rodada esgotado em {i - 1}/{len(cods)}: o cache guarda o que já foi; a próxima rodada continua "
                  f"e o {ANTIGOS.name} só é regravado com todos prontos")
            return
        exerc = exercicios(cod, legs, args.atualizar)
        if not exerc:      # a lista de uma legislatura traz o suplente do mandato que a cobre, mesmo que só tenha exercido antes dela
            antes_de_2003 += 1
            continue
        idp, man = identidade_do_ex(cod, args.atualizar)
        feitos.append(coletar(str(cod), idp, man, timedelta(days=30), bool(exerc.get(atual_leg))))
        if i % 20 == 0:
            print(f"   {i}/{len(cods)} ({(time.monotonic() - t0) / 60:.0f} min)", flush=True)
    gravar(ANTIGOS, sorted(feitos, key=lambda s: s["nome"]))
    por = Counter(l for s_ in feitos for l in set(s_["mandatos"]) | set(s_["exercicioPorAutoria"]))
    sem = Counter(po["partido"] or "sem filiação" for s_ in feitos for po in s_["posicao"].values() if po["score"] is None)
    print(f"Fora: {antes_de_2003} que só exerceram antes de 2003 (suplentes de mandato que cobre a {LEG_MIN}ª legislatura)")
    causas = Counter((s_["saida"] or {}).get("causa") or "sem saída registrada" for s_ in feitos)
    print(f"Como saíram (último período em exercício): {dict(causas.most_common())}")
    sup = [x for s_ in feitos + atuais for r in s_["mandatosDetalhe"] for x in r.get("suplentes", [])]
    print(f"Suplentes listados nos mandatos: {len(sup)} · assumiram: {sum(1 for x in sup if x[3])}")
    print(f"{ANTIGOS.name} gravado ({ANTIGOS.stat().st_size // 1024} kB): {len(feitos)} senadores · por legislatura "
          + " · ".join(f"{l}ª {por[l]}" for l in sorted(por, key=int)))
    print(f"Posição (senador × legislatura) sem score, por partido: {dict(sem.most_common(12))}")


if __name__ == "__main__":
    main()
