"""
Câmara Aberta — página "Presidente" (site/dados/presidencia.json): o mandato atual, as medidas provisórias editadas nele e os vetos, com a
situação registrada pelo Congresso.

Uso:
    python scripts/presidencia.py                # --dry-run (o padrão): candidato, relatório e amostra em cache/presidencia/
    python scripts/presidencia.py --publicar     # grava site/dados/presidencia.json, só se TODAS as checagens passarem
    python scripts/presidencia.py --so-montar    # sem rede: remonta do cache
    python scripts/presidencia.py --teste        # casos sintéticos, sem rede
    --minutos N (padrão 10): passado o prazo, nenhum pedido novo é feito e a rodada para sem publicar

Fontes (Etapa 1, capturas/diagnostico_presidencia.md):
  * Mandato: entradas/governos.json (o governo cujo período contém hoje). O que o arquivo não tem fica "—" na página.
  * MPs: Dados Abertos do Senado Federal e do Congresso Nacional, GET /dadosabertos/processo?sigla=MPV&ano={ano} (situação, deliberação,
    lei gerada) e GET /dadosabertos/processo/{id} (a lei estruturada: número, data, código da norma), só para as aprovadas.
  * Conferência: API da Câmara, GET /api/v2/proposicoes?siglaTipo=MPV&ano={ano} (a mesma contagem por ano; dá também o id da ficha na Câmara).
  * Vetos: GET /dadosabertos/materia/vetos/{ano} (a lista do ano, numerada de 1 a N sem buraco), /materia/vetos/aposrcn e /encerrados (dois
    serviços à parte que dizem quais estão em tramitação: conferência do campo EmTramitacao), /plenario/resultado/veto/{código do veto}
    (a situação de cada dispositivo) e /plenario/resultado/veto/dispositivo/{código do dispositivo} (os votos, só em veto parcial).

Regras (o que a página promete):
  * A situação é a do campo oficial (`situacaoAtual`). Vazio: vale a deliberação oficial (`siglaTipoDeliberacao`). Os dois vazios: "—", com o
    motivo. Valor que este código não conhece: a rodada PARA (um estado novo da fonte nunca vira outro por palpite).
  * Perda de eficácia NÃO é rejeição: é o prazo constitucional (art. 62, § 3º) que terminou sem votação final.
  * Erro da fonte nunca vira "sem MPs" nem "em tramitação": lista vazia, resposta fora do formato, HTTP de erro, contagem que não bate com a
    Câmara ou queda/subida de mais de 10% contra o publicado param a rodada, e o arquivo publicado fica como estava.
  * MP "convertida" sem a lei registrada fica RETIDA (fora da lista e das contagens, listada com o motivo).
  * Nada de ranking, taxa, "derrota" ou "vitória": só contagens por situação, cada uma com a definição.
  * Veto: o rótulo sai dos dispositivos (qualquer um "Não Apreciado" = em tramitação; todos mantidos = mantido; todos rejeitados = derrubado;
    os dois = derrubado em parte; só prejudicados = prejudicado; prejudicados ao lado dos outros contam à parte). Tem de bater com o campo
    EmTramitacao e com as listas de em tramitação e encerrados; não bateu, o veto fica RETIDO, listado com o motivo.
  * Placar (só dispositivo de veto parcial: o veto total não tem placar nos dados abertos, só no PDF do resultado, que não é lido): Sim =
    manter, Não = derrubar. Derrubar exige 257 deputados E 41 senadores votando Não (Constituição, art. 66, § 4º). Dispositivo "rejeitado"
    abaixo disso, ou "mantido" com os dois números alcançados, ou votação que a fonte devolve para OUTRO dispositivo (o código do veto e o do
    dispositivo são numerações diferentes que se cruzam): o veto fica RETIDO, com o motivo.
"""

import argparse
import email.utils
import json
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import coleta

VERSAO = "presidencia-2"
SAIDA = coleta.DADOS / "presidencia.json"
CACHE_P = coleta.CACHE / "presidencia"
CANDIDATO = CACHE_P / "candidato.json"
RELATORIO = CACHE_P / "relatorio.txt"
AMOSTRA = coleta.CACHE / "amostra_presidencia.csv"
GOVERNOS = coleta.ENTRADAS / "governos.json"
ESQUEMA = coleta.BASE / "schemas" / "presidencia.schema.json"
SENADO = "https://legis.senado.leg.br/dadosabertos"
CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
UA = "CamaraAberta/1.0 presidencia (https://github.com/paulopottermarchi/Senado_Move)"
BRASILIA = timezone(timedelta(hours=-3))
VALIDADE = timedelta(days=1)            # listas e MPs abertas
VALIDADE_FECHADA = timedelta(days=30)   # detalhe de MP já encerrada (a lei gerada não muda)
VARIACAO = 0.10                         # mais ou menos que isto de MPs contra o publicado: falha de fonte, a rodada para
INTERVALO = 0.5                         # segundos entre o COMEÇO de dois pedidos, somando todas as threads: no máximo 2 por segundo
ESPERA_MAX = 300                        # teto de uma espera pedida pela fonte (Retry-After), em segundos
PRAZO = [None]
URLS = {"congresso": "https://www.congressonacional.leg.br/materias/medidas-provisorias/-/mpv/{codigoMateria}",
        "api": SENADO + "/processo/{id}",
        "camara": "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={camaraId}",
        "lei": "https://legis.senado.leg.br/norma/{codigo}",
        "veto": "https://www.congressonacional.leg.br/materias/vetos/-/veto/detalhe/{codigo}",
        "vetoApi": SENADO + "/plenario/resultado/veto/{codigo}"}
VOTOS_NECESSARIOS = {"camara": 257, "senado": 41}   # maioria absoluta de cada Casa (CF, art. 66, § 4º): 513 deputados e 81 senadores
VALIDADE_VOTOS = timedelta(days=365)               # a votação de um dispositivo já apurado não muda
PARALELO = 3                                       # resultados de veto e votos de dispositivo: até 3 em voo, no mesmo ritmo de 2 por segundo

# Categorias da página, na ordem em que aparecem; cada uma com a definição que vai ao lado do número.
SITUACOES = [
    ("convertida", "Convertida em lei",
     "O Congresso aprovou a MP, na íntegra ou na forma de um projeto de lei de conversão (com mudanças no texto), e ela virou lei. O número da lei é o registrado pelo Congresso."),
    ("aprovada_sem_lei", "Aprovada, sem lei registrada",
     "O Congresso aprovou a MP e a fonte ainda não registra a lei. A sanção cabe ao Presidente."),
    ("perdeu_eficacia", "Perdeu a eficácia",
     "O prazo para o Congresso votar a MP (60 dias, prorrogáveis por mais 60; Constituição, art. 62, § 3º) terminou sem votação final. Não é rejeição: "
     "a MP deixa de valer porque não foi votada a tempo."),
    ("revogada", "Revogada", "A MP foi revogada antes da conclusão da votação no Congresso, segundo o registro do Congresso."),
    ("rejeitada", "Rejeitada", "A Câmara ou o Senado votou e rejeitou a MP."),
    ("prejudicada", "Prejudicada", "O Congresso declarou a MP prejudicada (sem objeto), segundo o registro."),
    ("em_tramitacao", "Em tramitação",
     "A MP ainda está em análise no Congresso (comissão mista, Câmara ou Senado). A situação oficial de cada uma aparece na lista."),
    ("sem_situacao", "—", "A fonte não traz a situação nem a deliberação desta MP."),
]
CATEGORIAS = [c for c, _, _ in SITUACOES]
# situação oficial (situacaoAtual, como o Congresso grava) de MP encerrada → categoria
POR_SITUACAO = {"TRANSFORMADA EM NORMA JURÍDICA": "convertida", "TRANSFORMADA EM NORMA JURÍDICA COM VETO PARCIAL": "convertida",
                "SEM EFICÁCIA": "perdeu_eficacia", "REVOGADA": "revogada", "REJEITADA": "rejeitada", "REJEITADA A MEDIDA PROVISÓRIA": "rejeitada",
                "PREJUDICADA": "prejudicada", "REMETIDA À SANÇÃO": "aprovada_sem_lei", "APROVADA": "aprovada_sem_lei",
                "APROVADA A MEDIDA PROVISÓRIA": "aprovada_sem_lei", "APROVADO O PROJETO DE LEI DE CONVERSÃO": "aprovada_sem_lei"}
# deliberação oficial (siglaTipoDeliberacao, de /processo/tipos-decisao) → categoria; só vale quando a situação vem vazia
POR_DELIBERACAO = {"PERDA_EFICACIA": "perdeu_eficacia", "SEM_EFICACIA": "perdeu_eficacia", "REVOGADO": "revogada", "PREJUDICADO": "prejudicada",
                   "REJEITADO_PLENARIO": "rejeitada", "REJEITADO_PLENARIO_CD": "rejeitada",
                   "APROVADO_NA_INTEGRA": "aprovada_sem_lei", "APROVADO_PLV": "aprovada_sem_lei"}
# descrição oficial de cada deliberação que este código reconhece, copiada de GET /dadosabertos/processo/tipos-decisao (7/11/2026)
DELIBERACOES = {"PERDA_EFICACIA": "Perda de eficácia, em decorrência do término do prazo para sua votação no Congresso",
                "SEM_EFICACIA": "Sem eficácia (art. 48, II e XI, do RISF)", "REVOGADO": "Revogada", "PREJUDICADO": "Prejudicada",
                "REJEITADO_PLENARIO": "Rejeitada pelo Plenário", "REJEITADO_PLENARIO_CD": "Rejeitada pelo Plenário da Câmara dos Deputados",
                "APROVADO_NA_INTEGRA": "Aprovada na íntegra", "APROVADO_PLV": "Aprovada na forma de Projeto de Lei de Conversão"}
assert set(DELIBERACOES) == set(POR_DELIBERACAO)
# Rótulo do veto (sai dos dispositivos), na ordem da página; cada um com a definição que vai ao lado do número.
ROTULOS_VETO = [
    ("em_tramitacao", "Em tramitação",
     "Pelo menos uma parte vetada (dispositivo) ainda não foi votada pelo Congresso. Ao lado de cada veto, quantos dispositivos já foram "
     "decididos e quantos faltam."),
    ("mantido", "Mantido", "O Congresso votou e manteve o veto: a parte vetada continua fora da lei."),
    ("derrubado_em_parte", "Derrubado em parte",
     "O Congresso derrubou parte dos dispositivos (o registro oficial diz “rejeitado”) e manteve os outros. As partes derrubadas vão à promulgação."),
    ("derrubado", "Derrubado",
     "O Congresso derrubou o veto (o registro oficial diz “rejeitado”): a parte vetada vai à promulgação e passa a valer. Derrubar exige a "
     "maioria absoluta de cada Casa, votando em separado: 257 deputados e 41 senadores (Constituição, art. 66, § 4º)."),
    ("prejudicado", "Prejudicado", "Todos os dispositivos do veto foram declarados prejudicados, sem votação, segundo o registro do Congresso."),
]
ROTULOS_IDS = [c for c, _, _ in ROTULOS_VETO]
# Situação de cada dispositivo, como o Congresso grava → id da página (contada só nos vetos parciais: o veto total é 1 veto, não 1 dispositivo)
SITUACOES_DISPOSITIVO = [
    ("nao_apreciado", "Não apreciado", "Ainda não votado pelo Congresso."),
    ("mantido", "Mantido", "Votado; o veto a este dispositivo continua."),
    ("rejeitado", "Derrubado (rejeitado)", "Votado; o veto a este dispositivo caiu, e o texto vai à promulgação."),
    ("prejudicado", "Prejudicado", "Declarado prejudicado, sem votação, segundo o registro."),
]
DISP_IDS = [c for c, _, _ in SITUACOES_DISPOSITIVO]
POR_SITUACAO_DISP = {"Não Apreciado": "nao_apreciado", "Mantido": "mantido", "Rejeitado": "rejeitado", "Prejudicado": "prejudicado"}
# tipo de voto (TipoVoto, como o Congresso grava) → posição no placar [sim, não, abstenção, em branco], as colunas do resultado oficial.
# Sim = manter o veto; Não = derrubar. "Art. 17" é o registro de quem preside a sessão, que não vota (Regimento Comum): fica fora do placar,
# como na Câmara (votos.py). Vocabulário medido nos 1.201 dispositivos votados do mandato (8/11/2026); valor novo para a rodada.
VOTOS = {"Sim": 0, "Não": 1, "Abstenção": 2, "Branco": 3}
FORA_DO_PLACAR = {"Art. 17"}
CAMPOS_DISPOSITIVO = ["identificador", "descricao", "situacao", "dataSessao", "tipoVotacao", "camara", "senado"]
# palavras que julgam ou acusam: nunca em texto da saída nem da página (o teste lê presidente.html inteiro). "parado" com fronteira de
# palavra, porque "separado" (votar em separado) é texto da página.
PROIBIDAS = re.compile(r"derrot|vit[oó]ri|culp|governista|fracass|ranking|\batras(?:o|os|ad[oa]s?)\b|\bdemor|\bparad[oa]s?\b", re.I)
TEXTO_PAGINA = coleta.BASE / "site" / "presidente.html"


class FonteFalhou(RuntimeError):
    """A fonte não respondeu como devia: nada é publicado."""


class Prazo(FonteFalhou):
    pass


def agora():
    return datetime.now(BRASILIA)


# ---------------------------------------------------------------- rede e cache

_sessao = None


def sessao():
    global _sessao
    if _sessao is None:
        _sessao = coleta.requests.Session()
        _sessao.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return _sessao


def pedir(url, params=None, tentativas=3, intervalo=None, aceitar_404=False):
    """JSON da fonte, ou FonteFalhou com o motivo da última tentativa (nunca devolve vazio no lugar de erro). Com aceitar_404, um 404 devolve
    None: quem chama decide o que ele significa (e nunca o grava no cache)."""
    ultimo = "sem resposta"
    for n in range(tentativas):
        if PRAZO[0] is not None and time.monotonic() > PRAZO[0]:
            raise Prazo(f"prazo da rodada esgotado antes de pedir {url}")
        no_ritmo(INTERVALO if intervalo is None else intervalo)
        try:
            r = sessao().get(url, params=params, timeout=(10, 60))
        except (coleta.requests.ConnectionError, coleta.requests.Timeout) as e:
            ultimo = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(2 ** n)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ultimo = coleta.motivo_http(r)
            espera = espera_pedida(r, 2 ** n)
            if PRAZO[0] is not None and time.monotonic() + espera > PRAZO[0]:
                raise Prazo(f"{url}: a fonte pediu {espera:.0f} s de espera (Retry-After) e o prazo da rodada acaba antes ({ultimo})")
            time.sleep(espera)
            continue
        if r.status_code == 404 and aceitar_404:
            return None
        if r.status_code != 200:
            raise FonteFalhou(f"{url} {params or ''}: {coleta.motivo_http(r)}")
        try:
            return r.json()
        except ValueError:
            ultimo = f"HTTP 200 sem JSON ({r.headers.get('content-type') or 'sem tipo'})"
            time.sleep(2 ** n)
    raise FonteFalhou(f"{url} {params or ''}: falhou após {tentativas} tentativas ({ultimo})")


def espera_pedida(r, padrao):
    """Segundos de espera que a fonte pede no Retry-After (número ou data HTTP), com teto de ESPERA_MAX; sem o cabeçalho, `padrao`."""
    v = (r.headers.get("Retry-After") or "").strip()
    if v.isdigit():
        return min(float(v), ESPERA_MAX)
    try:
        quando = email.utils.parsedate_to_datetime(v)
        return max(0.0, min((quando - datetime.now(timezone.utc)).total_seconds(), ESPERA_MAX))
    except (TypeError, ValueError, IndexError):
        return padrao


_ritmo = threading.Lock()
_proximo = [0.0]


def no_ritmo(intervalo=None):
    """Espaça o COMEÇO de todo pedido, de qualquer thread e inclusive as novas tentativas: no máximo 1 a cada `intervalo` segundos."""
    intervalo = INTERVALO if intervalo is None else intervalo
    with _ritmo:
        agora_ = time.monotonic()
        espera = _proximo[0] - agora_
        _proximo[0] = max(agora_, _proximo[0]) + intervalo
    if espera > 0:
        time.sleep(espera)


def em_paralelo(funcao, itens):
    """funcao(x) para cada x, até PARALELO de cada vez, na ordem dos itens. Uma falha cancela o que não começou e sobe (nada pela metade)."""
    ex = ThreadPoolExecutor(PARALELO)
    try:
        futuros = [ex.submit(funcao, x) for x in itens]
        return [f.result() for f in futuros]
    except BaseException:
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        ex.shutdown(wait=True)


def cache_ler(nome, validade, so_montar):
    p = CACHE_P / nome
    c = coleta.ler_cache(p)
    if c is None:
        if so_montar:
            raise FonteFalhou(f"--so-montar sem {nome} no cache: rode sem --so-montar antes")
        return None
    if so_montar or agora() - datetime.fromisoformat(c["_baixadoEm"]) < validade:
        return c["dados"]
    return None


def cache_gravar(nome, dados):
    coleta.gravar_texto(CACHE_P / nome, json.dumps({"_baixadoEm": agora().isoformat(timespec="seconds"), "dados": dados}, ensure_ascii=False))


def lista_senado(ano, so_montar):
    """As MPs de um ano na fonte do Congresso, conferidas uma a uma (formato e identificação)."""
    nome = f"mpv_senado_{ano}.json"
    dados = cache_ler(nome, VALIDADE, so_montar)
    if dados is None:
        dados = pedir(SENADO + "/processo", {"sigla": "MPV", "ano": ano})
        conferir_lista_senado(dados, ano)
        cache_gravar(nome, dados)
    conferir_lista_senado(dados, ano)
    return dados


def conferir_lista_senado(dados, ano):
    if not isinstance(dados, list):
        raise FonteFalhou(f"Congresso, MPV {ano}: a resposta não é uma lista ({type(dados).__name__})")
    for m in dados:
        if not isinstance(m, dict) or not re.fullmatch(rf"MPV \d+/{ano}", str(m.get("identificacao") or "")) or not isinstance(m.get("id"), int) \
                or not isinstance(m.get("codigoMateria"), int) or m.get("tramitando") not in ("Sim", "Não"):
            raise FonteFalhou(f"Congresso, MPV {ano}: registro fora do formato: {json.dumps(m, ensure_ascii=False)[:200]}")
    ids = [m["identificacao"] for m in dados]
    if len(ids) != len(set(ids)):
        raise FonteFalhou(f"Congresso, MPV {ano}: MP repetida na lista ({[k for k, n in Counter(ids).items() if n > 1][:5]})")


def lista_camara(ano, so_montar):
    """{número: id} das MPs de um ano na Câmara, seguindo as páginas (link `next`)."""
    nome = f"mpv_camara_{ano}.json"
    dados = cache_ler(nome, VALIDADE, so_montar)
    if dados is None:
        dados, url, params = [], CAMARA + "/proposicoes", {"siglaTipo": "MPV", "ano": ano, "itens": 100, "ordem": "ASC", "ordenarPor": "id"}
        while url:
            j = pedir(url, params)
            if not isinstance(j, dict) or not isinstance(j.get("dados"), list):
                raise FonteFalhou(f"Câmara, MPV {ano}: resposta sem `dados`")
            dados += [{"id": x["id"], "numero": x["numero"], "ano": x["ano"]} for x in j["dados"]]
            url = next((l["href"] for l in j.get("links") or [] if l.get("rel") == "next"), None)
            params = None
        cache_gravar(nome, dados)
    return dados


def detalhe(m, so_montar):
    """O processo completo de uma MP (só para as aprovadas: é onde está a lei estruturada).
    Validade: o detalhe que JÁ traz a lei vale 30 dias (a lei registrada não muda); o que ainda não a traz (aprovada, aguardando a sanção) vale
    1 dia, para a MP passar a "convertida" na rodada seguinte ao registro da lei. Com 30 dias para todos, uma MP sancionada ficaria retida
    ("a fonte diz que virou lei, mas não registra a lei") até o cache vencer."""
    nome = f"detalhe/{m['id']}.json"
    d = cache_ler(nome, VALIDADE_FECHADA, so_montar)
    if d is not None and not so_montar and lei_do_detalhe(d) is None:
        d = cache_ler(nome, VALIDADE, so_montar)
    if d is None:
        d = pedir(SENADO + f"/processo/{m['id']}")
        if not isinstance(d, dict) or d.get("id") != m["id"]:
            raise FonteFalhou(f"Congresso, processo {m['id']} ({m['identificacao']}): detalhe fora do formato")
        cache_gravar(nome, d)
    return d


def registros_veto(resposta):
    """Os registros de veto de uma resposta de /materia/vetos/… (dict com Codigo, Materia e MateriaVetada), onde estiverem no envelope."""
    out = []

    def anda(o):
        if isinstance(o, dict):
            if "Codigo" in o and isinstance(o.get("Materia"), dict) and isinstance(o.get("MateriaVetada"), dict):
                out.append(o)
                return
            for x in o.values():
                anda(x)
        elif isinstance(o, list):
            for x in o:
                anda(x)
    anda(resposta)
    return out


def conferir_lista_vetos(dados, ano, hoje):
    """Formato de cada registro e numeração de 1 a N sem buraco nem repetição (medido nos quatro anos do mandato: contínua)."""
    if not dados and ano < hoje.year:
        raise FonteFalhou(f"Congresso, vetos de {ano}: ano fechado sem nenhum veto na fonte")
    for v in dados:
        m, mv = v.get("Materia") or {}, v.get("MateriaVetada") or {}
        if not (str(v.get("Codigo") or "").isdigit() and str(m.get("Codigo") or "").isdigit() and m.get("Sigla") == "VET"
                and str(m.get("Ano")) == str(ano) and str(m.get("Numero") or "").isdigit() and v.get("Total") in ("Sim", "Não")
                and m.get("EmTramitacao") in ("Sim", "Não") and mv.get("Sigla") and str(mv.get("Numero") or "").isdigit()
                and str(mv.get("Ano") or "").isdigit() and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", str(v.get("DataPublicacao") or ""))):
            raise FonteFalhou(f"Congresso, vetos de {ano}: registro fora do formato: {json.dumps(v, ensure_ascii=False)[:200]}")
    nums = sorted(int(v["Materia"]["Numero"]) for v in dados)
    if nums != list(range(1, len(nums) + 1)):
        faltam = sorted(set(range(1, (nums[-1] if nums else 0) + 1)) - set(nums))
        repetidos = sorted(n for n, c in Counter(nums).items() if c > 1)
        raise FonteFalhou(f"Congresso, vetos de {ano}: numeração com buraco ou repetição (faltam {faltam[:10]}, repetidos {repetidos[:10]})")


def lista_vetos(ano, so_montar, hoje):
    """Os vetos de um ano. 404 só é aceito no ano corrente (começo de ano sem veto ainda), e aí a main confere nas outras duas listas."""
    nome = f"vetos_{ano}.json"
    dados = cache_ler(nome, VALIDADE, so_montar)
    if dados is None:
        r = pedir(SENADO + f"/materia/vetos/{ano}", aceitar_404=(ano == hoje.year))
        dados = [] if r is None else registros_veto(r)
        conferir_lista_vetos(dados, ano, hoje)
        cache_gravar(nome, dados)
    conferir_lista_vetos(dados, ano, hoje)
    return dados


def listas_situacao(so_montar):
    """{"tramitando": [...], "encerrados": [...]}: código, número e ano de cada veto nas duas listas do Congresso (outros serviços que o da
    lista do ano), para conferir o EmTramitacao de cada veto. A de encerrados nunca é vazia (mais de mil vetos); a de em tramitação pode ser."""
    out = {}
    for chave, rota in (("tramitando", "aposrcn"), ("encerrados", "encerrados")):
        nome = f"vetos_{rota}.json"
        dados = cache_ler(nome, VALIDADE, so_montar)
        if dados is None:
            dados = [{"codigo": str(v["Codigo"]), "numero": str(v["Materia"].get("Numero")), "ano": str(v["Materia"].get("Ano"))}
                     for v in registros_veto(pedir(SENADO + f"/materia/vetos/{rota}"))]
            if rota == "encerrados" and not dados:
                raise FonteFalhou("Congresso, /materia/vetos/encerrados: lista vazia")
            cache_gravar(nome, dados)
        out[chave] = dados
    return out


def resultado_veto(v, so_montar):
    """Os dispositivos de um veto (situação, votos?, tipo e data da sessão), reduzidos. A resposta tem de ser DESTE veto. 404 (veto que
    acabou de chegar e ainda não tem resultado) devolve None, sem cache: o veto fica retido nesta rodada."""
    cod = str(v["Codigo"])
    nome = f"veto/{cod}.json"
    dados = cache_ler(nome, VALIDADE if v["Materia"]["EmTramitacao"] == "Sim" else VALIDADE_FECHADA, so_montar)
    if dados is not None and not isinstance(dados, dict):
        if so_montar:
            raise FonteFalhou(f"--so-montar com o resultado do veto {cod} no formato antigo do cache: rode sem --so-montar antes")
        dados = None                                   # formato antigo (só a lista de dispositivos, sem os PDFs): pede de novo
    if dados is None:
        r = pedir(SENADO + f"/plenario/resultado/veto/{cod}", aceitar_404=True)
        if r is None:
            return None
        corpo = next(iter(r.values()), None) if isinstance(r, dict) and len(r) == 1 else None
        vv = corpo.get("Veto") if isinstance(corpo, dict) else None
        if not isinstance(vv, dict) or str(vv.get("Codigo")) != cod:
            raise FonteFalhou(f"Congresso, resultado do veto {cod} (VET {v['Materia']['Numero']}/{v['Materia']['Ano']}): a resposta não é deste veto")
        ds = (vv.get("Dispositivos") or {}).get("Dispositivo") or []
        ds = ds if isinstance(ds, list) else [ds]
        pdfs = ((vv.get("PdfsResultadoVotacao") or {}).get("PdfResultadoVotacao")) or []
        pdfs = pdfs if isinstance(pdfs, list) else [pdfs]
        dados = {"dispositivos": [{k: x.get(k) for k in ("Codigo", "Identificador", "Descricao", "Situacao", "PossuiVotos", "TipoVotacao", "DataSessao")}
                                  for x in ds if isinstance(x, dict)],
                 # o documento do resultado oficial, só como LINK (o arquivo não é lido): no veto total é o único lugar com o placar
                 "pdfs": [{"url": str(x.get("URL")), "descricao": str(x.get("Descricao") or "").strip() or None}
                          for x in pdfs if isinstance(x, dict) and str(x.get("URL") or "").startswith("https://")]}
        cache_gravar(nome, dados)
    return dados


def votos_dispositivo(disp, so_montar):
    """Os votos de um dispositivo, contados por Casa e por tipo de voto, como a fonte os grava (o vocabulário é conferido ao montar). Guarda
    também a parte vetada, o tipo e o texto da sessão, para conferir que a votação é deste dispositivo. Sem votação na fonte: {"erro": …}."""
    cod = str(disp["Codigo"])
    nome = f"votos/{cod}.json"
    dados = cache_ler(nome, VALIDADE_VOTOS, so_montar)
    if dados is not None and not so_montar and dados.get("dataSessao") != disp.get("DataSessao"):
        dados = None                                   # votado de novo noutra sessão: pede outra vez
    if dados is None:
        r = pedir(SENADO + f"/plenario/resultado/veto/dispositivo/{cod}", aceitar_404=True)
        corpo = next(iter(r.values()), None) if isinstance(r, dict) and len(r) == 1 else None
        vot = corpo.get("Votacao") if isinstance(corpo, dict) else None
        if not isinstance(vot, dict):
            return {"erro": "a fonte não traz a votação deste dispositivo"}

        def conta(casa):
            bloco = vot.get(casa)
            vs = bloco.get("Voto") if isinstance(bloco, dict) else None
            if not vs:
                return None                            # Casa não apurada (a segunda só é apurada se a primeira derrubar)
            vs = vs if isinstance(vs, list) else [vs]
            return dict(Counter(str(x.get("TipoVoto")) for x in vs if isinstance(x, dict)))
        dados = {"dataSessao": disp.get("DataSessao"), "parte": vot.get("ParteVetada"), "tipo": vot.get("TipoVotacao"),
                 "sessao": vot.get("Sessao"), "camara": conta("Camara"), "senado": conta("Senado")}
        cache_gravar(nome, dados)
    return dados


# ---------------------------------------------------------------- regras

def carregar_mandato(hoje, caminho=GOVERNOS):
    gov = json.loads(caminho.read_text(encoding="utf-8"))["governos"]
    atual = [g for g in gov if g["inicio"] <= hoje.isoformat() <= g["fim"]]
    if len(atual) != 1:
        raise FonteFalhou(f"entradas/governos.json: {len(atual)} governos contêm {hoje} (esperado 1)")
    g = atual[0]
    fim = None if g["fim"] >= "2099" else g["fim"]       # "2099-12-31" é o marcador de governo em curso, não uma data
    return {"id": g["id"], "rotulo": g["rotulo"], "presidente": g.get("presidente"), "inicio": g["inicio"], "fim": fim,
            "partido": [p["sigla"] for p in g.get("partidoPresidente") or []], "nota": g.get("nota"),
            "fonte": "entradas/governos.json (datas e partido conferidos no Wikidata, propriedades P39 e P102)"}


def anos_do_mandato(mandato, hoje):
    fim = min(hoje.year, int((mandato["fim"] or "9999")[:4]))
    return list(range(int(mandato["inicio"][:4]), fim + 1))


NORMA = re.compile(r"(Lei(?: Complementar)?) nº ([\d.]+),? de (\d{2})/(\d{2})/(\d{4})")


def lei_do_detalhe(d):
    """A lei gerada, do detalhe estruturado; None se o detalhe não a traz completa."""
    n = (d or {}).get("normaGerada")
    if not isinstance(n, dict) or not n.get("numero") or not n.get("dataAssinatura") or not n.get("codigo"):
        return None
    return {"numero": int(n["numero"]), "ano": int(str(n["dataAssinatura"])[:4]), "data": str(n["dataAssinatura"])[:10],
            "descricao": n.get("descricao") or f"Lei nº {n['numero']}", "codigo": int(n["codigo"])}


def classificar(m, d=None):
    """(categoria, situação oficial mostrada, motivo da retenção ou None, lei ou None). A lista é a única fonte da situação; o detalhe só
    dá a lei. Valor desconhecido levanta (a rodada para)."""
    sit = (m.get("situacaoAtual") or "").strip() or None
    deli = (m.get("siglaTipoDeliberacao") or "").strip() or None
    if m.get("tramitando") == "Sim":
        return "em_tramitacao", sit, None, None
    if sit:
        if sit not in POR_SITUACAO:
            raise FonteFalhou(f"{m['identificacao']}: situação nova na fonte ({sit!r}): incluir em POR_SITUACAO antes de publicar")
        cat = POR_SITUACAO[sit]
    elif deli:
        if deli not in POR_DELIBERACAO:
            raise FonteFalhou(f"{m['identificacao']}: deliberação nova na fonte ({deli!r}): incluir em POR_DELIBERACAO antes de publicar")
        cat = POR_DELIBERACAO[deli]
    else:
        return "sem_situacao", None, None, None
    lei = lei_do_detalhe(d) if d is not None else None
    texto_lei = m.get("normaGerada")
    if cat == "convertida":
        if not lei:
            return cat, sit, "a fonte diz que virou lei, mas não registra a lei (número, data e código da norma)", None
        mt = NORMA.search(str(texto_lei or ""))
        if not mt or int(mt[2].replace(".", "")) != lei["numero"]:
            return cat, sit, f"a lei da lista ({texto_lei!r}) não é a do detalhe (nº {lei['numero']})", None
        return cat, sit, None, lei
    if cat == "aprovada_sem_lei" and lei:              # aprovada e com a lei já registrada: é convertida
        return "convertida", sit, None, lei
    return cat, sit, None, None


def lei_da_norma(n):
    """A lei (NormaGerada da lista de vetos) no mesmo formato da lei das MPs; None se a fonte não a traz completa."""
    if not isinstance(n, dict) or not str(n.get("NumeroNorma") or "").isdigit() or not str(n.get("CodigoNorma") or "").isdigit() \
            or not str(n.get("AnoNorma") or "").isdigit() or not n.get("DataAssinatura"):
        return None
    return {"numero": int(n["NumeroNorma"]), "ano": int(n["AnoNorma"]), "data": str(n["DataAssinatura"])[:10],
            "descricao": n.get("NomeNorma") or f"Lei nº {n['NumeroNorma']}", "codigo": int(n["CodigoNorma"])}


def placar(contagem):
    """[sim, não, abstenção, em branco] de uma Casa a partir da contagem por tipo de voto; tipo que este código não conhece PARA a rodada."""
    if contagem is None:
        return None
    novos = sorted(set(contagem) - set(VOTOS) - FORA_DO_PLACAR)
    if novos:
        raise FonteFalhou(f"tipo de voto novo na fonte ({novos}): incluir em VOTOS antes de publicar")
    p = [0, 0, 0, 0]
    for k, n in contagem.items():
        if k in VOTOS:
            p[VOTOS[k]] += int(n)
    return p


def data_da_sessao(texto):
    """'Sessão Conjunta nº 4 de 09/05/2024 às 10:00h' → '2024-05-09'."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(texto or ""))
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().rstrip(".;").casefold()


def rotulo_veto(cont):
    """O rótulo do veto a partir da contagem dos dispositivos. Prejudicado ao lado de decididos não muda o rótulo (conta à parte)."""
    if cont["nao_apreciado"]:
        return "em_tramitacao"
    dec = {k for k in ("mantido", "rejeitado") if cont[k]}
    if not dec:
        return "prejudicado" if cont["prejudicado"] else None
    return "mantido" if dec == {"mantido"} else "derrubado" if dec == {"rejeitado"} else "derrubado_em_parte"


def fmt_placar(p):
    return "não apurado" if p is None else f"{p[0]} Sim, {p[1]} Não, {p[2]} abstenção, {p[3]} em branco"


def classificar_veto(v, disps, votos, tramitando, encerrados, pdfs=()):
    """(item, None) ou (None, motivo da retenção). Valor novo da fonte (situação de dispositivo, tipo de voto) levanta: a rodada para."""
    cod, m, mv = str(v["Codigo"]), v["Materia"], v["MateriaVetada"]
    ident = f"VET {int(m['Numero'])}/{m['Ano']}"
    total, em = v["Total"] == "Sim", m["EmTramitacao"] == "Sim"
    if (cod in tramitando) == (cod in encerrados) or (cod in tramitando) != em:
        return None, ("a fonte diverge sobre se o veto está em tramitação (campo EmTramitacao "
                      f"{m['EmTramitacao']!r}; na lista de em tramitação: {'sim' if cod in tramitando else 'não'}; na de encerrados: {'sim' if cod in encerrados else 'não'})")
    if disps is None:
        return None, "a fonte ainda não traz o resultado (os dispositivos) deste veto"
    if total and len(disps) != 1:
        return None, f"veto total com {len(disps)} dispositivos no resultado (esperado 1)"
    if not total and (not disps or str(v.get("QuantidadeDispositivos")) != str(len(disps))):
        return None, f"a lista diz {v.get('QuantidadeDispositivos')} dispositivos e o resultado traz {len(disps)}"
    cont, linhas = {c: 0 for c in DISP_IDS}, []
    for x in disps:
        s = POR_SITUACAO_DISP.get(x.get("Situacao"))
        if s is None:
            raise FonteFalhou(f"{ident}: situação de dispositivo nova na fonte ({x.get('Situacao')!r}): incluir em POR_SITUACAO_DISP antes de publicar")
        cont[s] += 1
        cam = sen = None
        if not total and x.get("PossuiVotos") == "Sim" and x.get("Codigo"):
            vt = votos.get(str(x["Codigo"])) or {"erro": "votação não baixada"}
            if vt.get("erro"):
                return None, f"{x.get('Identificador')}: {vt['erro']}"
            if _norm(vt.get("parte")) != _norm(x.get("Descricao")) or vt.get("tipo") != x.get("TipoVotacao") \
                    or data_da_sessao(vt.get("sessao")) != str(x.get("DataSessao") or "")[:10]:
                return None, (f"{x.get('Identificador')}: a votação que a fonte devolve não é a deste dispositivo "
                              f"(parte vetada {vt.get('parte')!r} × {x.get('Descricao')!r}; sessão {vt.get('sessao')!r} × {x.get('DataSessao')!r})")
            cam, sen = placar(vt.get("camara")), placar(vt.get("senado"))
            alcanca = (cam is not None and cam[1] >= VOTOS_NECESSARIOS["camara"]) and (sen is not None and sen[1] >= VOTOS_NECESSARIOS["senado"])
            if s == "rejeitado" and not alcanca:
                return None, (f"{x.get('Identificador')} consta como rejeitado (derrubado), mas o placar não chega à maioria absoluta "
                              f"(Câmara: {fmt_placar(cam)}; Senado: {fmt_placar(sen)}; derrubar exige 257 e 41 votos Não)")
            if s == "mantido" and alcanca:
                return None, (f"{x.get('Identificador')} consta como mantido, mas o placar chega à maioria absoluta nas duas Casas "
                              f"(Câmara: {fmt_placar(cam)}; Senado: {fmt_placar(sen)})")
        linhas.append([x.get("Identificador"), x.get("Descricao"), s, str(x.get("DataSessao") or "")[:10] or None, x.get("TipoVotacao"), cam, sen])
    rot = rotulo_veto(cont)
    if rot is None:
        return None, "nenhum dispositivo no resultado"
    if (rot == "em_tramitacao") != em:
        return None, f"os dispositivos dizem {'em tramitação' if rot == 'em_tramitacao' else 'decidido'}, e o campo EmTramitacao diz {m['EmTramitacao']!r}"
    msg = v.get("Mensagem") or {}
    item = {"codigo": int(cod), "codigoMateria": int(m["Codigo"]), "numero": int(m["Numero"]), "ano": int(m["Ano"]), "total": total,
            "assunto": (v.get("Assunto") or "").strip() or None, "dataPublicacao": str(v.get("DataPublicacao"))[:10], "situacao": rot,
            "materiaVetada": {"sigla": mv["Sigla"], "numero": int(mv["Numero"]), "ano": int(mv["Ano"])},
            "lei": lei_da_norma(mv.get("NormaGerada")),
            "mensagem": ({"numero": int(msg["Numero"]), "ano": int(msg["Ano"]), "url": msg["UrlPlanalto"]}
                         if str(msg.get("Numero") or "").isdigit() and str(msg.get("Ano") or "").isdigit()
                         and str(msg.get("UrlPlanalto") or "").startswith("https://") else None),
            "sobrestandoDesde": (str(v.get("DataSobrestacaoPauta") or "")[:10] or None) if v.get("SobrestandoPauta") == "Sim" else None,
            "contagem": cont, "dispositivos": linhas,
            # veto total: o placar só existe no documento do resultado; vai o LINK (o arquivo não é lido nem transcrito)
            "resultadoPdf": [{"url": x["url"], "descricao": x.get("descricao")} for x in pdfs] if total else []}
    return item, None


def montar_vetos(mandato, listas, tramitando, encerrados, resultados, votos):
    """O bloco "vetos" do JSON. `listas` = {ano: vetos do ano}; `resultados` = {código do veto: dispositivos}; `votos` = {código do dispositivo: …}."""
    itens, retidos, por_ano = [], [], []
    for ano in sorted(listas):
        lst = [v for v in listas[ano] if mandato["inicio"] <= str(v.get("DataPublicacao") or "")[:10] <= (mandato["fim"] or "9999")]
        por_ano.append({"ano": ano, "fonte": len(listas[ano]), "noMandato": len(lst)})
        for v in lst:
            r = resultados.get(str(v["Codigo"]))
            item, motivo = classificar_veto(v, r["dispositivos"] if r else None, votos, tramitando, encerrados, (r or {}).get("pdfs") or ())
            if motivo:
                retidos.append({"identificacao": f"VET {int(v['Materia']['Numero'])}/{ano}", "motivo": motivo})
            else:
                itens.append(item)
    itens.sort(key=lambda x: (-x["ano"], -x["numero"]))
    cont = Counter(x["situacao"] for x in itens)
    disp = Counter()
    for x in itens:
        if not x["total"]:                             # dispositivos: só dos vetos parciais (o veto total conta como 1 veto)
            disp.update(x["contagem"])
    return {"definicoes": [{"id": c, "rotulo": r, "definicao": d} for c, r, d in ROTULOS_VETO],
            "definicoesDispositivo": [{"id": c, "rotulo": r, "definicao": d} for c, r, d in SITUACOES_DISPOSITIVO],
            "necessarios": dict(VOTOS_NECESSARIOS), "camposDispositivo": CAMPOS_DISPOSITIVO,
            "total": len(itens), "contagem": {c: cont.get(c, 0) for c in ROTULOS_IDS},
            "totais": {"total": sum(1 for x in itens if x["total"]), "parcial": sum(1 for x in itens if not x["total"])},
            "dispositivos": {c: disp.get(c, 0) for c in DISP_IDS},
            "porAno": por_ano, "retidos": retidos, "itens": itens}


def montar(mandato, por_ano_senado, por_ano_camara, detalhes):
    """O JSON da página e as retidas. `detalhes` = {id: processo} das aprovadas."""
    itens, retidas, por_ano = [], [], []
    camara_id = {(int(x["numero"]), int(x["ano"])): x["id"] for lst in por_ano_camara.values() for x in lst}
    for ano in sorted(por_ano_senado):
        lst = [m for m in por_ano_senado[ano] if mandato["inicio"] <= (m.get("dataApresentacao") or "")[:10] <= (mandato["fim"] or "9999")]
        # fonte × câmara: a contagem bruta do ano nas duas fontes (a conferência); noMandato: as do ano apresentadas dentro do mandato
        por_ano.append({"ano": ano, "fonte": len(por_ano_senado[ano]), "camara": len(por_ano_camara.get(ano, [])), "noMandato": len(lst)})
        for m in lst:
            num = int(m["identificacao"].split()[1].split("/")[0])
            cat, sit, motivo, lei = classificar(m, detalhes.get(m["id"]))
            if motivo:
                retidas.append({"identificacao": m["identificacao"], "motivo": motivo})
                continue
            item = {"id": m["id"], "codigoMateria": m["codigoMateria"], "numero": num, "ano": ano,
                    "ementa": (m.get("ementa") or "").strip() or None, "data": (m.get("dataApresentacao") or "")[:10] or None,
                    "situacao": cat, "situacaoFonte": sit, "deliberacaoFonte": (m.get("siglaTipoDeliberacao") or None),
                    "dataDeliberacao": (m.get("dataDeliberacao") or "")[:10] or None, "lei": lei,
                    "camaraId": camara_id.get((num, ano))}
            if item["deliberacaoFonte"] and item["deliberacaoFonte"] not in DELIBERACOES:
                raise FonteFalhou(f"{m['identificacao']}: deliberação nova na fonte ({item['deliberacaoFonte']!r}): incluir em DELIBERACOES antes de publicar")
            if cat == "sem_situacao":
                item["motivo"] = "a fonte não traz a situação nem a deliberação"
            elif not sit:
                item["motivo"] = "situação vazia na fonte: vale a deliberação oficial"
            itens.append(item)
    itens.sort(key=lambda x: (-x["ano"], -x["numero"]))
    contagem = Counter(x["situacao"] for x in itens)
    return {"_versao": VERSAO, "_geradoEm": agora().isoformat(timespec="minutes"),
            "_fontes": {"mps": {"nome": "Dados Abertos do Senado Federal e do Congresso Nacional", "url": SENADO + "/processo?sigla=MPV",
                                "documentacao": SENADO + "/v3/api-docs"},
                        "conferencia": {"nome": "Dados Abertos da Câmara dos Deputados", "url": CAMARA + "/proposicoes?siglaTipo=MPV"},
                        "mandato": mandato["fonte"]},
            "urls": URLS,
            "mandato": {k: v for k, v in mandato.items() if k != "fonte"},
            "mps": {"definicoes": [{"id": c, "rotulo": r, "definicao": t} for c, r, t in SITUACOES],
                    "deliberacoes": {k: v for k, v in DELIBERACOES.items() if any(x["deliberacaoFonte"] == k for x in itens)},
                    "total": len(itens), "contagem": {c: contagem.get(c, 0) for c in CATEGORIAS},
                    "porAno": por_ano, "retidas": retidas, "itens": itens}}


def checagens(saida):
    erros = []
    mps = saida["mps"]
    for a in mps["porAno"]:
        if a["fonte"] != a["camara"]:
            erros.append(f"MPV {a['ano']}: {a['fonte']} na fonte do Congresso e {a['camara']} na Câmara: contagem por ano não bate")
    soma = sum(mps["contagem"].values())
    if soma != mps["total"] or mps["total"] != len(mps["itens"]):
        erros.append(f"contagens por situação somam {soma}, o total é {mps['total']} e a lista tem {len(mps['itens'])}")
    if mps["total"] + len(mps["retidas"]) != sum(a["noMandato"] for a in mps["porAno"]):
        erros.append("itens + retidas não somam o total da fonte por ano")
    for x in mps["itens"]:
        if x["situacao"] == "convertida" and not x["lei"]:
            erros.append(f"MPV {x['numero']}/{x['ano']} convertida sem lei")
    ids = [(x["numero"], x["ano"]) for x in mps["itens"]]
    if len(ids) != len(set(ids)):
        erros.append("MP repetida na lista")
    # o ano corrente pode começar vazio; um ano FECHADO sem nenhuma MP é falha de fonte
    hoje = agora().date()
    for a in mps["porAno"]:
        if a["ano"] < hoje.year and a["fonte"] == 0:
            erros.append(f"MPV {a['ano']}: ano fechado sem nenhuma MP na fonte")
    vt = saida.get("vetos")
    if vt is not None:
        soma = sum(vt["contagem"].values())
        if soma != vt["total"] or vt["total"] != len(vt["itens"]) or vt["totais"]["total"] + vt["totais"]["parcial"] != vt["total"]:
            erros.append(f"vetos: contagens por rótulo somam {soma}, o total é {vt['total']} e a lista tem {len(vt['itens'])}")
        if vt["total"] + len(vt["retidos"]) != sum(a["noMandato"] for a in vt["porAno"]):
            erros.append("vetos: itens + retidos não somam o total da fonte por ano")
        disp = Counter()
        for x in vt["itens"]:
            if sum(x["contagem"].values()) != len(x["dispositivos"]):
                erros.append(f"VET {x['numero']}/{x['ano']}: contagem de dispositivos diferente da lista")
            if not x["total"]:
                disp.update(x["contagem"])
        if {c: disp.get(c, 0) for c in DISP_IDS} != vt["dispositivos"]:
            erros.append("vetos: a contagem de dispositivos não é a soma dos vetos parciais")
        ids = [(x["numero"], x["ano"]) for x in vt["itens"]]
        if len(ids) != len(set(ids)):
            erros.append("veto repetido na lista")
    erros += proibidos(saida)
    return erros


def proibidos(obj):
    """Nenhum texto da saída fala em derrota, vitória, culpa (nem ranking): a página só conta."""
    vt = obj.get("vetos") or {}
    texto = json.dumps({k: v for k, v in obj.items() if k not in ("mps", "vetos")}
                       | {"definicoes": obj["mps"]["definicoes"], "vetos": [vt.get("definicoes"), vt.get("definicoesDispositivo")]}, ensure_ascii=False)
    m = PROIBIDAS.search(texto)
    return [f"palavra proibida nos textos da saída: {m[0]!r}"] if m else []


def variacao(saida, publicado):
    """Mais de 10% de MPs a mais ou a menos contra o publicado (mesma versão e mesmo mandato): falha de fonte, a rodada para."""
    if not publicado or publicado.get("_versao") != VERSAO or (publicado.get("mandato") or {}).get("id") != saida["mandato"]["id"]:
        return []
    erros = []
    for chave, nome in (("mps", "MPs"), ("vetos", "vetos")):
        if chave not in publicado or chave not in saida:
            continue
        a, n = publicado[chave]["total"], saida[chave]["total"]
        if a >= 20 and abs(n - a) > a * VARIACAO:
            erros.append(f"{nome}: {a} no publicado e {n} agora (variação maior que {VARIACAO:.0%}): nada publicado")
    return erros


def validar_esquema(saida):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema não instalado: a saída não pode ser conferida contra schemas/presidencia.schema.json"]
    v = Draft202012Validator(json.loads(ESQUEMA.read_text(encoding="utf-8")))
    return [f"esquema: {'.'.join(map(str, e.absolute_path)) or '<raiz>'}: {e.message}" for e in v.iter_errors(saida)][:20]


def serializar(saida):
    """JSON compacto, uma MP e um veto por linha (o diff do git mostra só o que mudou)."""
    sep = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    partes = [sep({k: v for k, v in saida.items() if k not in ("mps", "vetos")})[:-1]]
    for chave in ("mps", "vetos"):
        if chave in saida:
            bloco = dict(saida[chave])
            itens = bloco.pop("itens")
            partes.append(f',\n"{chave}":' + sep(bloco)[:-1] + ',\n"itens":[\n' + ",\n".join(sep(x) for x in itens) + "\n]}")
    return "".join(partes) + "}\n"


def gravar(caminho, saida):
    texto = serializar(saida)
    json.loads(texto)
    coleta.gravar_texto(caminho, texto, newline="\n")


def amostra(saida, n=10, semente=20261107, n_vetos=15):
    """10 MPs e 15 vetos para conferir à mão: pelo menos um de cada situação (ou rótulo) presente e pelo menos 2 vetos totais, o resto
    sorteado (semente fixa)."""
    rnd = random.Random(semente)
    itens = saida["mps"]["itens"]
    escolha = []
    for c in CATEGORIAS:
        grupo = [x for x in itens if x["situacao"] == c]
        if grupo:
            escolha.append(rnd.choice(grupo))
    resto = [x for x in itens if x not in escolha]
    escolha += rnd.sample(resto, max(0, min(n - len(escolha), len(resto))))
    rot = {c: r for c, r, _ in SITUACOES}
    linhas = ["tipo;identificacao;situacao;situacao_fonte;detalhe_fonte;lei;link_congresso;link_api;link_extra;confere"]
    for x in escolha[:n]:
        lei = f"{x['lei']['descricao']} ({URLS['lei'].format(codigo=x['lei']['codigo'])})" if x["lei"] else ""
        cam = URLS["camara"].format(camaraId=x["camaraId"]) if x["camaraId"] else ""
        linhas.append(";".join(str(v).replace(";", ",") for v in (
            "MP", f"MPV {x['numero']}/{x['ano']}", rot[x["situacao"]], x["situacaoFonte"] or "", x["deliberacaoFonte"] or "", lei,
            URLS["congresso"].format(codigoMateria=x["codigoMateria"]), URLS["api"].format(id=x["id"]), cam, "")))
    vt = saida.get("vetos")
    if vt:
        itens = vt["itens"]
        esc_v = []
        for c in ROTULOS_IDS:
            grupo = [x for x in itens if x["situacao"] == c and x not in esc_v]
            if grupo:
                esc_v.append(rnd.choice(grupo))
        totais = [x for x in itens if x["total"] and x not in esc_v]
        esc_v += rnd.sample(totais, max(0, min(2 - sum(1 for x in esc_v if x["total"]), len(totais))))
        resto = [x for x in itens if x not in esc_v]
        esc_v += rnd.sample(resto, max(0, min(n_vetos - len(esc_v), len(resto))))
        rv = {c: r for c, r, _ in ROTULOS_VETO}
        for x in esc_v[:n_vetos]:
            c = x["contagem"]
            disp = ", ".join(f"{n} {k}" for k, n in c.items() if n)
            plac = "; ".join(f"{d[0]}: Câmara {fmt_placar(d[5])}, Senado {fmt_placar(d[6])}" for d in x["dispositivos"] if d[5] is not None)[:300]
            lei = f"{x['lei']['descricao']} ({URLS['lei'].format(codigo=x['lei']['codigo'])})" if x["lei"] else ""
            linhas.append(";".join(str(v).replace(";", ",") for v in (
                "VETO", f"VET {x['numero']}/{x['ano']} ({'total' if x['total'] else 'parcial'}) ao {x['materiaVetada']['sigla']} "
                f"{x['materiaVetada']['numero']}/{x['materiaVetada']['ano']}", rv[x["situacao"]], f"dispositivos: {disp}",
                plac or ("placar não disponível nos dados abertos" if x["total"] and x["situacao"] != "em_tramitacao" else ""), lei,
                URLS["veto"].format(codigo=x["codigo"]), URLS["vetoApi"].format(codigo=x["codigo"]), (x["mensagem"] or {}).get("url", ""), "")))
    return "\n".join(linhas) + "\n"


def relatorio(saida, erros, args, segundos, pedidos):
    m = saida["mps"]
    rot = {c: r for c, r, _ in SITUACOES}
    l = [f"PRESIDENTE — {saida['mandato']['rotulo']} (desde {saida['mandato']['inicio']}) · {'publicação' if args.publicar else 'dry-run'} · "
         f"{segundos:.0f} s · {pedidos} pedidos à rede",
         f"MPs: {m['total']} publicáveis · retidas {len(m['retidas'])}",
         "por ano (Congresso × Câmara): " + " · ".join(f"{a['ano']}: {a['fonte']} × {a['camara']}" for a in m["porAno"]),
         "por situação: " + " · ".join(f"{rot[c]} {n}" for c, n in m["contagem"].items() if n or c in ("rejeitada",))]
    sem = [x for x in m["itens"] if x.get("motivo")]
    if sem:
        l.append("situação pela deliberação ou sem situação: " + ", ".join(f"MPV {x['numero']}/{x['ano']} ({rot[x['situacao']]})" for x in sem))
    for r in m["retidas"]:
        l.append(f"RETIDA {r['identificacao']}: {r['motivo']}")
    vt = saida.get("vetos")
    if vt:
        rv = {c: r for c, r, _ in ROTULOS_VETO}
        l += [f"vetos: {vt['total']} publicáveis ({vt['totais']['total']} totais, {vt['totais']['parcial']} parciais) · retidos {len(vt['retidos'])}",
              "vetos por ano (fonte × no mandato): " + " · ".join(f"{a['ano']}: {a['fonte']} × {a['noMandato']}" for a in vt["porAno"]),
              "vetos por rótulo: " + " · ".join(f"{rv[c]} {n}" for c, n in vt["contagem"].items()),
              "dispositivos dos vetos parciais: " + " · ".join(f"{k} {n}" for k, n in vt["dispositivos"].items())]
        com_placar = sum(1 for x in vt["itens"] for d in x["dispositivos"] if d[5] is not None)
        l.append(f"dispositivos com placar: {com_placar}; vetos totais decididos (placar só no PDF, não lido): "
                 f"{sum(1 for x in vt['itens'] if x['total'] and x['situacao'] != 'em_tramitacao')}")
        for r in vt["retidos"]:
            l.append(f"RETIDO {r['identificacao']}: {r['motivo']}")
    l += [f"ERRO: {e}" for e in erros] or ["checagens: todas passaram"]
    return "\n".join(l)


# ---------------------------------------------------------------- testes

def testar():
    import tempfile
    from pathlib import Path
    from unittest import mock
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1

    def mp(num, ano=2023, sit="SEM EFICÁCIA", deli="PERDA_EFICACIA", tram="Não", norma=None, data=None):
        return {"identificacao": f"MPV {num}/{ano}", "id": 1000 + num, "codigoMateria": 5000 + num, "tramitando": tram, "situacaoAtual": sit,
                "siglaTipoDeliberacao": deli, "normaGerada": norma, "dataApresentacao": data or f"{ano}-03-01", "ementa": f"Ementa {num}",
                "dataDeliberacao": f"{ano}-06-01"}

    def det(num, lei=15000, data="2024-03-06", codigo=40000000):
        return {"id": 1000 + num, "normaGerada": {"numero": lei, "dataAssinatura": data, "codigo": codigo, "descricao": f"Lei nº {lei} de 06/03/2024"}}

    def veto(num, ano=2023, total=True, tram="Não", qtd=0, data=None, sigla="PL"):
        base = (ano - 2023) * 100                        # códigos distintos por ano (2023 = 16001, 16002…)
        return {"Codigo": str(16000 + base + num), "Materia": {"Codigo": str(160000 + base + num), "Sigla": "VET", "Numero": str(num), "Ano": str(ano),
                                                       "EmTramitacao": tram},
                "MateriaVetada": {"Sigla": sigla, "Numero": str(100 + num), "Ano": str(ano), "NormaGerada": None},
                "Mensagem": {"Numero": str(900 + num), "Ano": str(ano), "UrlPlanalto": "https://www.planalto.gov.br/msg"},
                "Total": "Sim" if total else "Não", "Assunto": f"Assunto {num}", "DataPublicacao": data or f"{ano}-04-01",
                "SobrestandoPauta": "Sim" if tram == "Sim" else "Não", "DataSobrestacaoPauta": f"{ano}-05-01", "QuantidadeDispositivos": str(qtd)}

    def disp(ident, sit, cod=None, votos="Sim", desc="§ 1º do art. 2º", data="2023-05-10"):
        return {"Codigo": cod, "Identificador": ident, "Descricao": desc, "Situacao": sit, "PossuiVotos": votos,
                "TipoVotacao": "Cédula" if votos == "Sim" else None, "DataSessao": data if votos == "Sim" else None}

    def vt(nao_c, nao_s=None, sim_c=10, sim_s=5, parte="§ 1º do art. 2º", sessao="Sessão Conjunta nº 1 de 10/05/2023 às 10:00h", extra=None):
        cam = {"Sim": sim_c, "Não": nao_c} | (extra or {})
        return {"dataSessao": "2023-05-10", "parte": parte, "tipo": "Cédula", "sessao": sessao, "camara": cam,
                "senado": None if nao_s is None else {"Sim": sim_s, "Não": nao_s}}

    def vetos_falsos(url):
        """API simulada dos vetos para as rodadas inteiras: um veto total por ano (2023 a 2026), mantido e encerrado."""
        m = re.search(r"/materia/vetos/(\d{4})$", url)
        if m:
            return {"ListaVetos": {"Vetos": {"Veto": [veto(1, ano=int(m[1]))]}}}
        if url.endswith("/aposrcn"):
            return {"ListaVetos": {"Vetos": {"Veto": []}}}
        if url.endswith("/encerrados"):
            return {"ListaVetos": {"Vetos": {"Veto": [veto(1, ano=a) for a in range(2023, 2027)]}}}
        m = re.search(r"/plenario/resultado/veto/(\d+)$", url)
        if m:
            return {"ResultadoVetoCN": {"Veto": {"Codigo": m[1], "Dispositivos": {"Dispositivo": disp("1.xx.000", "Mantido", desc="Veto total")},
                                                 "PdfsResultadoVotacao": {"PdfResultadoVotacao": {"URL": f"https://legis.senado.leg.br/r/{m[1]}",
                                                                                                  "Descricao": "Resultado da cédula"}}}}}
        raise FonteFalhou(f"rota não simulada: {url}")

    # 1. as três MPs de 2023 sem situação: vale a deliberação oficial (perda de eficácia), com o motivo
    for n in (1169, 1179, 1204):
        cat, sit, motivo, lei = classificar(mp(n, sit=None))
        igual((cat, sit, motivo, lei), ("perdeu_eficacia", None, None, None), f"MPV {n}/2023 sem situação usa a deliberação")
    igual(classificar(mp(1, sit=None, deli=None))[0], "sem_situacao", "sem situação e sem deliberação: —")
    igual(classificar(mp(2, sit="MATÉRIA COM A RELATORIA", deli=None, tram="Sim"))[:2], ("em_tramitacao", "MATÉRIA COM A RELATORIA"),
          "tramitando: em tramitação, com a situação oficial")
    # 2. convertida exige a lei; a lei da lista tem de ser a do detalhe
    c = mp(3, sit="TRANSFORMADA EM NORMA JURÍDICA", deli="APROVADO_PLV", norma="Lei nº 15.000 de 06/03/2024")
    igual(classificar(c, det(3))[0], "convertida", "convertida com lei")
    igual(classificar(c, {"id": 1003, "normaGerada": None})[2] is not None, True, "convertida sem lei fica retida")
    igual(classificar(c, det(3, lei=15001))[2] is not None, True, "lei da lista diferente da do detalhe: retida")
    igual(classificar(mp(4, sit="REMETIDA À SANÇÃO", deli="APROVADO_PLV"), {"id": 1004})[0], "aprovada_sem_lei", "aprovada sem lei registrada")
    # 3. valor novo na fonte para a rodada
    for kw in ({"sit": "ARQUIVADA PELO FIM DO MUNDO"}, {"sit": None, "deli": "DELIBERACAO_NOVA"}):
        try:
            classificar(mp(5, **kw))
            igual(True, False, "valor desconhecido devia parar")
        except FonteFalhou:
            ok += 1
    # 4. montar + checagens: contagem por ano contra a Câmara, retida fora das contagens, soma por situação
    mand = {"id": "lula3", "rotulo": "Lula III", "presidente": "Lula", "inicio": "2023-01-01", "fim": None, "partido": ["PT"], "nota": None,
            "fonte": "teste"}
    sen = {2023: [mp(10), mp(11, sit=None), c | {"identificacao": "MPV 3/2023"}]}
    cam = {2023: [{"id": 9, "numero": "10", "ano": 2023}, {"id": 8, "numero": "11", "ano": 2023}, {"id": 7, "numero": "3", "ano": 2023}]}
    s = montar(mand, sen, cam, {1003: {"id": 1003, "normaGerada": None}})
    igual((s["mps"]["total"], len(s["mps"]["retidas"]), s["mps"]["contagem"]["perdeu_eficacia"]), (2, 1, 2), "retida sai da lista e das contagens")
    igual(checagens(s), [], "checagens passam com a retida declarada")
    igual(s["mps"]["itens"][0]["camaraId"] in (8, 9), True, "id da Câmara pelo número exato")
    s2 = montar(mand, sen, {2023: cam[2023][:2]}, {1003: det(3)})
    igual(any("contagem por ano não bate" in e for e in checagens(s2)), True, "Câmara com outro total: a rodada para")
    # 5. variação de mais de 10% contra o publicado
    pub = {"_versao": VERSAO, "mandato": {"id": "lula3"}, "mps": {"total": 100}}
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 89}}, pub) != [], True, "queda de 11%: para")
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 111}}, pub) != [], True, "subida de 11%: para")
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 105}}, pub), [], "5%: segue")
    igual(variacao({"mandato": {"id": "outro"}, "mps": {"total": 5}}, pub), [], "outro mandato: não compara")
    # 6. esquema sem campos extras
    s = montar(mand, sen, cam, {1003: det(3)})
    s["vetos"] = montar_vetos(mand, {2023: [veto(1), veto(2, total=False, qtd=2)]}, set(), {"16001", "16002"},
                              {"16001": {"dispositivos": [disp("1.23.000", "Mantido", desc="Veto total")],
                                         "pdfs": [{"url": "https://legis.senado.leg.br/r/16001", "descricao": "Resultado da cédula"}]},
                               "16002": {"dispositivos": [disp("2.23.001", "Rejeitado", cod=7), disp("2.23.002", "Mantido", cod=8)], "pdfs": []}},
                              {"7": vt(300, 50), "8": vt(100)})
    igual(validar_esquema(s), [], "a saída passa no esquema")
    extra = json.loads(serializar(s))
    extra["mps"]["itens"][0]["placar"] = 1
    igual(bool(validar_esquema(extra)), True, "campo extra é recusado pelo esquema")
    igual(json.loads(serializar(s)) == json.loads(json.dumps(s, ensure_ascii=False)), True, "a gravação uma por linha é o mesmo JSON")
    # 7. palavras proibidas: nos textos da saída e na página
    igual(proibidos(s), [], "textos da saída sem derrota/vitória/culpa")
    igual(bool(proibidos(s | {"_x": "uma vitória do governo"})), True, "palavra proibida é pega")
    for frase, deve in (("o veto está parado", True), ("houve atraso na votação", True), ("a demora do Congresso", True),
                        ("a derrota do governo", True), ("uma vitória", True), ("votando em separado", False), ("o prazo terminou", False)):
        igual(bool(PROIBIDAS.search(frase)), deve, f"palavra proibida em {frase!r}")
    if TEXTO_PAGINA.exists():                          # a página inteira: texto fixo, rótulos e os textos que o script dela escreve
        m = PROIBIDAS.search(TEXTO_PAGINA.read_text(encoding="utf-8"))
        igual(m and m[0], None, "presidente.html sem derrota, vitória ou culpa")
    # 8. mandato a partir de governos.json; 2099 é marcador, não data
    with tempfile.TemporaryDirectory() as t:
        g = Path(t) / "g.json"
        g.write_text(json.dumps({"governos": [{"id": "x", "rotulo": "X", "presidente": "P", "inicio": "2023-01-01", "fim": "2099-12-31",
                                              "partidoPresidente": [{"sigla": "AA"}]}]}), encoding="utf-8")
        mm = carregar_mandato(date(2026, 11, 7), g)
        igual((mm["fim"], mm["partido"]), (None, ["AA"]), "mandato em curso: fim desconhecido, partido da tabela")
        igual(anos_do_mandato(mm, date(2026, 11, 7)), [2023, 2024, 2025, 2026], "anos do mandato até hoje")
        try:
            carregar_mandato(date(2020, 1, 1), g)
            igual(True, False, "sem governo na data devia parar")
        except FonteFalhou:
            ok += 1
    # 9. erro da fonte nunca vira "sem MPs": a rodada inteira contra uma API simulada que falha; o publicado fica intacto
    with tempfile.TemporaryDirectory() as t:
        T = Path(t)
        pub = T / "presidencia.json"
        pub.write_text('{"intacto":1}', encoding="utf-8")
        respostas = {"lista_vazia": [], "formato": [{"identificacao": "X"}], "http": FonteFalhou("HTTP 503")}
        for nome, resp in respostas.items():
            def falso(url, params=None, tentativas=3, resp=resp, **k):
                if isinstance(resp, Exception):
                    raise resp
                if "/vetos/" in url or "/resultado/veto/" in url:
                    return vetos_falsos(url)
                if "camara" in url:
                    return {"dados": [{"id": 1, "numero": 1, "ano": params["ano"]}], "links": []}
                return resp if params.get("ano") == 2024 else [mp(1, params["ano"])]
            with mock.patch(f"{__name__}.pedir", falso), mock.patch(f"{__name__}.CACHE_P", T / f"cache_{nome}"), mock.patch(f"{__name__}.SAIDA", pub), \
                    mock.patch(f"{__name__}.CANDIDATO", T / "cand.json"), mock.patch(f"{__name__}.RELATORIO", T / "rel.txt"), \
                    mock.patch(f"{__name__}.AMOSTRA", T / "amostra.csv"), mock.patch(f"{__name__}.INTERVALO", 0):
                saiu = None
                try:
                    main(["--publicar"], hoje=date(2025, 6, 1))
                except SystemExit as e:
                    saiu = e.code
                igual((saiu not in (None, 0), pub.read_text(encoding="utf-8")), (True, '{"intacto":1}'), f"fonte com erro ({nome}): para e o publicado fica")
                igual(nome != "http" or not (T / f"cache_{nome}" / "mpv_senado_2024.json").exists(), True, "resposta com erro não vai para o cache")
    # 10. aprovada sem lei registrada vira "convertida" na rodada seguinte ao registro da lei: duas rodadas inteiras (main) contra uma API
    #     simulada, com o relógio andando 25 h; o detalhe guardado sem a lei NÃO segura a MP por 30 dias
    with tempfile.TemporaryDirectory() as t:
        T = Path(t)
        dia = [datetime(2023, 6, 1, 8, 0, tzinfo=BRASILIA)]
        estado = {"lista": mp(4, sit="REMETIDA À SANÇÃO", deli="APROVADO_PLV"), "det": {"id": 1004, "normaGerada": None}}
        pedidos_det = []

        def falso(url, params=None, tentativas=3, **k):
            if "/vetos/" in url or "/resultado/veto/" in url:
                return vetos_falsos(url)
            if "camara" in url:
                return {"dados": [{"id": 7, "numero": "4", "ano": 2023}, {"id": 8, "numero": "10", "ano": 2023}], "links": []}
            if url.endswith("/processo"):
                return [estado["lista"], mp(10)]
            pedidos_det.append(url)
            return estado["det"]
        pub = T / "presidencia.json"
        patches = [mock.patch(f"{__name__}.pedir", falso), mock.patch(f"{__name__}.CACHE_P", T / "cache"), mock.patch(f"{__name__}.SAIDA", pub),
                   mock.patch(f"{__name__}.CANDIDATO", T / "cand.json"), mock.patch(f"{__name__}.RELATORIO", T / "rel.txt"),
                   mock.patch(f"{__name__}.AMOSTRA", T / "amostra.csv"), mock.patch(f"{__name__}.INTERVALO", 0),
                   mock.patch(f"{__name__}.agora", lambda: dia[0])]
        for x in patches:
            x.start()
        try:
            def rodada():
                main(["--publicar"], hoje=dia[0].date())
                return next(x for x in json.loads(pub.read_text(encoding="utf-8"))["mps"]["itens"] if x["numero"] == 4)
            a = rodada()
            igual((a["situacao"], a["lei"]), ("aprovada_sem_lei", None), "dia 1: aprovada, aguardando a sanção, sem lei")
            # dia 2 (25 h depois): a lista passa a dizer que virou lei e o detalhe traz a lei
            dia[0] += timedelta(hours=25)
            estado["lista"] = mp(4, sit="TRANSFORMADA EM NORMA JURÍDICA", deli="APROVADO_PLV", norma="Lei nº 15.000 de 06/03/2024")
            estado["det"] = det(4)
            b = rodada()
            igual((b["situacao"], (b["lei"] or {}).get("numero")), ("convertida", 15000), "dia 2: convertida, com a lei do detalhe")
            igual(len(pedidos_det), 2, "o detalhe sem lei foi pedido de novo no dia seguinte")
            # dia 3: a lei já está no detalhe guardado, que vale 30 dias: nenhum pedido novo de detalhe
            dia[0] += timedelta(hours=25)
            rodada()
            igual(len(pedidos_det), 2, "detalhe com a lei não é pedido de novo antes de 30 dias")
            # outro caminho da fonte: a lista ainda diz "remetida à sanção", mas o detalhe já traz a lei
            estado["lista"] = mp(4, sit="REMETIDA À SANÇÃO", deli="APROVADO_PLV")
            dia[0] += timedelta(hours=25)
            c2 = rodada()
            igual(c2["situacao"], "convertida", "lista atrasada e detalhe com a lei: convertida")
        finally:
            for x in patches:
                x.stop()
    # 11. vetos: rótulo pelos dispositivos
    z = {c: 0 for c in DISP_IDS}
    for cont, esperado in (({"mantido": 3}, "mantido"), ({"rejeitado": 2}, "derrubado"), ({"mantido": 1, "rejeitado": 1}, "derrubado_em_parte"),
                           ({"mantido": 1, "nao_apreciado": 1}, "em_tramitacao"), ({"prejudicado": 4}, "prejudicado"),
                           ({"mantido": 2, "prejudicado": 5}, "mantido"), ({"rejeitado": 1, "prejudicado": 1}, "derrubado")):
        igual(rotulo_veto(z | cont), esperado, f"rótulo {cont}")
    enc, tram = {"16001", "16002", "16003"}, {"16004"}

    def cv(v, ds, votos=None):
        return classificar_veto(v, ds, votos or {}, tram, enc)
    item, mot = cv(veto(1), [disp("1.23.000", "Rejeitado", desc="Veto total")])
    igual((mot, item["situacao"], item["dispositivos"][0][5:], item["resultadoPdf"]), (None, "derrubado", [None, None], []),
          "veto total derrubado: sem placar (e sem PDF quando a fonte não traz)")
    item, _ = classificar_veto(veto(2, total=False, qtd=1), [disp("2.23.001", "Mantido", cod=7)], {"7": vt(10)}, tram, enc,
                               [{"url": "https://legis.senado.leg.br/r/x", "descricao": None}])
    igual(item["resultadoPdf"], [], "veto parcial não leva o PDF (o placar está nos dados abertos)")
    igual(cv(veto(5), [disp("5.23.000", "Mantido")])[1] is not None, True, "em nenhuma das listas de situação: retido")
    igual(cv(veto(4), [disp("4.23.000", "Mantido")])[1] is not None, True, "lista diz em tramitação, campo diz encerrado: retido")
    item, mot = cv(veto(4, tram="Sim"), [disp("4.23.000", "Não Apreciado", votos="Não")])
    igual((mot, item["situacao"], item["sobrestandoDesde"]), (None, "em_tramitacao", "2023-05-01"), "em tramitação, sobrestando a pauta")
    igual(cv(veto(1, tram="Sim"), [disp("1.23.000", "Não Apreciado", votos="Não")])[1] is not None, True, "campo em tramitação fora da lista: retido")
    igual(cv(veto(1), None)[1] is not None, True, "sem resultado na fonte: retido")
    igual(cv(veto(1), [disp("a", "Mantido"), disp("b", "Mantido")])[1] is not None, True, "veto total com 2 dispositivos: retido")
    p2 = veto(2, total=False, qtd=2)
    item, mot = cv(p2, [disp("2.23.001", "Rejeitado", cod=7), disp("2.23.002", "Mantido", cod=8)], {"7": vt(300, 50), "8": vt(100)})
    igual((mot, item["situacao"], item["dispositivos"][0][5:], item["dispositivos"][1][6]), (None, "derrubado_em_parte", [[10, 300, 0, 0], [5, 50, 0, 0]], None),
          "parcial: derrubado em parte, placar por Casa; Senado não apurado no mantido")
    igual(item["contagem"], {"nao_apreciado": 0, "mantido": 1, "rejeitado": 1, "prejudicado": 0}, "contagem dos dispositivos")
    ruins = {"Câmara abaixo de 257": {"7": vt(256, 50), "8": vt(100)}, "Senado abaixo de 41": {"7": vt(300, 40), "8": vt(100)},
             "Senado não apurado": {"7": vt(300), "8": vt(100)}, "mantido com os dois números": {"7": vt(300, 50), "8": vt(300, 41)},
             "outra parte vetada": {"7": vt(300, 50, parte="art. 9º"), "8": vt(100)},
             "outra sessão": {"7": vt(300, 50, sessao="Sessão Conjunta nº 3 de 11/05/2023"), "8": vt(100)},
             "votação ausente": {"8": vt(100)}, "fonte sem votação": {"7": {"erro": "a fonte não traz a votação deste dispositivo"}, "8": vt(100)}}
    for nome, votos in ruins.items():
        igual(cv(p2, [disp("2.23.001", "Rejeitado", cod=7), disp("2.23.002", "Mantido", cod=8)], votos)[1] is not None, True, f"retido: {nome}")
    igual(cv(veto(3, total=False, qtd=3), [disp("3.23.001", "Mantido", cod=9)], {"9": vt(10)})[1] is not None, True,
          "a lista diz 3 dispositivos e o resultado traz 1: retido")
    for nome, ds, votos in (("situação nova", [disp("x", "Sobrestado")], {}),
                            ("tipo de voto novo", [disp("2.23.001", "Mantido", cod=7), disp("2.23.002", "Mantido", cod=8)],
                             {"7": vt(10, extra={"Obstrução": 3}), "8": vt(10)})):
        try:
            cv(veto(3) if nome == "situação nova" else p2, ds, votos)
            igual(True, False, f"{nome} devia parar a rodada")
        except FonteFalhou:
            ok += 1
    igual(data_da_sessao("Sessão Conjunta nº 4 de 09/05/2024 às 10:00h"), "2024-05-09", "data do texto da sessão")
    igual(placar({"Sim": 3, "Não": 300, "Abstenção": 2, "Branco": 4, "Art. 17": 1}), [3, 300, 2, 4], "em branco contado; Art. 17 (quem preside) fora")
    # lista do ano: numeração contínua; ano fechado vazio para; ano corrente vazio passa (a main confere nas outras listas)
    for nome, dados, ano in (("buraco", [veto(1), veto(3)], 2023), ("repetido", [veto(1), veto(1)], 2023), ("fechado vazio", [], 2023),
                             ("formato", [veto(1) | {"Total": "Talvez"}], 2023)):
        try:
            conferir_lista_vetos(dados, ano, date(2026, 11, 8))
            igual(True, False, f"lista de vetos com {nome} devia parar")
        except FonteFalhou:
            ok += 1
    conferir_lista_vetos([], 2026, date(2026, 1, 5))
    ok += 1
    # montar: retido fora das contagens; dispositivos só dos parciais; checagens passam
    vv = montar_vetos(mand, {2023: [veto(1), p2, veto(3, data="2022-12-30")]}, set(), enc,
                      {"16001": {"dispositivos": [disp("1.23.000", "Mantido", desc="Veto total")],
                                 "pdfs": [{"url": "https://legis.senado.leg.br/r/16001", "descricao": "Resultado da cédula"}]},
                       "16002": {"dispositivos": [disp("2.23.001", "Rejeitado", cod=7), disp("2.23.002", "Mantido", cod=8)], "pdfs": []}},
                      {"7": vt(256, 50), "8": vt(100)})
    igual(vv["itens"][0]["resultadoPdf"], [{"url": "https://legis.senado.leg.br/r/16001", "descricao": "Resultado da cédula"}],
          "veto total leva o link do resultado oficial (PDF), sem ler o arquivo")
    igual((vv["total"], len(vv["retidos"]), vv["porAno"][0]["noMandato"], vv["dispositivos"]["mantido"]), (1, 1, 2, 0),
          "veto de antes do mandato fica fora; o retido sai das contagens; o veto total não entra nos dispositivos")
    s2 = montar(mand, sen, cam, {1003: det(3)}) | {"vetos": vv}
    igual(checagens(s2), [], "checagens passam com o veto retido declarado")
    igual(validar_esquema(s2), [], "esquema aceita os vetos")
    x2 = json.loads(serializar(s2))
    igual(x2 == json.loads(json.dumps(s2, ensure_ascii=False)), True, "a gravação um veto por linha é o mesmo JSON")
    x2["vetos"]["itens"][0]["placarTotal"] = 1
    igual(bool(validar_esquema(x2)), True, "campo extra no veto é recusado")
    x3 = json.loads(serializar(s2))
    x3["vetos"]["itens"][0]["dispositivos"][0].append("a mais")
    igual(bool(validar_esquema(x3)), True, "campo a mais no dispositivo é recusado")
    pubv = {"_versao": VERSAO, "mandato": {"id": "lula3"}, "mps": {"total": 2}, "vetos": {"total": 200}}
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 2}, "vetos": {"total": 179}}, pubv) != [], True, "vetos: queda de 10,5%: para")
    igual(variacao({"mandato": {"id": "lula3"}, "mps": {"total": 2}, "vetos": {"total": 190}}, pubv), [], "vetos: 5%: segue")
    # 12. rodada inteira: o resultado dos vetos falha (HTTP 503) → nada publicado; ano corrente vazio com veto nas outras listas → nada publicado
    for nome in ("503", "vazio"):
        with tempfile.TemporaryDirectory() as tt:
            T = Path(tt)
            pub = T / "presidencia.json"
            pub.write_text('{"intacto":1}', encoding="utf-8")

            def falso(url, params=None, tentativas=3, nome=nome, **k):
                if "camara" in url:
                    return {"dados": [{"id": 7, "numero": "10", "ano": 2023}], "links": []}
                if url.endswith("/processo"):
                    return [mp(10)]
                if nome == "503" and "/resultado/veto/" in url:
                    raise FonteFalhou("HTTP 503, 'Service Unavailable'")
                if nome == "vazio" and url.endswith("/materia/vetos/2023"):
                    return None                        # 404 aceito no ano corrente
                return vetos_falsos(url)
            with mock.patch(f"{__name__}.pedir", falso), mock.patch(f"{__name__}.CACHE_P", T / "cache"), mock.patch(f"{__name__}.SAIDA", pub), \
                    mock.patch(f"{__name__}.CANDIDATO", T / "cand.json"), mock.patch(f"{__name__}.RELATORIO", T / "rel.txt"), \
                    mock.patch(f"{__name__}.AMOSTRA", T / "amostra.csv"), mock.patch(f"{__name__}.INTERVALO", 0), \
                    mock.patch(f"{__name__}.agora", lambda: datetime(2023, 6, 1, 8, tzinfo=BRASILIA)):
                saiu = None
                try:
                    main(["--publicar"], hoje=date(2023, 6, 1))
                except SystemExit as e:
                    saiu = e.code
                igual((saiu not in (None, 0), pub.read_text(encoding="utf-8")), (True, '{"intacto":1}'), f"vetos com erro ({nome}): para e o publicado fica")
    # 13. ritmo: 2 por segundo somando as threads; Retry-After em segundos ou em data HTTP, com teto
    class R:
        def __init__(self, h):
            self.headers = h
    igual(espera_pedida(R({"Retry-After": "7"}), 1), 7.0, "Retry-After em segundos")
    igual(espera_pedida(R({"Retry-After": "9999"}), 1), float(ESPERA_MAX), "Retry-After acima do teto vira o teto")
    igual(espera_pedida(R({}), 4), 4, "sem Retry-After: a espera padrão")
    futura = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    igual(25 <= espera_pedida(R({"Retry-After": futura}), 1) <= 31, True, "Retry-After em data HTTP")
    _proximo[0] = 0.0
    t0 = time.monotonic()
    em_paralelo(lambda _: no_ritmo(0.1), range(6))
    igual(time.monotonic() - t0 >= 0.45, True, "6 começos em paralelo com 0,1 s de intervalo levam pelo menos 0,5 s (um por vez)")
    print(f"{ok} verificações passaram")


# ---------------------------------------------------------------- rodada

def main(argv=None, hoje=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="o padrão: candidato, relatório e amostra em cache/")
    modo.add_argument("--publicar", action="store_true", help="grava site/dados/presidencia.json (só se as checagens passarem)")
    ap.add_argument("--so-montar", action="store_true", help="sem rede: remonta do cache")
    ap.add_argument("--minutos", type=float, default=10, help="prazo da rodada (padrão 10)")
    ap.add_argument("--teste", action="store_true", help="casos sintéticos, sem rede")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    t0, PRAZO[0] = time.monotonic(), time.monotonic() + args.minutos * 60
    hoje = hoje or agora().date()
    pedidos = [0]
    original = pedir

    def contando(*a, **k):
        pedidos[0] += 1
        return original(*a, **k)
    try:
        globals()["pedir"] = contando
        mandato = carregar_mandato(hoje)
        anos = anos_do_mandato(mandato, hoje)
        print(f"Presidente: {mandato['rotulo']} desde {mandato['inicio']}; MPs de {anos[0]} a {anos[-1]}…", flush=True)
        sen = {a: lista_senado(a, args.so_montar) for a in anos}
        cam = {a: lista_camara(a, args.so_montar) for a in anos}
        aprovadas = [m for a in anos for m in sen[a] if m.get("tramitando") == "Não" and (
            (m.get("situacaoAtual") or "") in {s for s, c in POR_SITUACAO.items() if c in ("convertida", "aprovada_sem_lei")}
            or (not m.get("situacaoAtual") and POR_DELIBERACAO.get(m.get("siglaTipoDeliberacao") or "") == "aprovada_sem_lei"))]
        detalhes = {m["id"]: detalhe(m, args.so_montar) for m in aprovadas}
        saida = montar(mandato, sen, cam, detalhes)
        # vetos: listas do ano, as duas listas de situação, o resultado de cada veto e os votos de cada dispositivo votado de veto parcial
        lv = {a: lista_vetos(a, args.so_montar, hoje) for a in anos}
        sit = listas_situacao(args.so_montar)
        for a in anos:
            if not lv[a] and any(x["ano"] == str(a) for x in sit["tramitando"] + sit["encerrados"]):
                raise FonteFalhou(f"Congresso, vetos de {a}: a lista do ano veio vazia, mas as listas de em tramitação e encerrados têm vetos de {a}")
        do_mandato = [v for a in anos for v in lv[a] if mandato["inicio"] <= str(v.get("DataPublicacao") or "")[:10] <= (mandato["fim"] or "9999")]
        res = dict(zip([str(v["Codigo"]) for v in do_mandato], em_paralelo(lambda v: resultado_veto(v, args.so_montar), do_mandato)))
        alvos = [x for v in do_mandato if v["Total"] == "Não" for x in ((res.get(str(v["Codigo"])) or {}).get("dispositivos") or [])
                 if x.get("PossuiVotos") == "Sim" and x.get("Codigo")]
        votos = dict(zip([str(x["Codigo"]) for x in alvos], em_paralelo(lambda x: votos_dispositivo(x, args.so_montar), alvos)))
        saida["vetos"] = montar_vetos(mandato, lv, {x["codigo"] for x in sit["tramitando"]}, {x["codigo"] for x in sit["encerrados"]}, res, votos)
    except FonteFalhou as e:
        sys.exit(f"fonte com erro: {e}. Nada publicado; site/dados/presidencia.json fica como estava.")
    finally:
        globals()["pedir"] = original
    publicado = coleta.ler_cache(SAIDA)
    erros = checagens(saida) + variacao(saida, publicado) + validar_esquema(saida)
    gravar(CANDIDATO, saida)
    coleta.gravar_texto(AMOSTRA, amostra(saida), newline="\n")
    texto = relatorio(saida, erros, args, time.monotonic() - t0, pedidos[0])
    coleta.gravar_texto(RELATORIO, texto + "\n", newline="\n")
    print(texto)
    if erros:
        sys.exit(f"checagem falhou ({len(erros)}): nada publicado")
    if args.publicar:
        gravar(SAIDA, saida)
        print(f"site/dados/presidencia.json publicado: {saida['mps']['total']} MPs e {saida['vetos']['total']} vetos.")
    else:
        print(f"dry-run: candidato em {CANDIDATO}, amostra em {AMOSTRA}")


if __name__ == "__main__":
    main()
