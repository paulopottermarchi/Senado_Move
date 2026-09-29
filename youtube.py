"""
Câmara Aberta — inscritos do canal OFICIAL de cada deputado no YouTube, como o YouTube informa.

Uso:
    python youtube.py      # precisa de YOUTUBE_API_KEY no ambiente para os números

O canal vem do cadastro da Câmara (/deputados/{id}, campo redeSocial), nunca de busca por
nome. A resposta da Câmara traz também o CPF: só redeSocial é guardado.

Política da API do YouTube (developers.google.com/youtube/terms/developer-policies), e como
este script a cumpre:
  - o número exibido é o que a API devolve (subscriberCount), sem recalcular;
  - nada de métrica derivada nem agregação entre canais de donos diferentes: sem ranking,
    sem soma, sem combinar com outra fonte — o site mostra o número na ficha de cada um;
  - dado guardado há mais de 30 dias sem ser atualizado é apagado (fica só o link);
  - a origem aparece junto do número ("dados do YouTube").
Endereço /c/NOME ou youtube.com/NOME não tem identificador que a API resolva sem busca
(que seria adivinhar o canal): fica só o link, sem número.
Sem a chave, grava os links dos canais e os números ainda válidos (menos de 30 dias).
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime

import requests

import coleta

SAIDA = coleta.BASE / "youtube.json"
CACHE_REDES = coleta.CACHE / "redes"
API_YT = "https://www.googleapis.com/youtube/v3/channels"
VALIDADE_DIAS = 30

_CANAL = re.compile(r"youtube\.com/channel/(UC[\w-]{22})", re.I)
_HANDLE = re.compile(r"youtube\.com/(@[\w.\-]+)", re.I)
_USER = re.compile(r"youtube\.com/user/([\w.\-]+)", re.I)


def redes(id_dep):
    """redeSocial do cadastro da Câmara, com cache. O CPF da mesma resposta NÃO é guardado."""
    caminho = CACHE_REDES / f"{id_dep}.json"
    c = coleta.ler_cache(caminho)
    if c is None:
        r = (coleta.get(f"{coleta.API}/deputados/{id_dep}") or {}).get("dados") or {}
        c = {"redeSocial": r.get("redeSocial") or []}
        coleta.gravar_cache(caminho, c)
    return c["redeSocial"]


def canal_do_cadastro(urls):
    """(url, tipo, chave) do primeiro link de YouTube do cadastro, ou None."""
    for u in urls:
        if "youtube.com" not in u.lower() and "youtu.be" not in u.lower():
            continue
        for rx, tipo in ((_CANAL, "id"), (_HANDLE, "forHandle"), (_USER, "forUsername")):
            m = rx.search(u)
            if m:
                return u, tipo, m[1]
        return u, None, None   # /c/NOME e afins: sem identificador resolvível
    return None


def consultar(chave_api, tipo, valores):
    """channels.list → {valor pedido: (título, inscritos ou None se oculto, id do canal)}."""
    saida = {}
    lotes = [valores[i:i + 50] for i in range(0, len(valores), 50)] if tipo == "id" else [[v] for v in valores]
    for lote in lotes:
        params = {"part": "snippet,statistics", "key": chave_api, tipo: ",".join(lote)}
        for n in range(3):
            r = requests.get(API_YT, params=params, timeout=30)
            if r.status_code == 200:
                break
            if r.status_code in (400, 403):
                raise SystemExit(f"YouTube recusou ({r.status_code}): {r.text[:300]}")
            time.sleep(5 * (n + 1))
        for it in r.json().get("items", []):
            st = it.get("statistics") or {}
            valor = it["id"] if tipo == "id" else lote[0]
            saida[valor] = (it["snippet"]["title"],
                            None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                            it["id"])
        time.sleep(0.2)
    return saida


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    deputados = json.loads(coleta.SAIDA.read_text(encoding="utf-8"))
    anterior = coleta.ler_cache(SAIDA) or {}
    hoje = date.today()
    chave_api = os.environ.get("YOUTUBE_API_KEY")

    canais = {}
    for d in deputados:
        c = canal_do_cadastro(redes(d["id"]))
        if c:
            canais[d["id"]] = c
    print(f"Canal de YouTube no cadastro da Câmara: {len(canais)} de {len(deputados)} "
          f"({sum(1 for u, t, _ in canais.values() if t is None)} sem identificador resolvível)", flush=True)

    numeros = {}
    if chave_api:
        for tipo in ("id", "forHandle", "forUsername"):
            valores = sorted({k for _, t, k in canais.values() if t == tipo})
            if valores:
                numeros[tipo] = consultar(chave_api, tipo, valores)
        print(f"Consultados no YouTube: {sum(len(v) for v in numeros.values())}", flush=True)
    else:
        print("Sem YOUTUBE_API_KEY: só os links; números antigos valem até completar "
              f"{VALIDADE_DIAS} dias.", flush=True)

    saida = {}
    for id_dep, (url, tipo, chave) in canais.items():
        e = {"url": url}
        achado = numeros.get(tipo, {}).get(chave) if tipo else None
        if achado:
            e.update({"canal": achado[0], "inscritos": achado[1], "oculto": achado[1] is None,
                      "idCanal": achado[2], "em": hoje.isoformat()})
        else:
            velho = anterior.get(str(id_dep)) or {}
            # Política do YouTube: dado da API com mais de 30 dias sem atualizar é apagado.
            if velho.get("em") and (hoje - date.fromisoformat(velho["em"])).days <= VALIDADE_DIAS \
                    and velho.get("url") == url:
                e.update({k: velho[k] for k in ("canal", "inscritos", "oculto", "idCanal", "em") if k in velho})
            elif tipo and chave_api:
                e["nota"] = "canal do cadastro não encontrado no YouTube"
            elif not tipo:
                e["nota"] = "endereço sem identificador de canal"
        saida[str(id_dep)] = e

    obj = {"_fonte": "YouTube Data API (channels.list), para o canal informado no cadastro da Câmara",
           "_aviso": "Número de inscritos como o YouTube informa. Sem ranking nem soma, pela política da API.",
           "_gerado": datetime.now().isoformat(timespec="minutes"), **saida}
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, SAIDA)
    com = sum(1 for e in saida.values() if "inscritos" in e)
    print(f"{SAIDA.name} gravado: {len(saida)} canais, {com} com número de inscritos.")


if __name__ == "__main__":
    main()
