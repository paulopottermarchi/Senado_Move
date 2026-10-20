"""
Câmara Aberta — DIAGNÓSTICO E3 (sonda): a licitação de cada contrato, por identificador exato.

Para cada contrato: idCompra = UASG da compra (6) + código da modalidade (2) + número (5) + ano (4), tudo
do registro do contrato no Contratos.gov.br (unidade_compra, codigo_modalidade, licitacao_numero). Não se
casa por nome, objeto ou valor. Rotas (endpoints citados na documentação OpenAPI de cada serviço, baixada
para cache/diag_vinculo/doc_*.json):

  Compras.gov.br dados abertos (dadosabertos.compras.gov.br, sem cadastro):
    GET /modulo-legado/1.1_consultarLicitacao_Id?id_compra=…          licitação (Lei 8.666/10.520): nome_responsavel,
                                                                       funcao_responsavel
    GET /modulo-legado/3.1_consultarPregoes_Id?id_compra=…            pregão: portaria (nº e data) do pregoeiro
    GET /modulo-legado/7_consultarRdc?uasg&numero_aviso&…             RDC (Lei 12.462): nome_responsavel, funcao_responsavel
    GET /modulo-legado/5.1_consultarCompraSemLicitacao_Id?idCompra=…  dispensa/inexigibilidade: declarante e ratificador
    GET /modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id?tipo=idCompra&codigo=…   compra no PNCP (Lei 14.133)
    GET /modulo-uasg/1_consultarUasg?codigoUasg=…                      CNPJ do órgão
  PNCP (pncp.gov.br/api/pncp/v1, documentada em /api/pncp/v3/api-docs):
    GET /orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos            documentos da contratação (edital, homologação, ata)
    GET /orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos/{n}        o documento

Saída: cache/diag_vinculo/e3/{contrato}.json (bruto) e cache/diag_vinculo/e3_sonda.json (resumo).
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

import coleta
import diag_vinculo_comum as D

B = "https://dadosabertos.compras.gov.br"
PNCP = "https://pncp.gov.br/api/pncp/v1"
PASTA = D.DIAG / "e3"
PASTA.mkdir(exist_ok=True)


def get(url, params=None, intervalo=1.0):
    d, st = D.pedir(url, params=params, via="E3", intervalo=intervalo)
    return d, st


def resultado(d):
    if isinstance(d, dict):
        return d.get("resultado") or []
    return d or []


def cnpj_do_orgao(uasg, memo={}):
    if uasg in memo:
        return memo[uasg]
    d, st = get(B + "/modulo-uasg/1_consultarUasg", dict(codigoUasg=uasg, statusUasg="true", pagina=1))
    rs = resultado(d)
    memo[uasg] = (rs[0].get("cnpjCpfOrgao") if rs else None)
    return memo[uasg]


def numero_ano(lic):
    m = re.fullmatch(r"0*(\d+)/(\d{4})", lic or "")
    return (int(m[1]), int(m[2])) if m else (None, None)


def id_compra(r):
    n, ano = numero_ano(r.get("licitacao_numero"))
    if n is None:
        return None
    return f"{r['unidade_compra']}{int(r['codigo_modalidade']):02d}{n:05d}{ano}"


def sondar(cid, r):
    """Tenta todas as rotas aplicáveis e devolve o bruto (cacheado em disco)."""
    cam = PASTA / f"{cid}.json"
    if cam.exists():
        return json.loads(cam.read_text(encoding="utf-8"))
    n, ano = numero_ano(r.get("licitacao_numero"))
    idc = id_compra(r)
    mod = int(r["codigo_modalidade"])
    out = {"contrato": cid, "numero": r["numero"], "licitacao": r.get("licitacao_numero"), "idCompra": idc,
           "uasg_compra": r["unidade_compra"], "modalidade": r["modalidade"], "ano_licitacao": ano,
           "fundamento": r.get("fundamento_legal"), "rotas": {}}
    if idc is None:
        out["erro"] = "licitacao_numero fora do formato NNNNN/AAAA"
        cam.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    R = out["rotas"]
    # legado: licitação por id (pregão, concorrência, tomada de preços)
    if mod in (2, 3, 5):
        d, st = get(B + "/modulo-legado/1.1_consultarLicitacao_Id", dict(id_compra=idc))
        R["legado_licitacao"] = {"status": st, "resultado": resultado(d)}
    if mod == 5:
        d, st = get(B + "/modulo-legado/3.1_consultarPregoes_Id", dict(id_compra=idc))
        R["legado_pregao"] = {"status": st, "resultado": resultado(d)}
    if mod == 99:
        d, st = get(B + "/modulo-legado/7_consultarRdc", dict(
            uasg=r["unidade_compra"], numero_aviso=int(f"{n}{ano}"),
            data_publicacao_min=f"{ano}-01-01", data_publicacao_max=f"{ano}-12-31", pagina=1, tamanhoPagina=20))
        R["legado_rdc"] = {"status": st, "resultado": resultado(d)}
    if mod in (6, 7):
        d, st = get(B + "/modulo-legado/5.1_consultarCompraSemLicitacao_Id", dict(idCompra=idc))
        R["legado_sem_licitacao"] = {"status": st, "resultado": resultado(d)}
    # PNCP (Lei 14.133): qualquer modalidade pode estar lá
    d, st = get(B + "/modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id", dict(tipo="idCompra", codigo=idc))
    R["pncp_contratacao"] = {"status": st, "resultado": resultado(d)}
    cam.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--todos", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cont, _, _ = D.universo()
    alvo = sorted(cont) if args.todos else D.amostra()
    unicas = {}
    for cid in alvo:
        r = D.cache_contrato(cid)["registro"]
        unicas.setdefault((r["unidade_compra"], r.get("licitacao_numero"), r["codigo_modalidade"]), []).append(cid)
    print(f"{len(alvo)} contratos · {len(unicas)} licitações distintas", flush=True)
    for i, (k, cids) in enumerate(sorted(unicas.items()), 1):
        r = D.cache_contrato(cids[0])["registro"]
        o = sondar(cids[0], r)
        for outro in cids[1:]:            # mesma licitação: o resultado é o mesmo
            (PASTA / f"{outro}.json").write_text(json.dumps({**o, "contrato": outro, "mesma_licitacao_que": cids[0]},
                                                           ensure_ascii=False), encoding="utf-8")
        if i % 10 == 0:
            print(f"   {i}/{len(unicas)}", flush=True)
    # resumo
    cob = Counter()
    for cid in alvo:
        o = json.loads((PASTA / f"{cid}.json").read_text(encoding="utf-8"))
        for rota, v in o.get("rotas", {}).items():
            cob[(rota, v["status"], bool(v["resultado"]))] += 1
    for k, v in sorted(cob.items()):
        print(k, v)


if __name__ == "__main__":
    main()
