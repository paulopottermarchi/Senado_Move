"""
Câmara Aberta — resultado da eleição de 2022 para deputado federal (TSE).

Uso:
    python eleicao.py   # baixa (uma vez, com cache) e grava eleicao2022.json

Roda uma vez: o resultado de 2022 não muda. Gera eleicao2022.json, tabela estática
que vai para o repositório e que coleta.py junta a cada deputado (como faz com
ideologia.json). A rodada diária não baixa nada do TSE.

Fonte: Divulgação de Resultados do TSE, arquivo simplificado por UF, eleição 546
(cargos estaduais e federais por UF, 1º turno de 2022), cargo 6 (deputado federal):
    https://resultados.tse.jus.br/oficial/ele2022/546/dados-simplificados/{uf}/{uf}-c0006-e000546-r.json
O portal de dados abertos do TSE (cdn.tse.jus.br) recusa acesso automatizado; este é o
mesmo resultado oficial, publicado pelo sistema de divulgação.

Quociente eleitoral (Código Eleitoral, art. 106): votos válidos da UF ÷ vagas da UF,
"desprezada a fração se igual ou inferior a meio, equivalente a um, se superior".
Votos válidos = nominais + legenda. As vagas vêm do próprio arquivo.

Entram todos os candidatos com voto válido, inclusive "Não eleito": a situação é a da
totalização de 26/12/2022, e sete deputados que hoje são titulares constavam como não
eleitos nela (AP, AL, DF, TO — retotalização posterior das sobras). O voto deles é o
mesmo; a situação mudou depois. coleta.py anota a divergência.
"""

import sys

import coleta  # sessão HTTP com truststore, throttle, cache atômico, nome_chave

UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO"
       .split())
URL = ("https://resultados.tse.jus.br/oficial/ele2022/546/dados-simplificados/"
       "{uf}/{uf}-c0006-e000546-r.json")
CACHE_TSE = coleta.CACHE / "tse"
SAIDA = coleta.BASE / "eleicao2022.json"
TOTAL_VAGAS = 513


# A mesma normalização do lado da Câmara (coleta.aplicar_eleicao).
nome_chave = coleta.nome_chave


def quociente(validos, vagas):
    """Art. 106: fração até meio é desprezada; acima de meio, arredonda para cima."""
    q, resto = divmod(validos, vagas)
    return q + 1 if 2 * resto > vagas else q


def baixar(uf):
    caminho = CACHE_TSE / f"{uf}.json"
    d = coleta.ler_cache(caminho)
    if d is None:
        d = coleta.get(URL.format(uf=uf.lower()))
        if d is None:
            raise RuntimeError(f"TSE devolveu 404 para {uf}")
        coleta.gravar_cache(caminho, d)
    return d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ufs, candidatos, problemas, ambiguos = {}, {}, [], []
    for uf in UFS:
        d = baixar(uf)
        cand = d["cand"]
        vv, vnom, vl, vagas = int(d["vv"]), int(d["vnom"]), int(d["vl"]), int(d["v"])
        eleitos = sum(c["e"] == "s" for c in cand)

        # Sanidade: o arquivo tem de fechar consigo mesmo.
        if vnom + vl != vv:
            problemas.append(f"{uf}: nominais {vnom} + legenda {vl} ≠ válidos {vv}")
        soma = sum(int(c["vap"]) for c in cand if c["dvt"] == "Válido")
        if soma != vnom:
            problemas.append(f"{uf}: soma dos candidatos válidos {soma} ≠ nominais {vnom}")
        if eleitos != vagas:
            problemas.append(f"{uf}: {eleitos} eleitos para {vagas} vagas")
        if d.get("tf") != "s":
            problemas.append(f"{uf}: totalização não finalizada (tf={d.get('tf')})")

        chaves, repetidos = {}, set()
        for c in cand:
            if not c["dvt"].startswith("Válido"):
                continue    # voto anulado: não entra no quociente nem na tabela
            k = nome_chave(c["nm"])
            if k in chaves:
                repetidos.add(k)
            chaves[k] = {"nome": c["nm"], "numero": c["n"], "votos": int(c["vap"]),
                         "situacao": c["st"], "validade": c["dvt"]}
        # Nome de urna NÃO é único na UF em todos os casos (ex.: dois "LIBERATO" na BA,
        # de partidos diferentes). Nome repetido não identifica ninguém: sai do cruzamento.
        for k in repetidos:
            del chaves[k]
            ambiguos.append(f"{uf}: {k}")
        candidatos[uf] = chaves
        ufs[uf] = {"validos": vv, "nominais": vnom, "legenda": vl, "vagas": vagas,
                   "quociente": quociente(vv, vagas), "totalizacao": d.get("dt")}

    vagas = sum(u["vagas"] for u in ufs.values())
    if vagas != TOTAL_VAGAS:
        problemas.append(f"vagas somam {vagas}, não {TOTAL_VAGAS}")
    if problemas:
        print("CHECAGEM FALHOU — eleicao2022.json NÃO foi gravado:")
        for p in problemas:
            print("  -", p)
        sys.exit(2)

    saida = {
        "_fonte": "TSE, Divulgação de Resultados 2022, eleição 546, cargo 6 (deputado "
                  "federal), arquivo simplificado por UF",
        "_url": URL,
        "_quociente": "Código Eleitoral, art. 106: votos válidos ÷ vagas, desprezada a "
                      "fração igual ou inferior a meio, arredondada para 1 se superior",
        "_candidatos": "todos com voto válido; situação da totalização de 26/12/2022; "
                       "chave = nome de urna sem acento, em maiúsculas; nome repetido na "
                       "UF fica fora (ver _ambiguos)",
        "_ambiguos": ambiguos,
        "ufs": ufs,
        "candidatos": candidatos,
    }
    coleta.gravar_cache(SAIDA, saida)
    n = sum(len(c) for c in candidatos.values())
    print(f"{SAIDA.name} gravado · 27 UFs · {vagas} vagas · {n} candidatos com voto válido"
          f" · nomes ambíguos fora do cruzamento: {ambiguos or 'nenhum'}")
    for uf in ("SP", "MG", "RJ", "RR"):
        u = ufs[uf]
        print(f"   {uf}: {u['validos']:>10,} válidos ÷ {u['vagas']:>2} vagas = quociente "
              f"{u['quociente']:,}".replace(",", "."))


if __name__ == "__main__":
    main()
