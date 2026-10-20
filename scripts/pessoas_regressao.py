"""
Câmara Aberta — testes de regressão da camada "Pessoas documentadas".

Uso (depois de rodar `python scripts/pessoas.py --dry-run`):
    python scripts/pessoas_regressao.py

Duas partes:
  A) casos sintéticos (nomes inventados, sem PDF): testemunha, empresa, nome ilegível, reconciliação, cargo com vírgula;
  B) os casos que você conferiu à mão em cache/amostra_validacao.csv, contra cache/pessoas_arestas.jsonl:
     - todo "não" (testemunha ou não representante) NUNCA pode sair como representante PUBLICADO naquele documento;
     - todo "sim" tem de sair publicado;
     - todo "parcial" (texto corrompido) só pode sair publicado com a grafia legível certa ou ficar retido como
       "nome ilegível" — nunca publicado com "�";
     - nenhuma aresta publicável tem "�" no nome nem no cargo.
Sai com código 1 se algo falhar.
"""

import csv
import json
import sys

import coleta
import pessoas as PE
import pessoas_pdf as P

FFFD = P.FFFD
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
            confere(bool(pub), f"SIM sai publicado: {nome} em {doc[-30:]}")
        elif v == "parcial":
            esp = ESPERADO.get(nome)
            if pub:
                confere(all(not P.tem_ilegivel(a["nome"]) and not P.tem_ilegivel(a.get("cargo")) for a in pub),
                        f"PARCIAL publicado sem '�': {nome} → {[a['nome'] for a in pub]}")
                if esp:
                    confere(any(P.chave(a["nome"]) == P.chave(esp) for a in pub), f"PARCIAL com a grafia certa: {nome} → {[a['nome'] for a in pub]} (esperado {esp})")
            else:
                confere(any(a["motivo_retencao"] == "nome_ilegivel" for a in mesmos), f"PARCIAL ou publicado certo ou retido como nome ilegível: {nome}")
    sai = [a for a in arestas if a["publicavel"] and a.get("nome") and (P.tem_ilegivel(a["nome"]) or P.tem_ilegivel(a.get("cargo")))]
    confere(not sai, f"nenhuma aresta publicável com '�' ({len(sai)})")
    nao_rep = [a for a in arestas if a["via"] == "E2" and a["publicavel"] and a.get("nome") and "metodo" in a and "preambulo_contratante" not in a["metodo"]]
    confere(not nao_rep, "toda aresta de E2 publicável veio de nome no bloco E no preâmbulo")
    sem_ident = [a for a in arestas if a["via"] == "E2" and a["publicavel"] and a.get("nome") and a["identificador_exato"]["tipo"] not in ("numero_do_contrato_no_texto", "processo_no_texto")]
    confere(not sem_ident, "toda aresta de E2 publicável tem o número do contrato (ou o processo) no texto do PDF")
    # E3: os dois verificados na validação
    for r in linhas:
        if r["via"] == "E3" and r["correto"] == "sim":
            e3 = [a for a in arestas if a["via"] == "E3" and a["contrato_id"] == r["contrato"] and a.get("nome")]
            confere(any(P.chave(a["nome"]) == P.chave(r["nome"]) and a["publicavel"] for a in e3), f"E3 SIM sai publicado: {r['nome']} (contrato {r['contrato']})")
    print(f"  E2 conferidos: {cont}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parte_a()
    parte_b()
    print(f"\n{ok} verificações passaram, {len(falhas)} falharam")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
