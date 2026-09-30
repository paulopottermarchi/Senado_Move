"""
Câmara Aberta — consulta pública do Senado (e-Cidadania) sobre cada lei votada.

Uso:
    python scripts/ecidadania.py           # consulta as que faltam e as ainda abertas
    python scripts/ecidadania.py --todas   # consulta de novo todas

Rodar DEPOIS de votos.py: lê proposicoes.json e grava ecidadania.json, lido por
leis.html e pelo gráfico 2, ao lado da enquete da Câmara (enquetes.py).

Toda proposição que tramita no Senado fica aberta a opinião no e-Cidadania, com as
respostas Sim e Não (Resolução 26/2013). Caminho, sem adivinhar nada pelo nome:
  1. Dados Abertos do Senado, /dadosabertos/processo?sigla&numero&ano — desde 2019 as
     duas casas usam numeração única (por isso a Câmara renumerou o PL 347/2003 como
     PL 5809/2025 ao enviá-lo). Só o registro do Senado (casaIdentificadora SF) com a
     mesma identificação.
  2. Confirmação no próprio registro do Senado (/dadosabertos/processo/{id}): a casa
     iniciadora é a Câmara com o mesmo número, ou o número da Câmara consta em
     outrosNumeros, ou a matéria começou no Senado com a mesma identificação. Sem
     confirmação, fica de fora e sai no relatório.
  3. A página da consulta, www12.senado.leg.br/ecidadania/visualizacaomateria?id=
     {codigoMateria} — contagens de Sim e Não como o Senado publica. O robots.txt do
     www12 não veda nada. Uma requisição por segundo.

Matéria que ainda não chegou ao Senado é procurada de novo a cada rodada; consulta
encerrada não muda e é lida uma vez. Consulta pública NÃO é pesquisa de opinião: o
site mostra sempre quantas pessoas votaram.

Falha de rede não aborta. Página ou registro em formato inesperado aborta sem gravar.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

import coleta  # truststore, ler_cache/gravar_cache atômicos
from enquetes import numero_base
from semana import BRASILIA

BUSCA = "https://legis.senado.leg.br/dadosabertos/processo"
PROCESSO = "https://legis.senado.leg.br/dadosabertos/processo/{id}"
PAGINA = "https://www12.senado.leg.br/ecidadania/visualizacaomateria?id={codigo}"
CACHE_SF = coleta.CACHE / "senado"
PROPOSICOES = coleta.DADOS / "proposicoes.json"
SAIDA = coleta.DADOS / "ecidadania.json"
INTERVALO = 1.0

sessao = requests.Session()
sessao.headers["User-Agent"] = ("camara-aberta/1.0 (dados públicos; "
                                "github.com/paulopottermarchi/Senado_Move)")

_FAVOR = re.compile(r'class="contabilizacao-favor"[^>]*>\s*([\d.]+)\s*<')
_CONTRA = re.compile(r'class="contabilizacao-contra"[^>]*>\s*([\d.]+)\s*<')
_FINAL = re.compile(r"RESULTADO\s+FINAL", re.I)
_ENCERRADA = re.compile(r"n[ãa]o\s+(?:é|&eacute;)\s+mais\s+pass[íi]vel\s+de\s+vota", re.I)


class FormatoMudou(Exception):
    pass


_ultima = [0.0]


def pedir(url, params=None, json_=True):
    """GET com intervalo mínimo e novas tentativas. None = falha de rede (tenta de novo
    na próxima rodada); 404 devolve {} (json) ou ''."""
    for n in range(3):
        espera = INTERVALO - (time.monotonic() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.monotonic()
        try:
            r = sessao.get(url, params=params, timeout=60,
                           headers={"Accept": "application/json"} if json_ else {})
        except requests.RequestException as e:
            erro = str(e)
        else:
            if r.status_code == 404:
                return {} if json_ else ""
            if r.status_code == 200:
                return r.json() if json_ else r.content.decode("utf-8", "replace")
            erro = f"HTTP {r.status_code}"
        time.sleep(5 * (n + 1))
    print(f"   {url}: falhou ({erro})", flush=True)
    return None


def materia_no_senado(numero):
    """Número da Câmara → (processo do Senado, motivo). Processo None com motivo quando
    não há matéria confirmada; (None, None) quando a rede falhou."""
    base = numero_base(numero)
    if not base:
        return None, "número em formato desconhecido"
    sigla, resto = base.split(" ")
    num, ano = resto.split("/")
    caminho = CACHE_SF / f"busca-{sigla}-{num}-{ano}.json"
    achados = coleta.ler_cache(caminho)
    if achados is None:
        achados = pedir(BUSCA, {"sigla": sigla, "numero": num, "ano": ano})
        if achados is None:
            return None, None
        if not isinstance(achados, list):
            raise FormatoMudou(f"busca de {base} não devolveu lista")
        if achados:  # vazio não vai para o cache: a matéria pode chegar ao Senado amanhã
            coleta.gravar_cache(caminho, achados)
    sf = [p for p in achados if p.get("casaIdentificadora") == "SF"
          and numero_base(p.get("identificacao")) == base]
    if not sf:
        # Não quer dizer que não chegou: matéria antiga ganha número novo no Senado (PL
        # 490/2007 virou PL 2903/2023), e a API do Senado não busca pelo número da Câmara.
        return None, "número não encontrado no Senado"
    if len(sf) > 1:
        # Dois registros (medido: 7 casos, todos matérias que começaram no Senado): o
        # original, onde a consulta aconteceu, e a volta do texto da Câmara, com sufixo
        # na identificação ("PL 458/2021 (Substitutivo-CD)", "PEC 66/2023 (fase 2)").
        # Vale o único sem sufixo.
        exato = [p for p in sf if (p.get("identificacao") or "").strip() == base]
        sf = exato if len(exato) == 1 else sf
    if len(sf) > 1:
        return None, f"{len(sf)} registros no Senado com o mesmo número"
    proc = sf[0]

    # O próprio resultado da busca confirma a origem na maioria dos casos: o Senado como
    # casa revisora de matéria vinda da Câmara, com o número único. O registro completo
    # (/processo/{id}) tem centenas de kB e leva minutos; só é pedido quando a busca não
    # basta — por exemplo, matéria que começou no Senado.
    if proc.get("objetivo") == "Revisora" and "Câmara dos Deputados" in (proc.get("autoria") or ""):
        return {"id": proc["id"], "codigoMateria": proc["codigoMateria"],
                "identificacao": proc["identificacao"],
                "confirmacao": "Senado como casa revisora, autoria da Câmara"}, None

    caminho = CACHE_SF / f"processo-{proc['id']}.json"
    det = coleta.ler_cache(caminho)
    if det is None:
        bruto = pedir(PROCESSO.format(id=proc["id"]))
        if bruto is None:
            return None, None
        if "codigoMateria" not in bruto:
            raise FormatoMudou(f"processo {proc['id']} sem codigoMateria")
        # só o necessário para a confirmação — o registro completo tem centenas de kB
        det = {k: bruto.get(k) for k in ("id", "codigoMateria", "identificacao",
                                         "siglaCasaIniciadora", "identificacaoProcessoInicial",
                                         "outrosNumeros", "tramitando")}
        coleta.gravar_cache(caminho, det)

    # Confirmação no registro do Senado: nunca pelo nome ou pela ementa.
    inicial = numero_base(det.get("identificacaoProcessoInicial"))
    da_camara = [o for o in det.get("outrosNumeros") or []
                 if o.get("siglaEnteIdentificador") == "CD"
                 and str(o.get("numero", "")).lstrip("0") == num
                 and str(o.get("ano", ano)) == ano]
    if det.get("siglaCasaIniciadora") == "CD" and inicial == base:
        como = "iniciada na Câmara com o mesmo número"
    elif da_camara:
        como = "número da Câmara em outrosNumeros"
    elif det.get("siglaCasaIniciadora") == "SF" and inicial == base:
        como = "iniciada no Senado com o mesmo número"
    else:
        return None, (f"número igual, origem não confirmada (iniciadora "
                      f"{det.get('siglaCasaIniciadora')}, {det.get('identificacaoProcessoInicial')})")
    return {**det, "confirmacao": como}, None


def consulta(codigo):
    """Página do e-Cidadania → {sim, nao, situacao}, ou None se a rede falhou."""
    t = pedir(PAGINA.format(codigo=codigo), json_=False)
    if t is None:
        return None
    if not t:
        return {"situacao": "sem página"}
    # Os blocos antigos do leiaute ficaram em comentários HTML com "10+": só vale o que
    # está fora de comentário, e tem de haver exatamente um par.
    t_sem = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    fav, con = _FAVOR.findall(t_sem), _CONTRA.findall(t_sem)
    if len(fav) != 1 or len(con) != 1:
        raise FormatoMudou(f"matéria {codigo}: {len(fav)} contagens de Sim e {len(con)} de Não")
    encerrada = bool(_FINAL.search(t_sem) or _ENCERRADA.search(t_sem))
    return {"sim": int(fav[0].replace(".", "")), "nao": int(con[0].replace(".", "")),
            "situacao": "encerrada" if encerrada else "aberta"}


def gravar(obj):
    """Uma proposição por linha: o diff diário mostra só o que mudou."""
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

    saida, fora, falhas, lidas = {}, [], 0, 0
    for n, p in enumerate(props, 1):
        id_prop, numero = p["idProposicao"], p["numero"]
        if n % 20 == 0:
            print(f"   {n} de {len(props)}…", flush=True)
        try:
            proc, motivo = materia_no_senado(numero)
        except FormatoMudou as e:
            sys.exit(f"\nREGISTRO DO SENADO EM FORMATO INESPERADO — {numero}: {e}\nNada foi gravado.")
        if proc is None:
            if motivo is None:
                falhas += 1
            else:
                fora.append((numero, motivo))
            continue
        codigo = proc["codigoMateria"]
        caminho = CACHE_SF / f"ecidadania-{codigo}.json"
        c = coleta.ler_cache(caminho)
        if args.todas or c is None or (c["situacao"] != "encerrada" and c.get("consultadaEm") != hoje):
            try:
                novo = consulta(codigo)
            except FormatoMudou as e:
                sys.exit(f"\nPÁGINA DO E-CIDADANIA EM FORMATO INESPERADO — {numero}: {e}\n"
                         f"Nada foi gravado. Conferir {PAGINA.format(codigo=codigo)}")
            lidas += 1
            if novo is None:
                falhas += 1
            else:
                c = {**novo, "consultadaEm": hoje}
                coleta.gravar_cache(caminho, c)
        if c is not None:
            saida[str(id_prop)] = {**c, "codigoMateria": codigo,
                                   "identificacao": proc["identificacao"],
                                   "confirmacao": proc["confirmacao"]}

    gravar({
        "_fonte": "e-Cidadania, Senado Federal: consulta pública de cada matéria (Resolução 26/2013)",
        "_url": PAGINA.format(codigo="{codigoMateria}"),
        "_ligacao": "número único Câmara–Senado, confirmado no registro do Senado "
                    "(legis.senado.leg.br/dadosabertos/processo)",
        "_aviso": "Participa quem procurou o site, não uma amostra da população.",
        **dict(sorted(saida.items())),
    })

    com = [c for c in saida.values() if "sim" in c]
    tot = sorted(c["sim"] + c["nao"] for c in com)
    nome = {str(p["idProposicao"]): p["numero"] for p in props}
    print(f"\nPáginas lidas nesta rodada: {lidas} · falhas de rede: {falhas}")
    print(f"Ligadas ao Senado: {len(saida)} de {len(props)} · com consulta: {len(com)} "
          f"(encerradas {sum(c['situacao'] == 'encerrada' for c in com)}, abertas "
          f"{sum(c['situacao'] == 'aberta' for c in com)})")
    print(f"Como a ligação foi confirmada: "
          f"{dict((k, sum(c['confirmacao'] == k for c in saida.values())) for k in {c['confirmacao'] for c in saida.values()})}")
    if tot:
        print(f"Votos na consulta: mediana {tot[len(tot) // 2]} · máx {tot[-1]:,} · "
              f"menos de 500: {sum(t < 500 for t in tot)} · zero: {sum(t == 0 for t in tot)}"
              .replace(",", "."))
        for t, k in sorted(((c["sim"] + c["nao"], k) for k, c in saida.items() if "sim" in c),
                           reverse=True)[:6]:
            print(f"   {nome[k]:<22} {t:>10,}".replace(",", "."))
    print(f"Fora ({len(fora)}):")
    for n, m in sorted(fora, key=lambda x: x[1]):
        print(f"   {n:<34} {m}")
    print(f"{SAIDA.name} gravado.")


if __name__ == "__main__":
    main()
