"""
Câmara Aberta — leitura do PDF do contrato ou do termo para a camada "Pessoas documentadas" (via E2).

Reaproveita, sem pymupdf (AGPL, fora do requirements.txt), a lógica do piloto signatarios.py e do
diagnóstico diag_vinculo_e2.py: texto por página com pypdf, blocos de assinatura eletrônica (SUAP, SEI,
ICP-Brasil/gov.br) e o casamento do nome com o trecho do CONTRATANTE no preâmbulo.

O que sai de um PDF (`analisar`): só o REPRESENTANTE do órgão contratante — nome que está no bloco de
assinatura E no trecho do contratante do preâmbulo — e CONTAGENS do resto. Nome de testemunha, de
quem assina pela empresa e de qualquer outro signatário do órgão NÃO é guardado em lugar nenhum.

Segunda leitura (`segunda_leitura`): outro leitor de PDF, o pdfminer.six (licença MIT), lê só as páginas em que há representante e confere se
o NOME aparece igual no texto dele. É um critério de publicação (pessoas.py): se os dois leitores não concordam, o nome fica retido.

Texto ilegível: a fonte de alguns PDFs perde a ligadura "ti"/"tt" ("Batista" → "Ba�sta"). pypdf deixa o
rastro (U+FFFD); o nome com esse rastro não é publicado como está (ver `casa` e pessoas.py, `reconciliar`).
"""

import io
import logging
import re
import unicodedata
from bisect import bisect_right

import pypdf

logging.getLogger("pypdf").setLevel(logging.CRITICAL)

FFFD = "�"
MAX_PDF = 40 * 1024 * 1024
MAX_PAGINAS = 80          # PDF maior: só o começo (preâmbulo) e o fim (assinaturas)
VERSAO_LEITOR = 3         # sobe quando o parser muda: os PDFs são lidos de novo
LEITOR2 = "pdfminer.six"  # o segundo leitor; a versão instalada entra no registro (ver `leitor2_id`)

TIPOS_ARQUIVO = ("Contrato", "Termo Aditivo", "Termo Apostilamento", "Termo de Apostilamento",
                 "Termo Rescisão", "Termo de Rescisão", "Termo de Reconhecimento de Dívida")

# --- expressões -----------------------------------------------------------------------------------
_SEI = re.compile(r"Documento assinado eletronicamente por:?\s+([^,]{3,120}?),\s*(.{2,140}?),\s*em\s+"
                  r"(\d{2}/\d{2}/\d{4})(?:,?\s*às\s*(\d{2}:\d{2}))?")
_SUAP_MARCA = re.compile(r"Documento assinado eletronicamente por:\s")
_SUAP_ITEM = re.compile(r"(.{5,200}?)\s*,\s*([^,]{2,160}?)\s*,\s*em\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})")
_GOVBR = re.compile(r"Documento assinado digitalmente\s+(?:gov\.br\s+)?([A-ZÀ-Ý][A-Za-zÀ-ÿ' .-]{5,90}?)\s+"
                    r"(?:Data:\s*)?(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})")
_ICP = re.compile(r"Assinado de forma digital por\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ' .-]{5,90}?)(?::\s*\d{11}|\s+Dados:|\s*$)")
_FIM_SUAP = re.compile(r"Este documento foi emi|Para comprovar|A autenticidade deste documento|Código de Autentica|"
                       r"Código Verificador")
_CPF_NO_NOME = re.compile(r":?\d{11}")
_DOC_PESSOAL = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{1,2}\.\d{3}\.\d{3}-[\dXx]|"
                          r"SIAPE\s*(?:n[º°o.]*\s*)?\d+|RG\s*(?:n[º°o.]*\s*)?[\d.xX-]+", re.I)
_MATRICULA = re.compile(r"matr[íi]cula(?:\s+funcional)?\s*(?:n[º°o.]*\s*)?[\d.-]+", re.I)
_FIM_CONTRATANTE = re.compile(r"(?:denominad|designad|chamad)[oa]\s+(?:simplesmente\s+)?(?:de\s+)?[\"“]?CONTRATANTE", re.I)
_FIM_JANELA = re.compile(r",?\s+e\s+(?:a|o)\s+(?:empresa\s+)?|\bCONTRATAD[AO]\b", re.I)
# quem assina pela EMPRESA (nunca candidato, nome não guardado) e quem assina como testemunha (idem)
_EMPRESA = re.compile(r"Usu[aá]rio\s+Externo|Representante\s+Legal|Procurador|S[óo]ci[oa]|Propriet[aá]ri[oa]", re.I)
_TESTEMUNHA_CARGO = re.compile(r"Testemunha", re.I)
# lista estruturada de testemunhas no corpo: "Testemunhas: 1. Nome: X 2. Nome: Y", "TESTEMUNHAS: 1 Nome: …"
_TESTEMUNHAS = re.compile(r"TESTEMUNHAS?\s*:?\s*(?:1\s*[-.)]?\s*)?Nome\s*:", re.I)
_CORTE_TESTEMUNHAS = re.compile(r"Documento assinado|C[óo]digo Verificador|\(Assinatura|\(Documento assinado|\(documento datado|"
                                r"Este documento foi|A autenticidade|Refer[êe]ncia: Caso|REPRESENTANTE LEGAL|C[âa]mara Nacional|"
                                r"CONTRATAD[AO]\b", re.I)
_REPRESENTADO = re.compile(r"representad[oa][^.]{0,90}?\b(?:pel[oa]|por)\s+(?:sua\s+|seu\s+)?(.{3,100}?)[,\s]*"
                           r"(?:(?:Sr|Sra|Dr|Dra)\.?\s*)?$", re.I)


# --- nomes ------------------------------------------------------------------------------------------

def tem_ilegivel(s):
    return bool(s) and (FFFD in s or "\x00" in s)


def chave(nome):
    """Sem acento, maiúsculo, só letras, dígitos e espaço; o marcador de letra ilegível é preservado. Serve para AGRUPAR
    o mesmo nome escrito de formas parecidas (acento, caixa, pontuação); para COMPARAR com dano de ligadura, ver `casa`."""
    s = "".join(c if c == FFFD else unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
                for c in nome).upper()
    return " ".join(re.sub(rf"[^A-Z0-9{FFFD} ]", " ", s).split())


def _tok(w):
    """Palavra sem acento, em caixa alta, sem pontuação nas pontas; o caractere ilegível e o miolo ficam como estão."""
    s = "".join(c if c == FFFD else unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode() for c in w).upper()
    return s.strip(".,;:()[]\"'“”")


# Sequências que a ligadura de fonte engole nos PDFs do SUAP/SEI: "Batista" → "Ba�sta", "BaBsta", "Basta", "Logís>ca".
_LIG = re.compile(r"TI|TT|FI|FL|FF|FT|TF")


def _dano(x, y):
    """`x` é `y` com as ligaduras estragadas? (cada TI, TT, FI, FL, FF, FT ou TF de `y` virou 0 a 2 caracteres quaisquer)"""
    if x == y or not _LIG.search(y):
        return False
    padrao = _LIG.sub("\x00", re.escape(y)).replace("\x00", r"\S{0,2}")
    return re.fullmatch(padrao, x) is not None


def palavra_igual(a, b):
    """Igual; ou igual valendo o caractere ilegível por uma ou duas letras; ou igual a menos de ligadura estragada."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if FFFD in x and FFFD not in y:
            if re.fullmatch(re.escape(x).replace(re.escape(FFFD), "[A-Z]{1,2}"), y) is not None:
                return True
    return _dano(a, b) or _dano(b, a)


def casa(a, b):
    """Os dois nomes (ou cargos) são o mesmo texto, valendo ligadura estragada ou ilegível por uma ou duas letras?"""
    pa, pb = [_tok(w) for w in a.split()], [_tok(w) for w in b.split()]
    return len(pa) == len(pb) and all(palavra_igual(x, y) for x, y in zip(pa, pb))


def degradada(x, y):
    """`x` é uma versão estragada de `y` (y é a grafia completa)? Só vale se `y` traz alguma sequência de ligadura."""
    px, py = [_tok(w) for w in x.split()], [_tok(w) for w in y.split()]
    if len(px) != len(py) or px == py:
        return False
    return all(a == b or _dano(a, b) for a, b in zip(px, py)) and any(a != b for a, b in zip(px, py))


def palavras(texto):
    """[(palavra normalizada, início no texto original)] — para achar o nome no preâmbulo."""
    return [(_tok(m.group()), m.start()) for m in re.finditer(r"\S+", texto) if _tok(m.group())]


def acha(nome, lista):
    """Posição (no texto original) em que o nome aparece em `lista`, palavra por palavra; ou None.
    Aceita o nome completo ou o começo dele com pelo menos duas palavras (o preâmbulo às vezes abrevia)."""
    alvo = [_tok(w) for w in nome.split() if _tok(w)]
    for n in (len(alvo), 2):
        if n > len(alvo) or n < 2:
            continue
        for i in range(len(lista) - n + 1):
            if all(palavra_igual(x, y) for x, (y, _) in zip(alvo[:n], lista[i:i + n])):
                return lista[i][1]
    return None


# Palavras que só aparecem em nome de EMPRESA ("RITA DE CASSIA RIBEIRO LTDA", "DAP-9 CONSTRUTORA"): nunca é representante do órgão.
_PALAVRAS_DE_EMPRESA = {"LTDA", "EIRELI", "EPP", "MEI", "SA", "S/A", "CONSTRUTORA", "CONSTRUCOES", "ENGENHARIA", "COMERCIO", "SERVICOS",
                        "INDUSTRIA", "EMPREENDIMENTOS", "SOLUCOES", "TECNOLOGIA", "ASSOCIADOS", "CONSORCIO"}
_EMPRESA_NOME = re.compile(r"\bEMPRESA\s+([A-ZÀ-Ý0-9][A-Za-zÀ-ÿ0-9 &.'/-]{3,90}?)\s*(?:,|;|\.\s|inscrit|com sede|CNPJ|doravante|neste ato|ao contrato)",
                           re.I)


def eh_empresa(nome, empresas):
    c = chave(nome)
    if set(c.split()) & _PALAVRAS_DE_EMPRESA:
        return True
    return any(e == c or (len(c.split()) >= 2 and e.startswith(c + " ")) for e in empresas)


def dedup_nome(s):
    """pypdf repete o texto das assinaturas SUAP/SEI, às vezes sem espaço ("Ana SilvaAna Silva")."""
    s = " ".join((s or "").split()).strip(" .,:;-")
    for _ in range(2):
        n = len(s)
        if n >= 6 and n % 2 == 0 and s[: n // 2] == s[n // 2:]:
            s = s[: n // 2].strip()
        p = s.strip(" .-,:").split()
        if len(p) % 2 == 0 and p[: len(p) // 2] == p[len(p) // 2:]:
            s = " ".join(p[: len(p) // 2])
        m = re.fullmatch(r"(.{4,}?)\s*\1", s)
        if m:
            s = m[1].strip()
    return s


def apaga_pessoal(t):
    return _MATRICULA.sub("<matrícula>", _DOC_PESSOAL.sub("<documento>", _CPF_NO_NOME.sub("", t)))


# --- texto ------------------------------------------------------------------------------------------

def texto_paginas(pdf):
    """Texto de cada página (pypdf). Letra sem texto vira U+FFFD; ligaduras (ﬁ) viram letras (NFKC)."""
    r = pypdf.PdfReader(io.BytesIO(pdf))
    n = len(r.pages)
    quais = range(n) if n <= MAX_PAGINAS else list(range(20)) + list(range(n - 20, n))
    out = {}
    for i in quais:
        try:
            t = r.pages[i].extract_text() or ""
        except Exception:
            t = ""
        t = "".join(FFFD if (ord(c) < 32 and c not in "\n\t\r") else c for c in t)
        out[i + 1] = " ".join(unicodedata.normalize("NFKC", t).split())
    return out, n


def camada(chars):
    """'sim' (60% ou mais das páginas com texto), 'nao' (digitalizado: quase nada), 'parcial'."""
    tot = sum(chars)
    if not chars or tot < 200:
        return "nao"
    boas = sum(1 for c in chars if c >= 100)
    return "sim" if boas / len(chars) >= 0.6 else "parcial"


def numero_no_texto(numero, plano):
    """O número do contrato (67154/2021) aparece no texto, tolerando '67-154/2021' e 'N° 67154 / 2021'."""
    m = re.fullmatch(r"0*(\d+)/(\d{4})", numero or "")
    if not m:
        return False
    rx = re.compile(r"(?<!\d)" + r"\D{0,3}".join(re.escape(ch) for ch in m[1]) + r"\s*/\s*" + m[2])
    return bool(rx.search(plano))


def processo_no_texto(processo, plano):
    d = re.sub(r"\D", "", processo or "")
    return len(d) >= 12 and d in re.sub(r"\D", "", plano)


def _iso(d):
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


# --- segunda leitura (outro leitor de PDF) -----------------------------------------------------------

def leitor2_id():
    """"pdfminer.six 20260107", ou None se a biblioteca não está instalada (sem ela nenhum nome é publicado: pessoas.py retém)."""
    try:
        import pdfminer
        return f"{LEITOR2} {pdfminer.__version__}, comparação 3"
    except ImportError:
        return None


def texto_pdfminer(pdf, paginas):
    """{nº da página (1-based): texto} lido com o pdfminer.six, só das páginas pedidas. Página que falha fica fora do resultado."""
    from pdfminer.high_level import extract_text
    out = {}
    for n in sorted(set(paginas)):
        try:
            out[n] = unicodedata.normalize("NFKC", extract_text(io.BytesIO(pdf), page_numbers=[n - 1]) or "")
        except Exception:
            continue
    return out


def concorda_nome(nome, texto):
    """O nome, palavra por palavra (sem acento, caixa e pontuação), aparece no texto do segundo leitor? O texto dele não pode ter o nome
    com letra perdida: "(cid:123)" ou U+FFFD no lugar de uma letra faz a comparação falhar, e é isso que se quer."""
    if not nome or not texto:
        return False
    alvo = chave(nome)
    return bool(alvo) and (" " + alvo + " ") in (" " + chave(texto) + " ")


_ANCORA_ASSINATURA = re.compile(r"Documento\s*assinado\s*eletronicamente\s*por", re.I)
JANELA_ANCORA = 2600          # quanto texto depois da âncora conta como "perto": o mesmo corte que `assinaturas` usa nos itens do SUAP
MIN_PALAVRAS_SEM_ESPACOS = 3  # o nome do leitor principal precisa ter 3 palavras ou mais…
MIN_LETRAS_SEM_ESPACOS = 12   # …e 12 letras ou mais (nome curto demais se confunde com outro)


def _letras(s):
    """Só as letras A-Z, sem espaço, acento nem caixa."""
    return re.sub(r"[^A-Z]", "", chave(s).replace(" ", ""))


def _caixa_confere(nome, segmento, copias):
    """Quando o texto do segundo leitor tem maiúsculas e minúsculas ("FernandaAmorimRocha"), as maiúsculas marcam onde cada palavra
    começa: cada palavra do nome que começa com maiúscula tem de começar com maiúscula no segmento (em cada cópia). Isso separa
    "Mari Ana Costa" de "MarianaCosta", que têm as mesmas letras. Segmento todo em maiúsculas (ou todo em minúsculas) não tem essa informação."""
    letras = []
    for ch in segmento:
        if not ch.isalpha():
            continue
        base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode().upper()
        if len(base) != 1:
            return False
        letras.append((base, ch.isupper()))
    if not (any(maius for _, maius in letras) and any(not maius for _, maius in letras)):
        return True
    total = len(letras) // copias
    pos = 0
    for w in nome.split():
        n = len(_letras(w))
        if not n:
            continue
        if w[:1].isupper():
            for c in range(copias):
                if not letras[pos + c * total][1]:
                    return False
        pos += n
    return True


def concorda_sem_espacos(nome, texto):
    """Comparação do segundo leitor para PDFs em que ele junta as palavras ("FernandaAmorimRocha", SUAP do IFSP), com TODAS estas âncoras:
      (a) igualdade da sequência inteira de letras (sem espaço, acento e caixa) com um trecho do texto — não substring. O trecho é o que
          fica entre dois sinais de pontuação ou números; o SUAP repete o nome na lista de assinaturas ("NomeNome"), então vale também
          o nome duas vezes seguidas, e nada além disso;
      (b) o nome do leitor principal tem 3 palavras ou mais e 12 letras ou mais;
      (c) o trecho está logo depois de "Documento assinado eletronicamente por", na mesma página (`JANELA_ANCORA` caracteres);
      e o trecho tem de ser de fato "colado" (sem espaço dentro) e sem U+FFFD. Mais a conferência de maiúsculas de `_caixa_confere`."""
    alvo = _letras(nome)
    if not alvo or len([w for w in nome.split() if _letras(w)]) < MIN_PALAVRAS_SEM_ESPACOS or len(alvo) < MIN_LETRAS_SEM_ESPACOS or not texto:
        return False
    for m in _ANCORA_ASSINATURA.finditer(texto):
        janela = texto[m.end(): m.end() + JANELA_ANCORA]
        for seg in re.split(r"[^A-Za-zÀ-ÿ\s\ufffd]+", janela):
            bruto = seg.strip()
            if not bruto or FFFD in bruto or re.search(r"\s", bruto):
                continue
            letras = _letras(bruto)
            if letras == alvo and _caixa_confere(nome, bruto, 1):
                return True
            if letras == alvo + alvo and _caixa_confere(nome, bruto, 2):
                return True
    return False


def contem_sem_espacos(nome, texto):
    """Só informativo (não libera nada): as letras do nome aparecem em algum lugar do texto, ignorando os espaços? Marca o caso "o outro leitor
    junta as palavras mas as âncoras de `concorda_sem_espacos` não fecham", para o relatório."""
    alvo = _letras(nome)
    return len(alvo) >= MIN_LETRAS_SEM_ESPACOS and alvo in _letras(texto)


def segunda_leitura(pdf, rec, ler=None):
    """Acrescenta ao registro `rec` (saída de `analisar`) o resultado da segunda leitura: `leitor2` (qual leitor) e, em cada representante,
    `leitor2_nome` = True (o nome confere), False (o outro leitor não traz o nome igual) ou None (a segunda leitura não pôde ser feita).
    `leitor2_obs` anota o caso do texto sem espaços: "igualdade_sem_espacos" (aceito) ou "so_substring_sem_espacos" (não aceito)."""
    reps = rec.get("representantes") or []
    if not reps:
        return rec
    idl = leitor2_id()
    rec["leitor2"] = idl
    if idl is None:
        for r in reps:
            r["leitor2_nome"] = None
        return rec
    try:
        textos = (ler or texto_pdfminer)(pdf, [r["pagina"] for r in reps])
    except Exception:
        textos = {}
    for r in reps:
        t = textos.get(r["pagina"])
        ok = None if t is None else concorda_nome(r["nome"], t)
        if ok is False:
            if concorda_sem_espacos(r["nome"], t):
                ok = True
                r["leitor2_obs"] = "igualdade_sem_espacos"       # o outro leitor junta as palavras; vale pela igualdade ancorada (ver concorda_sem_espacos)
            elif contem_sem_espacos(r["nome"], t):
                r["leitor2_obs"] = "so_substring_sem_espacos"    # as letras aparecem, mas as âncoras não fecham: o nome continua retido
        r["leitor2_nome"] = ok
    return rec


# --- assinaturas ------------------------------------------------------------------------------------

def assinaturas(paginas):
    """Todas as assinaturas eletrônicas do documento: {formato, nome, cargo, data, hora, pagina, trecho}.
    Devolve também o texto corrido. Quem assina é classificado depois; aqui ninguém é descartado."""
    plano, offs = "", []
    for n in sorted(paginas):
        offs.append((len(plano), n))
        plano += paginas[n] + " "

    inicios = [o for o, _ in offs]

    def pagina_do(pos):
        return offs[max(bisect_right(inicios, pos) - 1, 0)][1]

    achados = []
    for m in _SUAP_MARCA.finditer(plano):                 # SUAP: um marcador, vários itens "NOME, CARGO, em data hora."
        trecho = plano[m.end(): m.end() + 2600]
        f = _FIM_SUAP.search(trecho)
        trecho = trecho[: f.start()] if f else trecho
        for mi in _SUAP_ITEM.finditer(trecho):
            n, c, d, h = mi.groups()
            achados.append({"formato": "SUAP", "nome": dedup_nome(n), "cargo": dedup_nome(c), "data": d, "hora": h,
                            "pagina": pagina_do(m.end() + mi.start()),
                            "trecho": apaga_pessoal("Documento assinado eletronicamente por: " + mi.group(0))})
    for m in _SEI.finditer(plano):                        # SEI: uma frase por assinatura
        n, c, d, h = m.groups()
        if re.search(r"\d{2}/\d{2}/\d{4}", n):
            continue
        n, c = dedup_nome(n), dedup_nome(c)
        if any(a["formato"] == "SUAP" and chave(a["nome"]) == chave(n) and a["data"] == d for a in achados):
            continue
        achados.append({"formato": "SEI", "nome": n, "cargo": c, "data": d, "hora": h, "pagina": pagina_do(m.start()),
                        "trecho": apaga_pessoal(plano[m.start(): m.end()])})
    for m in _GOVBR.finditer(plano):
        achados.append({"formato": "gov.br", "nome": dedup_nome(m[1]), "cargo": None, "data": m[2], "hora": m[3],
                        "pagina": pagina_do(m.start()), "trecho": apaga_pessoal(plano[m.start(): m.end()])})
    for m in _ICP.finditer(plano):
        achados.append({"formato": "ICP-Brasil", "nome": dedup_nome(_CPF_NO_NOME.sub("", m[1])), "cargo": None,
                        "data": None, "hora": None, "pagina": pagina_do(m.start()),
                        "trecho": apaga_pessoal(plano[m.start(): m.end()])})
    vistos, unicos = set(), []
    for a in achados:
        k = (chave(a["nome"]), a["data"], a["cargo"] and chave(a["cargo"]))
        if k not in vistos:
            vistos.add(k)
            unicos.append(a)
    return unicos, plano


_FIM_CONTRATADA = re.compile(r"(?:denominad|designad|chamad)[oa]\s+(?:simplesmente\s+)?(?:de\s+)?[\"“]?CONTRATAD[AO]", re.I)


def contratante(plano):
    """(texto do trecho do CONTRATANTE no preâmbulo, lista de palavras, nomes de empresa do preâmbulo) ou ("", [], set()).
    Quando a cláusula da CONTRATADA vem antes ("a empresa X, … denominada CONTRATADA, e o INSTITUTO …, CONTRATANTE"), o trecho do
    contratante só começa depois dela: o representante da empresa nunca entra na janela do órgão."""
    pre = apaga_pessoal(plano[:9000])
    ancora = _FIM_CONTRATANTE.search(pre) or re.search(r"\bCONTRATANTE\b", pre)
    empresas = {chave(m.group(1)) for m in _EMPRESA_NOME.finditer(pre[:3500])}
    if not ancora:
        return "", [], empresas
    inicio = 0
    for m in _FIM_CONTRATADA.finditer(pre):
        if m.end() <= ancora.start():
            inicio = m.end()
    fim = _FIM_JANELA.search(pre, ancora.end())
    trecho = pre[inicio: fim.start() if fim else ancora.end() + 600]
    return trecho, palavras(trecho), empresas


def spans_testemunhas(plano):
    """Trechos em que o próprio documento lista as TESTEMUNHAS (lista estruturada "Nome: …"). Quem está aí
    nunca é representante, mesmo que o nome também apareça no preâmbulo."""
    out = []
    for m in _TESTEMUNHAS.finditer(plano):
        trecho = plano[m.end(): m.end() + 320]
        c = _CORTE_TESTEMUNHAS.search(trecho)
        out.append(trecho[: c.start()] if c else trecho)
    return out


def funcao_e_grafia(preambulo, pos, nome):
    """No preâmbulo: a função que o texto dá a quem representa o órgão ("Diretor de Logística e Aquisições") e
    a grafia do nome lá (para reconciliar nome corrompido), quando o preâmbulo traz o nome inteiro."""
    antes = preambulo[max(0, pos - 220): pos]
    m = _REPRESENTADO.search(antes)
    funcao = m[1].strip(" ,") if m else None
    if funcao and (len(funcao) > 80 or len(funcao.split()) > 9):
        funcao = None                       # pegou frase demais: melhor sem função do que com lixo
    n = len([w for w in nome.split() if _tok(w)])
    ds = preambulo[pos:].split()[:n]
    grafia = " ".join(w.strip(",.;:()") for w in ds) if len(ds) == n else None
    return funcao, grafia


def analisar(pdf, numero, processo):
    """Lê um PDF de contrato ou termo. Devolve o registro guardado em cache (sem o PDF e sem nome de quem não
    é representante do órgão)."""
    paginas, total = texto_paginas(pdf)
    return analisar_paginas(paginas, total, numero, processo)


def analisar_paginas(paginas, total, numero, processo):
    """O mesmo, a partir do texto das páginas {nº da página: texto} (é o que os testes usam, sem PDF)."""
    chars = [len(paginas[n]) for n in sorted(paginas)]
    rec = {"paginas": total, "camada_texto": camada(chars), "representantes": [],
           "contagens": {"empresa": 0, "testemunha": 0, "outro_orgao": 0, "indeterminado": 0}}
    if rec["camada_texto"] == "nao":
        return rec
    ass, plano = assinaturas(paginas)
    rec["numero_no_texto"] = numero_no_texto(numero, plano)
    rec["processo_no_texto"] = processo_no_texto(processo, plano)
    trecho_contr, lista, empresas = contratante(plano)
    rec["tem_preambulo_contratante"] = bool(lista)
    spans = [palavras(s) for s in spans_testemunhas(plano)]
    rec["formatos"] = sorted({a["formato"] for a in ass})
    for a in ass:
        cargo = a["cargo"] or ""
        if _TESTEMUNHA_CARGO.search(cargo):
            rec["contagens"]["testemunha"] += 1
            continue
        if _EMPRESA.search(cargo) or eh_empresa(a["nome"], empresas):
            rec["contagens"]["empresa"] += 1
            continue
        if any(acha(a["nome"], s) is not None for s in spans):
            rec["contagens"]["testemunha"] += 1
            continue
        pos = acha(a["nome"], lista) if lista else None
        if pos is None:
            rec["contagens"]["indeterminado" if a["formato"] in ("gov.br", "ICP-Brasil") else "outro_orgao"] += 1
            continue
        funcao, grafia = funcao_e_grafia(trecho_contr, pos, a["nome"])
        rec["representantes"].append({"nome": a["nome"], "cargo": a["cargo"], "funcaoPreambulo": funcao,
                                      "grafiaPreambulo": grafia, "data": _iso(a["data"]), "hora": a["hora"],
                                      "pagina": a["pagina"], "formato": a["formato"], "trecho": a["trecho"][:300]})
    return rec
