"""
Câmara Aberta — "Tributos cuja alíquota pode mudar por decreto" (site/dados/contexto/tributos.json), da página de economia (bloco "Mais indicadores").

Uso:
    python scripts/receita.py                 # no máximo uma vez por semana: baixa, confere e grava
    python scripts/receita.py --forcar        # ignora o intervalo de uma semana
    python scripts/receita.py --so-montar     # não baixa nada: remonta o JSON dos arquivos em cache/receita/
    python scripts/receita.py --simular       # baixa e confere, mas não grava
    python scripts/receita.py --imprimir      # além de gravar, imprime o último valor de cada série e as frases geradas
    python scripts/receita.py --teste         # testes sintéticos, sem rede

Fonte: Receita Federal do Brasil, arrecadação das receitas federais, em planilhas (.xlsx) dos dados abertos e do relatório mensal. A Receita NÃO tem API para isto
(procurado em 20/10/2026), e o formato é planilha estruturada, não PDF. Dois arquivos, ambos em R$ milhões:
  1. o ANEXO do último relatório mensal ("Análise Mensal — <mês> — Anexo"): Tabelas IV e IV-A (por ano, 1995 até o mês corrente; a "A" é a série corrigida pelo IPCA pela
     própria Receita, a preços do último mês), V e V-A (os mesmos meses de cada ano) e III e III-A (mensal, desde 2021, com as linhas agrupadas como a Receita as agrupa);
  2. a série histórica mensal por tributo, a preços correntes (1994 até o último ano fechado), usada para CONFERIR a tabela anual.
A tabela corrigida pelo IPCA é refeita todo mês com os preços do último mês: por isso a série inteira é baixada de novo a cada rodada e o mês-base aparece na página.
Nada é deflacionado por este site, nem somado, nem separado: cada linha é a que a Receita publica (na tabela anual, Imposto sobre Importação e IPI-Vinculado à Importação
são linhas separadas; na mensal, a Receita publica os dois juntos). Não existe série de "alíquota média efetiva" nesses arquivos, e este site não a calcula.

Os marcadores de decreto vêm de entradas/decretos_iof.json (lido à mão no Planalto, ver scripts/gerar_decretos_iof.py). A página não liga nenhum decreto a variação nenhuma.

Checagens que ABORTAM (o arquivo anterior fica): unidade diferente de R$ milhões; mês-base das tabelas corrigidas diferente do mês do relatório ou entre as tabelas; linha
oficial que sumiu; valor negativo; ano fechado com mês faltando; tabela anual diferente da soma dos meses da série histórica (exceto arredondamento); IPI-Total diferente
da soma das suas linhas; valor corrente de 2026 diferente entre as Tabelas IV e V; valor real abaixo do corrente; data no futuro; decreto duplicado; palavra de julgamento.
"""

import argparse
import json
import re
import sys
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coleta
import xlsx_lite
from coleta import CACHE, ler_cache, gravar_cache
from contexto_trabalho import Aborta, MES, MENOS, PROIBIDAS as PROIBIDAS_ECONOMIA, checar_textos as checar_proibidas, dinheiro, fato, pts, variacao_txt

BRASILIA = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRASILIA).date()
CACHE_R = CACHE / "receita"
SAIDA = coleta.DADOS / "contexto"
DECRETOS = coleta.ENTRADAS / "decretos_iof.json"
VERSAO = "receita-1"
SEMANAL = 7
INTERVALO = 2.0
TENTATIVAS = 4

GOV = "https://www.gov.br/receitafederal/pt-br"
URL_ANEXO = GOV + "/centrais-de-conteudo/publicacoes/relatorios/arrecadacao-federal/{ano}/analise-mensal-{mes}-{ano}-anexo.xlsx"
URL_SERIE = GOV + "/acesso-a-informacao/dados-abertos/receitadata/arrecadacao/serie-historica/arrecadacao-das-receitas-federais-1994-a-{ano}.xlsx"
PAGINA_SERIE = GOV + "/acesso-a-informacao/dados-abertos/receitadata/arrecadacao/serie-historica"
PAGINA_RELATORIOS = GOV + "/centrais-de-conteudo/publicacoes/relatorios/arrecadacao-federal/{ano}"
MES_URL = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MES_EXTENSO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
TOLERANCIA = 1.0          # R$ milhões: a Receita arredonda cada linha

# as linhas oficiais da tabela por ano (o rótulo é o da Receita; espaços repetidos e recuo não contam)
LINHAS = OrderedDict([
    ("ii", "IMPOSTO SOBRE IMPORTAÇÃO"), ("ipi_total", "I.P.I-TOTAL"), ("ipi_fumo", "I.P.I-FUMO"), ("ipi_bebidas", "I.P.I-BEBIDAS"),
    ("ipi_automoveis", "I.P.I-AUTOMÓVEIS"), ("ipi_vinculado", "I.P.I-VINCULADO À IMPORTAÇÃO"), ("ipi_outros", "I.P.I-OUTROS"),
    ("iof", "IOF - I. S/ OPERAÇÕES FINANCEIRAS")])
NOME = {"ii": "Imposto sobre Importação", "ipi_total": "IPI-Total", "ipi_fumo": "IPI-Fumo", "ipi_bebidas": "IPI-Bebidas", "ipi_automoveis": "IPI-Automóveis",
        "ipi_vinculado": "IPI-Vinculado à Importação", "ipi_outros": "IPI-Outros", "iof": "IOF"}
# colunas da tabela mensal agrupada (rótulo normalizado, sem quebra de linha)
COLUNAS_MENSAL = OrderedDict([("ii_ipiv", "I. IMPORTAÇÃO E IPI-VINCULADO"), ("ipi_exceto", "IPI (EXCETO IPI-VINCULADO)")])
NOME_MENSAL = {"ii_ipiv": "I. Importação e IPI-Vinculado", "ipi_exceto": "IPI (exceto IPI-Vinculado)"}
MESES_ABREV = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAIO": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}

# texto proibido: o da página de economia e o do brief destes tributos (nada de "arrecadatório", "regulatório", "aumento de imposto")
PROIBIDAS = PROIBIDAS_ECONOMIA + [r"arrecadat[óo]ri", r"regulat[óo]ri", r"aumento de imposto", r"aumentou o imposto", r"carga tribut", r"sobretaxa", r"dependên", r"\bdepende",
                                  r"extrafiscal", r"\bpunir", r"estimul", r"desestimul"]

BASE_LEGAL = ("Constituição, art. 153, § 1º: “É facultado ao Poder Executivo, atendidas as condições e os limites estabelecidos em lei, alterar as alíquotas dos impostos "
              "enumerados nos incisos I, II, IV e V.” Os incisos são: I, importação de produtos estrangeiros; II, exportação; IV, produtos industrializados; V, operações de "
              "crédito, câmbio e seguro, ou relativas a títulos ou valores mobiliários (IOF).")
URL_CONSTITUICAO = "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm#art153"
FRASE_LEIS = "A linha do tempo de leis desta página não mostra mudanças feitas por decreto."


# ------------------------------------------------------------------------------------------------ coleta
_ultimo = [0.0]


def baixar(url):
    """Bytes de uma planilha do gov.br (o endereço do arquivo precisa de /@@download/file), ou None se o arquivo não existe (404). Espera entre pedidos e repete falha de rede."""
    for n in range(TENTATIVAS):
        espera = _ultimo[0] + INTERVALO - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        _ultimo[0] = time.monotonic()
        try:
            r = coleta.sessao.get(url + "/@@download/file", timeout=120)
        except (coleta.requests.ConnectionError, coleta.requests.Timeout):
            time.sleep(2 ** n)
            continue
        if r.status_code in (401, 403, 404):
            return None
        if r.status_code >= 500:
            time.sleep(2 ** n)
            continue
        if r.status_code != 200 or r.content[:2] != b"PK":
            raise Aborta(f"resposta inesperada ({r.status_code}) de {url}: {r.content[:80]!r}")
        return r.content
    raise Aborta(f"a Receita Federal não respondeu depois de {TENTATIVAS} tentativas: {url}")


def anexo_mais_recente():
    """O anexo do último relatório mensal publicado: do mês anterior para trás, até 14 meses. (ano, mês, bytes)."""
    ano, mes = HOJE.year, HOJE.month - 1
    for _ in range(14):
        if mes == 0:
            ano, mes = ano - 1, 12
        b = baixar(URL_ANEXO.format(ano=ano, mes=MES_URL[mes - 1]))
        if b:
            return ano, mes, b
        mes -= 1
    raise Aborta("nenhum anexo de relatório mensal da Receita encontrado nos últimos 14 meses")


def serie_historica(ano_anexo):
    """A série histórica mensal por tributo, do último ano fechado: tenta o ano anterior ao do anexo e, se ainda não saiu, o anterior a esse. (último ano, bytes)."""
    for ano in (ano_anexo - 1, ano_anexo - 2):
        b = baixar(URL_SERIE.format(ano=ano))
        if b:
            return ano, b
    raise Aborta(f"série histórica da Receita (1994 a {ano_anexo - 1}) não encontrada")


def baixar_tudo():
    ano, mes, anexo = anexo_mais_recente()
    ult_ano, serie = serie_historica(ano)
    CACHE_R.mkdir(parents=True, exist_ok=True)
    for nome in CACHE_R.glob("*.xlsx"):
        nome.unlink()
    (CACHE_R / f"anexo-{ano}-{mes:02d}.xlsx").write_bytes(anexo)
    (CACHE_R / f"serie-1994-a-{ult_ano}.xlsx").write_bytes(serie)
    gravar_cache(CACHE_R / "baixado.json", {"baixadoEm": HOJE.isoformat(), "anexo": {"ano": ano, "mes": mes}, "serieAte": ult_ano})
    return ler_cache_planilhas()


def ler_cache_planilhas():
    meta = ler_cache(CACHE_R / "baixado.json")
    if not meta:
        raise Aborta("--so-montar sem cache da Receita: rode receita.py antes")
    a, m, ult = meta["anexo"]["ano"], meta["anexo"]["mes"], meta["serieAte"]
    try:
        return {"baixadoEm": meta["baixadoEm"], "ano": a, "mes": m, "serieAte": ult,
                "anexo": (CACHE_R / f"anexo-{a}-{m:02d}.xlsx").read_bytes(), "serie": (CACHE_R / f"serie-1994-a-{ult}.xlsx").read_bytes()}
    except FileNotFoundError as e:
        raise Aborta(f"--so-montar: falta {e.filename}") from e


# ------------------------------------------------------------------------------------------------ leitura das planilhas
def norm(s):
    return re.sub(r"\s+", " ", s or "").strip().upper() if isinstance(s, str) else ""


def cabecalho(linhas, onde):
    """Unidade, tipo (corrente ou real) e mês-base de uma tabela do anexo."""
    texto = [norm(c) for l in linhas[:10] for c in l if isinstance(c, str)]
    unid = next((t for t in texto if t.startswith("UNIDADE")), None)
    if unid != "UNIDADE: R$ MILHÕES":
        raise Aborta(f"{onde}: unidade {unid!r} (esperado 'UNIDADE: R$ MILHÕES')")
    preco = next((t for t in texto if t.startswith("(A PREÇOS")), None)
    if preco == "(A PREÇOS CORRENTES)":
        return {"tipo": "corrente", "base": None}
    m = re.fullmatch(r"\(A PREÇOS DE ([A-ZÇÃ]+)/(\d{4}) - IPCA\)", preco or "")
    meses = [x.upper() for x in MES_EXTENSO]
    if not m or m.group(1) not in meses:
        raise Aborta(f"{onde}: não entendi o tipo de preço {preco!r}")
    return {"tipo": "real", "base": f"{m.group(2)}-{meses.index(m.group(1)) + 1:02d}"}


def tabela_anual(linhas, onde):
    """Tabelas IV, IV-A, V e V-A: uma linha por tributo, uma coluna por ano. {tipo, base, linhas: {chave: {ano: valor}}, parcial: [anos]}."""
    meta = cabecalho(linhas, onde)
    i = next((k for k, l in enumerate(linhas) if l and norm(l[0]) == "RECEITAS"), None)
    if i is None:
        raise Aborta(f"{onde}: sem o cabeçalho 'RECEITAS'")
    colunas, parcial = {}, []
    for j, c in enumerate(linhas[i + 1]):
        m = re.match(r"\s*(\d{4})", str(c)) if c is not None else None
        if m:
            colunas[j] = int(m.group(1))
            if "*" in str(c):
                parcial.append(int(m.group(1)))
    if not colunas:
        raise Aborta(f"{onde}: sem os anos no cabeçalho")
    dados = {}
    for l in linhas[i + 2:]:
        rot = norm(l[0]) if l else ""
        if rot.startswith("(*)"):
            break
        for chave, nome in LINHAS.items():
            if rot == norm(nome):
                dados[chave] = {colunas[j]: l[j] for j in colunas if j < len(l) and isinstance(l[j], float)}
    faltam = [NOME[k] for k in LINHAS if k not in dados or not dados[k]]
    if faltam:
        raise Aborta(f"{onde}: linha oficial que sumiu: {faltam}")
    return dict(meta, linhas=dados, parcial=parcial)


def tabela_mensal(linhas, onde):
    """Tabelas III e III-A: meses em linha (JAN…DEZ), com uma linha de total depois de cada ano. {tipo, base, meses: {coluna: {(ano, mês): valor}}, anos}."""
    meta = cabecalho(linhas, onde)
    i = next((k for k, l in enumerate(linhas) if l and norm(l[0]) == "MÊS"), None)
    if i is None:
        raise Aborta(f"{onde}: sem o cabeçalho 'MÊS'")
    colunas = {}
    for j, c in enumerate(linhas[i]):
        for chave, nome in COLUNAS_MENSAL.items():
            if norm(c) == nome:
                colunas[chave] = j
    if set(colunas) != set(COLUNAS_MENSAL):
        raise Aborta(f"{onde}: colunas oficiais que sumiram: {sorted(set(COLUNAS_MENSAL) - set(colunas))}")
    valores = {k: {} for k in COLUNAS_MENSAL}
    anos, pendentes = {}, []
    for l in linhas[i + 1:]:
        rot = norm(l[0]) if l else ""
        if rot in MESES_ABREV:
            pendentes.append((MESES_ABREV[rot], l))
            continue
        m = re.fullmatch(r"JAN-([A-Z]+) (\d{4})", rot)
        if not m:
            continue
        ano, ate = int(m.group(2)), MESES_ABREV[m.group(1)]
        if [mm for mm, _ in pendentes] != list(range(1, ate + 1)):
            raise Aborta(f"{onde}: {ano} com meses {[mm for mm, _ in pendentes]} (esperado 1 a {ate})")
        for chave, j in colunas.items():
            soma = 0.0
            for mm, lm in pendentes:
                v = lm[j] if j < len(lm) else None
                if not isinstance(v, float):
                    raise Aborta(f"{onde}: {ano}-{mm:02d} sem valor em {NOME_MENSAL[chave]}")
                valores[chave][(ano, mm)] = v
                soma += v
            if abs(soma - l[j]) > TOLERANCIA:
                raise Aborta(f"{onde}: {NOME_MENSAL[chave]} {ano}: a soma dos meses ({soma:.1f}) difere do total publicado ({l[j]:.1f})")
        anos[ano] = ate
        pendentes = []
    if not anos:
        raise Aborta(f"{onde}: nenhum ano lido")
    return dict(meta, meses=valores, anos=anos)


def serie_mensal(abas):
    """A série histórica por tributo, a preços correntes: {chave: {(ano, mês): valor}} e os totais anuais publicados {chave: {ano: total}}."""
    meses, totais = {k: {} for k in LINHAS}, {k: {} for k in LINHAS}
    for nome, linhas in abas.items():
        if not re.fullmatch(r"\d{4}", nome):
            continue
        ano = int(nome)
        cab = cabecalho(linhas, f"série histórica {nome}")
        if cab["tipo"] != "corrente":
            raise Aborta(f"série histórica {nome}: esperada a preços correntes")
        i = next((k for k, l in enumerate(linhas) if l and norm(l[0]) == "RECEITAS"), None)
        if i is None or [norm(c) for c in linhas[i][1:14]] != [m.upper() for m in ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ", "TOTAL"]]:
            raise Aborta(f"série histórica {nome}: cabeçalho de meses diferente do esperado")
        for l in linhas[i + 1:]:
            rot = norm(l[0]) if l else ""
            for chave, nome_l in LINHAS.items():
                if rot == norm(nome_l):
                    vals = l[1:14]
                    if len(vals) < 13 or not all(isinstance(v, float) for v in vals):
                        raise Aborta(f"série histórica {nome}: {NOME[chave]} com mês faltando")
                    for m in range(12):
                        meses[chave][(ano, m + 1)] = vals[m]
                    totais[chave][ano] = vals[12]
    return meses, totais


# ------------------------------------------------------------------------------------------------ conferências
def conferir(anexo, serie, ano_anexo, mes_anexo, ult_ano):
    """Todas as checagens que abortam. `anexo`: {tabela: lida}; `serie`: (meses, totais)."""
    base = f"{ano_anexo}-{mes_anexo:02d}"
    for nome in ("IV-A", "V-A", "III-A"):
        if anexo[nome]["tipo"] != "real" or anexo[nome]["base"] != base:
            raise Aborta(f"Tabela {nome}: mês-base {anexo[nome]['base']} (esperado {base}, o mês do relatório); a base mudou ou a tabela não é a corrigida")
    for nome in ("IV", "V", "III"):
        if anexo[nome]["tipo"] != "corrente":
            raise Aborta(f"Tabela {nome}: esperada a preços correntes")
    if (ano_anexo, mes_anexo) > (HOJE.year, HOJE.month):
        raise Aborta(f"relatório de {base} no futuro")
    if anexo["IV"]["parcial"] != [ano_anexo]:
        raise Aborta(f"Tabela IV: anos parciais {anexo['IV']['parcial']} (esperado só {ano_anexo})")
    for nome in ("IV", "IV-A", "V", "V-A"):
        for chave, por_ano in anexo[nome]["linhas"].items():
            if min(por_ano) != 1995 or max(por_ano) != ano_anexo or sorted(por_ano) != list(range(1995, ano_anexo + 1)):
                raise Aborta(f"Tabela {nome}: {NOME[chave]} com anos {min(por_ano)}–{max(por_ano)} ou com ano faltando")
            if any(v < 0 for v in por_ano.values()):
                raise Aborta(f"Tabela {nome}: valor de arrecadação negativo em {NOME[chave]}")
    # real não pode ficar abaixo do corrente (a correção leva tudo a preços de um mês posterior) nem absurdamente acima
    for tab_c, tab_r in (("IV", "IV-A"), ("V", "V-A")):
        for chave in LINHAS:
            for ano, vc in anexo[tab_c]["linhas"][chave].items():
                vr = anexo[tab_r]["linhas"][chave][ano]
                if vc > 0 and not 0.999 <= vr / vc <= 25:
                    raise Aborta(f"{NOME[chave]} {ano}: valor real {vr:.1f} contra corrente {vc:.1f} (razão fora de 0,999 a 25)")
    # o ano corrente da Tabela IV é janeiro a mês do relatório: tem de ser igual ao da Tabela V
    for chave in LINHAS:
        a, b = anexo["IV"]["linhas"][chave][ano_anexo], anexo["V"]["linhas"][chave][ano_anexo]
        if abs(a - b) > TOLERANCIA:
            raise Aborta(f"{NOME[chave]} {ano_anexo}: Tabela IV ({a:.1f}) e Tabela V ({b:.1f}) deveriam ser iguais (janeiro a mês do relatório)")
    # IPI-Total = soma das linhas de IPI, em cada tabela e ano
    for nome in ("IV", "IV-A"):
        L = anexo[nome]["linhas"]
        for ano in L["ipi_total"]:
            soma = sum(L[k][ano] for k in ("ipi_fumo", "ipi_bebidas", "ipi_automoveis", "ipi_vinculado", "ipi_outros"))
            if abs(soma - L["ipi_total"][ano]) > TOLERANCIA * 2:
                raise Aborta(f"Tabela {nome} {ano}: IPI-Total ({L['ipi_total'][ano]:.1f}) difere da soma das linhas ({soma:.1f})")
    # série histórica mensal: 12 meses em cada ano fechado, e a soma dos meses = tabela anual (corrente)
    meses, totais = serie
    for chave in LINHAS:
        for ano in range(1995, ult_ano + 1):
            faltando = [m for m in range(1, 13) if (ano, m) not in meses[chave]]
            if faltando:
                raise Aborta(f"série histórica: {NOME[chave]} {ano} sem os meses {faltando}")
            soma = sum(meses[chave][(ano, m)] for m in range(1, 13))
            anual = anexo["IV"]["linhas"][chave][ano]
            if abs(soma - anual) > TOLERANCIA * 2 or abs(totais[chave][ano] - anual) > TOLERANCIA * 2:
                raise Aborta(f"{NOME[chave]} {ano}: a Tabela IV ({anual:.1f}) difere da soma dos meses da série histórica ({soma:.1f}; total publicado {totais[chave][ano]:.1f})")
    # tabela mensal agrupada: anos fechados com 12 meses, o corrente até o mês do relatório
    for nome in ("III", "III-A"):
        t = anexo[nome]
        for ano, ate in t["anos"].items():
            esperado = mes_anexo if ano == ano_anexo else 12
            if ate != esperado or ano > ano_anexo:
                raise Aborta(f"Tabela {nome}: {ano} vai até o mês {ate} (esperado {esperado})")
        for chave, dados in t["meses"].items():
            if any(v < 0 for v in dados.values()):
                raise Aborta(f"Tabela {nome}: valor negativo em {NOME_MENSAL[chave]}")
        if max(t["anos"]) != ano_anexo or sorted(t["anos"]) != list(range(min(t["anos"]), ano_anexo + 1)):
            raise Aborta(f"Tabela {nome}: anos {sorted(t['anos'])} com ano faltando ou fora do esperado")


# ------------------------------------------------------------------------------------------------ montagem
def bi(v):
    """R$ milhões → "R$ 85,4 bilhões"."""
    return dinheiro(v / 1000, "R$", 1) + " bilhões"


def rot_mes(ano, mes):
    return f"{MES[mes - 1]}/{ano}"


def construir(planilhas, gerado):
    ano, mes = planilhas["ano"], planilhas["mes"]
    abas_anexo, abas_serie = xlsx_lite.abas(planilhas["anexo"]), xlsx_lite.abas(planilhas["serie"])
    anexo = {}
    for nome, leitor in (("IV", tabela_anual), ("IV-A", tabela_anual), ("V", tabela_anual), ("V-A", tabela_anual), ("III", tabela_mensal), ("III-A", tabela_mensal)):
        if f"Tabela {nome}" not in abas_anexo:
            raise Aborta(f"o anexo não tem a Tabela {nome}")
        anexo[nome] = leitor(abas_anexo[f"Tabela {nome}"], f"Tabela {nome}")
    serie = serie_mensal(abas_serie)
    conferir(anexo, serie, ano, mes, planilhas["serieAte"])

    base = f"{ano}-{mes:02d}"
    base_txt = f"{MES_EXTENSO[mes - 1]}/{ano}"
    base_abrev = rot_mes(ano, mes)
    DADOS_ATE = f"{rot_mes(ano, mes)} (relatório mensal da Receita; anos fechados até {ano - 1})"
    periodo = f"jan–{MES[mes - 1]}/{ano}" if mes > 1 else f"jan/{ano}"
    fechados = list(range(1995, ano))
    real, corr = anexo["IV-A"]["linhas"], anexo["IV"]["linhas"]
    mreal, mcorr = anexo["V-A"]["linhas"], anexo["V"]["linhas"]
    ult = ano - 1

    def anual(tab, chave):
        return [(f"{a}-01-01", tab[chave][a]) for a in fechados]

    def mesmos(tab, chave):
        return [(f"{a}-01-01", tab[chave][a]) for a in range(1995, ano + 1)]

    def serie_graf(id_, nome, unid, freq, lista, apenas_tabela=False, casas=3):
        s = {"id": id_, "nome": nome, "unidade": unid, "freq": freq, "pontos": pts([(d, v / 1000) for d, v in lista], 1.0, casas)}
        if apenas_tabela:
            s["somenteTabela"] = True
        return s

    NR = f"a preços de {base_abrev} (IPCA, Receita)"
    NC = "valores correntes (sem correção)"
    fonte_anexo = {"nome": f"Receita Federal, Análise Mensal da Arrecadação, {base_txt}, Anexo (Tabelas IV, IV-A, V, V-A, III e III-A)",
                   "url": URL_ANEXO.format(ano=ano, mes=MES_URL[mes - 1]) + "/view"}
    fonte_serie = {"nome": f"Receita Federal, arrecadação das receitas federais, série histórica 1994 a {planilhas['serieAte']} (dados abertos)", "url": PAGINA_SERIE}
    fontes = [fonte_anexo, fonte_serie]

    def pc(v0, v1):
        """"variação de +20,5%": do valor `v0` (o mais antigo) para `v1`."""
        return f"variação de {variacao_txt(v1, v0, 'pct')}"

    # ---- decretos (lidos à mão no Planalto)
    dec = ler_cache(DECRETOS)
    if not dec or not dec.get("decretos"):
        raise Aborta("entradas/decretos_iof.json ausente ou vazio")
    chaves = [(d["numero"], d["ano"]) for d in dec["decretos"]]
    if len(set(chaves)) != len(chaves):
        raise Aborta("decreto duplicado em entradas/decretos_iof.json")
    if any(d["publicacao"] > HOJE.isoformat() for d in dec["decretos"]):
        raise Aborta("decreto com data de publicação no futuro")
    decretos = [{"data": d["publicacao"], "rotulo": f"Decreto nº {d['numero']}", "ano": d["ano"], "decreto": d["data"], "artigos": d["artigosDeAliquota"], "url": d["url"]}
                for d in dec["decretos"]]

    secoes, graficos, notas = [], OrderedDict(), OrderedDict()

    # ---- 9. a seção-mãe: o que é e o que a página não mostra
    secoes.append({"id": "tributos", "n": 9, "titulo": "Tributos cuja alíquota pode mudar por decreto",
                   "frase": (f"A Constituição permite que o Poder Executivo altere, por decreto e nos limites da lei, as alíquotas de alguns impostos, entre eles o IOF, o Imposto sobre Importação e o IPI. "
                             f"Abaixo, a arrecadação desses três, segundo a Receita Federal, até {base_abrev}."),
                   "definicao": "A arrecadação é o valor que a Receita Federal registra como arrecadado, em reais.",
                   "baseLegal": {"texto": BASE_LEGAL, "url": URL_CONSTITUICAO}, "notaFixa": FRASE_LEIS,
                   "real": (f"“Real” significa corrigido pelo IPCA pela própria Receita Federal, a preços de {base_txt}: a Receita refaz a tabela todo mês com os preços do último mês, "
                            f"por isso a série inteira é baixada de novo a cada rodada e os valores reais de rodadas diferentes não se comparam."),
                   "fontes": fontes})

    # ---- 9.1 IOF
    fa = fato(anual(real, "iof"), "A", "ano")
    corr_u, corr_a = corr["iof"][ult], corr["iof"][ult - 1]
    ytd, ytd_a = mreal["iof"][ano], mreal["iof"][ano - 1]
    frase = (f"A arrecadação do IOF foi de {bi(real['iof'][ult])} em {ult} (a preços de {base_abrev}, IPCA), contra {bi(real['iof'][ult - 1])} em {ult - 1} ({pc(real['iof'][ult - 1], real['iof'][ult])}). "
             f"Em valores correntes (sem correção), foi de {bi(corr_u)} em {ult} e de {bi(corr_a)} em {ult - 1}. "
             f"De janeiro a {MES_EXTENSO[mes - 1]} de {ano} ({periodo}), foi de {bi(ytd)} a preços de {base_abrev}, contra {bi(ytd_a)} no mesmo período de {ano - 1} ({pc(ytd_a, ytd)}).")
    secoes.append({"id": "iof", "dadosAte": DADOS_ATE, "n": 9.1, "titulo": "Quanto foi arrecadado de IOF?", "grafico": "g_iof", "grafico2": "g_iof_mesmos", "fato": fa, "frase": frase,
                   "definicao": "O IOF é o imposto sobre operações de crédito, câmbio e seguro, ou relativas a títulos ou valores mobiliários (Constituição, art. 153, inciso V).",
                   "fontes": fontes, "decretos": True})
    graficos["g_iof"] = {"titulo": f"IOF: arrecadação por ano, {fechados[0]} a {ult}", "rotData": "ano", "fmt": "bilhoes", "zero": True, "rotulosDiretos": False, "marcarPontos": True,
                         "notas": ["realRF", "decretos"], "decretos": decretos,
                         "series": [serie_graf("iof_real", f"IOF, {NR}", "R$ bilhões", "A", anual(real, "iof")),
                                    serie_graf("iof_corr", f"IOF, {NC}", "R$ bilhões", "A", anual(corr, "iof"))]}
    graficos["g_iof_mesmos"] = {"titulo": f"IOF: arrecadação de janeiro a {MES_EXTENSO[mes - 1]} de cada ano, {periodo} e os mesmos meses dos anos anteriores", "rotData": "ano", "fmt": "bilhoes",
                                "zero": True, "rotulosDiretos": False, "marcarPontos": True, "notas": ["realRF", "mesmos"],
                                "series": [serie_graf("iof_mesmos_real", f"IOF, jan–{MES[mes - 1]}, {NR}", "R$ bilhões", "A", mesmos(mreal, "iof")),
                                           serie_graf("iof_mesmos_corr", f"IOF, jan–{MES[mes - 1]}, {NC}", "R$ bilhões", "A", mesmos(mcorr, "iof"))]}

    # ---- 9.2 Imposto sobre Importação e IPI-Vinculado (anual, linhas separadas como a Receita publica na tabela anual)
    frase = (f"A arrecadação do Imposto sobre Importação foi de {bi(real['ii'][ult])} em {ult} e a do IPI-Vinculado à Importação, de {bi(real['ipi_vinculado'][ult])} (a preços de {base_abrev}, IPCA), "
             f"contra {bi(real['ii'][ult - 1])} e {bi(real['ipi_vinculado'][ult - 1])} em {ult - 1} ({pc(real['ii'][ult - 1], real['ii'][ult])} e {pc(real['ipi_vinculado'][ult - 1], real['ipi_vinculado'][ult]).replace('variação de ', '')}). "
             f"De janeiro a {MES_EXTENSO[mes - 1]} de {ano} ({periodo}): {bi(mreal['ii'][ano])} e {bi(mreal['ipi_vinculado'][ano])}, contra {bi(mreal['ii'][ano - 1])} e {bi(mreal['ipi_vinculado'][ano - 1])} no mesmo período de {ano - 1} "
             f"({pc(mreal['ii'][ano - 1], mreal['ii'][ano])} e {pc(mreal['ipi_vinculado'][ano - 1], mreal['ipi_vinculado'][ano]).replace('variação de ', '')}).")
    secoes.append({"id": "importacao", "dadosAte": DADOS_ATE, "n": 9.2, "titulo": "Quanto foi arrecadado de Imposto sobre Importação e de IPI vinculado à importação?", "grafico": "g_importacao",
                   "fato": fato(anual(real, "ii"), "A", "ano"), "frase": frase,
                   "definicao": "O Imposto sobre Importação incide sobre a importação de produtos estrangeiros (Constituição, art. 153, inciso I); IPI-Vinculado à Importação é o nome que a Receita dá à linha do IPI ligada à importação.",
                   "notaFixa": ("Na tabela anual a Receita publica o Imposto sobre Importação e o IPI-Vinculado em linhas separadas, e na tabela mensal (mais abaixo) publica os dois juntos. Cada gráfico "
                                "mostra a linha como a tabela de onde vem; este site não somou nem separou nada."), "fontes": fontes})
    graficos["g_importacao"] = {"titulo": f"Imposto sobre Importação e IPI-Vinculado à Importação: arrecadação por ano, {fechados[0]} a {ult}", "rotData": "ano", "fmt": "bilhoes", "zero": True,
                                "rotulosDiretos": False, "marcarPontos": True, "notas": ["realRF", "linhas"],
                                "series": [serie_graf("ii_real", f"Imposto sobre Importação, {NR}", "R$ bilhões", "A", anual(real, "ii")),
                                           serie_graf("ipiv_real", f"IPI-Vinculado à Importação, {NR}", "R$ bilhões", "A", anual(real, "ipi_vinculado")),
                                           serie_graf("ii_corr", f"Imposto sobre Importação, {NC}", "R$ bilhões", "A", anual(corr, "ii"), True),
                                           serie_graf("ipiv_corr", f"IPI-Vinculado à Importação, {NC}", "R$ bilhões", "A", anual(corr, "ipi_vinculado"), True)]}

    # ---- 9.3 IPI, demais linhas oficiais
    linhas_ipi = [("ipi_fumo", "IPI-Fumo"), ("ipi_bebidas", "IPI-Bebidas"), ("ipi_automoveis", "IPI-Automóveis"), ("ipi_outros", "IPI-Outros")]
    por_linha = "; ".join(f"{nome.replace('IPI-', '').lower()}, {bi(real[k][ult])}" for k, nome in linhas_ipi)
    frase = (f"A arrecadação do IPI-Total foi de {bi(real['ipi_total'][ult])} em {ult} (a preços de {base_abrev}, IPCA), contra {bi(real['ipi_total'][ult - 1])} em {ult - 1} ({pc(real['ipi_total'][ult - 1], real['ipi_total'][ult])}). "
             f"Por linha oficial da Receita, em {ult}: {por_linha}; vinculado à importação, {bi(real['ipi_vinculado'][ult])}. "
             f"De janeiro a {MES_EXTENSO[mes - 1]} de {ano} ({periodo}), o IPI-Total foi de {bi(mreal['ipi_total'][ano])}, contra {bi(mreal['ipi_total'][ano - 1])} no mesmo período de {ano - 1} ({pc(mreal['ipi_total'][ano - 1], mreal['ipi_total'][ano])}).")
    secoes.append({"id": "ipi", "dadosAte": DADOS_ATE, "n": 9.3, "titulo": "Quanto foi arrecadado de IPI, por linha oficial?", "grafico": "g_ipi", "fato": fato(anual(real, "ipi_total"), "A", "ano"), "frase": frase,
                   "definicao": "O IPI é o imposto sobre produtos industrializados (Constituição, art. 153, inciso IV); a Receita publica a arrecadação dele em linhas: fumo, bebidas, automóveis, vinculado à importação e outros.",
                   "notaFixa": "O gráfico mostra as linhas do IPI que a Receita publica à parte, menos a do vinculado à importação (acima). O IPI-Total é a linha publicada pela Receita, não uma soma feita aqui.",
                   "fontes": fontes})
    graficos["g_ipi"] = {"titulo": f"IPI: arrecadação por ano, por linha oficial, {fechados[0]} a {ult}", "rotData": "ano", "fmt": "bilhoes", "zero": True, "rotulosDiretos": False,
                         "marcarPontos": False, "notas": ["realRF", "linhas"],
                         "series": [serie_graf(f"{k}_real", f"{nome}, {NR}", "R$ bilhões", "A", anual(real, k)) for k, nome in linhas_ipi]
                         + [serie_graf("ipi_total_real", f"IPI-Total, {NR}", "R$ bilhões", "A", anual(real, "ipi_total"), True)]
                         + [serie_graf(f"{k}_corr", f"{nome}, {NC}", "R$ bilhões", "A", anual(corr, k), True) for k, nome in linhas_ipi]
                         + [serie_graf("ipi_total_corr", f"IPI-Total, {NC}", "R$ bilhões", "A", anual(corr, "ipi_total"), True)]}

    # ---- 9.4 mensal, as linhas agrupadas como a Receita as agrupa (desde 2021)
    mr, mc = anexo["III-A"]["meses"], anexo["III"]["meses"]
    def mensal(dados, chave):
        return [(f"{a}-{m:02d}-01", v) for (a, m), v in sorted(dados[chave].items())]
    ii_r, ii_c = mr["ii_ipiv"][(ano, mes)], mc["ii_ipiv"][(ano, mes)]
    ip_r, ip_c = mr["ipi_exceto"][(ano, mes)], mc["ipi_exceto"][(ano, mes)]
    frase = (f"Em {base_abrev}, a arrecadação da linha “I. Importação e IPI-Vinculado”, que a Receita publica agrupada, foi de {bi(ii_r)} a preços de {base_abrev} ({bi(ii_c)} em valores correntes), "
             f"contra {bi(mr['ii_ipiv'][(ano - 1, mes)])} em {rot_mes(ano - 1, mes)} ({pc(mr['ii_ipiv'][(ano - 1, mes)], ii_r)}). "
             f"A da linha “IPI (exceto IPI-Vinculado)” foi de {bi(ip_r)} ({bi(ip_c)} em valores correntes), contra {bi(mr['ipi_exceto'][(ano - 1, mes)])} em {rot_mes(ano - 1, mes)} ({pc(mr['ipi_exceto'][(ano - 1, mes)], ip_r)}).")
    ini_m = min(anexo["III"]["anos"])
    f_m = fato([(d, v / 1000) for d, v in mensal(mr, "ii_ipiv")], "M", "mes")
    secoes.append({"id": "tributos-mensal", "dadosAte": DADOS_ATE, "n": 9.4, "titulo": "E mês a mês, desde 2021?", "grafico": "g_tributos_mensal", "fato": f_m, "frase": frase,
                   "definicao": "A Receita publica, mês a mês desde 2021, duas linhas agrupadas: Imposto sobre Importação junto com o IPI-Vinculado, e o IPI sem o vinculado.",
                   "notaFixa": "Aqui as linhas são as agrupadas da Tabela III da Receita; nada foi somado nem separado por este site.", "fontes": [fonte_anexo]})
    graficos["g_tributos_mensal"] = {"titulo": f"Arrecadação mensal por linha agrupada da Receita, {rot_mes(ini_m, 1)} a {base_abrev}", "rotData": "mes", "fmt": "bilhoes", "zero": True,
                                     "rotulosDiretos": False, "marcarPontos": False, "notas": ["realRF", "linhas"],
                                     "series": [serie_graf("ii_ipiv_real", f"I. Importação e IPI-Vinculado, {NR}", "R$ bilhões", "M", mensal(mr, "ii_ipiv")),
                                                serie_graf("ipi_exceto_real", f"IPI (exceto IPI-Vinculado), {NR}", "R$ bilhões", "M", mensal(mr, "ipi_exceto")),
                                                serie_graf("ii_ipiv_corr", f"I. Importação e IPI-Vinculado, {NC}", "R$ bilhões", "M", mensal(mc, "ii_ipiv"), True),
                                                serie_graf("ipi_exceto_corr", f"IPI (exceto IPI-Vinculado), {NC}", "R$ bilhões", "M", mensal(mc, "ipi_exceto"), True)]}

    notas["realRF"] = {"curta": f"Valores reais: tabela da Receita Federal corrigida pelo IPCA, a preços de {base_txt}; a base muda todo mês e a série inteira é baixada de novo. Os valores correntes estão na camada avançada.",
                     "texto": (f"Os valores reais são os da tabela “a preços de {base_txt} - IPCA” que a própria Receita Federal publica; este site não deflaciona nada. A Receita refaz a tabela "
                               f"todo mês com os preços do último mês, então os valores reais de rodadas diferentes não se comparam. Os valores correntes (sem correção) vêm da tabela a preços correntes."),
                     "fontes": [fonte_anexo]}
    notas["linhas"] = {"curta": "Cada linha é a que a Receita publica na tabela de onde vem; nada foi somado nem separado por este site.",
                       "texto": ("Na tabela anual (IV e IV-A) o Imposto sobre Importação e o IPI-Vinculado à Importação são linhas separadas, e o IPI-Total é uma linha própria; na tabela mensal "
                                 "(III e III-A) a Receita agrupa Imposto sobre Importação com IPI-Vinculado e publica o IPI sem o vinculado. Este site mostra cada linha como a tabela a publica."),
                       "fontes": [fonte_anexo]}
    notas["mesmos"] = {"curta": f"Janeiro a {MES_EXTENSO[mes - 1]} de cada ano: o ano corrente só é comparado com os mesmos meses dos outros anos.",
                       "texto": (f"O ano corrente tem só {mes} {'mês' if mes == 1 else 'meses'} fechados ({periodo}). Por isso ele nunca é comparado com um ano inteiro: o gráfico mostra janeiro a "
                                 f"{MES_EXTENSO[mes - 1]} de cada ano (Tabelas V e V-A da Receita), e o gráfico anual acima traz só anos fechados."),
                       "fontes": [fonte_anexo]}
    notas["decretos"] = {"curta": "Os losangos marcam a publicação de decretos que alteraram alíquotas do regulamento do IOF. Não descrevem efeito nenhum e não mostram que o decreto causou variação.",
                         "texto": (dec["criterio"] + " " + dec["limite"] + " " + dec["_aviso"] + " " + FRASE_LEIS),
                         "fontes": [{"nome": dec["regulamento"]["nome"], "url": dec["regulamento"]["url"]}, {"nome": "Constituição, art. 153", "url": dec["baseLegal"]["url"]}]}

    conferencias = {"anualContraMensal": {"texto": f"Tabela anual a preços correntes contra a soma dos 12 meses da série histórica, 1995 a {ult}, nas {len(LINHAS)} linhas (diferença máxima, R$ milhões)",
                                          "maximo": round(max(abs(sum(serie[0][c][(a, m)] for m in range(1, 13)) - corr[c][a]) for c in LINHAS for a in range(1995, ult + 1)), 3)},
                    "ipiTotal": {"texto": "IPI-Total contra a soma das cinco linhas de IPI na Tabela IV (diferença máxima, R$ milhões)",
                                 "maximo": round(max(abs(sum(corr[k][a] for k in ("ipi_fumo", "ipi_bebidas", "ipi_automoveis", "ipi_vinculado", "ipi_outros")) - corr["ipi_total"][a]) for a in corr["ipi_total"]), 3)},
                    "tabelasIVeV": {"texto": f"Tabela IV e Tabela V no ano corrente ({periodo}), nas {len(LINHAS)} linhas (diferença máxima, R$ milhões)",
                                    "maximo": round(max(abs(corr[c][ano] - mcorr[c][ano]) for c in LINHAS), 3)}}
    return {"versao": VERSAO, "geradoEm": gerado, "hoje": HOJE.isoformat(), "coletadoEm": planilhas["baixadoEm"],
            "baseReal": {"indice": "IPCA", "mes": base, "rotulo": base_txt, "quem": "Receita Federal do Brasil"},
            "referencia": {"mes": base, "rotulo": base_abrev, "periodoCorrente": periodo, "mesesCorrente": mes, "ultimoAnoFechado": ult, "serieHistoricaAte": planilhas["serieAte"],
                           "paginaDosRelatorios": PAGINA_RELATORIOS.format(ano=ano)},
            "secoes": secoes, "graficos": graficos, "notas": notas, "conferencias": conferencias,
            "decretos": {"total": dec["total"], "porAno": dec["porAno"], "criterio": dec["criterio"], "limite": dec["limite"], "aviso": dec["_aviso"], "lidoEm": dec["lidoEm"],
                         "regulamento": dec["regulamento"], "baseLegal": dec["baseLegal"], "lista": decretos}}


def pontos_do_bloco(o):
    return sum(len(s["pontos"]) for g in o["graficos"].values() for s in g["series"])


def textos(o):
    t = []
    for s in o["secoes"]:
        t += [s["titulo"], s["frase"], s["definicao"], s.get("notaFixa", ""), s.get("real", ""), s.get("baseLegal", {}).get("texto", "")]
    t += [g["titulo"] for g in o["graficos"].values()] + [s["nome"] for g in o["graficos"].values() for s in g["series"]]
    t += [n["texto"] for n in o["notas"].values()] + [n["curta"] for n in o["notas"].values()]
    t += [o["decretos"]["criterio"], o["decretos"]["limite"], o["decretos"]["aviso"]] + [d["rotulo"] for d in o["decretos"]["lista"]]
    return [x for x in t if x]


def checar_textos(txts):
    checar_proibidas(txts, PROIBIDAS)


# ------------------------------------------------------------------------------------------------ gravação
def gravar(o):
    import contexto as C
    destino = SAIDA / "tributos.json"
    ant = ler_cache(destino)
    if ant and ant.get("graficos") and pontos_do_bloco(o) < 0.9 * pontos_do_bloco(ant):
        raise Aborta(f"o bloco dos tributos encolheu de {pontos_do_bloco(ant)} para {pontos_do_bloco(o)} pontos; nada gravado")
    C.gravar_json(destino, o, linhas=("secoes",))


def pode_rodar(forcar):
    est = ler_cache(CACHE_R / "estado.json") or {}
    ultima = est.get("ultima")
    if forcar or not ultima:
        return True, None
    dias = (HOJE - date.fromisoformat(ultima)).days
    return dias >= SEMANAL, dias


def imprimir(o):
    print(f"\nMês-base dos valores reais: {o['baseReal']['rotulo']} · relatório de {o['referencia']['rotulo']} · série histórica até {o['referencia']['serieHistoricaAte']}")
    print("\nSÉRIES (último valor, R$ bilhões)")
    for g in o["graficos"].values():
        for s in g["series"]:
            u = s["pontos"][-1]
            print(f"  {s['nome'][:70]:<70} {s['pontos'][0][0][:7]} → {u[0][:7]}  último = {u[1]}")
    print("\nFRASES")
    for s in o["secoes"]:
        print(f"\n{s['n']}. {s['titulo']}\n   {s['frase']}\n   ({s['definicao']})")
    print("\nCONFERÊNCIAS", json.dumps(o["conferencias"], ensure_ascii=False))
    d = o["decretos"]
    print(f"\nDECRETOS: {d['total']} — por ano {d['porAno']}")


# ------------------------------------------------------------------------------------------------ testes
def testar():
    ok = 0

    def igual(a, b, msg):
        nonlocal ok
        assert a == b, f"{msg}: {a!r} != {b!r}"
        ok += 1

    def aborta(fn, msg):
        nonlocal ok
        try:
            fn()
        except Aborta:
            ok += 1
            return
        raise AssertionError(f"deveria abortar: {msg}")

    igual(norm("  I.P.I-FUMO "), "I.P.I-FUMO", "rótulo com recuo")
    igual(bi(85413.4), "R$ 85,4 bilhões", "bilhões")
    igual(bi(-1500), MENOS + "R$ 1,5 bilhões", "sinal antes da moeda")
    igual(cabecalho([["UNIDADE: R$ MILHÕES"], ["(A PREÇOS DE AGOSTO/2026 - IPCA)"]], "t"), {"tipo": "real", "base": "2026-08"}, "mês-base")
    igual(cabecalho([["UNIDADE: R$ MILHÕES"], ["(A PREÇOS CORRENTES)"]], "t"), {"tipo": "corrente", "base": None}, "corrente")
    aborta(lambda: cabecalho([["UNIDADE: R$ MIL"], ["(A PREÇOS CORRENTES)"]], "t"), "unidade diferente")
    aborta(lambda: cabecalho([["UNIDADE: R$ MILHÕES"], ["(A PREÇOS DE SMARCO/2026 - IPCA)"]], "t"), "mês-base ilegível")

    def tabela(ano_final=2026, unid="UNIDADE: R$ MILHÕES", preco="(A PREÇOS CORRENTES)", sem=None):
        anos = list(range(1995, ano_final + 1))
        cab = [[], ["TABELA"], [unid], [preco], ["RECEITAS", "ARRECADAÇÃO"], [None] + [str(a) + (" (*)" if a == ano_final else "") for a in anos]]
        for k, nome in LINHAS.items():
            if k != sem:
                cab.append([nome] + [100.0 + a - 1995 for a in anos])
        return cab + [["(*) ATÉ AGOSTO."]]
    t = tabela_anual(tabela(), "t")
    igual(sorted(t["linhas"]), sorted(LINHAS), "todas as linhas lidas")
    igual((t["parcial"], t["linhas"]["iof"][2026], len(t["linhas"]["iof"])), ([2026], 131.0, 32), "ano parcial e valores")
    aborta(lambda: tabela_anual(tabela(sem="iof"), "t"), "linha oficial que sumiu")
    aborta(lambda: tabela_anual(tabela(unid="UNIDADE: R$ BILHÕES"), "t"), "unidade diferente")

    def mensal(anos=(2021, 2026), ate=8, sem_mes=None, soma_errada=False):
        linhas = [[], ["TABELA III"], ["UNIDADE: R$ MILHÕES"], ["(A PREÇOS CORRENTES)"], ["MÊS", "I. Renda", "IPI\n(Exceto\nIPI-Vinculado)", "I. Importação\ne\nIPI-Vinculado"]]
        for a in range(anos[0], anos[1] + 1):
            n = ate if a == anos[1] else 12
            ms = [m for m in range(1, n + 1) if m != sem_mes]
            nomes = {v: k for k, v in MESES_ABREV.items()}
            for m in ms:
                linhas.append([nomes[m], 1.0, 10.0, 20.0])
            linhas.append([f"JAN-{nomes[n]}\n{a}", n, 10.0 * len(ms) + (50 if soma_errada else 0), 20.0 * len(ms)])
        return linhas
    tm = tabela_mensal(mensal(), "t")
    igual((tm["anos"], tm["meses"]["ii_ipiv"][(2026, 8)]), ({2021: 12, 2022: 12, 2023: 12, 2024: 12, 2025: 12, 2026: 8}, 20.0), "tabela mensal")
    aborta(lambda: tabela_mensal(mensal(sem_mes=3), "t"), "mês faltando")
    aborta(lambda: tabela_mensal(mensal(soma_errada=True), "t"), "a soma dos meses difere do total")

    for palavra in ("o aumento de imposto de 2025", "um tributo arrecadatório", "função regulatória", "a dependência da receita", "o risco fiscal"):
        aborta(lambda palavra=palavra: checar_textos([palavra]), palavra)
    checar_textos(["A arrecadação do IOF foi de R$ 85,4 bilhões em 2025, a preços de ago/2026 (IPCA).", "O decreto alterou a alíquota no artigo 7º."])
    ok += 1
    # a lista de decretos
    dec = json.loads((coleta.ENTRADAS / "decretos_iof.json").read_text(encoding="utf-8")) if (coleta.ENTRADAS / "decretos_iof.json").exists() else None
    if dec:
        igual(dec["total"], len(dec["decretos"]), "o total de decretos")
        igual(len({(d["numero"], d["ano"]) for d in dec["decretos"]}), dec["total"], "nenhum decreto repetido")
        igual(all(d["data"] <= d["publicacao"] and d["url"].startswith("https://www.planalto.gov.br/") for d in dec["decretos"]), True, "datas e endereços do Planalto")
        checar_textos([dec["criterio"], dec["limite"], dec["_aviso"]])
        ok += 1
    # o texto fixo da página
    html = (Path(__file__).resolve().parent.parent / "site" / "contexto.html").read_text(encoding="utf-8")
    ini = html.index("/* ===================================================================== mais indicadores")
    js = html[ini:html.index("/* ===================================================================== início")]
    trechos = [a or b for a, b in re.findall(r"`([^`]*)`|'([^'\n]{12,})'", js)]
    estatico = re.sub(r"\$\{[^}]*\}", " ", " ".join(trechos))
    igual(len(estatico) > 1000, True, "o texto fixo do bloco foi lido")
    checar_textos([estatico])
    ok += 1
    print(f"{ok} verificações passaram")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so-montar", action="store_true")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--imprimir", action="store_true")
    ap.add_argument("--teste", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.teste:
        testar()
        return
    rede = not args.so_montar
    if rede:
        pode, dias = pode_rodar(args.forcar)
        if not pode:
            print(f"Receita Federal: última coleta há {dias} dia(s); a Receita publica um relatório por mês e a rodada é semanal. Nada a fazer (use --forcar).")
            return
    try:
        planilhas = baixar_tudo() if rede else ler_cache_planilhas()
        gerado = datetime.now(BRASILIA).isoformat(timespec="minutes")
        o = construir(planilhas, gerado)
        checar_textos(textos(o))
        if not args.simular:
            gravar(o)
    except Aborta as e:
        print(f"ABORTADO (nada gravado; o arquivo anterior fica): {e}")
        sys.exit(1)
    if args.simular:
        print("--simular: conferido, nada gravado.")
    else:
        if rede:
            gravar_cache(CACHE_R / "estado.json", {"ultima": HOJE.isoformat()})
        print(f"tributos.json gravado: {len(o['secoes'])} seções, {len(o['graficos'])} gráficos, {pontos_do_bloco(o)} pontos; relatório de {o['referencia']['rotulo']}, "
              f"valores reais a preços de {o['baseReal']['rotulo']}")
    if args.imprimir:
        imprimir(o)


if __name__ == "__main__":
    main()
