"""
Câmara Aberta — PILOTO: quem assinou cada contrato e termo de obra, lido do documento assinado.

Uso:
    python scripts/signatarios.py              # lê os contratos de obras.json; grava cache/obras/signatarios.json
    python scripts/signatarios.py --auditoria  # também grava a planilha de conferência (fora do repositório)

NÃO publica nada: é a medição que decide se a "tabela de evidências" entra no site.

Fonte: os PDFs que o Contratos.gov.br anexa ao contrato (`/contrato/{id}/arquivos`: contrato,
termos aditivos, apostilamentos), ligados pelo id do contrato — o mesmo documento assinado. Medido
no contrato 36154/2022 do IFSP: o preâmbulo diz quem representa o órgão, com cargo, portaria de
nomeação e delegação de competência; o bloco de assinatura eletrônica (SUAP, SEI) traz nome, cargo
com o código da função (REITOR - CD1) e data e hora, com código de autenticação.

Regras:
  - Quem responde pelo ato = quem está no bloco de assinatura E aparece no trecho do CONTRATANTE no
    preâmbulo (do início até "denominado CONTRATANTE"). Dois lugares do mesmo documento. O
    representante da empresa aparece depois, no trecho da contratada: não é pego.
  - Os demais signatários (assistente, técnico, coordenador que assinam na tramitação) ficam só
    pela função — não decidem o ato.
  - Letra ilegível: a fonte de alguns PDFs grava a ligadura "ti" sem texto ("Batista" sai
    "Ba\\x12sta"; o PyMuPDF põe um caractere de controle). Nome com caractere ilegível só é usado se
    casar com UMA versão legível da mesma pessoa nos documentos do mesmo contrato (o caractere vale
    uma ou duas letras); senão fica de fora, contado.
  - Dados pessoais: do PDF sai só nome, cargo, data e o número dos atos (nomeação, delegação). CPF,
    RG, SIAPE, estado civil e endereço não são gravados — nem no cache; o texto do PDF não é guardado.
"""

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter

import pymupdf

import coleta
import obras

CACHE_A = coleta.CACHE / "obras" / "assinaturas"
SAIDA = coleta.CACHE / "obras" / "signatarios.json"
TIPOS = ("Contrato", "Termo Aditivo", "Termo Apostilamento", "Termo de Apostilamento", "Termo de Rescisão")
MAX_PDF = 40 * 1024 * 1024
ILEGIVEL = "�"

# bloco de assinatura: "Nome, CARGO, em 26/08/2022 15:20:34." (SUAP) ou "..., em 26/08/2022, às 15:20" (SEI)
_ASSINA = re.compile(r"([A-Za-zÀ-ÿ�'. -]{5,90}?)\s*,\s*([^,]{2,90}?)\s*,\s*em\s+(\d{2}/\d{2}/\d{4})")
_MARCA = re.compile(r"assinado eletronicamente por:?", re.I)
_NOMEACAO = re.compile(r"nomead[oa]\s+pel[oa]\s+(Portaria[^,;]{0,90}|Decreto[^,;]{0,90})", re.I)
_DELEGACAO = re.compile(r"delega[çc][ãa]o\s+de\s+compet[êe]ncia\s+([^,;]{0,90})", re.I)
_FIM_CONTRATANTE = re.compile(r"(?:denominad|designad|chamad)[oa]\s+(?:simplesmente\s+)?(?:de\s+)?[\"“]?CONTRATANTE", re.I)
_DOC_PESSOAL = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}|SIAPE\s*(?:n[º°o.]*\s*)?\d+|RG\s*(?:n[º°o.]*\s*)?[\d.xX-]+", re.I)


def chave(nome):
    """Sem acento, maiúsculo, só letras, dígitos e espaço; o marcador de letra ilegível é preservado."""
    s = "".join(c if c == ILEGIVEL else unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
                for c in nome).upper()
    return " ".join(re.sub(rf"[^A-Z0-9{ILEGIVEL} ]", " ", s).split())


def palavra_igual(a, b):
    """Igual, ou igual valendo o caractere ilegível (de qualquer lado) por uma ou duas letras."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if ILEGIVEL in x and ILEGIVEL not in y:
            return re.fullmatch(re.escape(x).replace(re.escape(ILEGIVEL), "[A-Z]{1,2}"), y) is not None
    return False


def palavras(texto):
    """[(palavra normalizada, início no texto original)] — para achar o nome e a janela dos atos."""
    return [(p, m.start()) for m in re.finditer(r"\S+", texto) for p in chave(m.group()).split()]


def acha(nome, lista):
    """Posição (no texto original) em que o nome aparece em `lista`, palavra por palavra; ou None.
    Aceita o nome completo ou o começo dele com pelo menos duas palavras — o preâmbulo às vezes
    abrevia ("Reitor Sr. Silmario Batista" × "Silmario Batista dos Santos" na assinatura). A busca é
    só no trecho do contratante, que é curto e cita uma ou duas pessoas."""
    alvo = chave(nome).split()
    for n in (len(alvo), 2):
        if n > len(alvo) or n < 2:
            continue
        for i in range(len(lista) - n + 1):
            if all(palavra_igual(x, y) for x, (y, _) in zip(alvo[:n], lista[i:i + n])):
                return lista[i][1]
    return None


def desdobra(s):
    """O PyMuPDF às vezes repete o trecho ("Nelson Lisboa Junior Nelson Lisboa Junior")."""
    p = s.strip(" .-,:").split()
    if len(p) % 2 == 0 and p[:len(p) // 2] == p[len(p) // 2:]:
        return " ".join(p[:len(p) // 2])
    return " ".join(p)


def texto_do_pdf(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        t = "\n".join(p.get_text() for p in doc)
    # glifo sem texto (ligadura) vira caractere de controle: marcado como ilegível
    return "".join(ILEGIVEL if (ord(c) < 32 and c not in "\n\t\r") else c for c in t)


# Quem assina pela EMPRESA (pessoa privada) no SEI aparece como "Usuário Externo": o nome não é
# guardado, e essa pessoa nunca é candidata a responsável pelo ato do órgão.
_EXTERNO = re.compile(r"Usu[aá]rio\s+Externo|Representante\s+Legal|Testemunha|Procurador|S[óo]ci[oa]", re.I)
# fim do trecho do contratante: a cláusula da contratada (", e a empresa X", "CONTRATADA")
_FIM_JANELA = re.compile(r",?\s+e\s+(?:a|o)\s+(?:empresa\s+)?|\bCONTRATAD[AO]\b", re.I)
_MATRICULA = re.compile(r"matr[íi]cula(?:\s+funcional)?\s*(?:n[º°o.]*\s*)?[\d.-]+", re.I)


def apaga_pessoal(t):
    return _MATRICULA.sub("<matrícula>", _DOC_PESSOAL.sub("<documento>", t))


def trechos(pdf):
    """Só o que o leitor usa, com CPF, RG, SIAPE e matrícula apagados: o começo do texto (preâmbulo)
    e a região de cada bloco de assinatura. Guardado no cache para reler sem baixar o PDF de novo;
    o texto inteiro não é guardado."""
    plano = " ".join(texto_do_pdf(pdf).split())
    blocos = [plano[m.start(): m.start() + 2600] for m in _MARCA.finditer(plano)]
    return {"pre": apaga_pessoal(plano[:9000]), "blocos": [apaga_pessoal(b) for b in blocos],
            "caracteres": len(plano)}


def ler(tr):
    """Campos a partir dos trechos: signatários do bloco e o representante do contratante."""
    if tr["caracteres"] < 200:
        return {"signatarios": [], "representante": None, "temBloco": False, "temPreambulo": False,
                "ilegiveis": 0, "digitalizado": True}
    sig = []
    for bloco in tr["blocos"]:
        trecho = _MARCA.sub("", bloco, count=1)
        fim = re.search(r"Este documento foi emi|Para comprovar|A autenticidade deste documento|Código de Autentica", trecho)
        trecho = trecho[:fim.start()] if fim else trecho
        for n, c, d in _ASSINA.findall(trecho):
            n, c = desdobra(n), desdobra(c)
            if _EXTERNO.search(c):
                sig.append({"nome": None, "cargo": "externo (empresa ou testemunha)", "data": d})
            elif len(n.split()) >= 2 and not re.search(r"\d", n):
                sig.append({"nome": n, "cargo": c, "data": d})
    vistos, unicos = set(), []
    for s in sig:
        k = (chave(s["nome"] or ""), s["data"], s["cargo"])
        if k not in vistos:
            vistos.add(k)
            unicos.append(s)
    # Trecho do contratante: do começo até a cláusula da contratada. O representante pode vir antes
    # de "denominada CONTRATANTE" (IFSP) ou depois ("…CONTRATANTE, neste ato representada … pelo
    # Pró-Reitor…", UFSCar).
    pre = tr["pre"]
    ancora = _FIM_CONTRATANTE.search(pre) or re.search(r"\bCONTRATANTE\b", pre)
    if ancora:
        fim_j = _FIM_JANELA.search(pre, ancora.end())
        preambulo = pre[: fim_j.start() if fim_j else ancora.end() + 600]
    else:
        preambulo = ""
    fim = ancora
    lista = palavras(preambulo)
    representante = None
    for s in unicos:
        if not s["nome"]:
            continue
        pos = acha(s["nome"], lista)
        if pos is not None:
            janela = preambulo[pos: pos + 500]
            nome_m, deleg_m = _NOMEACAO.search(janela), _DELEGACAO.search(janela)
            nome = s["nome"]
            if ILEGIVEL in nome:           # assinatura ilegível, preâmbulo legível: fica o do preâmbulo
                do_pre = " ".join(w.strip(",.;:") for w in janela.split()[:len(chave(nome).split())])
                if ILEGIVEL not in do_pre:
                    nome = do_pre
            representante = {**s, "nome": nome, "via": "preâmbulo e assinatura",
                             "nomeacao": _DOC_PESSOAL.sub("", nome_m[1]).strip() if nome_m else None,
                             "delegacao": _DOC_PESSOAL.sub("", deleg_m[1]).strip() if deleg_m else None}
            break
    # Apostilamento é ato unilateral, sem cláusula de contratante: vale o signatário se for um só.
    internos = [s for s in unicos if s["nome"]]
    if representante is None and not fim and len(internos) == 1:
        representante = {**internos[0], "via": "único signatário", "nomeacao": None, "delegacao": None}
    return {"signatarios": unicos, "representante": representante,
            "temBloco": bool(tr["blocos"]), "temPreambulo": bool(fim),
            "ilegiveis": sum(1 for s in unicos if ILEGIVEL in (s["nome"] or ""))}


def documento(arq):
    """Trechos do documento (cache; o PDF só é baixado uma vez) e os campos lidos deles (recalculados
    a cada rodada — mudar uma regra do leitor não exige baixar tudo de novo)."""
    caminho = CACHE_A / f"{arq['id']}.json"
    c = coleta.ler_cache(caminho)
    if c is None or ("trechos" not in c and "erro" not in c):   # cache da 1ª versão: sem trechos
        try:
            r = obras.sessao.get(arq["path_arquivo"], timeout=180)
            ok = r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) <= MAX_PDF
        except Exception:
            return None                    # rede: próxima rodada
        c = {"id": arq["id"], "tipo": arq.get("tipo"), "descricao": arq.get("descricao"), "origem": arq.get("origem")}
        if not ok:
            c["erro"] = f"HTTP {r.status_code}" if r.status_code != 200 else "não é PDF ou maior que 40 MB"
        else:
            try:
                c["trechos"] = trechos(r.content)
            except Exception as e:         # PDF corrompido
                c["erro"] = f"PDF ilegível ({type(e).__name__})"
        coleta.gravar_cache(caminho, c)
    if "erro" in c:
        return c
    return {k: v for k, v in c.items() if k != "trechos"} | ler(c["trechos"])


def reconcilia(docs):
    """Nome com letra ilegível → a única versão legível da mesma pessoa nos documentos do contrato."""
    pessoas = [s for d in docs for s in d.get("signatarios", []) if s["nome"]] + \
              [d["representante"] for d in docs if d.get("representante")]
    legiveis = {chave(s["nome"]): s["nome"] for s in pessoas if ILEGIVEL not in s["nome"]}
    for s in pessoas:
        if ILEGIVEL in s["nome"] and not s.get("reconciliado"):
            rx = re.compile("^" + re.escape(chave(s["nome"])).replace(re.escape(ILEGIVEL), "[A-Z]{1,2}") + "$")
            cand = [k for k in legiveis if rx.match(k)]
            s["reconciliado"] = legiveis[cand[0]] if len(cand) == 1 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--auditoria", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    base = json.loads(obras.SAIDA.read_text(encoding="utf-8"))
    contratos = {c["id"]: c for o in base["obras"] for c in o["contratos"]}
    print(f"{len(contratos)} contratos em obras.json", flush=True)

    res, conta = {}, Counter()
    for n, (cid, c) in enumerate(sorted(contratos.items()), 1):
        if n % 25 == 0:
            print(f"   {n}/{len(contratos)} contratos · {conta['pdf']} documentos lidos", flush=True)
        cam = obras.CACHE_O / "arquivos" / f"{cid}.json"
        arqs = obras.fresco(cam, False)
        if arqs is None:
            try:
                arqs = obras.pedir(f"{obras.CT}/contrato/{cid}/arquivos", intervalo=0.5) or []
            except RuntimeError:           # a API cai por minutos às vezes: fica para a próxima rodada
                conta["contratos sem resposta da API"] += 1
                continue
            coleta.gravar_cache(cam, arqs)
        alvo = [a for a in arqs if a.get("tipo") in TIPOS and a.get("path_arquivo")]
        conta["contratos com algum arquivo"] += bool(arqs)
        conta["contratos com contrato ou termo em PDF"] += bool(alvo)
        docs = [d for d in (documento(a) for a in alvo) if d]
        conta["pdf"] += len(docs)
        reconcilia(docs)
        res[cid] = docs

    # medidas
    docs = [d for v in res.values() for d in v]
    # Segunda passada, entre TODOS os documentos: o reitor que assina ilegível em um contrato
    # ("Silmario Ba?sta dos Santos") aparece legível em outro do mesmo órgão. Continua valendo só
    # com UM nome legível que case, com o caractere ilegível valendo uma ou duas letras.
    reconcilia([d for d in docs if "erro" not in d])
    ok = [d for d in docs if "erro" not in d]
    print(f"\nContratos: {len(contratos)} · com algum arquivo: {conta['contratos com algum arquivo']} · "
          f"com contrato ou termo em PDF: {conta['contratos com contrato ou termo em PDF']}")
    print(f"Documentos: {len(docs)} · lidos: {len(ok)} · erros: {Counter(d['erro'] for d in docs if 'erro' in d)} · "
          f"digitalizados (só imagem, sem texto; não há leitura por OCR): {sum(1 for d in ok if d.get('digitalizado'))}")
    print(f"   com bloco de assinatura eletrônica: {sum(d['temBloco'] for d in ok)} · "
          f"com signatário extraído: {sum(1 for d in ok if d['signatarios'])} · "
          f"com o trecho do CONTRATANTE: {sum(d['temPreambulo'] for d in ok)}")
    print(f"   com representante confirmado (bloco + preâmbulo): {sum(1 for d in ok if d['representante'])}")
    print("   por tipo: " + ", ".join(f"{t} {sum(1 for d in ok if d['tipo'] == t and d['representante'])}/"
                                        f"{sum(1 for d in ok if d['tipo'] == t)}" for t in TIPOS if any(d['tipo'] == t for d in ok)))
    ileg = [s for d in ok for s in d["signatarios"] if ILEGIVEL in (s["nome"] or "")]
    print(f"   nomes com letra ilegível: {len(ileg)} · reconciliados: {sum(1 for s in ileg if s.get('reconciliado'))}")
    # Regra de PUBLICAÇÃO (mais rígida que a de confirmação): a fonte de alguns PDFs troca a ligadura
    # "ti" por "B" sem deixar marca ("Silmario BaBsta dos Santos", contrato 36154/2022 do IFSP). Se a
    # troca estiver no preâmbulo e na assinatura do mesmo PDF, a confirmação passaria. Então: o nome
    # tem de aparecer IDÊNTICO em pelo menos dois documentos diferentes, e sem maiúscula no meio da
    # palavra.
    def nome_final(r):
        return r.get("reconciliado") or r["nome"]
    docs_do_nome = {}
    for d in ok:
        for s in [x for x in d["signatarios"] if x["nome"]] + ([d["representante"]] if d.get("representante") else []):
            docs_do_nome.setdefault(nome_final(s), set()).add(d["id"])
    for d in ok:
        r = d.get("representante")
        if r:
            n = nome_final(r)
            r["publicavel"] = (ILEGIVEL not in n and not re.search(r"[a-zà-ÿ][A-Z]", n)
                               and len(docs_do_nome.get(n, ())) >= 2)
    pub = [d for d in ok if (d.get("representante") or {}).get("publicavel")]
    print(f"   publicáveis (nome idêntico em 2+ documentos, sem sinal de troca de letra): {len(pub)}")
    suspeitos = sorted({nome_final(d['representante']) for d in ok if d.get('representante')
                        and re.search(r"[a-zà-ÿ][A-Z]", nome_final(d['representante']))})
    print(f"   nomes com maiúscula no meio da palavra (troca de letra provável): {suspeitos[:8]}")
    ccr = sum(1 for v in res.values() if any(d.get("representante") for d in v))
    ccp = sum(1 for v in res.values() if any((d.get("representante") or {}).get("publicavel") for d in v))
    print(f"Contratos com responsável publicável: {ccp} de {len(contratos)}")
    print(f"Contratos com pelo menos um representante confirmado: {ccr} de {len(contratos)}")
    reps = Counter((d["representante"]["nome"], d["representante"]["cargo"]) for d in ok if d.get("representante"))
    print(f"Pessoas distintas como representante: {len({chave(n) for n, _ in reps})}")
    for (n, cg), k in reps.most_common(8):
        print(f"   {k:3} × {n} — {cg}")

    coleta.gravar_cache(SAIDA, {str(k): v for k, v in res.items()})
    if args.auditoria:
        aud = obras.CACHE_O / "signatarios_auditoria.csv"     # cache/: fora do repositório
        with open(aud, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["contrato", "numero", "arquivo", "documento", "tipo", "representante", "publicavel", "via", "cargo",
                        "data", "nomeacao", "delegacao", "outros signatarios (funcao)", "link"])
            for cid, v in sorted(res.items()):
                for d in v:
                    r = d.get("representante") or {}
                    w.writerow([cid, contratos[cid]["numero"], d.get("id"), d.get("descricao"), d.get("tipo"),
                                r.get("reconciliado") or r.get("nome"), r.get("publicavel"), r.get("via"),
                                r.get("cargo"), r.get("data"), r.get("nomeacao"), r.get("delegacao"),
                                " | ".join(s["cargo"] for s in d.get("signatarios", []) if s["nome"] != r.get("nome")),
                                f"https://contratos.comprasnet.gov.br/transparencia/contratos/{cid}"])
        print(f"Planilha de conferência: {aud}")


if __name__ == "__main__":
    main()
