"""
Câmara Aberta — coleta, Etapa A (deputados e proposições via API).

Uso:
    python coleta.py              # 10 primeiros deputados (validação)
    python coleta.py --limite 513 # todos — só depois de validar os 10

Cache em disco (cache/): pode interromper com Ctrl+C e rodar de novo; o que já
foi baixado não é pedido outra vez. O cache guarda só respostas cruas da API —
os números são sempre recalculados a partir dele, então mudar uma regra de
contagem não exige baixar nada de novo. Para forçar dados frescos, apague cache/.

Passo 9 (join com ideologia.json) roda sobre o resultado, sem chamar a API.
Fora desta etapa (ficam null no JSON): resumo por LLM (passo 8), apoioCruzado
(Etapa B) e relatorias (ver RELATORIAS).
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    # O servidor da Câmara não envia a cadeia intermediária do certificado.
    # truststore usa o repositório do sistema operacional, que completa a cadeia.
    # Não desligamos a verificação TLS.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    sys.exit("Falta dependência: pip install -r requirements.txt")

import requests

API = "https://dadosabertos.camara.leg.br/api/v2"
TAXA_MAX = 10          # req/s — limite declarado da API
WORKERS = 8
TIPOS = ("PEC", "PL")  # apresentadas = PECs + PLs (CLAUDE.md, "Eixo Y")

# Situação oficial de tramitação. Conta como lei SÓ isto — nunca inferir.
SITUACAO_LEI = "Transformado em Norma Jurídica"
COD_SITUACAO_LEI = 1140

# Federações partidárias registradas no TSE para a legislatura 2023–2027.
# Informação secundária (card); o eixo usa sempre o partido individual.
# CONFERIR contra o TSE antes de publicar — federações novas podem ter surgido.
FEDERACOES = {
    "PT": "PT-PCdoB-PV", "PCdoB": "PT-PCdoB-PV", "PV": "PT-PCdoB-PV",
    "PSDB": "PSDB-CIDADANIA", "CIDADANIA": "PSDB-CIDADANIA",
    "PSOL": "PSOL-REDE", "REDE": "PSOL-REDE",
}

# RELATORIAS: a API de dados abertos não permite listar as proposições
# relatadas por um deputado. /proposicoes/{id}/autores traz só autores, e o
# relator aparece apenas por proposição (statusProposicao.uriUltimoRelator e
# tramitações). Contar exigiria varrer todas as proposições da Câmara. Até haver
# fonte validada, gravamos null — nunca um número de cobertura desconhecida.
RELATORIAS = None

BASE = Path(__file__).resolve().parent
CACHE = BASE / "cache"
SAIDA = BASE / "deputados.json"
IDEOLOGIA = BASE / "ideologia.json"
FAIXAS = ("esquerda", "centro-esquerda", "centro", "centro-direita", "direita")


# ---------------------------------------------------------------- HTTP

class Limitador:
    """Espaça as requisições em 1/TAXA_MAX s, somadas entre todas as threads."""

    def __init__(self, por_segundo):
        self.intervalo = 1.0 / por_segundo
        self.proximo = 0.0
        self.trava = threading.Lock()

    def esperar(self):
        with self.trava:
            agora = time.monotonic()
            vez = max(agora, self.proximo)
            self.proximo = vez + self.intervalo
        if vez > agora:
            time.sleep(vez - agora)


limitador = Limitador(TAXA_MAX)
sessao = requests.Session()
sessao.headers["Accept"] = "application/json"
sessao.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=WORKERS + 2))
contador_req = 0
_trava_contador = threading.Lock()


def get(url, params=None, tentativas=6):
    global contador_req
    for n in range(tentativas):
        limitador.esperar()
        with _trava_contador:
            contador_req += 1
        try:
            r = sessao.get(url, params=params, timeout=60)
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(2 ** n)
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** n)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"falhou após {tentativas} tentativas: {url} {params or ''}")


def get_paginado(url, params):
    """Segue o link `next` do bloco `links` até acabar."""
    dados = []
    while url:
        r = get(url, params)
        params = None  # o `next` já traz a query completa
        if r is None:
            break
        dados += r["dados"]
        url = next((l["href"] for l in r.get("links", []) if l["rel"] == "next"), None)
    return dados


# ---------------------------------------------------------------- cache

def ler_cache(caminho):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def gravar_cache(caminho, obj):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, caminho)  # atômico: um Ctrl+C nunca deixa arquivo pela metade


# ---------------------------------------------------------------- coleta

def lista_deputados():
    caminho = CACHE / "deputados_lista.json"
    lista = ler_cache(caminho)
    if lista is None:
        lista = get_paginado(f"{API}/deputados",
                             {"itens": 100, "ordem": "ASC", "ordenarPor": "nome"})
        gravar_cache(caminho, lista)
    return lista


def proposicoes_do_deputado(id_dep):
    """Lista crua de PEC/PL em que o deputado aparece como autor (cache por deputado)."""
    caminho = CACHE / "deputados" / f"{id_dep}.json"
    lista = ler_cache(caminho)
    if lista is None:
        lista = get_paginado(f"{API}/proposicoes",
                             {"idDeputadoAutor": id_dep, "siglaTipo": ",".join(TIPOS),
                              "itens": 100, "ordem": "ASC", "ordenarPor": "id"})
        gravar_cache(caminho, lista)
    return lista


def dados_da_proposicao(id_prop, id_dep):
    """Autores + (se o deputado for autor) detalhe e votações de uma proposição.
    Cache por proposição — coautorias entre deputados reaproveitam o arquivo.
    Detalhe e votações só são baixados quando alguém precisa deles: para quem
    apenas assinou apoiamento, os autores bastam."""
    caminho = CACHE / "proposicoes" / f"{id_prop}.json"
    d = ler_cache(caminho) or {}
    mudou = False
    if "autores" not in d:
        aut = get(f"{API}/proposicoes/{id_prop}/autores")
        d["autores"] = aut["dados"] if aut else []
        mudou = True
    if "detalhe" not in d and e_autor(d["autores"], id_dep):
        det = get(f"{API}/proposicoes/{id_prop}")
        vot = get(f"{API}/proposicoes/{id_prop}/votacoes")
        d["detalhe"] = det["dados"] if det else None
        d["votacoes"] = vot["dados"] if vot else []
        mudou = True
    if mudou:
        gravar_cache(caminho, d)
    return d


# ---------------------------------------------------------------- regras

def e_autor(autores, id_dep):
    """Autoria = proponente 1. PEC exige 171 assinaturas de apoiamento; essas
    vêm com proponente 0 e NÃO são autoria."""
    sufixo = f"/deputados/{id_dep}"
    return any(a.get("uri", "").endswith(sufixo) and a.get("proponente") == 1
               for a in autores)


def virou_lei(detalhe):
    st = (detalhe or {}).get("statusProposicao") or {}
    return (st.get("descricaoSituacao") or "").strip() == SITUACAO_LEI \
        or st.get("codSituacao") == COD_SITUACAO_LEI


# Cada rótulo é lido separadamente: o texto da API é digitado à mão e vem com
# variações — "Sim: 300; não; 83; total; 385." (PL 1998/2020), "Sim 219",
# "Sim, 10", "13 votos "Sim"". Aspas não entram no separador do primeiro padrão:
# em `votos "Sim", 27 votos "Não"` o 27 é do Não, não do Sim.
def _rotulo(nome):
    return (re.compile(rf"\b{nome}\b[\s:;,]*(\d+)", re.I),
            re.compile(rf"(\d+)\s+votos?\s+\"?{nome}\b", re.I))


_PLACAR = {"sim": _rotulo("Sim"), "nao": _rotulo("N[ãa]o"),
           "abstencao": _rotulo("Absten[çc][ãa]o"),
           "total": _rotulo("Total(?: de votantes)?")}


def _ler(padroes, texto):
    for r in padroes:
        if m := r.search(texto):
            return int(m.group(1))
    return None


# Votação que representa a proposição: a do texto-base, em Plenário.
# Lista de PERMISSÃO, não de exclusão: o OBJETO do verbo — o que foi aprovado ou
# rejeitado — tem de ser o próprio texto-base. A versão anterior excluía palavras
# ("Requerimento", "Destaque"…) e vazava o que não estava na lista:
#   "Rejeitado o Recurso nº 33/2023, contra a apreciação conclusiva… sobre o
#    Projeto de Lei nº 3.905" (PL 3905/2021 — rejeitar o recurso mantém a
#    aprovação na comissão, e o projeto virou lei)
#   "Rejeitada a Emenda de Plenário ao Substitutivo." (PL 3042/2021)
# "Sube?menda": a API grafa "Submenda Substitutiva Global" às vezes.
_TEXTO_BASE = re.compile(
    r"^(Aprovad|Rejeitad)[oa]s?(?:,[^,]*,)?\s+(?:o|a|os|as)\s+"  # "Aprovada, em 1º turno, a"
    r"(Projeto de Lei|Proposta de Emenda|Substitutivo|Sube?menda Substitutiva"
    r"|Emenda Substitutiva)")


def e_texto_base(v):
    """A votação `v` (dict com siglaOrgao e descricao — da API ou do CSV em bulk)
    decide o texto-base em Plenário? Usada nas Etapas A e B."""
    if v.get("siglaOrgao") != "PLEN":
        return False
    desc = (v.get("descricao") or "").strip()
    cabeca = desc.split(".")[0]  # a oração principal; o placar vem depois
    if not _TEXTO_BASE.match(cabeca):
        return False
    # Na volta do Senado a Câmara vota as MUDANÇAS do Senado ("Rejeitado o
    # Substitutivo do Senado…"), não o projeto: rejeitá-las mantém o texto da
    # Câmara, que depois vira lei.
    return "Senado" not in cabeca


def votacao_texto_base(votacoes):
    candidatas = [v for v in votacoes if e_texto_base(v)]
    # a mais recente é a final (ex.: 2º turno de PEC)
    return max(candidatas, key=lambda v: v.get("dataHoraRegistro") or "", default=None)


def placar(votacao):
    """Placar só existe no texto de `descricao`, e só em votação nominal.
    Votação simbólica não tem placar → None (não é zero)."""
    desc = (votacao or {}).get("descricao") or ""
    v = {k: _ler(padroes, desc) for k, padroes in _PLACAR.items()}
    if v["sim"] is None or v["nao"] is None:
        return None
    v["abstencao"] = v["abstencao"] or 0
    v["total"] = v["total"] or v["sim"] + v["nao"] + v["abstencao"]
    return v


def status_da_votacao(votacao):
    """Regra do projeto: resultado ausente ou vazio → "tramitacao". Nunca inferir.
    (A API não tem `tipoResultado`; o campo equivalente é `aprovacao`: 1, 0 ou null.)"""
    ap = (votacao or {}).get("aprovacao")
    if ap == 1:
        return "aprovada"
    if ap == 0:
        return "rejeitada"
    return "tramitacao"


def titulo_curto(ementa, limite=110):
    e = re.sub(r"\s+", " ", ementa or "").strip()
    if len(e) <= limite:
        return e
    return e[:limite].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def processar_deputado(dep):
    id_dep = dep["id"]
    brutas = proposicoes_do_deputado(id_dep)
    with ThreadPoolExecutor(WORKERS) as ex:
        dados = list(ex.map(lambda p: dados_da_proposicao(p["id"], id_dep), brutas))

    pecs = leis = vir = aprovadas = nominais = 0
    so_apoiamento = 0
    itens = []
    violacoes = []
    for p, d in zip(brutas, dados):
        if not e_autor(d["autores"], id_dep):
            so_apoiamento += 1
            continue
        det = d["detalhe"] or {}
        if p["siglaTipo"] == "PEC":
            pecs += 1
        else:
            leis += 1
        lei = virou_lei(det)
        vir += lei
        vb = votacao_texto_base(d["votacoes"])
        pl = placar(vb)
        status = status_da_votacao(vb)
        aprovadas += status == "aprovada"
        nominais += pl is not None
        numero = f"{p['siglaTipo']} {p['numero']}/{p['ano']}"
        # Sanidade: rejeitar o texto-base e virar lei é contradição — sinal de
        # que a votação escolhida não é a que representa a proposição.
        if status == "rejeitada" and lei:
            violacoes.append({"deputado": dep.get("nome"), "numero": numero,
                              "idProposicao": p["id"], "votacao": vb.get("id"),
                              "descricao": vb.get("descricao")})
        itens.append({
            "titulo": titulo_curto(p.get("ementa")),
            "numero": numero,
            "votos": pl["total"] if pl else None,
            "status": status,
            "situacao": ((det.get("statusProposicao") or {}).get("descricaoSituacao")),
            "virouLei": lei,
            "nominal": pl is not None,
            "dataVotacao": vb.get("data") if vb else None,
            "resumo": None,  # passo 8 (LLM) — pulado nesta rodada
            "ementa": det.get("ementa") or p.get("ementa"),
            "urlCamara": "https://www.camara.leg.br/proposicoesWeb/"
                         f"fichadetramitacao?idProposicao={p['id']}",
        })

    # Top 3 por votos. Votação simbólica não tem placar; entre essas, desempata
    # quem teve votação de texto-base mais recente. Sem votação alguma, fica atrás.
    itens.sort(key=lambda i: (i["votos"] if i["votos"] is not None else -1,
                              i["dataVotacao"] or ""), reverse=True)

    partido = dep.get("siglaPartido")
    return violacoes, {
        "id": id_dep,
        "nome": dep.get("nome"),
        "partido": partido,
        "federacao": FEDERACOES.get(partido),
        "uf": dep.get("siglaUf"),
        "urlFoto": dep.get("urlFoto"),
        "score": None,       # passo 9 — preenchidos por aplicar_ideologia()
        "ideologia": None,
        "ideX": None,
        "scoreNota": None,
        "pecs": pecs,
        "leis": leis,
        "apresentadas": pecs + leis,
        "viraramLei": vir,
        # Texto-base aprovado em Plenário. NÃO é "aprovadas" em geral: aprovação
        # conclusiva em comissão não passa pelo Plenário, então este número pode
        # ser menor que viraramLei.
        "aprovadasPlenario": aprovadas,
        "relatorias": RELATORIAS,
        "apoioCruzado": None,  # Etapa B
        "votacoesNominais": nominais,
        "_apoiamentoPEC": so_apoiamento,  # assinaturas sem autoria — descartadas
        "topLeis": itens[:3],
    }


# ---------------------------------------------------------------- saída

def tabela(res):
    cols = [("Nome", "nome", 26), ("Partido", "partido", 13), ("UF", "uf", 3),
            ("PEC", "pecs", 4), ("PL", "leis", 4), ("Apresentadas", "apresentadas", 12),
            ("ViraramLei", "viraramLei", 10), ("Relatorias", "relatorias", 10)]
    fmt = lambda v: "—" if v is None else str(v)
    linha = "  ".join(f"{t:<{w}}" if i < 3 else f"{t:>{w}}" for i, (t, _, w) in enumerate(cols))
    print(linha)
    print("-" * len(linha))
    for d in res:
        print("  ".join(f"{fmt(d[k])[:w]:<{w}}" if i < 3 else f"{fmt(d[k]):>{w}}"
                        for i, (_, k, w) in enumerate(cols)))


# ---------------------------------------------------------------- passo 9

def carregar_ideologia():
    """Tabela partido → {"score": float | None, ...}. Chaves com _ são metadados."""
    try:
        bruto = json.loads(IDEOLOGIA.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"AVISO: {IDEOLOGIA.name} não encontrado — score/ideologia/ideX ficam null.")
        return {}
    return {k: v for k, v in bruto.items() if not k.startswith("_")}


def classificar(score):
    """Cinco faixas com os cortes do CLAUDE.md (definidos pelo projeto, não pelo
    BLS). Sem score → None; o frontend trata None como 'sem-classificacao'."""
    if score is None:
        return None
    if score < 3.50:
        return "esquerda"
    if score < 4.75:
        return "centro-esquerda"
    if score <= 6.25:
        return "centro"
    if score <= 7.50:
        return "centro-direita"
    return "direita"


def aplicar_ideologia(r, tabela):
    """Join pela sigla do partido individual (nunca a federação). Partido ausente
    da tabela ou com score null → os três campos null. Nunca estimar.
    scoreNota diz POR QUE não há score — "não consta da pesquisa" seria falso
    para PCdoB, PV e REDE, que constam mas com poucas respostas."""
    entrada = tabela.get(r["partido"])
    score = (entrada or {}).get("score")
    r["score"] = score
    r["ideologia"] = classificar(score)
    r["ideX"] = None if score is None else round((score - 1) / 9, 4)
    if score is not None:
        r["scoreNota"] = None
    elif entrada is None:
        r["scoreNota"] = f"partido ausente de {IDEOLOGIA.name}"
    else:
        r["scoreNota"] = entrada.get("nota") or f"score null em {IDEOLOGIA.name}, sem nota"


def resumo_ideologia(res, tabela):
    com = [d for d in res if d["score"] is not None]
    print(f"\nIdeologia ({IDEOLOGIA.name}): {len(com)} deputados com score · "
          f"{len(res) - len(com)} sem score")
    sem = Counter(d["partido"] for d in res if d["score"] is None)
    notas = {d["partido"]: d["scoreNota"] for d in res if d["score"] is None}
    for partido, n in sem.most_common():
        aviso = "  ← conferir a sigla" if partido not in tabela else ""
        print(f"   {partido:<14} {n:>3}  {notas[partido]}{aviso}")
    faixas = Counter(d["ideologia"] for d in com)
    print("Por faixa:")
    for f in FAIXAS:
        print(f"   {f:<16} {faixas[f]:>3}")
    print(f"   {'sem-classificacao':<16} {len(res) - len(com):>3}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--limite", type=int, default=10,
                    help="quantos deputados processar, na ordem da API (padrão 10)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    t0 = time.monotonic()
    ideologia = carregar_ideologia()
    deputados = lista_deputados()[:args.limite]
    resultado = []
    violacoes = []
    for n, dep in enumerate(deputados, 1):
        print(f"[{n}/{len(deputados)}] {dep['nome']} ({dep['siglaPartido']}-{dep['siglaUf']})",
              end="", flush=True)
        v, r = processar_deputado(dep)
        aplicar_ideologia(r, ideologia)
        resultado.append(r)
        violacoes += v
        print(f" — {r['apresentadas']} proposições, {r['viraramLei']} viraram lei"
              f" · {contador_req} req · {time.monotonic() - t0:.0f}s", flush=True)

    if violacoes:
        print(f"\nCHECAGEM DE SANIDADE FALHOU — {len(violacoes)} proposição(ões) com status "
              f"'rejeitada' e situação '{SITUACAO_LEI}'.\n{SAIDA.name} NÃO foi gravado "
              "(o cache foi preservado).\n")
        for v in violacoes:
            print(f"- {v['numero']} ({v['deputado']}) · idProposicao {v['idProposicao']}"
                  f" · votação {v['votacao']}\n    {v['descricao']}")
        sys.exit(2)

    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nChecagem de sanidade OK: nenhuma proposição 'rejeitada' virou lei.")
    print(f"{SAIDA.name} gravado · {len(resultado)} deputados · "
          f"{contador_req} requisições nesta execução · {time.monotonic() - t0:.0f}s\n")
    tabela(resultado)
    print("\nRelatorias: — = sem fonte validada na API (ver RELATORIAS em coleta.py).")
    resumo_ideologia(resultado, ideologia)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrompido. O cache foi preservado; rode de novo para continuar.")
