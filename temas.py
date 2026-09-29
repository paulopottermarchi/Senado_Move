"""
Câmara Aberta — temas das proposições, para a página do deputado (deputado.html).

Uso:
    python temas.py              # depois de coleta.py (e de votos.py, se for rodar)
    python temas.py --auditoria  # grava também cache/temas_auditoria.csv

Lê só o cache/ da Etapa A. Não chama a API e não altera deputados.json.
Grava temas/taxonomia.json e temas/{id}.json, um arquivo por deputado, para que a
página carregue apenas o deputado aberto.

A CATEGORIA É INTERPRETAÇÃO DESTE SITE, não dado da Câmara. Regras de palavras-chave
sobre a ementa oficial e, quando a ementa não basta, sobre a indexação oficial
(campo `keywords`). Cada rótulo grava o termo que o disparou, para que qualquer
pessoa confira por que a proposição está ali. Ver CLAUDE.md, "Página do deputado".

Regras de contagem herdadas de coleta.py, sem reimplementar: autoria é proponente 1
(e_autor), tipos são PEC e PL (TIPOS), "virou lei" é a situação oficial (virou_lei).
O script confere as contagens contra deputados.json e aborta sem gravar se divergirem.
"""

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import date

from coleta import BASE, CACHE, TIPOS, e_autor, ler_cache, virou_lei
import resumos  # passo 8: resumo do inteiro teor, gerado por resumos.py

SAIDA = BASE / "temas"
DEPUTADOS = BASE / "deputados.json"
VERSAO = "2026-09-28.2"   # mudar a cada alteração de regra: vai para a página
# 57ª legislatura. A frase-resumo e o filtro padrão da página contam só daqui para a
# frente: a API devolve a carreira inteira, e somar mandatos antigos põe veterano e
# estreante na mesma régua. Trocar a cada legislatura (a 58ª começa em 1º/2/2027).
INICIO_LEGISLATURA = "2023-02-01"

# Ordem de exibição. O bloco é só agrupamento de leitura; a unidade é a categoria.
BLOCOS = [
    ("Políticas sociais", ["Saúde", "Educação", "Trabalho, emprego e profissões",
                           "Previdência e assistência social"]),
    ("Grupos e direitos", ["Pessoa idosa e pessoa com deficiência",
                           "Mulheres e violência de gênero", "Crianças e adolescentes",
                           "Direitos humanos e igualdade"]),
    ("Estado, justiça e segurança", ["Direito penal e segurança pública",
                                     "Administração pública e servidores",
                                     "Direito civil, processo e justiça",
                                     "Política, eleições e processo legislativo",
                                     "Relações exteriores e soberania",
                                     "Defesa, Forças Armadas e militares"]),
    ("Economia e mercado", ["Economia, indústria, comércio e empreendedorismo",
                            "Sistema financeiro, crédito e pagamentos",
                            "Tributação e finanças públicas", "Consumidor",
                            "Propriedade intelectual"]),
    ("Infraestrutura e território", ["Transporte, trânsito e mobilidade",
                                     "Meio ambiente e animais",
                                     "Cidades, habitação e desenvolvimento urbano",
                                     "Energia, mineração, água e saneamento",
                                     "Agro e desenvolvimento rural"]),
    ("Tecnologia", ["Tecnologia, telecom e dados"]),
    ("Cultura e homenagens", ["Cultura, esporte e turismo", "Homenagens, datas e títulos"]),
]

# Padrões sobre texto normalizado: minúsculo e sem acento. Números de lei entram
# com o ponto (8\.069 = ECA). Um rótulo errado aparece na página com o termo que o
# causou; corrigir aqui, subir VERSAO e rodar de novo.
REGRAS = {
 "Homenagens, datas e títulos": r"institui o dia|institui a semana|institui o mes|dia nacional|dia mundial|semana nacional|mes nacional|capital nacional|capital brasileira|confere .{0,80}titulo|inscreve o nome|livro dos herois|^denomina\b|\bdenomina (o|a|os|as)\b|confere a denominacao|declara .{0,60}patrono|calendario oficial|reconhece .{0,100} como (capital|patrimonio|manifestacao|polo|berco|de relevante interesse cultural)|patrimonio cultural imaterial|manifestacao da cultura nacional",
 "Saúde": r"\bsus\b|saude|medicament|hospital|vacin|doenc|cancer|oncolog|medic[oa]s?\b|medicina|enfermag|farmac|diagnostic|sindrome|tratamento(?! (de dados|discriminatori|tributari|diferenciado|isonomico|igualitario|de esgoto|de agua|de residuos))|terapi|paciente|gestante|parto|neonatal|obstetric|anvisa|sangue|transplante|odontolog|suplemento[s]? alimentar|transtorno|fibromialgia|malaria|8\.080|10\.216|burnout|saude mental",
 "Educação": r"escola|ensino|educac|educand|estudant|aluno|universidad|professor|docente|creche|alfabetiz|\bfies\b|\benem\b|pedagog|bolsa de estudo|9\.394",
 "Direito penal e segurança pública": r"codigo penal|2\.848|processo penal|3\.689|execucao penal|7\.210|crime|criminal|\bpena\b|penas\b|tipo penal|tipifica|homicid|policia|policial|seguranca publica|prisional|presidio|arma[s]? de fogo|10\.826|trafico|drogas|11\.343|feminicid|estupro|8\.072|hediondo|organizacao criminosa|faccao|guarda municipal|delegad|anistia|agentes quimicos",
 "Mulheres e violência de gênero": r"mulher|violencia domestica|maria da penha|11\.340|feminicid|violencia de genero|igualdade de genero|gestante|maternidade|\bmaes?\b",
 "Crianças e adolescentes": r"crianca|adolescent|8\.069|infancia|infantil|infantojuvenil|15\.211",
 "Pessoa idosa e pessoa com deficiência": r"idos[oa]|10\.741|pessoa[s]? com deficiencia|13\.146|autis|\btea\b|acessibilidade|surd|\bcego|deficiencia visual|neurodiver|doenca[s]? rara|\btdah\b|dislexia",
 "Direitos humanos e igualdade": r"racial|racismo|7\.716|indigena|quilombol|lgbt|travesti|transgenero|orientacao sexual|identidade de genero|direitos humanos|discrimina|refugiad|migrant|situacao de rua|campones",
 "Trabalho, emprego e profissões": r"trabalh|\bclt\b|5\.452|empregad|emprego|salari|sindic|jornada|\bfgts\b|8\.036|seguro-desemprego|estagiar|profissao|profissional|piso salarial|exercicio profissional|conselho[s]? (federal|regional)",
 "Previdência e assistência social": r"previdenc|aposentad|\binss\b|8\.213|8\.212|beneficio de prestacao continuada|\bbpc\b|assistencia social|8\.742|bolsa familia|pensao por morte|cadastro unico|cadunico|vulnerabilidade social|baixa renda|fome|seguranca alimentar|abastecimento alimentar|auxilio emergencial",
 "Tributação e finanças públicas": r"imposto|tribut|\bicms\b|\bipi\b|\biof\b|\bpis\b|cofins|contribuicao social|isencao|isenta|aliquota|7\.713|9\.250|simples nacional|orcament|fundo nacional|precatori|divida publica|divida ativa|receita federal|\bcbs\b|\bibs\b|responsabilidade fiscal|erario|lucros de controladas|14\.754",
 "Sistema financeiro, crédito e pagamentos": r"instituic(ao|oes) financeira|\bbancos?\b(?! de (dados|leite|alimentos|sangue|horas|olhos|perfis|dna|germoplasma|sementes|precos))|bancari|credito|emprestimo|consignad|juros|cartao de credito|\bpix\b|meio[s]? de pagamento|arranjo[s]? de pagamento|instituic(ao|oes) de pagamento|criptoativ|ativos virtuais|endividamento|financiamento(?! de campanha| eleitoral| partidario)|\bseguros?\b|seguradora|previdencia complementar|mercado de capitais|valores mobiliarios|\bcvm\b|banco central|fintech|apostas|\bbets?\b|loteria|14\.790|fiduciaria|cambio|derivativos|cobranca de divida|protesto",
 "Consumidor": r"consumidor|8\.078|fornecedor|fatura|cobranca indevida|\bsac\b|recall|comercio eletronico|e-commerce|varejo|hospede|atendimento humano",
 "Tecnologia, telecom e dados": r"internet|digita|inteligencia artificial|\bia\b|algoritm|plataforma[s]? digita|rede[s]? socia|dados pessoais|protecao de dados|13\.709|\blgpd\b|marco civil|12\.965|cibernet|ciberseguranca|hacker|aplicativo|provedor|online|on-line|virtual|deepfake|software|telecomunic|telefon|9\.472|radiodifus|streaming|influenciador|biometr|reconhecimento facial|governo digital|14\.129|dados abertos|interoperabilidade|blockchain|jogos eletronicos|conectividade|banda larga|\b5g\b|sistemas automatizados|decis(ao|oes) automatizada|15\.211",
 "Propriedade intelectual": r"9\.610|9\.279|direito[s]? autora|propriedade industrial|propriedade intelectual|patente|obras? intelectua|indicacao geografica",
 "Transporte, trânsito e mobilidade": r"transit|9\.503|veiculo|rodovi|transport|aviac|aeroport|aeronave|codigo brasileiro de aeronautica|ferrovi|mobilidade|motorista|\bcnh\b|habilitacao|onibus|motocicl|ciclovia|bicicleta|pedagio|portuari|\bportos\b|hidrovi|vicina|estradas",
 "Meio ambiente e animais": r"meio ambiente|ambiental|ambientais|animais|animal|fauna|flora|floresta|desmatament|climatic|\bclima\b|residuos|reciclag|reciclad|poluic|sustentab|biodivers|queimad|mananc|bioma|amazonia|pantanal|caatinga|cerrado|\bpets?\b|maus.tratos|9\.985|9\.605|unidades de conservacao|vasilhame",
 "Energia, mineração, água e saneamento": r"energia|eletric|combustiv|petrole|\bgas\b|mineracao|minerio|mineral|hidric|saneamento|sanitari[ao]s? simplificad|esgot|abastecimento de agua|agua potavel|biocombustiv|hidrogenio|\bsolar|eolic|aneel|tarifa social",
 "Agro e desenvolvimento rural": r"agricult|agropecuar|agronegoc|pecuari|rural|pesca|pescador|aquicult|agrotox|safra|cooperativa|fundiari|reforma agraria|embrapa|\bleite\b|\bcafe\b|arroz|\bsoja\b|\bcarnes?\b",
 "Cultura, esporte e turismo": r"\bcultur|\barte[s]?\b|artist|musica|cinema|audiovisual|patrimonio (cultural|historico)|museu|biblioteca|livro|esport|atleta|futebol|olimpi|turism|turistic|lazer|festival|religi|igreja|templo|capoeira|carnaval|atividade fisica",
 "Administração pública e servidores": r"servidor|administracao publica|concurso publico|licitac|contrat(os|acoes|acao) public|14\.133|8\.666|improbidade|8\.429|transparencia|acesso a informacao|12\.527|agencia[s]? reguladora|autarqui|estata|cargo[s]? public|orgao[s]? public|8\.112|tribunal de contas|obras publicas|processo administrativo|decisoes administrativas|integridade regulatoria|regularidade federativa|servicos publicos|poder publico|regulatori|politicas publicas|ativos publicos|patrimonio publico|8\.159|arquivos publicos",
 "Direito civil, processo e justiça": r"codigo civil|10\.406|processo civil|13\.105|direito de familia|poder familiar|entidade familiar|convivencia familiar|pensao alimenticia|alimentos gravidicos|prestacao de alimentos|devedor(es)? de alimentos|guarda compartilhada|guarda dos filhos|adocao|heranca|sucessao|divorcio|casamento|uniao estavel|cartor|registro[s]? publico|6\.015|notari|judiciari|juiz|tribunal|\bvaras?\b|justica federal|defensoria|ministerio publico|advocac|advogad|\boab\b|8\.906|arbitragem|mediacao|condominio|locacao|8\.245|inquilin|usucapiao|responsabilidade civil|falencia|recuperacao judicial|11\.101",
 "Economia, indústria, comércio e empreendedorismo": r"empresa|empreend|microempre|\bmei\b|industri(?!alizad)|comercio|comercia|exportac|importac|zona franca|inovac|startup|franquia|concorrencia|\bcade\b|12\.529|desenvolvimento economico|economia|produtiv|consumo local",
 "Cidades, habitação e desenvolvimento urbano": r"habitac|moradia|minha casa|urban|regiao metropolitana|estatuto da cidade|10\.257|imovel|imoveis|loteamento|defesa civil|desastre|enchente|resiliencia municipal",
 "Política, eleições e processo legislativo": r"eleit|eleic|partido|9\.504|4\.737|candidat|mandato|parlamentar|congresso nacional|camara dos deputados|senado federal|medida[s]? provisoria|plebiscito|referendo",
 "Defesa, Forças Armadas e militares": r"forcas armadas|militar|exercito|marinha|comando da aeronautica|forca aerea|defesa nacional|bombeiro|6\.880",
 "Relações exteriores e soberania": r"relacoes exteriores|tratado|acordo internacional|convencao internacional|soberania|fronteira|estrangeir|diplomat|mercosul|internacionaliza",
}

# Nome curto de cada categoria, só para a frase-resumo (minúsculo, lido no meio da frase).
CURTO = {
    "Saúde": "saúde", "Educação": "educação", "Trabalho, emprego e profissões": "trabalho e emprego",
    "Previdência e assistência social": "previdência e assistência social",
    "Pessoa idosa e pessoa com deficiência": "pessoas idosas e com deficiência",
    "Mulheres e violência de gênero": "mulheres", "Crianças e adolescentes": "crianças e adolescentes",
    "Direitos humanos e igualdade": "direitos humanos",
    "Direito penal e segurança pública": "segurança pública e direito penal",
    "Administração pública e servidores": "administração pública",
    "Direito civil, processo e justiça": "justiça e direito civil",
    "Política, eleições e processo legislativo": "eleições e processo legislativo",
    "Relações exteriores e soberania": "relações exteriores",
    "Defesa, Forças Armadas e militares": "defesa e militares",
    "Economia, indústria, comércio e empreendedorismo": "economia e empresas",
    "Sistema financeiro, crédito e pagamentos": "crédito e sistema financeiro",
    "Tributação e finanças públicas": "tributos e contas públicas", "Consumidor": "consumidor",
    "Propriedade intelectual": "propriedade intelectual",
    "Transporte, trânsito e mobilidade": "transporte e trânsito",
    "Meio ambiente e animais": "meio ambiente e animais",
    "Cidades, habitação e desenvolvimento urbano": "cidades e habitação",
    "Energia, mineração, água e saneamento": "energia, mineração e saneamento",
    "Agro e desenvolvimento rural": "agropecuária", "Tecnologia, telecom e dados": "tecnologia e dados",
    "Cultura, esporte e turismo": "cultura, esporte e turismo",
    "Homenagens, datas e títulos": "homenagens e datas comemorativas",
}

CATEGORIAS = [c for _, cs in BLOCOS for c in cs]
assert set(CURTO) == set(CATEGORIAS), "CURTO precisa cobrir todas as categorias"
HOMENAGEM = CATEGORIAS.index("Homenagens, datas e títulos")
assert sorted(CATEGORIAS) == sorted(REGRAS), "BLOCOS e REGRAS precisam ter as mesmas categorias"
PADROES = [re.compile(REGRAS[c]) for c in CATEGORIAS]
LEI_NUM = re.compile(r"^\d{1,2}\.\d{3}$")


def normalizar(texto):
    """Minúsculo e sem acento, com um mapa de volta à posição no texto original,
    para que o termo exibido na página saia com a grafia oficial."""
    saida, mapa = [], []
    for i, ch in enumerate(texto):
        for b in unicodedata.normalize("NFKD", ch):
            if not unicodedata.combining(b) and b.isascii():
                saida.append(b.lower())
                mapa.append(i)
    return "".join(saida), mapa


def trecho_ementa(original, mapa, m):
    """Termo que disparou a regra, estendido até a palavra inteira."""
    ini, fim = mapa[m.start()], mapa[m.end() - 1] + 1
    while ini > 0 and original[ini - 1].isalnum():
        ini -= 1
    while fim < len(original) and (original[fim].isalnum()
                                   or (original[fim] == "." and fim + 1 < len(original)
                                       and original[fim + 1].isdigit())):
        fim += 1
    t = original[ini:fim].strip(" ,;:.()\"'")
    if LEI_NUM.match(t):
        return f"Lei {t}"
    palavras = t.split()
    if len(t) > 40 and len(palavras) > 3:   # regras com .{0,N}: mostra só as pontas
        t = f"{palavras[0]} … {' '.join(palavras[-2:])}"
    return t


def trecho_indexacao(original, mapa, m):
    """Na indexação, o termo é o item inteiro da lista (separada por vírgula ou ;)."""
    ini, fim = mapa[m.start()], mapa[m.end() - 1] + 1
    while ini > 0 and original[ini - 1] not in ",;":
        ini -= 1
    while fim < len(original) and original[fim] not in ",;":
        fim += 1
    return original[ini:fim].strip(" .")


def classificar(ementa, keywords):
    """[[índice da categoria, termo, fonte]] — fonte 'e' = ementa, 'k' = indexação.
    A indexação só é consultada quando a ementa não casa com nada: ementa curta do
    tipo "Altera a Lei nº X" não diz o assunto, e a indexação diz. Usar as duas
    sempre multiplicaria rótulos fracos."""
    for texto, fonte, trecho in ((ementa, "e", trecho_ementa),
                                 (keywords, "k", trecho_indexacao)):
        if not texto:
            continue
        norm, mapa = normalizar(texto)
        achados = []
        for i, padrao in enumerate(PADROES):
            m = padrao.search(norm)
            if m:
                achados.append([i, trecho(texto, mapa, m), fonte])
        if achados:
            return achados
    return []


def num(n):
    return f"{n:,}".replace(",", ".")


def frase(props):
    """Frase-resumo da legislatura, por modelo fixo: só números e nomes de categoria,
    nenhum adjetivo, nenhum LLM. As mesmas entradas dão sempre a mesma frase.

    Regras (ver CLAUDE.md, "Frase-resumo"):
    - conta só PEC e PL de autoria apresentadas desde INICIO_LEGISLATURA;
    - "sobretudo em" só com 5+ proposições e para até 2 categorias que tenham, cada uma,
      ao menos 20% delas (mínimo 2). Sem concentração, a frase não cita tema;
    - "virou lei" é a situação oficial; homenagens entre as leis são ditas à parte."""
    leg = [p for p in props if p["d"] >= INICIO_LEGISLATURA]
    n = len(leg)
    if not n:
        return "Nesta legislatura, não apresentou PEC nem projeto de lei como autor."
    s = f"Nesta legislatura, apresentou {num(n)} {'proposição' if n == 1 else 'proposições'}"
    cont = Counter(i for p in leg for i, _, _ in p["c"])
    corte = max(2, -(-n // 5))
    fortes = [(i, k) for i, k in cont.most_common() if k >= corte][:2]
    if n >= 5 and fortes:
        s += ", sobretudo em " + " e ".join(f"{CURTO[CATEGORIAS[i]]} ({num(k)})" for i, k in fortes)
    leis = [p for p in leg if p["l"]]
    if not leis:
        return s + ("; nenhuma virou lei." if n > 1 else "; ela não virou lei.")
    h = sum(1 for p in leis if any(i == HOMENAGEM for i, _, _ in p["c"]))
    s += f"; {num(len(leis))} {'virou lei' if len(leis) == 1 else 'viraram lei'}"
    if h and h == len(leis):
        s += " (homenagem ou data comemorativa)" if h == 1 else " (todas homenagens ou datas comemorativas)"
    elif h:
        s += f" ({num(h)} delas é homenagem ou data comemorativa)" if h == 1 \
            else f" ({num(h)} delas são homenagens ou datas comemorativas)"
    return s + "."


def gravar_se_mudou(caminho, obj):
    """Só reescreve o arquivo se o conteúdo mudou. Com a rodada diária, reescrever os
    514 arquivos todo dia faria o git guardar ~19 MB novos por dia sem mudança real."""
    novo = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    try:
        if caminho.read_text(encoding="utf-8") == novo:
            return False
    except FileNotFoundError:
        pass
    caminho.write_text(novo, encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--auditoria", action="store_true",
                    help="grava cache/temas_auditoria.csv para conferência humana")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.monotonic()

    lista = ler_cache(CACHE / "deputados_lista.json")
    if not lista:
        sys.exit("Sem cache/deputados_lista.json. Rode coleta.py antes.")
    publicado = {d["id"]: d for d in (ler_cache(DEPUTADOS) or [])}
    resumo_de = resumos.carregar()
    if not publicado:
        sys.exit(f"Sem {DEPUTADOS.name}. Rode coleta.py antes.")

    saidas, divergencias, auditoria = {}, [], []
    distintas, sem_tema, por_indexacao = {}, set(), set()
    for dep in lista:
        id_dep = dep["id"]
        brutas = ler_cache(CACHE / "deputados" / f"{id_dep}.json")
        if brutas is None:
            divergencias.append(f"{dep['nome']}: sem cache de proposições")
            continue
        props, n_tipo = [], Counter()
        for p in brutas:
            d = ler_cache(CACHE / "proposicoes" / f"{p['id']}.json") or {}
            if p.get("siglaTipo") not in TIPOS or not e_autor(d.get("autores", []), id_dep):
                continue
            det = d.get("detalhe") or {}
            ementa = (det.get("ementa") or p.get("ementa") or "").strip()
            keywords = (det.get("keywords") or "").strip()
            cats = classificar(ementa, keywords)
            n_tipo[p["siglaTipo"]] += 1
            distintas[p["id"]] = cats
            if not cats:
                sem_tema.add(p["id"])
            elif cats[0][2] == "k":
                por_indexacao.add(p["id"])
            props.append({
                "id": p["id"], "t": p["siglaTipo"], "n": p["numero"], "a": p["ano"],
                "d": (det.get("dataApresentacao") or p.get("dataApresentacao") or "")[:10],
                "e": ementa,
                "s": ((det.get("statusProposicao") or {}).get("descricaoSituacao") or ""),
                "l": 1 if virou_lei(det) else 0,
                "c": cats,
                # passo 8: resumo do inteiro teor (resumos.py); só quando existe
                **({"r": resumo_de[p["id"]]} if p["id"] in resumo_de else {}),
            })
            if args.auditoria:
                auditoria.append([id_dep, dep["nome"], f"{p['siglaTipo']} {p['numero']}/{p['ano']}",
                                  ementa, keywords,
                                  " | ".join(f"{CATEGORIAS[i]} [{t}] ({f})" for i, t, f in cats)])

        # Sanidade: a página tem de somar o mesmo que o card do gráfico.
        ref = publicado.get(id_dep)
        if ref is None:
            divergencias.append(f"{dep['nome']}: ausente de {DEPUTADOS.name}")
        elif (n_tipo["PEC"], n_tipo["PL"]) != (ref.get("pecs"), ref.get("leis")):
            divergencias.append(f"{dep['nome']}: PEC {n_tipo['PEC']} x {ref.get('pecs')}, "
                                f"PL {n_tipo['PL']} x {ref.get('leis')}")
        props.sort(key=lambda x: (x["d"], x["id"]), reverse=True)
        saidas[id_dep] = {
            "id": id_dep, "nome": dep["nome"], "partido": ref.get("partido") if ref else None,
            "federacao": ref.get("federacao") if ref else None, "uf": dep.get("siglaUf"),
            "urlFoto": dep.get("urlFoto"), "pecs": n_tipo["PEC"], "leis": n_tipo["PL"],
            "viraramLei": sum(x["l"] for x in props), "props": props,
        }

    if divergencias:
        print(f"CHECAGEM DE SANIDADE FALHOU — {len(divergencias)} deputado(s) com contagem "
              f"diferente de {DEPUTADOS.name}. Nada foi gravado.\n"
              "Provável causa: deputados.json de outra rodada. Rode coleta.py e depois este script.\n")
        for d in divergencias[:40]:
            print("-", d)
        sys.exit(2)

    SAIDA.mkdir(exist_ok=True)
    validos = {f"{i}.json" for i in saidas} | {"taxonomia.json", "resumo.json"}
    for velho in SAIDA.glob("*.json"):
        if velho.name not in validos:   # deputado que saiu do exercício
            velho.unlink()
    gerado = date.today().isoformat()
    taxonomia = {
        "versao": VERSAO, "geradoEm": gerado, "inicioLegislatura": INICIO_LEGISLATURA,
        "metodo": "Categorias atribuídas por este site a partir da ementa oficial e, quando "
                  "ela não basta, da indexação oficial da Câmara. É interpretação do site, "
                  "não classificação da Câmara, e pode conter erros.",
        "blocos": [{"nome": b, "categorias": [CATEGORIAS.index(c) for c in cs]}
                   for b, cs in BLOCOS],
        "categorias": CATEGORIAS,
    }
    (SAIDA / "taxonomia.json").write_text(json.dumps(taxonomia, ensure_ascii=False),
                                          encoding="utf-8")
    mudaram = 0
    resumo = {}
    for id_dep, obj in saidas.items():
        obj["frase"] = frase(obj["props"])
        resumo[id_dep] = obj["frase"]
        mudaram += gravar_se_mudou(SAIDA / f"{id_dep}.json", obj)
    # Arquivo leve (~90 kB) para a busca e a ficha do gráfico, que não baixam o deputado inteiro.
    gravar_se_mudou(SAIDA / "resumo.json", {"inicioLegislatura": INICIO_LEGISLATURA,
                                             "frases": resumo})

    if args.auditoria:
        with open(CACHE / "temas_auditoria.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["idDeputado", "deputado", "proposicao", "ementa", "indexacao", "categorias"])
            w.writerows(auditoria)

    n = len(distintas)
    rot = Counter(i for cats in distintas.values() for i, _, _ in cats)
    tam = sum(p.stat().st_size for p in SAIDA.glob("*.json"))
    maior = max(SAIDA.glob("*.json"), key=lambda p: p.stat().st_size)
    print(f"Checagem de sanidade OK: PEC e PL batem com {DEPUTADOS.name} para os {len(saidas)}.")
    print(f"{n} proposições distintas de autoria · {len(sem_tema)} sem categoria "
          f"({len(sem_tema) / n:.1%}) · {len(por_indexacao)} classificadas pela indexação "
          f"({len(por_indexacao) / n:.1%}) · {sum(len(c) for c in distintas.values()) / n:.2f} "
          "categorias por proposição")
    print(f"temas/: {len(saidas) + 2} arquivos, {tam / 1e6:.1f} MB, maior {maior.name} "
          f"({maior.stat().st_size / 1e3:.0f} kB) · {mudaram} de deputado reescritos · "
          f"{time.monotonic() - t0:.0f}s\n")
    for b, cs in BLOCOS:
        print(b)
        for c in cs:
            print(f"   {c:<52}{rot[CATEGORIAS.index(c)]:>6}")
    if args.auditoria:
        print(f"\nAuditoria: {CACHE / 'temas_auditoria.csv'}")


if __name__ == "__main__":
    main()
