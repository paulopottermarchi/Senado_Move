"""
Câmara Aberta — testes de regressão da camada "Pessoas documentadas".

Uso (depois de rodar `python scripts/pessoas.py --dry-run`):
    python scripts/pessoas_regressao.py
    python scripts/pessoas_regressao.py --so-sinteticos    # só a parte A (sem cache nem rede): é o que o CI dos PRs roda

Três partes:
  A) casos sintéticos (nomes inventados, sem PDF): testemunha, empresa, nome ilegível, reconciliação, cargo com vírgula, o filtro
     conservador do E2 (nome igual ao literal, segunda leitura, cargo), o cargo "Substituto(a)", as supressões e as vias;
  B) os casos que você conferiu à mão em cache/amostra_validacao.csv, contra cache/pessoas_arestas.jsonl:
     - todo "não" (testemunha ou não representante) NUNCA pode sair como representante PUBLICADO naquele documento;
     - todo "sim" sai publicado OU retido por um motivo do filtro conservador (nunca publicado errado);
     - todo "parcial" (texto corrompido) só pode sair publicado sem "�" ou ficar retido;
     - nenhuma aresta publicável tem "�" no nome nem no cargo;
  C) a primeira passada de conferência do E2 (cache/amostra_fase1_preenchida.csv, 60 linhas: 57 sim e 3 parcial): nenhuma sai
     publicada com nome diferente do conferido, e as 3 com cargo "Substituto(a)" (linhas 6, 9 e 33) mantêm o cargo.
Sai com código 1 se algo falhar.
"""

import csv
import json
import sys

import coleta
import pessoas as PE
import pessoas_pdf as P

FFFD = P.FFFD
RETENCOES_DO_FILTRO = {"nome_reconstruido", "leitores_divergem", "segunda_leitura_indisponivel", "sem_cargo", "nome_ilegivel"}
falhas, ok = [], 0


def confere(cond, msg):
    global ok
    if cond:
        ok += 1
    else:
        falhas.append(msg)
        print("  ✗", msg)


# ------------------------------------------------------------------------------------------------ A) sintéticos

def pag(*linhas):
    return {i + 1: " ".join(t.split()) for i, t in enumerate(linhas)}


def parte_a():
    print("A) casos sintéticos")
    pre = ("TERMO DE CONTRATO Nº 12345/2024 O INSTITUTO TESTE, neste ato representado pelo Diretor de Logística e Aquisições, "
           "Sr. Carlos Alberto Teste Silva, nomeado pela Portaria nº 1, de 2 de janeiro de 2024, doravante denominado "
           "CONTRATANTE, e a empresa XYZ LTDA, inscrita no CNPJ nº 00.000.000/0001-00, neste ato representada por João da Empresa.")
    sup = ("REPRESENTANTE LEGAL DA CONTRATANTE: Nome: Carlos Alberto Teste Silva Cargo: Diretor de Logística e Aquisições "
           "TESTEMUNHAS: 1 Nome: Maria Souza Teste Matrícula: 123 2 Nome: Joao Pereira Teste Matrícula: 456 (Assinatura eletrônica) "
           "Código Verificador: Documento assinado eletronicamente por: Maria Souza Teste, ASSISTENTE EM ADMINISTRACAO, em 01/02/2024 10:00:00. "
           "Carlos Alberto Teste Silva, DIRETOR - CD3 - DLA-PRA, em 01/02/2024 10:05:00. Joao Pereira Teste, TECNICO, em 01/02/2024 10:06:00. "
           "Ana Outra Setor, COORDENADOR(A) - FG1 - CCO-PRA, em 01/02/2024 10:07:00. Este documento foi emitido pelo SUAP em 01/02/2024.")
    r = P.analisar_paginas(pag(pre, sup), 2, "12345/2024", "23000.000001/2024-00")
    nomes = [x["nome"] for x in r["representantes"]]
    confere(nomes == ["Carlos Alberto Teste Silva"], f"só o representante sai (saiu {nomes})")
    confere(r["contagens"]["testemunha"] == 2, f"as 2 testemunhas são só contadas ({r['contagens']})")
    confere(r["contagens"]["outro_orgao"] == 1, f"o outro signatário do órgão é só contado ({r['contagens']})")
    confere(r["numero_no_texto"] is True, "o número do contrato é encontrado com 'Nº 12345/2024'")
    confere("Maria Souza Teste" not in json.dumps(r, ensure_ascii=False) and "Joao Pereira Teste" not in json.dumps(r, ensure_ascii=False),
            "nome de testemunha não vai para o registro do cache")
    confere(r["representantes"][0]["funcaoPreambulo"] == "Diretor de Logística e Aquisições", f"função no preâmbulo ({r['representantes'][0]['funcaoPreambulo']})")

    # SEI: testemunha e usuário externo (empresa) pelo cargo
    pre2 = ("CONTRATO Nº 777/2024 A UNIVERSIDADE TESTE, neste ato representada pela Pró-Reitora de Administração, Beatriz Exemplo Lima, "
            "doravante denominada CONTRATANTE, e a empresa ABC LTDA, representada por seu sócio.")
    sei = ("Documento assinado eletronicamente por JOSE DA EMPRESA, Usuário Externo, em 10/03/2024, às 10:00, conforme horário oficial. "
           "Documento assinado eletronicamente por Pedro Testemunha Exemplo, Testemunha, em 10/03/2024, às 11:00, conforme horário oficial. "
           "Documento assinado eletronicamente por Beatriz Exemplo Lima, Pró-Reitor(a), em 10/03/2024, às 12:00, conforme horário oficial.")
    r = P.analisar_paginas(pag(pre2, sei), 2, "00777/2024", None)
    confere([x["nome"] for x in r["representantes"]] == ["Beatriz Exemplo Lima"], f"SEI: só a representante sai ({[x['nome'] for x in r['representantes']]})")
    confere(r["contagens"]["empresa"] == 1 and r["contagens"]["testemunha"] == 1, f"SEI: empresa e testemunha só contadas ({r['contagens']})")
    confere(r["numero_no_texto"] is True, "número 00777/2024 achado como 'Nº 777/2024'")

    # selo digital (gov.br / ICP) de quem representa a EMPRESA nunca sai como representante do órgão
    pre3 = ("CONTRATO Nº 555/2024 O INSTITUTO EXEMPLO, neste ato representado por seu Reitor, Helena Modelo Prado, doravante "
            "denominado CONTRATANTE, e a empresa MARIA ALVES COMERCIO LTDA, neste ato representada por seu sócio Mauricio Garcia do Prado, "
            "doravante denominada CONTRATADA.")
    selos = ("Documento assinado digitalmente gov.br MAURICIO GARCIA DO PRADO Data: 05/05/2024 10:00:00 "
             "Documento assinado digitalmente gov.br HELENA MODELO PRADO Data: 05/05/2024 11:00:00")
    r = P.analisar_paginas(pag(pre3, selos), 2, "555/2024", None)
    nomes3 = [x["nome"].upper() for x in r["representantes"]]
    confere("MAURICIO GARCIA DO PRADO" not in nomes3, f"selo do sócio da empresa não sai como representante do órgão ({nomes3})")

    # SEI com cargo que contém vírgula
    s = "Documento assinado eletronicamente por Isolda Exemplo, Diretor(a) de Unidade, Substituto(a) Legal, em 07/07/2023, às 10:15, conforme"
    m = P._SEI.search(s)
    confere(m and m[1] == "Isolda Exemplo" and m[2] == "Diretor(a) de Unidade, Substituto(a) Legal", "SEI: o cargo com vírgula não contamina o nome")

    # documento sem o número do contrato: o representante sai, mas fica retido (testado em pessoas.py)
    r = P.analisar_paginas(pag(pre.replace("12345/2024", "99999/2024"), sup), 2, "12345/2024", None)
    confere(r["numero_no_texto"] is False and r["processo_no_texto"] is False, "número ausente do texto é detectado")

    # nome corrompido: preâmbulo do mesmo documento, depois outro documento do mesmo órgão, depois "ilegível"
    rep = {"nome": f"Silmario Ba{FFFD}sta dos Santos", "grafiaPreambulo": None}
    nomes_c = {"158154": {"Silmario Batista dos Santos": 3}}
    confere(PE.reconciliar_nome(rep, "158154", nomes_c) == ("Silmario Batista dos Santos", "outro_documento_do_mesmo_orgao"),
            "reconcilia com outro documento do mesmo órgão (e marca)")
    confere(PE.reconciliar_nome(rep, "999999", nomes_c) == (None, None), "outro órgão não serve: fica ilegível")
    confere(PE.reconciliar_nome(rep, "158154", {}) == (None, None), "sem grafia legível: fica ilegível")
    rep2 = {"nome": f"Fabio Zuccolo{FFFD}o Ferreira", "grafiaPreambulo": "Fábio Zuccolotto Ferreira"}
    confere(PE.reconciliar_nome(rep2, "154049", {}) == ("Fábio Zuccolotto Ferreira", "preambulo_do_mesmo_documento"),
            "reconcilia primeiro com a grafia do preâmbulo do mesmo documento")
    rep3 = {"nome": f"Silmario Ba{FFFD}sta dos Santos", "grafiaPreambulo": "Silmario Basta dos Santos"}      # ligadura perdida SEM rastro no preâmbulo
    confere(PE.reconciliar_nome(rep3, "158154", {}) == (None, None), "preâmbulo com a ligadura perdida em silêncio ('Basta') não vira o nome")
    rep4 = {"nome": f"Ana Ba{FFFD}sta", "grafiaPreambulo": None}
    confere(PE.reconciliar_nome(rep4, "1", {"1": {"Ana Batista": 1, "Ana Bastos": 5}}) == ("Ana Batista", "outro_documento_do_mesmo_orgao"),
            "só candidatos que casam com o texto corrompido contam")
    confere(PE.reconciliar_nome(rep4, "1", {"1": {"Ana Batista": 1, "Ana Baxista": 1}}) == (None, None), "duas grafias possíveis: não escolhe")
    confere(PE.reconciliar_cargo({"cargo": f"Subsecretário(a) de Assuntos Administra{FFFD}vos"}, "1", {"1": {"Subsecretário(a) de Assuntos Administrativos": 2}})
            == ("Subsecretário(a) de Assuntos Administrativos", "outro_documento_do_mesmo_orgao"), "reconcilia o cargo corrompido")
    confere(PE.reconciliar_cargo({"cargo": f"Subsecretário(a) de Assuntos Administra{FFFD}vos"}, "1", {}) == (None, None), "cargo corrompido sem outra grafia: fica sem cargo")

    # ligadura perdida SEM rastro ("Basta"): o nome parece legível, mas só vale a grafia completa do mesmo órgão
    rep5 = {"nome": "Silmario Basta dos Santos", "grafiaPreambulo": None}
    confere(PE.reconciliar_nome(rep5, "158154", {"158154": {"Silmario Batista dos Santos": 3}})[0] == "Silmario Batista dos Santos",
            "nome com a ligadura engolida em silêncio é trocado pela grafia completa do mesmo órgão")
    confere(PE.reconciliar_nome(rep5, "158154", {"158154": {"Silmario Batista dos Santos": 1, "Silmario Bastiffa dos Santos": 1}})[0] is None,
            "duas grafias completas possíveis: não publica")
    confere(PE.reconciliar_nome({"nome": "Maria Basta", "grafiaPreambulo": None}, "158154", {"158154": {"Silmario Batista dos Santos": 3}})[0] == "Maria Basta",
            "nome verdadeiro que lembra outro (palavras diferentes) não é alterado")
    nomes_c2, _ = PE.corpus_de([("1", {"representantes": [{"nome": "Silmario Basta dos Santos"}, {"nome": "Silmario Batista dos Santos"},
                                                           {"nome": f"Silmario Ba{FFFD}sta dos Santos"}]})])
    confere(set(nomes_c2["1"]) == {"Silmario Batista dos Santos"}, f"o corpus de grafias descarta a versão estragada ({set(nomes_c2['1'])})")
    confere(P.eh_empresa("Rita de Cassia Ribeiro LTDA", set()) and not P.eh_empresa("Rita de Cassia Ribeiro", set()),
            "nome com LTDA é empresa; sem a marca, só se o preâmbulo da contratada o disser")
    confere(P.eh_empresa("Mauricio Garcia do Prado", {P.chave("Mauricio Garcia do Prado")}), "nome que o preâmbulo dá à contratada nunca é representante do órgão")

    # função do preâmbulo: ligadura estragada, espaço partido e tratamento solto não saem
    reg = [("1", {"representantes": [{"funcaoPreambulo": "Diretora de Logística e Aquisições"}, {"funcaoPreambulo": "Diretora de LogísAca e Aquisições"},
                                      {"funcaoPreambulo": "Pró-Reitor de Administração"}]})]
    fun = PE.corpus_funcoes(reg)
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Diretora de LogísAca e Aquisições"}, "1", fun) == "Diretora de Logística e Aquisições",
            "função com ligadura estragada vira a grafia completa do mesmo órgão")
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Diretora de LogísBca e Aquisições"}, "9", {}) is None,
            "função com letra maiúscula no meio da palavra e sem grafia completa não sai")
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Pró Reitor de Administr ação"}, "1", fun) == "Pró-Reitor de Administração", "espaço partido é refeito pela grafia inteira")
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Pró Reitor de Administr ação"}, "9", {}) == "Pró Reitor de Administração",
            "palavra partida antes de 'ação' é refeita mesmo sem outra grafia")
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Diretor de Ação Social"}, "9", {}) == "Diretor de Ação Social", "'Ação' legítima não é colada ao 'de'")
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Senhor"}, "1", fun) is None, "'Senhor' sozinho não é função")
    confere(PE.reconciliar_funcao({"funcaoPreambulo": "Subsecretário de Assuntos Administrativos, o Senhor"}, "1", fun) == "Subsecretário de Assuntos Administrativos",
            "o tratamento no fim da frase é cortado")

    # a checagem derruba a rodada se sair nome com "�"
    saida = {"contratos": {"1": {"assinou": {"pessoas": [{"nome": f"Fulano Ba{FFFD}sta", "cargos": []}]}, "aviso": {"pessoas": []}, "registro": {"gestores": []}}}}
    confere(any("ilegível" in e for e in PE.checagens(saida, [])), "checagem: nome com � no que seria publicado derruba a rodada")
    saida["contratos"]["1"]["assinou"]["pessoas"] = [{"nome": "Fulano de Tal", "cargos": [], "obs": "CPF 123.456.789-09"}]
    confere(any("CPF" in e for e in PE.checagens(saida, [])), "checagem: padrão de CPF em qualquer saída derruba a rodada")
    confere(any("url_documento" in e for e in PE.checagens({"contratos": {}}, [{"nome": "Fulano", "aresta_id": "x", "via": "E2", "contrato_id": "1"}])),
            "checagem: aresta sem url_documento derruba a rodada")
    confere(PE.portaria("PORTARIA Nº 12/2023 - DRG/ITP/IFSP DE 17 DE FEVEREIRO DE 2023")["data"] == "2023-02-17", "portaria completa: número e data")
    confere(PE.portaria("1163/2025") is None, "portaria só com número e ano não é publicada como completa")
    novos_a()
    listas_vazias_a()


def contrato_sintetico(reps, cargo_corpus=None):
    """Entradas mínimas de montar_arestas para um contrato com um PDF e os representantes `reps` (já no formato do cache)."""
    c = {"id": "1", "uasg": "158154", "numero": "12345/2024", "processo": None}
    arq = {"id": "a1", "path_arquivo": "https://exemplo.test/a1.pdf", "tipo": "Contrato"}
    rec = {"paginas": 2, "camada_texto": "sim", "sha256": "ab" * 32, "numero_no_texto": True, "processo_no_texto": False,
           "contagens": {"empresa": 0, "testemunha": 0, "outro_orgao": 0, "indeterminado": 0}, "representantes": reps, "leitor2": "teste 1"}
    return ({"1": c}, {"1": ["O1"]}, ["1"], {"1": ([arq], [(arq, rec)])},
            {"1": {"registro": {"licitacao_numero": "1/2024", "unidade_compra": "158154", "codigo_modalidade": 5}, "responsaveis": []}},
            {"1": {"estado": "via_desligada", "ano": 2024, "id_compra": None}})


def rep(nome="Carlos Alberto Teste Silva", cargo="DIRETOR - CD3 - DLA-PRA", leitor2=True, **extra):
    r = {"nome": nome, "cargo": cargo, "funcaoPreambulo": "Diretor de Logística e Aquisições", "grafiaPreambulo": None, "data": "2024-02-01",
         "hora": "10:05:00", "pagina": 2, "formato": "SUAP", "trecho": f"Documento assinado eletronicamente por: {nome}, {cargo}, em 01/02/2024 10:05:00",
         "leitor2_nome": leitor2}
    r.update(extra)
    return r


def monta(reps, supressoes=None):
    arestas, blocos = PE.montar_arestas(*contrato_sintetico(reps), supressoes=supressoes or {})
    return [a for a in arestas if a["via"] == "E2" and a.get("nome")], blocos["1"]


def suap2(*assinaturas, antes="", depois=""):
    """Texto no formato que o pdfminer entrega para o SUAP do IFSP: palavras coladas e nome/cargo repetidos ("NomeNome,CARGOCARGO,em ...")."""
    itens = "".join(f"{n}{n},{c}{c},em25/11/202219:5{i}:01." for i, (n, c) in enumerate(assinaturas))
    return f"{antes}Documentoassinadoeletronicamentepor:{itens}{depois}"


def colado(nome):
    return "".join(nome.split())


def sem_espacos_a():
    """Segundo leitor que junta as palavras: aceito só com as âncoras (igualdade das letras, 3+ palavras e 12+ letras, perto da assinatura)."""
    print("A3) segundo leitor sem espaços: igualdade ancorada")
    reais = [("131609", "Davis Wilian Graciano de Toledo", "DIRETORADJUNTO-CD4-DALC-PRA"),
             ("163988", "Silmario Batista dos Santos", "DIRETOR(A)-CD3-DLA-PRA"),
             ("170450", "Fernanda Amorim Rocha", "DIRETOR-CD3-DLA-PRA"),
             ("176938", "Fernanda Amorim Rocha", "DIRETOR-CD3-DLA-PRA"),
             ("176939", "Fernanda Amorim Rocha", "DIRETOR-CD3-DLA-PRA")]
    for cid, nome, cargo in reais:
        txt = suap2(("DeirOliveiradeOliveira", "COORDENADOR-FG1-COC-PRA"), (colado(nome), cargo))
        confere(not P.concorda_nome(nome, txt), f"{cid}: o leitor por palavras NÃO concorda com o texto colado (é o caso que estava retido)")
        confere(P.concorda_sem_espacos(nome, txt), f"{cid}: {nome} confere pela igualdade sem espaços, ancorada na assinatura")
        # o nome em qualquer posição da lista de assinaturas, e com o corpo do documento antes (maiúsculas, sem espaço)
        corpo = f"{colado(nome).upper()}Nome:ELAINECRISTINADASILVA CPF:***.123.456-** "
        confere(P.concorda_sem_espacos(nome, suap2((colado(nome), cargo), antes=corpo)), f"{cid}: com o corpo do documento antes da lista")
        confere(P.concorda_sem_espacos(nome, suap2(("OutraPessoaDaSilva", "X-CD1"), ("MariaEduardaLima", "Y-CD2"), (colado(nome), cargo))),
                f"{cid}: depois de outras assinaturas")
    # --- falsos positivos: têm de continuar retidos
    f = "Fernanda Amorim Rocha"
    confere(not P.concorda_sem_espacos(f, suap2(("FernandaAmorimRochaSilva", "DIRETOR-CD3"))), "substring: o texto tem MAIS letras que o nome (Rocha → RochaSilva)")
    confere(not P.concorda_sem_espacos(f + " Lima", suap2(("FernandaAmorimRocha", "DIRETOR-CD3"))), "substring: o texto tem MENOS letras que o nome")
    confere(not P.concorda_sem_espacos(f, "Documentoassinadoeletronicamentepor:XFernandaAmorimRochaX,DIRETOR"), "substring: nome no meio de uma sequência maior")
    confere(not P.concorda_sem_espacos(f, "Documentoassinadoeletronicamentepor:" + colado(f) * 3 + ",DIRETOR"), "três cópias não valem (só uma ou duas, como o SUAP repete)")
    confere(not P.concorda_sem_espacos("Ana Maria Costa", suap2(("MariaAnaCosta", "DIRETOR-CD3"))), "nomes diferentes com as mesmas letras (outra ordem): retido")
    confere(not P.concorda_sem_espacos("Mari Ana Costa Silva", suap2(("MarianaCostaSilva", "DIRETOR-CD3"))),
            "mesmas letras e fronteira de palavra diferente (Mari Ana × Mariana), com maiúsculas no texto: retido")
    confere(not P.concorda_sem_espacos("Ana Cruz Lima", suap2(("AnaCruzLima", "DIRETOR-CD3"))), "nome curto (3 palavras, mas 11 letras): retido")
    confere(P.concorda_sem_espacos("Ana Costa Lima", suap2(("AnaCostaLima", "DIRETOR-CD3"))), "12 letras e 3 palavras: no limite, aceito")
    confere(not P.concorda_sem_espacos("Ana Lima", suap2(("AnaLima", "DIRETOR-CD3"))), "nome de 2 palavras: retido")
    confere(not P.concorda_sem_espacos("Mariana Albuquerque", suap2(("MarianaAlbuquerque", "DIRETOR-CD3"))), "2 palavras, mesmo com 18 letras: retido")
    confere(P.concorda_sem_espacos("Jose Carlos Silva", suap2(("JoseCarlosSilva", "DIRETOR-CD3"))), "3 palavras e 15 letras: no limite aceito")
    confere(not P.concorda_sem_espacos(f, f"{colado(f)}{colado(f)},DIRETOR-CD3-DLA-PRA,em25/11/2022 Documento nada a ver"), "sem a âncora 'Documento assinado eletronicamente por': retido")
    confere(not P.concorda_sem_espacos(f, f"{colado(f)}{colado(f)},DIRETOR-CD3 Documentoassinadoeletronicamentepor:OutraPessoaDaSilva,X-CD1"), "nome ANTES da âncora e não depois: retido")
    longe = suap2(("OutraPessoaDaSilva", "X-CD1"), depois="," + "assinatura dos documentos digitais; " * 90 + colado(f) + colado(f))
    confere(len(longe) > 2600 + 100 and not P.concorda_sem_espacos(f, longe), "nome longe demais da âncora (fora da janela): retido")
    confere(not P.concorda_sem_espacos(f, suap2((f"Fernanda{FFFD}AmorimRocha", "DIRETOR-CD3"))), "texto com U+FFFD: retido")
    confere(not P.concorda_sem_espacos(f, suap2(("Fernanda Amorim Rocha", "DIRETOR-CD3"))), "texto com espaços: não é o caso do texto colado (vai pelo leitor por palavras)")
    confere(not P.concorda_sem_espacos(f, ""), "texto vazio: retido")
    confere(not P.concorda_sem_espacos("", suap2(("FernandaAmorimRocha", "C-CD3"))), "nome vazio: retido")
    # --- pela segunda leitura: o que fica registrado
    if P.leitor2_id():
        reg = {"representantes": [{"nome": f, "pagina": 2}, {"nome": f, "pagina": 3}, {"nome": "Ana Maria Costa", "pagina": 4}, {"nome": "Beto Souza Dias", "pagina": 5}]}
        P.segunda_leitura(b"", reg, ler=lambda pdf, pags: {2: suap2((colado(f), "DIRETOR-CD3")), 3: suap2((colado(f) + "Silva", "DIRETOR-CD3")),
                                                           4: suap2(("MariaAnaCosta", "DIRETOR-CD3")), 5: "Beto Souza Dias assina"})
        r = reg["representantes"]
        confere([x["leitor2_nome"] for x in r] == [True, False, False, True], f"leitor2_nome: igualdade ancorada True; substring False; outra ordem False; por palavras True ({[x['leitor2_nome'] for x in r]})")
        confere(r[0].get("leitor2_obs") == "igualdade_sem_espacos", "aceito pela igualdade sem espaços: registrado em leitor2_obs")
        confere(r[1].get("leitor2_obs") == "so_substring_sem_espacos", "as letras aparecem mas as âncoras não fecham: registrado como so_substring_sem_espacos (retido)")
        confere(r[2].get("leitor2_obs") is None and r[3].get("leitor2_obs") is None, "sem texto colado, nada a registrar")
    # --- até a aresta: o aceito publica (com leitor2_obs gravado) e o retido segue retido
    ar, bl = monta([rep("Fernanda Amorim Rocha", leitor2=True, leitor2_obs="igualdade_sem_espacos")])
    confere(ar[0]["publicavel"] and ar[0]["motivo_retencao"] is None and ar[0]["leitor2_obs"] == "igualdade_sem_espacos"
            and [p["nome"] for p in bl["assinou"]["pessoas"]] == ["Fernanda Amorim Rocha"], "aresta aceita pela igualdade sem espaços: publica e leva leitor2_obs")
    ar, bl = monta([rep("Fernanda Amorim Rocha Lima", leitor2=False, leitor2_obs="so_substring_sem_espacos")])
    confere(not ar[0]["publicavel"] and ar[0]["motivo_retencao"] == "leitores_divergem" and ar[0]["leitor2_obs"] == "so_substring_sem_espacos"
            and not bl["assinou"]["pessoas"], "aresta só com substring: retida (leitores_divergem), fora do bloco, com a observação")
    # a rede de segurança: nome reconstruído NÃO é liberado pela igualdade sem espaços (a regra (a) do filtro continua valendo)
    ar, bl = monta([rep(nome=f"Silmario Ba{FFFD}sta dos Santos", grafiaPreambulo="Silmário Batista dos Santos", leitor2=True, leitor2_obs="igualdade_sem_espacos")])
    confere(ar[0]["motivo_retencao"] == "nome_reconstruido" and not bl["assinou"]["pessoas"],
            "nome reconstruído continua retido (nome_reconstruido), mesmo com o 2º leitor concordando sem espaços")


def novos_a():
    print("A2) filtro conservador do E2, cargo, supressões e vias")
    # --- (a) nome igual ao literal, (b) segundo leitor, (c) cargo
    ar, bl = monta([rep()])
    confere(len(ar) == 1 and ar[0]["publicavel"] and ar[0]["motivo_retencao"] is None, "nome igual ao literal, 2º leitor confere e cargo preenchido: publica")
    confere([p["nome"] for p in bl["assinou"]["pessoas"]] == ["Carlos Alberto Teste Silva"], "…e a pessoa entra no bloco")
    ar, bl = monta([rep(leitor2=False)])
    confere(not ar[0]["publicavel"] and ar[0]["motivo_retencao"] == "leitores_divergem" and not bl["assinou"]["pessoas"], "2º leitor diverge: retido (leitores_divergem), fora do bloco")
    confere(bl["assinou"]["lacuna"]["motivo"] == "leitores_divergem", "…e o motivo é o que a página mostra no lugar do nome")
    ar, bl = monta([rep(leitor2=None)])
    confere(ar[0]["motivo_retencao"] == "segunda_leitura_indisponivel" and not ar[0]["publicavel"], "sem segunda leitura: retido")
    ar, bl = monta([rep(cargo="")])
    confere(ar[0]["motivo_retencao"] == "sem_cargo" and not bl["assinou"]["pessoas"], "cargo vazio: retido (sem_cargo)")
    ar, bl = monta([rep(cargo=f"Subsecretário(a) de Assuntos Administra{FFFD}vos")])
    confere(ar[0]["motivo_retencao"] == "sem_cargo", "cargo estragado sem outra grafia e fora do vocabulário: fica sem cargo e retido")
    # nome reconstruído (preâmbulo do mesmo documento): retido, mesmo com o 2º leitor concordando
    ar, bl = monta([rep(nome=f"Fabio Zuccolo{FFFD}o Ferreira", grafiaPreambulo="Fábio Zuccolotto Ferreira")])
    confere(ar[0]["motivo_retencao"] == "nome_reconstruido" and not ar[0]["publicavel"] and ar[0]["nome"] == "Fábio Zuccolotto Ferreira",
            "nome reconstruído pelo preâmbulo: retido (nome_reconstruido), mas a aresta guarda a grafia reconstruída")
    ar, bl = monta([rep(nome=f"Silmario Ba{FFFD}sta dos Santos")])
    confere(ar[0]["motivo_retencao"] == "nome_ilegivel", "nome com � e sem grafia para reconciliar: nome_ilegivel")
    ar, bl = monta([rep(nome="Ana Beatriz Costa", leitor2=False, cargo="")])
    confere(ar[0]["retencoes"] == ["leitores_divergem", "sem_cargo"] and ar[0]["motivo_retencao"] == "leitores_divergem", "todas as falhas ficam anotadas; o motivo é a primeira")
    # um bom e um ruim no mesmo contrato: só o bom aparece
    ar, bl = monta([rep(), rep(nome="Outra Pessoa Teste", leitor2=False)])
    confere([p["nome"] for p in bl["assinou"]["pessoas"]] == ["Carlos Alberto Teste Silva"], "no mesmo contrato, só o representante que passa pelo filtro entra no bloco")

    # --- "Substituto(a)" (as linhas 6, 9 e 33 da amostra de 20/10/2026)
    casos = [("Documento assinado eletronicamente por Aline Ferreira Cajuhi, Superintendente Subs\ufffdtuto(a), em 31/12/2024, às 12:53, conforme",
              "Superintendente Substituto(a)"),
             ("Documento assinado eletronicamente por ANDRE DE OLIVEIRA MENDONCA, Coordenador Subs\ufffdtuto, em 12/12/2023, às 14:38, conforme",
              "Coordenador Substituto"),
             ("Documento assinado eletronicamente por Katia Cristina Iunes Minasian Santos, Diretor(a) de Unidade, Subs\ufffdtuto(a), em 25/10/2024, às 16:50, conforme",
              "Diretor(a) de Unidade, Substituto(a)")]
    for texto, esperado in casos:
        m = P._SEI.search(texto)
        cargo = P.dedup_nome(m[2])
        confere(P.tem_ilegivel(cargo), f"o cargo da amostra sai da leitura com '�' ({cargo!r})")
        got = PE.reconciliar_cargo({"cargo": cargo}, "158154", {})
        confere(got == (esperado, "vocabulario_de_cargos"), f"cargo com 'Substituto(a)' preservado: {got} (esperado {esperado!r})")
        ar, bl = monta([rep(nome=m[1], cargo=cargo)])
        confere(ar[0]["cargo"] == esperado and ar[0]["publicavel"] and ar[0]["cargo_reconciliado_por"] == "vocabulario_de_cargos"
                and bl["assinou"]["pessoas"][0]["cargos"] == [esperado], f"…e publicado com o cargo {esperado!r}")
    confere(PE.reconciliar_cargo({"cargo": f"subs{FFFD}tuto(a)"}, "1", {}) == ("substituto(a)", "vocabulario_de_cargos"), "caixa baixa é mantida")
    confere(PE.reconciliar_cargo({"cargo": f"Subs{FFFD}tuto Teste{FFFD}"}, "1", {}) == (None, None), "palavra estragada fora do vocabulário: o cargo todo fica de fora")
    confere(PE.reconciliar_cargo({"cargo": "Superintendente Substituto(a)"}, "1", {}) == ("Superintendente Substituto(a)", None), "cargo legível não é tocado")
    confere(PE.reconciliar_cargo({"cargo": f"Subsecretário(a) de Assuntos Administra{FFFD}vos"}, "1", {"1": {"Subsecretário(a) de Assuntos Administrativos": 1}})[1]
            == "outro_documento_do_mesmo_orgao", "a grafia completa do mesmo órgão continua valendo antes do vocabulário")

    # --- segunda leitura (concordância do nome) sem PDF
    confere(P.concorda_nome("Aline Ferreira Cajuhi", "Documento assinado eletronicamente por Aline  Ferreira\nCajuhi, Superintendente"), "mesmo nome, espaço e quebra de linha diferentes: concorda")
    confere(P.concorda_nome("Fabio Zuccolotto Ferreira", "assinado por FÁBIO ZUCCOLOTTO FERREIRA, Pró-Reitor"), "acento e caixa não contam")
    confere(not P.concorda_nome("Fabio Zuccolotto Ferreira", "assinado por Fabio Zuccolo(cid:132)o Ferreira, Pró-Reitor"), "letra perdida no outro leitor: não concorda")
    confere(not P.concorda_nome("Ana Costa", "Ana Maria Costa"), "palavra a mais no meio: não concorda")
    confere(not P.concorda_nome("Ana Costa", "Mariana Costa"), "o nome tem de casar palavra inteira ('Ana' não está em 'Mariana')")
    confere(not P.concorda_nome("Ana Costa", ""), "texto vazio não concorda")
    reg = {"representantes": [{"nome": "Ana Costa", "pagina": 2}, {"nome": "Beto Lima", "pagina": 3}, {"nome": "Cida Souza", "pagina": 4}]}
    P.segunda_leitura(b"", reg, ler=lambda pdf, pags: {2: "Ana Costa assina", 3: "Beto Limas ZZ"})
    if P.leitor2_id():
        confere([r["leitor2_nome"] for r in reg["representantes"]] == [True, False, None], f"True se o nome confere, False se não, None se a página não foi lida ({[r['leitor2_nome'] for r in reg['representantes']]})")
        confere(reg["leitor2"] == P.leitor2_id(), "o registro diz qual leitor fez a segunda leitura")
    sem_espacos_a()
    def quebra(pdf, pags):
        raise RuntimeError("pdf quebrado")
    reg2 = {"representantes": [{"nome": "Ana Costa", "pagina": 2}]}
    P.segunda_leitura(b"", reg2, ler=quebra)
    confere(reg2["representantes"][0]["leitor2_nome"] is None, "segundo leitor que falha: None (o nome fica retido), nunca True")
    confere(P.segunda_leitura(b"", {"representantes": []}) == {"representantes": []}, "sem representante não há o que ler")

    # --- supressões
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        cam = pathlib.Path(d) / "s.json"
        confere(PE.carregar_supressoes(cam) == {}, "sem o arquivo de supressões: nenhuma")
        cam.write_text(json.dumps({"suprimir": ["a" * 20, {"aresta_id": "b" * 20, "motivo": "erro de leitura"}]}), encoding="utf-8")
        confere(PE.carregar_supressoes(cam) == {"a" * 20: "sem motivo escrito", "b" * 20: "erro de leitura"}, "lê ids soltos e objetos com motivo")
        cam.write_text(json.dumps(["c" * 20]), encoding="utf-8")
        confere(PE.carregar_supressoes(cam) == {"c" * 20: "sem motivo escrito"}, "aceita também uma lista pura")
        for ruim in ('{"suprimir": ["curto"]}', '{"suprimir": "x"}', "não é json", '{"suprimir": [{"motivo": "sem id"}]}'):
            cam.write_text(ruim, encoding="utf-8")
            try:
                PE.carregar_supressoes(cam)
                confere(False, f"supressão inválida {ruim!r} devia derrubar a rodada")
            except SystemExit:
                confere(True, "")
    ar, bl = monta([rep()])
    aid = ar[0]["aresta_id"]
    ar2, bl2 = monta([rep()], supressoes={aid: "conferência manual"})
    confere(ar2[0]["motivo_retencao"] == "suprimida_manualmente" and not ar2[0]["publicavel"] and ar2[0]["suprimida_porque"] == "conferência manual",
            "aresta suprimida fica retida, com o motivo escrito")
    confere(not bl2["assinou"]["pessoas"] and bl2["assinou"]["lacuna"]["motivo"] == "suprimida_manualmente", "…e a pessoa não entra no bloco (aplicado antes de publicar)")
    saida2 = PE.saida_publicavel({"1": {}}, ["1"], {"1": bl2}, ar2)
    confere(not saida2["contratos"]["1"]["assinou"]["pessoas"], "…nem na saída publicável")
    sup_real = PE.carregar_supressoes()
    confere(isinstance(sup_real, dict), "entradas/pessoas_supressoes.json existe e é válido")

    # --- vias
    ar, bl = monta([rep()])
    bl["aviso"] = {"pessoas": [{"nome": "Alguem Do Aviso", "nomeExibicao": "Alguem Do Aviso", "cargo": "Pregoeiro"}], "lacuna": None}
    s = PE.saida_publicavel({"1": {}}, ["1"], {"1": bl}, [], vias=("e2", "e8"))
    confere(s["_vias"] == ["E2", "E8"], "a saída diz quais vias estão ligadas")
    confere(s["contratos"]["1"]["aviso"]["pessoas"] == [] and s["contratos"]["1"]["aviso"]["lacuna"]["motivo"] == "via_desligada", "E3 desligado: sem nome e com o motivo")
    confere(s["contratos"]["1"]["assinou"]["pessoas"], "E2 ligado: o nome continua")
    confere("E3" not in s["_cobertura"] and "E2" in s["_cobertura"], "a cobertura só conta as vias ligadas")
    s3 = PE.saida_publicavel({"1": {}}, ["1"], {"1": bl}, [], vias=("E2", "E3", "E8"))
    confere(s3["contratos"]["1"]["aviso"]["pessoas"][0]["nome"] == "Alguem Do Aviso", "com o E3 ligado de propósito, o nome do aviso sai")
    # a rede de segurança: aresta publicável de E2 com nome reconstruído derruba a rodada
    ruim = dict(ar[0], nome="Outro Nome", nome_literal="Nome Literal")
    confere(any("reconstruído" in e for e in PE.checagens({"contratos": {}}, [ruim])), "checagem: E2 publicável com nome diferente do literal derruba a rodada")
    confere(any("segundo leitor" in e for e in PE.checagens({"contratos": {}}, [dict(ar[0], leitor2_nome=None)])), "checagem: E2 publicável sem o segundo leitor derruba a rodada")
    confere(any("sem cargo" in e for e in PE.checagens({"contratos": {}}, [dict(ar[0], cargo=None)])), "checagem: E2 publicável sem cargo derruba a rodada")
    confere(PE.queda({"pessoas": {}, "_vias": ["E2"], "contratos": {}}, {"versao": "pessoas-1", "pessoas": {"E2": 190}}) == [], "queda não compara rodadas de regras diferentes")
    confere(PE.queda({"_vias": ["E2"], "contratos": {"1": {"assinou": {"pessoas": []}, "aviso": {"pessoas": []}, "registro": {"gestores": []}}}},
                     {"versao": PE.VERSAO, "pessoas": {"E2": 190}}), "queda na mesma regra continua derrubando a rodada")
    confere(not PE.queda({"_vias": ["E2"], "contratos": {}}, {"versao": PE.VERSAO, "pessoas": {"E3": 246}}), "via desligada não conta como queda")


def listas_vazias_a():
    """"Sem arquivo" só confirmado (rodada de 22/10/2026 no GitHub: a API devolveu vazio para um trecho inteiro e 47 contratos com PDF
    saíram "sem arquivo"). Sem rede: a consulta ao Contratos.gov.br é trocada por respostas prontas e o cache vai para uma pasta temporária."""
    import os
    import tempfile
    import time
    from collections import Counter
    from pathlib import Path
    print("A4) lista de arquivos: 'sem arquivo' só confirmado")
    pedir_orig, cache_orig = PE.obras.pedir, PE.obras.CACHE_O
    respostas, pedidos = {}, []

    def falso(url, params=None, intervalo=1.0, tentativas=6):
        cid = url.rstrip("/").split("/")[-2]
        pedidos.append(cid)
        fila = respostas.get(cid) or ["404"]
        r = fila.pop(0) if len(fila) > 1 else fila[0]
        if r == "404":
            return None
        if r == "erro":
            raise RuntimeError("sem resposta")
        return r
    with tempfile.TemporaryDirectory() as tmp:
        PE.obras.pedir, PE.obras.CACHE_O = falso, Path(tmp)
        try:
            (Path(tmp) / "arquivos").mkdir()
            cam = lambda cid: Path(tmp) / "arquivos" / f"{cid}.json"
            arq = [{"id": 1, "tipo": "Contrato", "path_arquivo": "https://x/1.pdf"}]
            vazios, consultas, est = {}, [], Counter()
            # 404 não é "sem arquivo"
            respostas["1"] = ["404"]
            confere(PE.listar_arquivos("1", False, vazios, consultas, est) is None and not cam("1").exists(), "404: pendente e fora do cache")
            # com arquivos: vale e vai para o cache
            respostas["2"] = [arq]
            confere(PE.listar_arquivos("2", False, vazios, consultas, est) == arq and cam("2").exists(), "lista com arquivos vale e é guardada")
            # vazio novo é provisório; a 2ª consulta decide
            respostas["3"], respostas["4"] = [[], arq], [[], []]
            p3 = PE.listar_arquivos("3", False, vazios, consultas, est)
            p4 = PE.listar_arquivos("4", False, vazios, consultas, est)
            confere(p3 == PE.VAZIO_PROVISORIO and p4 == PE.VAZIO_PROVISORIO and not cam("3").exists(), "vazio novo é provisório e não vai para o cache")
            out = PE.confirmar_vazios(["3", "4"], consultas, vazios, est)
            confere(out["3"] == arq and "3" not in vazios, "vazio desmentido pela 2ª consulta: valem os arquivos")
            confere(out["4"] == [] and "4" in vazios and PE.coleta.ler_cache(cam("4")) == [], "vazio nas duas consultas: confirmado (índice e cache)")
            # confirmado e recente: não consulta de novo
            pedidos.clear()
            confere(PE.listar_arquivos("4", False, vazios, [], est) == [] and not pedidos, "vazio confirmado e recente: sem nova consulta")
            # vazio guardado sem confirmação (regra antiga): consulta de novo
            PE.coleta.gravar_cache(cam("5"), [])
            respostas["5"] = [arq]
            confere(PE.listar_arquivos("5", False, vazios, [], est) == arq, "vazio guardado sem confirmação é consultado de novo")
            # vazio não apaga lista com arquivos guardada (vencida)
            PE.coleta.gravar_cache(cam("6"), arq)
            velho_ = time.time() - 40 * 86400
            os.utime(cam("6"), (velho_, velho_))
            respostas["6"] = [[]]
            confere(PE.listar_arquivos("6", False, vazios, [], est) == arq and PE.coleta.ler_cache(cam("6")) == arq, "vazio não apaga a lista guardada")
            respostas["7"] = ["erro"]
            confere(PE.listar_arquivos("7", False, vazios, [], est) is None, "API fora do ar e nada guardado: pendente")
            # --so-montar: vazio sem confirmação = pendente; confirmado = sem arquivo
            confere(PE.listar_arquivos("5b", True, vazios, [], est) is None, "--so-montar sem nada guardado: pendente")
            PE.coleta.gravar_cache(cam("8"), [])
            os.utime(cam("8"), (velho_, velho_))
            confere(PE.listar_arquivos("8", True, vazios, [], est) is None, "--so-montar com vazio não confirmado: pendente")
            confere(PE.listar_arquivos("4", True, vazios, [], est) == [], "--so-montar com vazio confirmado: sem arquivo")
            # sequência longa de vazios = falha da API: ninguém é confirmado e não há 2ª consulta
            seq = [str(100 + i) for i in range(PE.SEQUENCIA_VAZIA_SUSPEITA)]
            for c in seq:
                respostas[c] = [[]]
            cons, est2, vz = [], Counter(), {}
            prov = [c for c in seq if PE.listar_arquivos(c, False, vz, cons, est2) == PE.VAZIO_PROVISORIO]
            pedidos.clear()
            out = PE.confirmar_vazios(prov, cons, vz, est2)
            confere(all(v is None for v in out.values()) and not vz and not pedidos and est2["arquivos_vazio_suspeito"] == len(seq),
                    f"{len(seq)} vazios seguidos: falha da API, todos pendentes, sem confirmação")
            # sequência curta (o máximo verdadeiro foi 8) é consultada de novo e confirmada
            curta = [str(200 + i) for i in range(8)]
            for c in curta:
                respostas[c] = [[]]
            cons, est3, vz = [("x", "cheio")], Counter(), {}
            prov = [c for c in curta if PE.listar_arquivos(c, False, vz, cons, est3) == PE.VAZIO_PROVISORIO]
            cons.append(("y", "cheio"))
            out = PE.confirmar_vazios(prov, cons, vz, est3)
            confere(all(v == [] for v in out.values()) and set(vz) == set(curta), "8 vazios seguidos: confirmados pela 2ª consulta")
            # os contratos com arquivo lidos do cache contam na sequência: 20 vazios antigos consultados de novo, entre contratos com
            # arquivo guardados, não são "falha da API" (caso real: a 1ª rodada com esta regra reconsulta só os 120 vazios antigos)
            ordem, cons, est4, vz = [], [], Counter(), {}
            for i in range(60):
                c = str(300 + i)
                if i % 3:
                    PE.coleta.gravar_cache(cam(c), arq)
                else:
                    PE.coleta.gravar_cache(cam(c), [])
                    respostas[c] = [[]]
                ordem.append(c)
            prov = [c for c in ordem if PE.listar_arquivos(c, False, vz, cons, est4) == PE.VAZIO_PROVISORIO]
            out = PE.confirmar_vazios(prov, cons, vz, est4)
            confere(len(prov) == 20 and all(v == [] for v in out.values()) and not est4["arquivos_vazio_suspeito"],
                    "vazios intercalados com contratos de arquivo guardado: confirmados, sem alarme")
            # uma falha de consulta no meio não esconde a sequência
            cons = [(str(400 + i), "vazio") for i in range(10)] + [("x", "falha")] + [(str(420 + i), "vazio") for i in range(10)]
            out = PE.confirmar_vazios([c for c, r in cons if r == "vazio"], cons, {}, Counter())
            confere(all(v is None for v in out.values()), "falha de consulta no meio da sequência não a interrompe")
        finally:
            PE.obras.pedir, PE.obras.CACHE_O = pedir_orig, cache_orig
    # a queda também se mede contra o arquivo publicado (o cache pode sumir; o arquivo do repositório fica)
    pessoa = {"assinou": {"pessoas": [{"nome": "X"}]}, "aviso": {"pessoas": []}, "registro": {"gestores": []}}
    vazio = {"assinou": {"pessoas": []}, "aviso": {"pessoas": []}, "registro": {"gestores": []}}
    pub = {"_versao": PE.VERSAO, "contratos": {str(i): pessoa for i in range(30)}}
    novo = {"_vias": ["E2", "E8"], "contratos": {str(i): (pessoa if i < 10 else vazio) for i in range(30)}}
    confere(any("arquivo publicado" in e for e in PE.queda(novo, None, pub)), "sem estado no cache, a queda contra o arquivo publicado derruba a rodada")
    confere(PE.queda(novo, None, dict(pub, _versao="pessoas-1")) == [], "arquivo publicado de outra regra não conta")


# ------------------------------------------------------------------------------------------------ B) validados

ESPERADO = {f"Silmario Ba{FFFD}sta dos Santos": "Silmario Batista dos Santos", f"Fabio Zuccolo{FFFD}o Ferreira": "Fabio Zuccolotto Ferreira"}


def parte_b():
    print("B) casos validados à mão (cache/amostra_validacao.csv) contra cache/pessoas_arestas.jsonl")
    arq = coleta.CACHE / "pessoas_arestas.jsonl"
    csv_ = coleta.CACHE / "amostra_validacao.csv"
    if not arq.exists() or not csv_.exists():
        falhas.append("faltam cache/pessoas_arestas.jsonl ou cache/amostra_validacao.csv (rode pessoas.py --dry-run)")
        print("  ✗", falhas[-1])
        return
    arestas = [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()]
    linhas = list(csv.DictReader(open(csv_, encoding="utf-8-sig"), delimiter=";"))
    por_url = {}
    for a in arestas:
        if a["via"] == "E2" and a.get("nome"):
            por_url.setdefault(a["url_documento"], []).append(a)
    cont = {"sim": 0, "não": 0, "parcial": 0}
    for r in linhas:
        if r["via"] != "E2":
            continue
        doc, nome, v = r["url_documento"].split("  ")[0], r["nome"], r["correto"]
        mesmos = [a for a in por_url.get(doc, []) if P.casa(nome, a["nome"]) or P.casa(a["nome"], nome) or P.chave(a["nome"]) == P.chave(nome)
                  or (v == "parcial" and P.chave(a["nome_literal"]) == P.chave(nome))]
        pub = [a for a in mesmos if a["publicavel"]]
        cont[v] += 1
        if v == "não":
            confere(not pub, f"NÃO saiu como representante: {nome} em {doc[-30:]} (saiu publicado: {[a['nome'] for a in pub]})")
        elif v == "sim":
            confere(bool(pub) or any(a["motivo_retencao"] in RETENCOES_DO_FILTRO for a in mesmos),
                    f"SIM sai publicado ou retido pelo filtro conservador: {nome} em {doc[-30:]} ({[(a['publicavel'], a['motivo_retencao']) for a in mesmos]})")
        elif v == "parcial":
            esp = ESPERADO.get(nome)
            if pub:
                confere(all(not P.tem_ilegivel(a["nome"]) and not P.tem_ilegivel(a.get("cargo")) for a in pub),
                        f"PARCIAL publicado sem '�': {nome} → {[a['nome'] for a in pub]}")
                if esp:
                    confere(any(P.chave(a["nome"]) == P.chave(esp) for a in pub), f"PARCIAL com a grafia certa: {nome} → {[a['nome'] for a in pub]} (esperado {esp})")
            else:
                confere(any(a["motivo_retencao"] in RETENCOES_DO_FILTRO for a in mesmos), f"PARCIAL ou publicado certo ou retido pelo filtro: {nome}")
    sai = [a for a in arestas if a["publicavel"] and a.get("nome") and (P.tem_ilegivel(a["nome"]) or P.tem_ilegivel(a.get("cargo")))]
    confere(not sai, f"nenhuma aresta publicável com '�' ({len(sai)})")
    nao_rep = [a for a in arestas if a["via"] == "E2" and a["publicavel"] and a.get("nome") and "metodo" in a and "preambulo_contratante" not in a["metodo"]]
    confere(not nao_rep, "toda aresta de E2 publicável veio de nome no bloco E no preâmbulo")
    sem_ident = [a for a in arestas if a["via"] == "E2" and a["publicavel"] and a.get("nome") and a["identificador_exato"]["tipo"] not in ("numero_do_contrato_no_texto", "processo_no_texto")]
    confere(not sem_ident, "toda aresta de E2 publicável tem o número do contrato (ou o processo) no texto do PDF")
    # E3: os dois verificados na validação (só se o E3 estava ligado nesta rodada; está desligado por padrão)
    for r in linhas:
        if r["via"] == "E3" and r["correto"] == "sim" and any(a["via"] == "E3" for a in arestas):
            e3 = [a for a in arestas if a["via"] == "E3" and a["contrato_id"] == r["contrato"] and a.get("nome")]
            confere(any(P.chave(a["nome"]) == P.chave(r["nome"]) and a["publicavel"] for a in e3), f"E3 SIM sai publicado: {r['nome']} (contrato {r['contrato']})")
    print(f"  E2 conferidos: {cont}")


def parte_c():
    print("C) primeira passada de conferência do E2 (cache/amostra_fase1_preenchida.csv)")
    arq = coleta.CACHE / "pessoas_arestas.jsonl"
    csv_ = coleta.CACHE / "amostra_fase1_preenchida.csv"
    if not arq.exists() or not csv_.exists():
        print("  (sem cache/pessoas_arestas.jsonl ou cache/amostra_fase1_preenchida.csv: parte C não rodou)")
        return
    por_id = {}
    for l in arq.read_text(encoding="utf-8").splitlines():
        if l.strip():
            a = json.loads(l)
            por_id[a["aresta_id"]] = a
    linhas = [r for r in csv.DictReader(open(csv_, encoding="utf-8-sig"), delimiter=";") if r["via"] == "E2"]
    achadas = publicadas = 0
    for i, r in enumerate(linhas):
        a = por_id.get(r["aresta_id"])
        if a is None:
            continue                                   # a aresta mudou de id (nome ou página diferentes): não é falha, só não dá para comparar
        achadas += 1
        if a["publicavel"]:
            publicadas += 1
            confere(P.chave(a["nome"]) == P.chave(r["nome"]), f"linha {i}: publicada com o nome conferido ({a['nome']!r} × {r['nome']!r})")
            confere(a.get("cargo") and not P.tem_ilegivel(a["cargo"]), f"linha {i}: publicada com cargo legível ({a.get('cargo')!r})")
            confere(a["nome"] == a["nome_literal"] and a.get("leitor2_nome") is True, f"linha {i}: publicada com nome igual ao literal e o 2º leitor concordando")
        else:
            confere(a["motivo_retencao"] in RETENCOES_DO_FILTRO, f"linha {i}: retida por motivo do filtro ({a['motivo_retencao']})")
        if r["correto"] == "parcial":
            confere("Substitut" in (a.get("cargo") or "") and not P.tem_ilegivel(a.get("cargo")),
                    f"linha {i} ({r['nome']}): o cargo 'Substituto(a)' foi preservado ({a.get('cargo')!r})")
    print(f"  {achadas} de {len(linhas)} linhas E2 achadas nas arestas atuais · {publicadas} publicáveis pelo filtro")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parte_a()
    if "--so-sinteticos" in sys.argv[1:]:
        print("B) e C) puladas (--so-sinteticos): dependem do cache de um dry-run local de pessoas.py")
    else:
        parte_b()
        parte_c()
    print(f"\n{ok} verificações passaram, {len(falhas)} falharam")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
