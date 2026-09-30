"""
Câmara Aberta — notoriedade na Câmara de cada lei votada: o quanto o assunto foi falado
DENTRO da Câmara. Não mede importância nem mérito, e não mede o público.

Uso:
    python scripts/notoriedade.py              # depois de votos.py; grava notoriedade.json
    python scripts/notoriedade.py --atualizar  # pede de novo detalhe, autores e tramitações

É um índice composto — o briefing só o admite com três condições, todas cumpridas aqui:
(1) pesos publicados (iguais, média simples); (2) cada componente sempre visível ao lado da
porcentagem; (3) chamado do que é: notoriedade na Câmara. O público (enquete da Câmara,
consulta do Senado) fica fora, ao lado, sem entrar na conta.

Componentes, cada um de 0 a 100 por uma regra fixa:
  apensados    projetos apensados a ela na tramitação: outros deputados propondo o mesmo
               assunto. Fonte: tramitações, despacho "Apense-se a este(a) o(a) PL-1218/2007"
               (o vínculo some do cadastro quando a principal é votada e as apensadas são
               arquivadas; o histórico fica). Escala logarítmica até o máximo da página:
               cada apensado a mais pesa menos (0 → 0; o máximo → 100).
  nominais     votações nominais no Plenário sobre ela (nominaisPlenario, de votos.py):
               quantas vezes o Plenário parou para votar nome a nome. Mesma escala.
  placar       quão apertada foi a votação final: 100 × (1 − |sim − não| ÷ (sim + não)).
               450 × 0 → 0; 210 × 166 → 88. Placar apertado = assunto mais disputado.
  urgencia     100 se tramitou em regime de urgência (regime no cadastro, ou requerimento
               de urgência aprovado nas tramitações), 0 se não.
  assinaturas  só PEC de deputado com o registro completo (171+ assinaturas, o mínimo da
               Constituição): deputados que assinaram ÷ 513. Com menos de 171 no registro
               da API, o registro é incompleto e o componente sai da média.
A porcentagem é a média simples dos componentes que existem.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime

import coleta

PROPOSICOES = coleta.DADOS / "proposicoes.json"
SAIDA = coleta.DADOS / "notoriedade.json"
CACHE_NOT = coleta.CACHE / "notoriedade"
CADEIRAS = 513
MIN_PEC = 171   # CF, art. 60, I: um terço da Câmara

_APENSE = re.compile(r"Apense(?:m)?-se\s+a\s+est[ea]", re.I)
_REF = re.compile(r"\b(PEC|PLP|PLV|PDL|PL|MPV|PRC)\s*[-nº°. ]*\s*(\d[\d.]*)\s*/\s*(\d{4})")
_URG = re.compile(r"(Aprovad[oa]\s+o\s+Requerimento[^.]{0,120}urg[êe]ncia|regime\s+de\s+urg[êe]ncia)", re.I)


def dados(id_prop, atualizar):
    """Detalhe, autores e tramitações da API, com cache (uma vez por lei)."""
    caminho = CACHE_NOT / f"{id_prop}.json"
    c = None if atualizar else coleta.ler_cache(caminho)
    if c is None:
        det = (coleta.get(f"{coleta.API}/proposicoes/{id_prop}") or {}).get("dados") or {}
        aut = (coleta.get(f"{coleta.API}/proposicoes/{id_prop}/autores") or {}).get("dados") or []
        tra = (coleta.get(f"{coleta.API}/proposicoes/{id_prop}/tramitacoes") or {}).get("dados") or []
        c = {"regime": (det.get("statusProposicao") or {}).get("regime") or "",
             "autores": [{"nome": a.get("nome"), "tipo": a.get("tipo"), "proponente": a.get("proponente")}
                         for a in aut],
             # só o que serve: andamentos que falam em apensação ou urgência
             "tramitacoes": [{"data": (t.get("dataHora") or "")[:10], "despacho": t.get("despacho") or "",
                              "descricao": t.get("descricaoTramitacao") or ""}
                             for t in tra if re.search(r"apens|urg[êe]ncia", (t.get("despacho") or "") +
                                                       (t.get("descricaoTramitacao") or ""), re.I)]}
        coleta.gravar_cache(caminho, c)
    return c


def apensados(c, proprio):
    refs = set()
    for t in c["tramitacoes"]:
        if _APENSE.search(t["despacho"]):
            refs |= {f"{s} {int(n.replace('.', ''))}/{a}" for s, n, a in _REF.findall(t["despacho"])}
    refs.discard(proprio)
    return sorted(refs)


def urgencia(c):
    if "urg" in c["regime"].lower():
        return True, c["regime"]
    for t in c["tramitacoes"]:
        m = _URG.search(t["despacho"])
        if m:
            return True, f"{m[0][:80]} ({t['data']})"
    return False, c["regime"] or "sem registro de urgência"


def escala_log(n, maximo):
    return round(100 * math.log1p(n) / math.log1p(maximo)) if maximo else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--atualizar", action="store_true", help="pedir de novo à API")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    props = coleta.ler_cache(PROPOSICOES)
    if not props:
        sys.exit(f"{PROPOSICOES.name} não existe — rode votos.py antes.")

    brutos = {}
    for p in props:
        c = dados(p["idProposicao"], args.atualizar)
        m = re.match(r"([A-Z]+)\s+(\d+)/(\d{4})", p["numero"] or "")
        proprio = f"{m[1]} {int(m[2])}/{m[3]}" if m else ""
        brutos[p["idProposicao"]] = (p, c, apensados(c, proprio), *urgencia(c))
    max_ap = max(len(a) for _, _, a, _, _ in brutos.values())
    max_nom = max(len(p.get("nominaisPlenario") or []) for p, *_ in brutos.values())

    saida, pec_incompletas = {}, []
    for id_prop, (p, c, aps, urg, urg_txt) in brutos.items():
        comp = {"apensados": {"n": len(aps), "valor": escala_log(len(aps), max_ap), "quais": aps[:30]}}
        n_nom = len(p.get("nominaisPlenario") or [])
        comp["nominais"] = {"n": n_nom, "valor": escala_log(n_nom, max_nom)}
        if p["sim"] + p["nao"]:
            comp["placar"] = {"sim": p["sim"], "nao": p["nao"],
                              "valor": round(100 * (1 - abs(p["sim"] - p["nao"]) / (p["sim"] + p["nao"])))}
        comp["urgencia"] = {"sim": urg, "texto": urg_txt, "valor": 100 if urg else 0}
        if p["numero"].startswith("PEC"):
            assin = sum(1 for a in c["autores"] if (a.get("tipo") or "").lower().startswith("deputad"))
            # PEC do Senado ou do Executivo não coleta assinatura de deputado; e com menos de
            # 171 no registro da API, o registro está incompleto (a Constituição exige 171).
            # Nos dois casos o componente sai da média — zerado, puniria a PEC pelo registro.
            if assin >= MIN_PEC:
                comp["assinaturas"] = {"n": assin, "valor": round(100 * min(assin, CADEIRAS) / CADEIRAS)}
            elif assin:
                pec_incompletas.append((p["numero"], assin))
        vals = [v["valor"] for v in comp.values()]
        saida[str(id_prop)] = {"pct": round(sum(vals) / len(vals)), "componentes": comp}

    obj = {
        "_nome": "Notoriedade na Câmara",
        "_proposito": "O quanto o assunto foi falado dentro da Câmara. Não mede importância, mérito nem o público.",
        "_metodo": "Média simples de componentes de 0 a 100: apensados e votações nominais (escala "
                   "logarítmica até o máximo desta página), placar (100 × (1 − |sim − não| ÷ (sim + não)) "
                   "na votação final), urgência (100 se tramitou em urgência) e, só em PEC de deputado com "
                   "registro completo, assinaturas (deputados que assinaram ÷ 513). Pesos iguais.",
        "_maximos": {"apensados": max_ap, "nominais": max_nom},
        "_fonte": "Dados Abertos da Câmara: /proposicoes/{id}, /autores e /tramitacoes; votações de "
                  "proposicoes.json",
        "_gerado": datetime.now().date().isoformat(),
        **dict(sorted(saida.items(), key=lambda kv: int(kv[0]))),
    }
    tmp = SAIDA.with_suffix(".tmp")
    tmp.write_text("{\n" + ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:"
                                      f"{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                                      for k, v in obj.items()) + "\n}\n", encoding="utf-8")
    os.replace(tmp, SAIDA)

    # ------------------------------------------------ relatório
    n = len(saida)
    pcts = sorted(v["pct"] for v in saida.values())
    urg = sum(1 for v in saida.values() if v["componentes"]["urgencia"]["sim"])
    aps = sorted(v["componentes"]["apensados"]["n"] for v in saida.values())
    print(f"{n} leis · notoriedade na Câmara: mediana {pcts[n // 2]}% · de {pcts[0]}% a {pcts[-1]}%")
    print(f"Apensados: mediana {aps[n // 2]} · máx {max_ap} · com zero: {aps.count(0)} · "
          f"votações nominais: máx {max_nom} · urgência: {urg} de {n}")
    pecs = [v["componentes"]["assinaturas"]["n"] for v in saida.values() if "assinaturas" in v["componentes"]]
    print(f"PEC com assinaturas no índice: {len(pecs)}"
          + (f" (de {min(pecs)} a {max(pecs)})" if pecs else "")
          + f" · registro incompleto (fora): {pec_incompletas}")
    num = {str(p["idProposicao"]): p["numero"] for p in props}
    for k, v in sorted(saida.items(), key=lambda kv: -kv[1]["pct"])[:8]:
        c = v["componentes"]
        print(f"   {num[k]:<34} {v['pct']:>3}% · apensados {c['apensados']['n']} · nominais {c['nominais']['n']} · "
              f"placar {c.get('placar', {}).get('valor', '—')} · urgência {'sim' if c['urgencia']['sim'] else 'não'}"
              + (f" · assinaturas {c['assinaturas']['n']}" if "assinaturas" in c else ""))
    print(f"{SAIDA.name} gravado.")


if __name__ == "__main__":
    main()
