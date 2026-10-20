"""
Gera entradas/decretos_iof.json a partir da tabela abaixo, que foi LIDA À MÃO no navegador embutido (nunca por script: o Planalto reseta
conexão de script e o projeto não faz requisição programática a ele), em 20/10/2026:

  1. texto compilado do Decreto nº 6.306/2007 (regulamento do IOF) — cada anotação "(Redação dada / Incluído / Revogado pelo Decreto nº X, de ANO)"
     foi ligada ao artigo em que aparece; artigo "de alíquota" = artigo cujo caput traz a palavra "alíquota";
  2. página de cada decreto — data do decreto (título) e data de publicação no DOU (linha "Este texto não substitui o publicado no DOU de …").

CRITÉRIO (igual para todos os casos): o decreto é citado, no texto compilado do Decreto nº 6.306/2007, como autor de redação dada, inclusão ou revogação num
artigo de alíquota. Artigos de alíquota (caput com "alíquota"): 6, 7, 8, 15, 15-A, 15-B, 15-C, 22, 29, 30, 31, 32, 32-A a 32-D, 33, 39 e 45.
Ficam fora: os cinco atos internacionais citados no mesmo texto (Convenções de Viena e acordos), que não alteram o regulamento.
O arquivo não traz descrição de efeito de nenhum decreto: só data, número, artigos e endereço no Planalto.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# (número, ano, data do decreto, data da publicação no DOU, artigos de alíquota citados)
LISTA = [
    ("6.339", 2008, "2008-01-03", "2008-01-03", "7, 8, 15, 22"),
    ("6.345", 2008, "2008-01-04", "2008-01-07", "15"),
    ("6.391", 2008, "2008-03-12", "2008-03-13", "7, 8, 15"),
    ("6.453", 2008, "2008-05-12", "2008-05-13", "8, 15"),
    ("6.566", 2008, "2008-09-15", "2008-09-16", "15"),
    ("6.613", 2008, "2008-10-22", "2008-10-23", "15"),
    ("6.655", 2008, "2008-11-20", "2008-11-21", "8"),
    ("6.691", 2008, "2008-12-11", "2008-12-12", "7"),
    ("6.983", 2009, "2009-10-19", "2009-10-20", "15"),
    ("7.011", 2009, "2009-11-18", "2009-11-19", "8, 32-A"),
    ("7.323", 2010, "2010-10-04", "2010-10-05", "15"),
    ("7.330", 2010, "2010-10-18", "2010-10-19", "15"),
    ("7.412", 2010, "2010-12-30", "2010-12-31", "15, 15-A, 32, 32-A"),
    ("7.454", 2011, "2011-03-25", "2011-03-28", "15-A"),
    ("7.456", 2011, "2011-03-28", "2011-03-29", "15-A"),
    ("7.457", 2011, "2011-04-06", "2011-04-07", "15-A"),
    ("7.458", 2011, "2011-04-07", "2011-04-08", "7"),
    ("7.487", 2011, "2011-05-23", "2011-05-24", "7, 32, 33, 45"),
    ("7.536", 2011, "2011-07-26", "2011-07-27", "15-A, 32-B"),
    ("7.563", 2011, "2011-09-15", "2011-09-16", "32-B, 32-C"),
    ("7.632", 2011, "2011-12-01", "2011-12-01", "7, 15-A"),
    ("7.683", 2012, "2012-02-29", "2012-03-01", "15-A, 32-C"),
    ("7.698", 2012, "2012-03-09", "2012-03-12", "15-A"),
    ("7.699", 2012, "2012-03-15", "2012-03-16", "32-C"),
    ("7.726", 2012, "2012-05-21", "2012-05-22", "7, 8"),
    ("7.751", 2012, "2012-06-13", "2012-06-14", "15-A"),
    ("7.787", 2012, "2012-08-15", "2012-08-16", "22"),
    ("7.853", 2012, "2012-12-04", "2012-12-05", "15-A"),
    ("7.878", 2012, "2012-12-27", "2012-12-28", "32-C"),
    ("7.894", 2013, "2013-01-30", "2013-01-31", "15-A"),
    ("7.975", 2013, "2013-04-01", "2013-04-02", "8"),
    ("8.023", 2013, "2013-06-04", "2013-06-05", "15-A"),
    ("8.027", 2013, "2013-06-12", "2013-06-13", "32-C"),
    ("8.165", 2013, "2013-12-23", "2013-12-24", "32-A"),
    ("8.175", 2013, "2013-12-27", "2013-12-27", "15-A"),
    ("8.231", 2014, "2014-04-25", "2014-04-25", "8"),
    ("8.263", 2014, "2014-06-03", "2014-06-04", "15-A"),
    ("8.325", 2014, "2014-10-07", "2014-10-08", "8, 15-A, 15-B"),
    ("8.392", 2015, "2015-01-20", "2015-01-21", "7"),
    ("8.511", 2015, "2015-08-31", "2015-08-31", "8"),
    ("8.731", 2016, "2016-04-30", "2016-05-02", "15-B, 32"),
    ("9.017", 2017, "2017-03-30", "2017-03-30", "8"),
    ("9.297", 2018, "2018-03-01", "2018-03-02", "15-B"),
    ("10.305", 2020, "2020-04-01", "2020-04-02", "7, 8"),
    ("10.414", 2020, "2020-07-02", "2020-07-03", "7, 8"),
    ("10.504", 2020, "2020-10-02", "2020-10-02", "7, 8"),
    ("10.551", 2020, "2020-11-25", "2020-11-25", "7, 8"),
    ("10.572", 2020, "2020-12-11", "2020-12-11", "7, 8"),
    ("10.797", 2021, "2021-09-16", "2021-09-17", "7"),
    ("10.997", 2022, "2022-03-15", "2022-03-16", "15-C"),
    ("11.022", 2022, "2022-03-31", "2022-03-31", "8"),
    ("11.153", 2022, "2022-07-28", "2022-07-29", "15-B, 15-C"),
    ("11.667", 2023, "2023-08-24", "2023-08-25", "8"),
    ("11.840", 2023, "2023-12-21", "2023-12-22", "32"),
    ("12.132", 2024, "2024-08-07", "2024-08-08", "22"),
    ("12.466", 2025, "2025-05-22", "2025-05-22", "7, 8, 15-B, 15-C, 22, 45"),
    ("12.467", 2025, "2025-05-23", "2025-05-23", "15-B"),
    ("12.499", 2025, "2025-06-11", "2025-06-11", "7, 8, 15-B, 15-C, 22, 32-D, 45"),
]

LEGISLATURA = [(2007, 2010), (2011, 2014), (2015, 2018), (2019, 2022), (2023, 2026)]


def url(numero, ano):
    a, b = next(p for p in LEGISLATURA if p[0] <= ano <= p[1])
    return f"https://www.planalto.gov.br/ccivil_03/_ato{a}-{b}/{ano}/decreto/d{numero.replace('.', '')}.htm"


def main():
    decretos = [{"numero": n, "ano": ano, "data": d, "publicacao": p, "artigosDeAliquota": [x.strip() for x in arts.split(",")], "url": url(n, ano)}
                for n, ano, d, p, arts in LISTA]
    assert len({(d["numero"], d["ano"]) for d in decretos}) == len(decretos), "decreto duplicado"
    assert all(d["data"] <= d["publicacao"] for d in decretos), "publicação antes da data do decreto"
    assert [d["publicacao"] for d in decretos] == sorted(d["publicacao"] for d in decretos), "fora de ordem"
    por_ano = {}
    for d in decretos:
        por_ano[d["ano"]] = por_ano.get(d["ano"], 0) + 1
    out = {
        "_aviso": "Cada marcador mostra a publicação de um decreto, com número, data e endereço no Planalto. Não descreve efeito nenhum e não mostra que o decreto causou "
                  "variação na arrecadação. Decretos legislativos ou decisões judiciais podem ter suspendido efeitos de algum deles, e isso não aparece aqui.",
        "criterio": "Decreto citado, no texto compilado do Decreto nº 6.306/2007 (regulamento do IOF), como autor de redação dada, inclusão ou revogação num artigo de alíquota "
                    "(artigo cujo caput traz a palavra “alíquota”). O critério é o mesmo para todos os decretos.",
        "artigosDeAliquota": ["6", "7", "8", "15", "15-A", "15-B", "15-C", "22", "29", "30", "31", "32", "32-A", "32-B", "32-C", "32-D", "33", "39", "45"],
        "regulamento": {"nome": "Decreto nº 6.306, de 14 de dezembro de 2007 (regulamento do IOF), texto compilado",
                        "url": "https://www.planalto.gov.br/ccivil_03/_ato2007-2010/2007/decreto/d6306.htm"},
        "baseLegal": {"texto": "Constituição, art. 153, § 1º: “É facultado ao Poder Executivo, atendidas as condições e os limites estabelecidos em lei, alterar as alíquotas dos impostos "
                               "enumerados nos incisos I, II, IV e V.” Os incisos são: I, importação de produtos estrangeiros; II, exportação; IV, produtos industrializados; "
                               "V, operações de crédito, câmbio e seguro, ou relativas a títulos ou valores mobiliários (IOF).",
                      "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm#art153"},
        "lidoEm": "2026-10-20",
        "comoFoiLido": "No navegador embutido, página por página (texto compilado e cada decreto); nenhuma requisição programática ao Planalto.",
        "limite": "Só decretos que alteraram o regulamento vigente (Decreto nº 6.306, de 14/12/2007): o texto compilado dele não cita decreto de antes disso, "
                  "e mudanças feitas pelo regulamento anterior não estão marcadas.",
        "foraDaLista": "Os cinco atos internacionais citados no mesmo texto compilado (Convenções de Viena e acordos) não alteram o regulamento.",
        "porAno": por_ano,
        "total": len(decretos),
        "decretos": decretos,
    }
    caminho = RAIZ / "entradas" / "decretos_iof.json"
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        f.write("{\n" + ",\n".join(f'"{k}":{json.dumps(v, ensure_ascii=False)}' for k, v in out.items() if k != "decretos")
                + ',\n"decretos":[\n' + ",\n".join(json.dumps(d, ensure_ascii=False) for d in decretos) + "\n]\n}\n")
    print(f"{caminho.name}: {len(decretos)} decretos")
    for a in sorted(por_ano):
        print(f"  {a}: {por_ano[a]}")


if __name__ == "__main__":
    main()
