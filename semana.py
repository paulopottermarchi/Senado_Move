"""
Câmara Aberta — bloco "Esta semana na Câmara" (semana.json).

Uso:
    python semana.py                  # 7 dias terminando hoje
    python semana.py --ate 2026-09-27 # 7 dias terminando nessa data

Lê o cache (depois de atualiza.py) e deputados.json. Não chama a API.

Universo igual ao do gráfico: PEC e PL de autoria (proponente 1) de deputados em
exercício. A pauta do Plenário é a exceção declarada: sai inteira, como a Câmara
publica, de cache/pauta.json.

Toda data é da Câmara, nada é inferido:
- "novas"       = dataApresentacao na janela;
- "viraram lei" = situação oficial de lei e último andamento na janela;
- "andaram"     = último andamento (statusProposicao.dataHora) na janela, exceto a
                  própria apresentação. Só o ÚLTIMO andamento fica no cache: uma
                  proposição que andou duas vezes na semana aparece uma vez, com o
                  andamento mais recente.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from coleta import BASE, CACHE, TIPOS, e_autor, ler_cache, virou_lei
from temas import CATEGORIAS, CURTO, classificar
import resumos

SAIDA = BASE / "semana.json"
PAUTA = CACHE / "pauta.json"
APRESENTACAO = "Apresentação de Proposição"
MAX_ITENS = 12   # por lista; o total vai sempre junto
# Horário de Brasília fixo (sem horário de verão desde 2019). O runner do GitHub roda em
# UTC: sem isto, "atualizado às 9h30" sairia com 3 horas a mais.
BRASILIA = timezone(timedelta(hours=-3))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ate", type=date.fromisoformat, default=datetime.now(BRASILIA).date(),
                    help="último dia da janela (padrão: hoje)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    ate = args.ate
    de = ate - timedelta(days=6)
    janela = (de.isoformat(), ate.isoformat())

    lista = ler_cache(CACHE / "deputados_lista.json")
    publicado = {d["id"]: d for d in (ler_cache(BASE / "deputados.json") or [])}
    if not lista or not publicado:
        sys.exit("Falta cache/deputados_lista.json ou deputados.json. Rode coleta.py antes.")
    em_exercicio = {d["id"] for d in lista}

    props = {}   # id -> (resumo da proposição, detalhe, [ids dos deputados autores])
    for dep in lista:
        for p in ler_cache(CACHE / "deputados" / f"{dep['id']}.json") or []:
            if p.get("siglaTipo") not in TIPOS:
                continue
            d = ler_cache(CACHE / "proposicoes" / f"{p['id']}.json") or {}
            if not e_autor(d.get("autores", []), dep["id"]):
                continue
            if p["id"] not in props:
                props[p["id"]] = (p, d.get("detalhe") or {}, [])
            props[p["id"]][2].append(dep["id"])

    def autor(i):
        d = publicado.get(i, {})
        return {"id": i, "nome": d.get("nome"), "partido": d.get("partido"), "uf": d.get("uf")}

    resumo_de = resumos.carregar()

    def item(p, det, autores, data):
        st = det.get("statusProposicao") or {}
        ementa = (det.get("ementa") or p.get("ementa") or "").strip()
        return {
            "id": p["id"], "t": p["siglaTipo"], "n": p["numero"], "a": p["ano"],
            "e": ementa,
            # passo 8: resumo do inteiro teor (resumos.py), quando existe
            **({"r": resumo_de[p["id"]]} if p["id"] in resumo_de else {}),
            # Categorias pela classificação do site (temas.classificar, a mesma da página
            # do deputado), cada uma com o termo que a disparou e a origem: "e" ementa,
            # "k" indexação. Lista vazia = sem categoria, e a página diz isso.
            "temas": [[CURTO[CATEGORIAS[i]], termo, fonte]
                      for i, termo, fonte in classificar(ementa, det.get("keywords") or "")],
            "autores": [autor(i) for i in sorted(set(autores)) if i in em_exercicio],
            "data": data,
            "andamento": st.get("descricaoTramitacao") or "",
            "situacao": st.get("descricaoSituacao") or "",
            "despacho": (st.get("despacho") or "").strip()[:220],
        }

    novas, andaram, leis = [], [], []
    temas_novas = Counter()
    for p, det, autores in props.values():
        apres = (det.get("dataApresentacao") or p.get("dataApresentacao") or "")[:10]
        st = det.get("statusProposicao") or {}
        ultimo = (st.get("dataHora") or "")[:10]
        if janela[0] <= apres <= janela[1]:
            novas.append(item(p, det, autores, apres))
            temas_novas.update(nome for nome, _, _ in novas[-1]["temas"])
        if janela[0] <= ultimo <= janela[1]:
            if virou_lei(det):
                leis.append(item(p, det, autores, ultimo))
            elif (st.get("descricaoTramitacao") or "") != APRESENTACAO:
                andaram.append(item(p, det, autores, ultimo))

    ordem = lambda x: (x["data"], x["id"])   # mais recente primeiro
    for grupo in (novas, andaram, leis):
        grupo.sort(key=ordem, reverse=True)

    # Pauta: só eventos que ainda não aconteceram, a partir do dia seguinte à janela.
    pauta = None
    bruto = ler_cache(PAUTA)
    if bruto:
        futuros = [e for e in bruto.get("eventos", [])
                   if (e.get("dataHoraInicio") or "")[:10] > janela[1]]
        pauta = {"consultadaEm": bruto.get("consultadaEm"), "eventos": futuros}

    saida = {
        "de": janela[0], "ate": janela[1],
        "geradoEm": datetime.now(BRASILIA).isoformat(timespec="minutes")[:16],
        "universo": "PECs e projetos de lei de autoria de deputados em exercício. "
                    "A pauta é a do Plenário inteira, como a Câmara publica.",
        "viraramLei": {"total": len(leis), "itens": leis[:MAX_ITENS]},
        "andaram": {"total": len(andaram), "itens": andaram[:MAX_ITENS]},
        "novas": {"total": len(novas),
                  "temas": [[nome, k] for nome, k in temas_novas.most_common(3)],
                  "itens": novas[:MAX_ITENS]},
        "pauta": pauta,
    }
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")),
                     encoding="utf-8")
    print(f"{SAIDA.name}: {janela[0]} a {janela[1]} · {len(novas)} novas · {len(andaram)} andaram · "
          f"{len(leis)} viraram lei · pauta: "
          + ("sem cache/pauta.json" if pauta is None else f"{len(pauta['eventos'])} evento(s)"))


if __name__ == "__main__":
    main()
