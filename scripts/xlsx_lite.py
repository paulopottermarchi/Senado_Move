"""
Câmara Aberta — leitor mínimo de planilha .xlsx, só com a biblioteca padrão (zipfile + xml). Sem openpyxl: o repositório não tem essa
dependência e as planilhas da Receita Federal são simples (texto e número, sem fórmula que importe).

    abas = abas(bytes_do_xlsx)            # {nome da aba: [[valor, ...], ...]}  (None nas células vazias; número vira float; texto, str)

Só lê o valor já calculado e guardado em cada célula (`<v>`); nunca recalcula fórmula.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
RID = "{%s}id" % NS["r"]


def _coluna(ref):
    """'AB12' → índice da coluna começando em 0 (A=0, Z=25, AA=26)."""
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _textos(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])) for si in raiz.findall("m:si", NS)]


def abas(conteudo):
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    textos = _textos(z)
    livro = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("p:Relationship", NS)}
    out = {}
    for aba in livro.find("m:sheets", NS):
        alvo = rels[aba.get(RID)]
        caminho = alvo.lstrip("/") if alvo.startswith("/") else "xl/" + alvo
        linhas = []
        for lin in ET.fromstring(z.read(caminho)).iter("{%s}row" % NS["m"]):
            n = int(lin.get("r"))
            while len(linhas) < n - 1:
                linhas.append([])
            cel = []
            for c in lin.findall("m:c", NS):
                i = _coluna(c.get("r"))
                while len(cel) < i:
                    cel.append(None)
                v, t = c.find("m:v", NS), c.get("t")
                if t == "inlineStr":
                    valor = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                elif v is None or v.text is None:
                    valor = None
                elif t == "s":
                    valor = textos[int(v.text)]
                elif t in ("str", "e"):
                    valor = v.text
                elif t == "b":
                    valor = bool(int(v.text))
                else:
                    valor = float(v.text)
                cel.append(valor)
            linhas.append(cel)
        out[aba.get("name")] = linhas
    return out
