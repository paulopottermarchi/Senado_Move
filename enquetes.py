"""
Câmara Aberta — enquete da Câmara sobre cada lei votada (consulta pública).

Uso:
    python enquetes.py           # consulta as que faltam e as ainda abertas
    python enquetes.py --todas   # consulta de novo todas

Rodar DEPOIS de votos.py: lê proposicoes.json (as votações finais de texto-base) e
grava enquetes.json, lido por leis.html e pelo gráfico 2 do protótipo.

Fonte: a página de resultado da enquete de cada proposição,
    https://www.camara.leg.br/enquetes/{idProposicao}/resultados
Os dados abertos da Câmara não têm enquetes (nem API nem arquivo em lote). A página é
pública, sem login, e /enquetes não é vedado no robots.txt. Uma requisição por segundo.
Enquete encerrada não muda e é consultada uma vez só; aberta é consultada de novo a
cada rodada.

Enquete NÃO é pesquisa de opinião: participa quem procurou o site, não uma amostra do
país, e campanha organizada enche uma enquete em poucos dias. O arquivo guarda as
contagens das cinco opções como a Câmara publica; o site mostra sempre o número de
participações ao lado de qualquer percentual.

Falha de rede não aborta: a proposição fica sem enquete nesta rodada e o resto segue.
Página em formato inesperado (a Câmara mudou o leiaute) aborta sem gravar.
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

import coleta  # truststore, ler_cache/gravar_cache atômicos
from semana import BRASILIA

URL = "https://www.camara.leg.br/enquetes/{id}/resultados"
CACHE_ENQ = coleta.CACHE / "enquetes"
PROPOSICOES = coleta.BASE / "proposicoes.json"
SAIDA = coleta.BASE / "enquetes.json"
INTERVALO = 1.0  # segundos entre requisições: é o site, não a API
OPCOES = ["Concordo totalmente", "Concordo na maior parte", "Estou indeciso",
          "Discordo na maior parte", "Discordo totalmente"]

sessao = requests.Session()
sessao.headers["User-Agent"] = ("camara-aberta/1.0 (dados públicos; "
                                "github.com/paulopottermarchi/Senado_Move)")

_TITULO = re.compile(r"<h1[^>]*>\s*Enquete d[oa]\s+(.*?)\s*</h1>", re.S)
_SITUACAO = re.compile(r"Resultado\s+(final|parcial)\s+desde\s+(\d{2}/\d{2}/\d{4})")
_ENCERRADA = re.compile(r"Enquete encerrada em\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4})")
_LINHA = re.compile(
    r'resumo-resposta__opiniao[^>]*>\s*(.*?)\s*</th>\s*'
    r'<td[^>]*resumo-resposta__participacoes[^>]*>\s*([\d.]+)\s*</td>\s*'
    r'<td[^>]*resumo-resposta__percentual[^>]*>\s*(\d+)%', re.S)


class LeiauteMudou(Exception):
    pass


def iso(data_br):
    d, m, a = data_br.split("/")
    return f"{a}-{m}-{d}"


def numero_base(numero):
    """'PEC 66/2023 (Fase 1 - CD)' → 'PEC 66/2023'. A enquete usa só sigla e número."""
    m = re.match(r"\s*([A-Z]+)\s+(\d+)/(\d{4})", numero or "")
    return f"{m[1]} {int(m[2])}/{m[3]}" if m else None


def interpretar(texto, numero):
    """Resultado da página → {votos, total, situacao, inicio, encerradaEm}.
    Qualquer desvio do formato conhecido levanta LeiauteMudou."""
    t = _TITULO.search(texto)
    if not t:
        raise LeiauteMudou("sem o título 'Enquete do …'")
    titulo = html.unescape(re.sub(r"<[^>]+>", "", t[1])).strip()
    if not numero_base(titulo):
        raise LeiauteMudou(f"título sem número de proposição: {titulo!r}")
    linhas = [(html.unescape(o).strip(), int(n.replace(".", "")), int(p))
              for o, n, p in _LINHA.findall(texto)]
    if [o for o, _, _ in linhas] != OPCOES:
        raise LeiauteMudou(f"opções inesperadas: {[o for o, _, _ in linhas]}")
    votos = [n for _, n, _ in linhas]
    total = sum(votos)
    # O percentual publicado tem de bater com a contagem — com folga: a Câmara força a
    # soma a 100 jogando a sobra do arredondamento numa opção (PEC 221/2019: 1.443 de
    # 1.764 é 81,8%, publicado 83%). A contagem é o dado; o site calcula o percentual
    # dela. A conferência existe para pegar coluna trocada, não o arredondamento.
    for o, n, p in linhas:
        if total and abs(100 * n / total - p) > 3:
            raise LeiauteMudou(f"{o}: {n} de {total} não dá {p}%")
    if total and not 97 <= sum(p for _, _, p in linhas) <= 103:
        raise LeiauteMudou(f"percentuais somam {sum(p for _, _, p in linhas)}%")
    s = _SITUACAO.search(texto)
    if not s:
        raise LeiauteMudou("sem 'Resultado final/parcial desde'")
    e = _ENCERRADA.search(texto)
    if (s[1] == "final") != bool(e):
        raise LeiauteMudou("resultado final sem data de encerramento, ou o inverso")
    # A página é endereçada pelo id, então é da proposição certa mesmo com outro número:
    # a Câmara renumera proposições (id 106701 era o PL 347/2003 e hoje é o PL 5809/2025;
    # a enquete ficou com o número antigo). O site avisa, para quem abrir o link.
    outro = numero_base(titulo) if numero_base(titulo) != numero_base(numero) else None
    return {"votos": votos, "total": total,
            "situacao": "encerrada" if e else "aberta",
            "inicio": iso(s[2]), "encerradaEm": iso(e[1]) if e else None,
            **({"numeroNaEnquete": outro} if outro else {})}


def consultar(id_prop, numero):
    """Página da enquete → dicionário, ou None se a rede falhou (tenta de novo na
    próxima rodada). 404 = a proposição não tem enquete."""
    for n in range(3):
        try:
            r = sessao.get(URL.format(id=id_prop), timeout=40)
        except requests.RequestException as e:
            erro = str(e)
        else:
            if r.status_code == 404:
                return {"situacao": "sem enquete"}
            if r.status_code == 200:
                return interpretar(r.content.decode("utf-8", "replace"), numero)
            erro = f"HTTP {r.status_code}"
        time.sleep(5 * (n + 1))
    print(f"   {numero}: falhou ({erro}) — fica sem enquete nesta rodada", flush=True)
    return None


def gravar(obj):
    """Uma proposição por linha: o diff diário mostra só as enquetes que mudaram."""
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text("{\n" + ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:"
                                      f"{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                                      for k, v in obj.items()) + "\n}\n", encoding="utf-8")
    os.replace(tmp, SAIDA)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--todas", action="store_true", help="consultar de novo todas")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    props = coleta.ler_cache(PROPOSICOES)
    if not props:
        sys.exit(f"{PROPOSICOES.name} não existe — rode votos.py antes.")
    hoje = datetime.now(BRASILIA).date().isoformat()

    saida, consultadas, falhas = {}, 0, 0
    for p in props:
        id_prop, numero = p["idProposicao"], p["numero"]
        caminho = CACHE_ENQ / f"{id_prop}.json"
        e = coleta.ler_cache(caminho)
        # Encerrada não muda. Aberta (ou sem enquete) é consultada de novo uma vez por dia.
        if args.todas or e is None or (e["situacao"] != "encerrada"
                                       and e.get("consultadaEm") != hoje):
            if consultadas:
                time.sleep(INTERVALO)
            try:
                novo = consultar(id_prop, numero)
            except LeiauteMudou as erro:
                sys.exit(f"\nPÁGINA EM FORMATO INESPERADO — {numero} (id {id_prop}): {erro}\n"
                         f"Nada foi gravado. Conferir {URL.format(id=id_prop)}")
            consultadas += 1
            if novo is None:
                falhas += 1
            else:
                e = {**novo, "consultadaEm": hoje}
                coleta.gravar_cache(caminho, e)
        if e is not None:
            saida[str(id_prop)] = e

    com = [e for e in saida.values() if e["situacao"] != "sem enquete"]
    gravar({
        "_fonte": "Enquetes da Câmara dos Deputados, página de resultado de cada proposição",
        "_url": URL.format(id="{idProposicao}"),
        "_opcoes": OPCOES,
        "_aviso": "Participa quem procurou o site, não uma amostra da população.",
        **dict(sorted(saida.items())),
    })

    tot = sorted(e["total"] for e in com)
    nome = {str(p["idProposicao"]): p["numero"] for p in props}
    print(f"\nConsultadas nesta rodada: {consultadas} · falhas de rede: {falhas}")
    print(f"Com enquete: {len(com)} de {len(props)} · encerradas: "
          f"{sum(e['situacao'] == 'encerrada' for e in com)} · abertas: "
          f"{sum(e['situacao'] == 'aberta' for e in com)} · sem enquete: "
          f"{sum(e['situacao'] == 'sem enquete' for e in saida.values())} · "
          f"não consultadas: {len(props) - len(saida)}")
    if tot:
        print(f"Participações: mediana {tot[len(tot) // 2]} · máx {tot[-1]} · "
              f"menos de 500: {sum(t < 500 for t in tot)} · zero: {sum(t == 0 for t in tot)}")
        maiores = sorted(((e["total"], k) for k, e in saida.items() if e in com), reverse=True)[:5]
        for t, k in maiores:
            print(f"   {nome[k]:<18} {t:>9,}".replace(",", "."))
    outros = [(nome[k], e["numeroNaEnquete"]) for k, e in saida.items() if e.get("numeroNaEnquete")]
    if outros:
        print(f"Enquete com outro número (renumeração da Câmara — conferir): {outros}")
    print(f"{SAIDA.name} gravado.")


if __name__ == "__main__":
    main()
