"""
Câmara Aberta — atualização diária incremental, via API.

Uso:
    python atualiza.py            # desde a última atualização bem-sucedida
    python atualiza.py --dias 7   # força a janela dos últimos 7 dias

Depois dele, na ordem (é o que o workflow do GitHub Actions roda):
    python coleta.py --limite 513   # regrava deputados.json a partir do cache (~16 s)
    python votos.py --atualizar     # rebaixa só os arquivos em lote do ano corrente
    python temas.py
    python semana.py

Por que existe: coleta.py nunca pergunta de novo o que já está no cache. Isso é
ótimo para retomar uma coleta interrompida e péssimo para acompanhar a Câmara: a
situação de uma proposição cacheada nunca muda. Este script invalida no cache
exatamente o que mudou na janela, e nada mais.

Passos:
1. Relista os deputados em exercício. Suplente novo não tem lista no cache;
   coleta.py baixa a carreira dele na rodada seguinte, sozinho.
2. PROPOSIÇÕES NOVAS: PEC e PL apresentadas na janela
   (/proposicoes?dataApresentacaoInicio&dataApresentacaoFim). Baixa os autores e, se
   houver proponente em exercício, detalhe e votações; acrescenta a proposição à
   lista de cada deputado que assina, como o idDeputadoAutor faria.
3. PROPOSIÇÕES QUE TRAMITARAM: PEC e PL com tramitação na janela
   (/proposicoes?dataInicio&dataFim). As que já têm detalhe no cache são baixadas
   de novo: é isso que atualiza situação, "virou lei" e votações.
4. PAUTA do Plenário dos próximos 7 dias (/orgaos/{id}/eventos e
   /eventos/{id}/pauta) → cache/pauta.json.
5. HISTÓRICO de exercício de todos os deputados em exercício
   (/deputados/{id}/historico): licenças, retornos e posses de suplente mudam a
   qualquer hora, e o filtro de mandato do site depende disso (~513 requisições).

cache/ultima_atualizacao.json só é gravado no fim, se tudo deu certo. Uma rodada
que falha no meio deixa o cache coerente (cada arquivo é gravado de forma atômica)
e a próxima rodada repete a mesma janela.

NÃO VERIFICADO contra a API ao escrever (a rede do ambiente de desenvolvimento
não alcançava a Câmara). Na primeira rodada conferir no log:
- se dataInicio/dataFim de /proposicoes filtram por tramitação na janela, como diz a
  documentação: a linha "tramitaram" mostra quantas voltaram com andamento dentro dela;
- o id do Plenário (buscado por sigla PLEN) e o formato dos itens de /eventos/{id}/pauta.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import coleta
from coleta import API, CACHE, TIPOS, e_autor, get, get_paginado, gravar_cache, ler_cache

ESTADO = CACHE / "ultima_atualizacao.json"
PAUTA = CACHE / "pauta.json"
MIN_DEPUTADOS = 500   # abaixo disso a listagem veio incompleta: aborta
SOBREPOSICAO = 1      # dias repetidos da janela anterior, para não perder o fim do dia
BRASILIA = timezone(timedelta(hours=-3))   # o runner do GitHub roda em UTC


def janelas(ini, fim):
    """A API exige início e fim no mesmo ano: quebra o intervalo na virada."""
    while ini <= fim:
        f = min(fim, date(ini.year, 12, 31))
        yield ini, f
        ini = f + timedelta(days=1)


def buscar(params_datas, ini, fim):
    """PEC e PL de /proposicoes numa janela, uma chamada paginada por ano."""
    achadas = {}
    for a, b in janelas(ini, fim):
        chave_ini, chave_fim = params_datas
        for p in get_paginado(f"{API}/proposicoes", {
                "siglaTipo": ",".join(TIPOS), chave_ini: a.isoformat(), chave_fim: b.isoformat(),
                "itens": 100, "ordem": "ASC", "ordenarPor": "id"}):
            achadas[p["id"]] = p
    return list(achadas.values())


def ids_deputados(autores):
    for a in autores:
        uri = a.get("uri") or ""
        if "/deputados/" in uri:
            yield int(uri.rstrip("/").rsplit("/", 1)[1])


def relistar_deputados():
    velha = {d["id"] for d in ler_cache(CACHE / "deputados_lista.json") or []}
    nova = get_paginado(f"{API}/deputados", {"itens": 100, "ordem": "ASC", "ordenarPor": "nome"})
    if len(nova) < MIN_DEPUTADOS:
        raise RuntimeError(f"/deputados devolveu {len(nova)}; esperado ~513. Nada foi gravado.")
    gravar_cache(CACHE / "deputados_lista.json", nova)
    ids = {d["id"] for d in nova}
    return ids, ids - velha, velha - ids


def acrescentar_novas(ini, fim, em_exercicio):
    novas = buscar(("dataApresentacaoInicio", "dataApresentacaoFim"), ini, fim)
    com_autor = 0
    for p in novas:
        # 1ª chamada só com autores (id_dep=None nunca é autor, então não baixa detalhe)
        d = coleta.dados_da_proposicao(p["id"], None)
        assinam = [i for i in ids_deputados(d["autores"]) if i in em_exercicio]
        proponentes = [i for i in assinam if e_autor(d["autores"], i)]
        if proponentes:
            coleta.dados_da_proposicao(p["id"], proponentes[0])   # detalhe e votações
            com_autor += 1
        for i in assinam:   # autoria e apoiamento, como idDeputadoAutor devolve
            caminho = CACHE / "deputados" / f"{i}.json"
            lista = ler_cache(caminho)
            if lista is None:
                continue    # deputado sem cache: coleta.py baixa a carreira inteira dele
            if all(x["id"] != p["id"] for x in lista):
                lista.append(p)
                gravar_cache(caminho, lista)
    return len(novas), com_autor


def rebaixar_tramitadas(ini, fim):
    tramitaram = buscar(("dataInicio", "dataFim"), ini, fim)
    rebaixadas, dentro_janela, mudou_situacao = 0, 0, []
    for p in tramitaram:
        caminho = CACHE / "proposicoes" / f"{p['id']}.json"
        d = ler_cache(caminho)
        if not d or not d.get("detalhe"):
            continue    # fora do universo: não é autoria de deputado em exercício
        antes = (d["detalhe"].get("statusProposicao") or {}).get("descricaoSituacao")
        det = get(f"{API}/proposicoes/{p['id']}")
        vot = get(f"{API}/proposicoes/{p['id']}/votacoes")
        if det is None:
            continue    # 404 momentâneo: mantém o que havia
        d["detalhe"] = det["dados"]
        d["votacoes"] = vot["dados"] if vot else []
        gravar_cache(caminho, d)
        rebaixadas += 1
        st = d["detalhe"].get("statusProposicao") or {}
        if ini.isoformat() <= (st.get("dataHora") or "")[:10] <= fim.isoformat():
            dentro_janela += 1
        if st.get("descricaoSituacao") != antes:
            mudou_situacao.append((p["id"], antes, st.get("descricaoSituacao")))
    return len(tramitaram), rebaixadas, dentro_janela, mudou_situacao


def atualizar_historicos(ids):
    with ThreadPoolExecutor(coleta.WORKERS) as ex:
        list(ex.map(lambda i: coleta.historico_do_deputado(i, atualizar=True), ids))
    return len(ids)


def baixar_pauta(hoje):
    orgaos = get(f"{API}/orgaos", {"sigla": "PLEN"})
    plen = next((o["id"] for o in (orgaos or {}).get("dados", []) if o.get("sigla") == "PLEN"), None)
    if plen is None:
        raise RuntimeError("Não achei o órgão PLEN em /orgaos.")
    eventos = get_paginado(f"{API}/orgaos/{plen}/eventos", {
        "dataInicio": hoje.isoformat(), "dataFim": (hoje + timedelta(days=7)).isoformat(),
        "itens": 100, "ordem": "ASC", "ordenarPor": "dataHoraInicio"})
    saida = []
    for e in eventos:
        pauta = get(f"{API}/eventos/{e['id']}/pauta")
        itens = []
        for it in (pauta or {}).get("dados", []):
            # O nome do campo da proposição varia entre versões da API; guardar o que vier.
            prop = it.get("proposicao_") or it.get("proposicao") or {}
            itens.append({"ordem": it.get("ordem"), "titulo": it.get("titulo"),
                          "regime": it.get("regime"), "situacao": it.get("situacaoItem"),
                          "proposicao": {k: prop.get(k) for k in
                                         ("id", "siglaTipo", "numero", "ano", "ementa")}})
        saida.append({"id": e["id"], "dataHoraInicio": e.get("dataHoraInicio"),
                      "tipo": e.get("descricaoTipo"), "situacao": e.get("situacao"),
                      "descricao": e.get("descricao"), "itens": itens})
    gravar_cache(PAUTA, {"consultadaEm": datetime.now(BRASILIA).isoformat(timespec="minutes")[:16],
                         "idPlenario": plen, "eventos": saida})
    return len(saida), sum(len(e["itens"]) for e in saida)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dias", type=int, help="janela fixa em dias, ignorando a última rodada")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.monotonic()
    hoje = datetime.now(BRASILIA).date()

    estado = ler_cache(ESTADO)
    if args.dias:
        ini = hoje - timedelta(days=args.dias)
    elif estado:
        ini = date.fromisoformat(estado["ate"]) - timedelta(days=SOBREPOSICAO)
    else:
        ini = hoje - timedelta(days=2)
    print(f"Janela: {ini} a {hoje}")

    em_exercicio, entraram, sairam = relistar_deputados()
    print(f"Deputados em exercício: {len(em_exercicio)} · entraram {len(entraram)} · "
          f"saíram {len(sairam)}")

    n_novas, n_autoria = acrescentar_novas(ini, hoje, em_exercicio)
    print(f"Novas PEC/PL na janela: {n_novas} · de autoria de deputado em exercício: {n_autoria}")

    n_tram, n_reb, n_dentro, mudou = rebaixar_tramitadas(ini, hoje)
    print(f"Tramitaram na janela: {n_tram} · no universo, rebaixadas: {n_reb} · "
          f"com último andamento dentro da janela: {n_dentro} · mudaram de situação: {len(mudou)}")
    if n_reb and n_dentro == 0:
        print("ATENÇÃO: nenhuma rebaixada tem andamento na janela. Conferir se dataInicio/dataFim "
              "de /proposicoes filtram por tramitação.")
    for pid, a, b in mudou[:15]:
        print(f"   {pid}: {a} → {b}")

    n_ev, n_it = baixar_pauta(hoje)
    print(f"Pauta do Plenário, próximos 7 dias: {n_ev} evento(s), {n_it} item(ns)")

    print(f"Históricos de exercício rebaixados: {atualizar_historicos(sorted(em_exercicio))}")

    gravar_cache(ESTADO, {"ate": hoje.isoformat(),
                          "em": datetime.now(BRASILIA).isoformat(timespec="minutes")[:16]})
    print(f"\nOK · {coleta.contador_req} requisições · {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrompido. O cache foi preservado; rode de novo para continuar.")
