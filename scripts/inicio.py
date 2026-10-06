"""Resumo da página inicial: `site/dados/inicio.json`.

A página inicial mostra um número grande em cada destino (513 deputados, 81 senadores, 164 votações…). Em vez de baixar os
arquivos pesados de cada página (o fluxo entre as casas tem 6 MB) só para tirar um número, este script lê os arquivos que as
rodadas já gravaram e guarda aqui só as contas. Nada é calculado de memória: cada campo sai de um arquivo de `site/dados/`, e o
que não puder ser lido fica de fora (a página some com o número e mantém o texto).

Roda no fim de `semana.py`, que é o último passo da rodada diária, ou à mão: `python scripts/inicio.py`.
"""
import json
from datetime import datetime, timedelta, timezone

from coleta import DADOS

SAIDA = DADOS / "inicio.json"
TIPOS_FLUXO = ("PL", "PLP", "PEC")      # o recorte da página "Entre as casas" (decretos legislativos ficam à parte)


def _ler(nome):
    try:
        return json.loads((DADOS / nome).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pct(lei, total):
    return round(100 * lei / total, 1) if total else None


def montar():
    saida = {"geradoEm": datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")}

    dep = _ler("deputados.json")
    if isinstance(dep, list) and dep:
        saida["deputados"] = len(dep)
        saida["partidos"] = len({d["partido"] for d in dep if d.get("partido")})
        saida["ufs"] = len({d["uf"] for d in dep if d.get("uf")})
        saida["comPosicao"] = sum(1 for d in dep if d.get("score") is not None)
        saida["semPosicao"] = sum(1 for d in dep if d.get("score") is None)

    sen = _ler("senadores.json")
    if isinstance(sen, list) and sen:
        saida["senadores"] = len(sen)

    leis = _ler("proposicoes.json")
    if isinstance(leis, list) and leis:
        anos = sorted(p["data"][:4] for p in leis if p.get("data"))
        saida["leis"] = {"votacoes": len(leis), "de": anos[0], "ate": anos[-1]}

    blocos = _ler("blocos.json")
    if isinstance(blocos, dict) and blocos.get("blocos"):
        saida["blocos"] = {"n": len(blocos["blocos"]), "tamanhos": [b["n"] for b in blocos["blocos"]]}

    fluxo = _ler("fluxo.json")
    if isinstance(fluxo, dict) and fluxo.get("agregados"):
        ag, fl = fluxo["agregados"], {}
        for direcao, chave in (("CD>SF", "cdSf"), ("SF>CD", "sfCd")):
            partes = [ag.get(f"{direcao}|{t}") for t in TIPOS_FLUXO]
            if all(partes):
                fl[chave] = _pct(sum(p["lei"] for p in partes), sum(p["proposicoes"] for p in partes))
        if fl:
            saida["fluxo"] = fl

    obras = _ler("obras.json")
    if isinstance(obras, dict) and obras.get("obras"):
        lista = obras["obras"]
        saida["obras"] = {"total": len(lista),
                          "cresceram": sum(1 for o in lista if o.get("inicial") and o.get("atual") and o["atual"] > o["inicial"])}
    return saida


def main():
    saida = montar()
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{SAIDA.name}: " + ", ".join(f"{k}={v}" for k, v in saida.items() if k != "geradoEm"))


if __name__ == "__main__":
    main()
