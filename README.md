# Câmara Aberta

O que cada deputado e senador propôs, o que virou lei e como votou, a partir de dados públicos
oficiais, organizados para qualquer pessoa entender.

Site estático (HTML, CSS e JavaScript puros, sem build e sem npm), atualizado todos os dias por um
workflow do GitHub Actions e publicado no GitHub Pages. Tudo o que o navegador baixa de terceiros para
funcionar está versionado em `site/vendor/`, em versão fixa e sem CDN: o Motion 14.0.0 (animação, MIT; `site/motion-init.js`), o
Three.js 0.180.0 (3D, MIT; o modelo decorativo do topo das páginas, depois da primeira pintura, e o plenário dos senadores, só por clique; sempre sob demanda, por importmap e `site/cena3d.js`) e a fonte Barlow Condensed
(SIL OFL, só para títulos grandes e números de destaque). Tudo é melhoria progressiva: sem JavaScript, sem WebGL ou com erro,
a página fica completa (o SVG do plenário e uma imagem estática no topo). As animações respeitam `prefers-reduced-motion`; o
tema escuro vale por `prefers-color-scheme` ou pelo alternador da barra, nas páginas que declaram `data-escuro-ok`
(hoje, todas). O visual editorial (grade larga, título e números em condensada, um modelo 3D por página, camadas escuras e claras) vale nas páginas com `data-ed`. Site independente, sem vínculo com a Câmara
dos Deputados, o Senado Federal ou o TSE.

## O que o site mostra

| Página | O que responde |
|---|---|
| **Início** (`index.html`) | A porta de entrada: busca de deputado, um cartão para cada página, "Esta semana na Câmara" (o que virou lei, andou ou foi apresentado nos últimos 7 dias, a pauta do Plenário e as notícias) e o espectro político em cada região do país. |
| **Deputados** (`deputados.html`) | Onde cada um dos 513 deputados está no espectro político e quanto propôs — com alternância entre proposições *apresentadas* e as que *viraram lei*. Ranking (com votação, votos, conversão, orientação e alinhamento ao Governo), partidos e as votações mais disputadas e mais consensuais. |
| **Partidos** (aba da página inicial) | As proposições de cada partido, por legislatura desde 1991 e por tema: proposições distintas, conversão, quem concentra o volume e perfil por tema; alinhamento ao Governo governo a governo, desde 2003; na legislatura atual, também coesão e posição no espectro. |
| **Perfil do deputado** (`deputado.html?id=…`) | Todas as PEC e PL de autoria, por tema, com o tempo e o caminho de cada uma na Câmara. Em cartões: o que fez (com as aprovadas em Plenário ao lado das que viraram lei), como vota (alinhamento ao Governo e votos diferentes da orientação da própria bancada, com a mediana da Câmara e os colegas de partido ao lado, **o tema das leis em que votou Sim, Não ou se absteve** (por bloco e por categoria, com a contagem de cada um) e quem vota parecido), **mandatos** (linha do tempo desde 2003, a condição de posse e a eleição de 2022), leituras na Wikipédia e obras com emenda dele. Mesmo desenho do perfil do senador. |
| **Senadores** (`senadores.html`) | Os 81 senadores no mesmo gráfico: autoria, leis, relatorias e votos no Plenário, com composição da esquerda à direita, alinhamento ao Governo e filtro de legislatura desde 2003, com todos os que exerceram o mandato (inclusive quem já saiu), seus suplentes e a data e a causa da saída. Em abas: gráfico, tabela e **partidos** (proposições, conversão, alinhamento ao Governo e coesão de cada bancada, na legislatura atual e, quando o arquivo dos senadores que já saíram existe, nas anteriores desde 2003; calculado no navegador a partir dos mesmos arquivos). A ficha de cada senador abre sobre a página. |
| **Perfil do senador** (`senador.html?id=…`) | Para cada senador, em exercício ou que já saiu: todas as PEC e PL de que é o **primeiro autor**, por tema (as mesmas categorias do perfil do deputado, atribuídas pela ementa oficial do Senado), com a situação de cada uma e, nas que viraram lei, o número e a data da norma; como vota (alinhamento ao Governo e votos diferentes da orientação da própria bancada, com a mediana do Senado ao lado, e o tema das matérias em que votou Sim, Não ou se absteve, com a contagem de cada um), mandatos, suplentes e saída. No topo, o **mapa de assentos do plenário** em 3D (com vaivém leve, e uma vista de cima parada) marca a bancada do estado: as 3 cadeiras que o Senado destaca no perfil de cada senador; a cadeira de cada um não é publicada, e o site não a inventa. É a mesma lista que dá os números do gráfico, conferida contra `senadores.json` a cada rodada. Não tem o caminho por órgão (o Senado não o entrega junto da lista de autoria), nem leituras na Wikipédia nem obras com emenda. |
| **Como votou, por tema** (`votos.html?casa=…&id=…&tema=…`) | Clicando num tema em "Como votou, por tema" nos perfis de deputado e de senador: as leis (ou votações, no Senado) daquele tema que o parlamentar votou, separadas em Sim, Não e abstenção, com o número, a data, o resultado, a ementa e os demais temas de cada uma. A lista reproduz exatamente os números do perfil. |
| **Leis votadas** (`leis.html`) | As votações finais nominais desde 2023: placar, posição média de quem votou sim e não, adesão por faixa do espectro, quem votou diferente da orientação da própria bancada, enquete da Câmara e consulta do Senado. |
| **Quem vota com quem** (`blocos.html`) | Os blocos que se formam pela semelhança dos votos, sem olhar o partido, para a **Câmara** e, pelo botão "Senado" (`?casa=s`), para o **Senado**: o mesmo método sobre as votações nominais abertas do Plenário desde 2023 (`blocos_senado.py`). O Senado não tem a seção por tema (sem a votação de texto-base, o Sim não quer dizer aprovar), e a página diz isso. |
| **Entre as casas** (`fluxo.html`) | Proposições aprovadas numa casa e remetidas à outra (Câmara → Senado e Senado → Câmara): quantas viraram lei, quanto tempo levaram na casa revisora e em que órgão estão paradas. A unidade é a proposição, nunca o parlamentar. |
| **Obras** (`obras.html`) | Obras federais em São Paulo com contrato no Contratos.gov.br: quanto o contrato cresceu, termo a termo, com os documentos oficiais. |
| **Contexto econômico** (`contexto.html`) | O que o Congresso está fazendo em matéria econômica (PEC e PLP de tema oficial Economia ou Finanças Públicas e Orçamento: sancionadas, em tramitação e paradas há mais de um ano), ao lado das séries oficiais de inflação e meta, PIB, despesa, dívida e resultado do governo. Os dois lados ficam separados: a página não liga lei a indicador, não julga número e diz isso no topo. |
| **[De onde vêm as leis](site/origem.html)** (`origem.html`) | De quem era a proposição que virou lei, entre as leis ordinárias e complementares sancionadas na legislatura atual: deputados, Senado, Executivo (projeto de lei, medida provisória convertida ou PLN), Judiciário, Ministério Público, Defensoria e controle, e órgãos da Câmara. A origem é a autoria, não diz quem aprovou nem quem ajudou a escrever o texto. Fica fora da barra do topo: abre pela página inicial, pela visão "Viraram lei" de Deputados, pela ficha de cada deputado e por um link no rodapé de todas as páginas. |

## Princípios

- **Todo número é rastreável a uma fonte oficial.** Bases diferentes só se ligam por identificador;
  nome só por igualdade exata, e com conferência extra (mandato, posse). Nunca por nome parecido.
- **Lacuna fica lacuna.** Quando um dado não existe ou não pôde ser ligado com segurança, a página
  mostra "—" e o motivo. Nada é estimado ou preenchido.
- **Sem índice de "qualidade".** Quem escolhe os pesos escolhe o ranking. O site mostra volume
  (apresentadas) ao lado de resultado (viraram lei) e deixa a comparação para quem lê.
- **Enquadramento antes do número.** Votar diferente da orientação da própria bancada não é traição por definição; aditivo
  de contrato não é irregularidade; leitura na Wikipédia mede procura, não aprovação.
- **As cores do espectro só representam ideologia.** Tema, bloco de voto e valor de obra usam
  tinta neutra ou cores fora dessa paleta. Os símbolos dos temas (abaixo) seguem a mesma regra.

## Método, em resumo

Cada página traz, no fim, a seção "Como é calculado" com as regras completas.

- **Espectro político.** Média simples das respostas da wave 9 (2021) do *Brazilian Legislative
  Survey*, de Timothy Power e Cesar Zucco ([Harvard Dataverse](https://dataverse.harvard.edu/dataverse/bls),
  DOI 10.7910/DVN/WM9IZ8), **calculada por este projeto** — não é a estimativa reescalonada que os
  autores publicam. Escala de 1 (esquerda) a 10 (direita). É a posição do **partido**, não da pessoa:
  todos os deputados de um partido têm o mesmo valor, e a pequena dispersão horizontal no gráfico só
  existe para os pontos não se sobreporem.
- **Os pontos de corte das cinco faixas** (esquerda, centro-esquerda, centro, centro-direita, direita)
  **foram definidos por este projeto, não pelo BLS.** A pesquisa dá o número; a faixa é interpretação.
  Por isso a cor de cada ponto é contínua e as faixas aparecem só na legenda, nos filtros e na adesão
  por faixa das votações.
- **Partido sem posição na pesquisa** fica sem posição, em cinza — nunca estimado. É o caso da União
  Brasil (fusão de 2022, posterior à pesquisa), de partidos com poucas respostas (PCdoB, PV, REDE) e
  de partidos fora do questionário. Hoje, 82 dos 513 deputados.
- **Virou lei** = situação oficial "Transformado em Norma Jurídica". **Aprovada** vem do registro
  oficial de aprovação, nunca da comparação entre sim e não (emenda à Constituição exige 308 votos).
- **A votação que representa uma proposição** é a votação final do texto-base, e só se for nominal —
  votação simbólica não registra o voto de cada deputado.
- **Apoio cruzado**, por votação: distância entre a posição do autor e a posição média de quem
  votou sim, sempre normalizada pelo tamanho de cada bancada.
- **Votou diferente da orientação da própria bancada**: o voto Sim ou Não contra a orientação Sim ou
  Não **registrada** pela liderança — na Câmara, a mais estreita que orientou e inclui o partido do
  deputado naquele dia (o partido, a federação ou o bloco); no Senado, o partido. Orientação liberada
  não conta, nem as de Governo, Oposição, Maioria e Minoria. É registro das duas Casas, não estimativa.
- **Partidos**: vêm dos arquivos anuais em lote da Câmara, de **todos** os autores (não só dos deputados de
  hoje), de 1991 em diante. Cada proposição conta **uma vez em cada partido** que teve autor nela, no partido que o
  autor tinha **na data da apresentação** (99,6% batem com o histórico oficial de exercício), com a sigla da época
  — nunca a soma dos totais por deputado. Quem trocou de partido conta em cada um, só com o que apresentou
  enquanto estava nele. O tema é o das páginas dos deputados (7 blocos, 27 categorias, por palavras-chave:
  interpretação do site). Partido com menos de 10 deputados autores aparece, marcado, fora da ordenação.
  Conversão reflete também acesso ao poder e tempo, não só mérito. Posição no espectro e coesão só existem
  para a legislatura atual.
- **Presença** é contagem de votos registrados, nunca taxa: sem o período de exercício de cada um, a
  conta trataria licença e suplência como falta.
- **Obras**: crescimento = valor global atual − valor inicial, os dois campos do registro do contrato;
  a natureza de cada termo aditivo (acréscimo, reajuste, prorrogação…) sai do texto do termo.
- **Pessoas documentadas (obras)**: três fontes, e nada além delas, ligadas ao contrato por identificador exato
  (número do contrato, processo, ou licitação e UASG) — nunca por nome. (1) *Quem assinou pelo órgão*: do PDF do contrato
  ou do termo anexado no Contratos.gov.br; entra só o representante do órgão contratante cujo nome está no bloco de assinatura
  eletrônica **e** no trecho do preâmbulo que identifica o contratante, e só se o número do contrato (ou o processo) consta no
  texto. Testemunhas, quem assina pela empresa e os demais signatários não são listados (e os nomes não são guardados). PDF só
  imagem não é lido: não há OCR. Nome corrompido pela extração (a ligadura "ti" some em alguns PDFs) só é publicado depois de
  reconciliado com a grafia do preâmbulo do mesmo documento ou, em segundo lugar, de outro documento do mesmo órgão; se sobrar
  "�", fica de fora. (2) *Responsável registrado no aviso de licitação*: do Compras.gov.br (módulo legado), por licitação e UASG,
  com nome e cargo como a fonte escreve; **não** diz quem conduziu a sessão, nem quem homologou. (3) *Responsável registrado no
  contrato*: o gestor do `/contrato/{id}/responsaveis`; fiscais aparecem só em número. Onde falta dado, a página mostra "—" e o
  motivo. Cada vínculo é uma aresta com url do documento, página, hash, identificador que casou, data do vínculo (separada da de
  publicação), trecho literal e método (`cache/pessoas_arestas.jsonl`, também as retidas, com o motivo). Sem CPF, RG ou SIAPE;
  sem parlamentares, sem ranking de pessoas. A publicação no site fica desligada até o `scripts/pessoas.py` rodar com `--publicar`.
- **Alinhamento ao Governo**: para cada deputado, a frequência com que votou como o líder do Governo orientou
  (registro de orientação da Câmara, só Sim ou Não) nas votações nominais do Plenário; o número do partido é a mediana
  entre os deputados dele, com 3 ou mais. O recorte é o **governo** (Lula I, Lula II, Dilma I, Dilma II, Temer,
  Bolsonaro, Lula III), não a legislatura: a 55ª teve dois presidentes. Quem é o Governo em cada votação vem do
  registro da Câmara; a tabela `entradas/governos.json` só dá os cortes de data e o partido do presidente (conferidos
  no Wikidata). Só desde 2003, quando a Câmara passou a publicar as orientações. Mede proximidade com o governo do
  momento, não ideologia nem mérito.
- **Entre as casas**: só entram as proposições aprovadas numa casa e remetidas à outra (PL, PLP, PEC e PDL), a partir do
  Senado (`/processo`, o endpoint atual: `/materia/*` está desativado) e da tramitação da Câmara. A ligação entre as duas é
  **só por identificador exato** (o número da outra casa que o Senado registra, ou o número de origem que a Câmara cita ao
  receber); nunca por título ou ementa. Sem vínculo confirmado, a proposição fica fora das contas e é contada. Desfecho vem
  de campo estruturado, rejeitada se distingue de arquivada, e "parada" descreve (mais de 365 dias sem andamento), nunca
  acusa de atraso. Nada de autor, relator ou presidente de comissão: agrega por tipo, direção e órgão.
- **De onde vêm as leis** (`scripts/origem_leis.py`). **A origem é a autoria da proposição que virou lei. Não indica quem a
  aprovou nem quem mais contribuiu para o texto final.** Entram as leis ordinárias e complementares da legislatura atual, pela
  data da norma, uma vez cada; as emendas constitucionais não são sancionadas e ficam numa linha à parte. **Quem define a lista
  é o Senado** (campo `normaGerada` de `/processo`), porque uma lei de hoje pode vir de um PLS de 2007; a Câmara só informa a
  autoria. A ligação entre a lei e a proposição é **só por identificador exato** (o mesmo nas duas Casas, o que o Senado
  registra como número da outra Casa, ou o número de lei que a própria Câmara grava na tramitação, desde que uma única
  proposição o reivindique), nunca por título, ementa ou nome. O autor (`proponente = 1`, pelo identificador dele) vira
  categoria pela tabela `scripts/mapa_origem.json`, com a fonte oficial de cada nome. Lei sem proposição achada fica "Não
  classificada", e a divergência de número entre as Casas fica fora da conta e é listada. O Executivo aparece em três partes
  (projeto de lei, medida provisória convertida e PLN, as leis de crédito e orçamento) e a página dá o total com e sem as PLN.
  A data é a do Senado: onde a Câmara registra outra, a do Senado foi a que o Planalto confirmou, em todos os casos
  conferidos. A contagem por ano e tipo é comparada com a que foi conferida à mão nas listas do Planalto
  (`entradas/origem_leis_referencia.json`; o script nunca consulta o site do Planalto), e as exceções curadas, cada uma com
  os links oficiais e a data da verificação, ficam em `entradas/normas_excecoes.json` (teto de 5). O script **falha de
  propósito** se a data de hoje passar do fim da legislatura configurada em `legislaturas.json`: a 58ª começa em 1º/2/2027, e
  sem isso a 57ª continuaria sendo contada como a atual. Falhou, o site mantém o JSON anterior e mostra a data dele.
- **Alinhamento ao Governo (deputados e senadores)**: coluna no ranking dos deputados e na tabela dos senadores. É a parcela dos
  votos Sim ou Não, nas votações nominais do Plenário desde 2023 em que o líder do Governo orientou Sim ou Não (o registro de
  orientação de cada Casa), em que a pessoa votou como ele orientou; mínimo de 50 votações na Câmara e 30 no Senado. Mede
  proximidade com o governo do momento, não ideologia, lealdade nem mérito.
- **Logos dos partidos**: o da Câmara quando o endereço oficial abre (34 dos 62 registros); para os demais, o do
  Wikimedia Commons, só por uma tabela curada (`entradas/logos_partidos.json`) em que cada logo foi conferido na
  imagem e ligado ao partido pelo nome oficial. Partido sem logo seguro fica só com o nome: nunca se busca por
  título de arquivo. O site referencia os endereços e não guarda cópia; a página lista fonte e licença.
- **Resumos por IA**, quando existem, são feitos a partir do texto integral da proposição, marcados
  como tal, e a ementa oficial fica a um clique.

## Fontes

| Fonte | Para quê |
|---|---|
| [Dados Abertos da Câmara](https://dadosabertos.camara.leg.br) (API e arquivos anuais) | deputados, proposições, tramitações, votações, votos e orientações de bancada |
| [Dados Abertos do Senado](https://legis.senado.leg.br/dadosabertos/) e e-Cidadania | senadores, autoria, relatorias, votações; consulta pública das matérias |
| TSE — Divulgação de Resultados 2022 | votos de cada deputado e quociente eleitoral (tabela fixa em `entradas/`) |
| Brazilian Legislative Survey (Power e Zucco) | posição dos partidos no espectro |
| IBGE (API SIDRA), Banco Central (SGS e página de metas) e Tesouro Nacional (API de Séries Temporais) | IPCA, PIB, meta de inflação e banda, dívida, despesa, resultado primário e nominal do Governo Central |
| Planalto (legislação) | endereço do texto de cada lei, só como link; a contagem de leis por ano e tipo foi conferida à mão nas listas do Planalto e está em `entradas/origem_leis_referencia.json` |
| IBGE (API SIDRA: PNAD Contínua e Contas Nacionais Trimestrais) e Banco Central (SGS, balanço de pagamentos) | "Mais indicadores" da página de economia: rendimento, ocupação, massa de renda do trabalho, desocupação, informalidade, subutilização, renda domiciliar per capita e Gini, transações correntes e renda nacional disponível bruta (nominal). Produtividade: só link para o [Observatório da Produtividade Regis Bonelli (FGV IBRE)](https://ibre.fgv.br/observatorio-produtividade), sem cálculo nosso |
| [Comex Stat](https://comexstat.mdic.gov.br/), do MDIC (API pública `api-comexstat.mdic.gov.br`) | "Para onde o Brasil exporta e o que exporta", na página de economia: valor exportado em US$ FOB (dólares correntes) por país de destino e por produto (posição SH4 do Sistema Harmonizado), desde 1997, com os nomes das tabelas oficiais do MDIC |
| ObrasGov.br, Contratos.gov.br e CGU | obras, contratos, termos aditivos, emendas parlamentares, sanções (CEIS/CNEP) |
| Contratos.gov.br (PDFs e responsáveis) e Compras.gov.br (dados abertos) | pessoas documentadas de cada obra: quem assinou pelo órgão, responsável no aviso de licitação e gestor do contrato |
| Enquetes e Agência Câmara (RSS) | enquete de cada lei votada; notícias ligadas às proposições, cada uma com a categoria da lei pela mesma classificação do site |
| Wikipédia (API de leituras) | leituras do artigo de cada deputado, ligado por identificador |
| Logo oficial da Câmara e Wikimedia Commons | logos dos partidos, só referenciados (nenhuma imagem é copiada), com crédito e licença na aba Partidos |

## Estrutura do repositório

```
site/                 o que vai ao ar — o GitHub Pages publica a pasta inteira, e só ela
  index.html          página inicial; deputados.html, a dos deputados (prototipo.html só redireciona)
  deputado.html  senador.html  votos.html  leis.html  blocos.html  fluxo.html  obras.html  senadores.html  contexto.html
  origem.html         "De onde vêm as leis": fora da barra do topo (inicial, deputados, ficha e rodapé levam a ela)
  origem.js           a barra de composição e o texto de "De onde vêm as leis", iguais nas quatro telas que os mostram
  estilo.css  site.js  temas.js  paineis.js  motion-init.js  favicon.ico     (temas.js: símbolos dos temas e o botão por tema; paineis.js: as peças dos perfis de parlamentar)
  cena3d.js  modelos3d.js  cupula3d.js  plenario3d.js   3D sob demanda: base comum, modelos do topo, plenário dos senadores
  mapa-assentos.js    mapa 3D do plenário em senador.html: projeção própria pintada em SVG (sem Three.js nem WebGL)
  img/                imagem estática de cada modelo (a mesma câmera do 3D, para quem não tem WebGL)
  vendor/             Motion, Three.js e a fonte condensada, em versão fixa
  dados/              os JSON que as páginas leem, gerados pelos scripts
    temas/            um arquivo por deputado, a taxonomia e o resumo
    senadores/        um arquivo por senador (os de hoje e os que já saíram) e a frase de cada um
scripts/              coleta e processamento, em Python (mapa_origem.json: autor → categoria, com a fonte de cada nome)
entradas/             tabelas fixas e resultados pagos, versionados e não publicados
                      (ideologia.json, eleicao2022.json, governos.json, logos_partidos.json, resumos por IA,
                      normas_excecoes.json e origem_leis_referencia.json, de "De onde vêm as leis")
cache/                respostas cruas das APIs — fora do git (centenas de MB), regenerável
.github/workflows/    a rodada diária
```

Os scripts calculam todos os caminhos a partir da raiz do repositório (`scripts/coleta.py`:
`BASE`, `DADOS`, `ENTRADAS`, `CACHE`), então rodam de qualquer pasta.

## Rodando localmente

Testado com Python 3.13, a versão do workflow.

```bash
pip install -r requirements.txt
```

A primeira coleta, com o cache vazio, leva cerca de 3 horas (≈106 mil requisições à API da Câmara,
respeitando o limite de 10 por segundo). Depois disso, regerar os JSON a partir do cache leva segundos.
Para validar antes, rode com 10 deputados (`--limite 10`).

```bash
python scripts/coleta.py --limite 513
```

```bash
python scripts/votos.py
```

`coleta.py` regrava `deputados.json` sem os votos: sempre rode `votos.py` depois dele. A ordem
completa da rodada diária está no workflow:

```
atualiza → coleta → votos → blocos → notoriedade → wikipedia → noticias → youtube → enquetes
→ ecidadania → resumos → obras → pessoas → senado → tramitacoes → temas → senador_paginas → partidos → governo → logos → fluxo → origem_leis → semana
```

Para ver o site, sirva a pasta `site/` (o navegador não carrega os JSON de arquivos abertos direto
do disco) e abra `http://localhost:8000`:

```bash
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Scripts que não entram na rodada diária:

- `scripts/eleicao.py` — gera `entradas/eleicao2022.json` uma vez. O portal de dados abertos do TSE
  recusa acesso automatizado, e o projeto não contorna esse bloqueio; por isso a tabela é fixa.
- `scripts/comex.py` — exportações por destino e por produto (Comex Stat), grava `site/dados/comex/`. Roda na rodada diária mas só trabalha a cada 7 dias; `--forcar` ignora o intervalo,
  `--so-montar` remonta do cache, `--simular` confere sem gravar, `--imprimir` mostra totais, os 10 maiores e as frases, `--teste` roda os testes sem rede.
- `scripts/contexto_trabalho.py` — roda dentro de `contexto.py` (módulo irmão, no workflow diário) e também sozinho (`--teste` roda os testes, sem rede;
  `--imprimir` mostra o último valor de cada série e as frases geradas). Grava `site/dados/contexto/trabalho.json`.
- `scripts/signatarios.py` — piloto que lê os PDFs dos contratos de obra para achar quem assinou.
  Não publica nada; precisa de `pymupdf` (fora do `requirements.txt`; licença AGPL ou comercial, por isso fora do projeto, que é MIT).
- `scripts/diag_vinculo_*.py` — o diagnóstico de viabilidade que deu origem às pessoas documentadas (relatório em `cache/`).
  Medição e registro; `diag_vinculo_e2.py` e `signatarios.py` usam `pymupdf` em comparações locais.

`scripts/pessoas.py` roda depois de `obras.py` (usa o cache dele) e **só escreve em `site/` com `--publicar`**. Sem a
flag, grava em `cache/` as arestas (`pessoas_arestas.jsonl`), o candidato (`pessoas_candidato.json`) e a amostra de
validação (`amostra_fase1.csv`). `--dry-run` mostra o relatório e o diff sem atualizar a rodada anterior. Os testes de
regressão (`python scripts/pessoas_regressao.py`) usam os casos conferidos à mão: quem não é representante nunca sai como tal.
Só usa `pypdf` (BSD-3), nada de `pymupdf`.

### Chaves de API (opcionais)

| Variável | Para quê | Sem ela |
|---|---|---|
| `ANTHROPIC_API_KEY` | resumos por IA das proposições (`resumos.py`) | aplica só os resumos já existentes |
| `YOUTUBE_API_KEY` | inscritos do canal oficial de cada deputado (`youtube.py`) | ficam só os links |

Localmente, como variáveis de ambiente; no workflow, como segredos do repositório
(*Settings → Secrets and variables → Actions*). Nunca no código.

### Certificado da Câmara

O servidor da Câmara não envia o certificado intermediário, e `requests` falha na verificação. Os
scripts usam `truststore`, que completa a cadeia pelo sistema operacional — por isso o workflow roda
em Windows. **Nunca desligar a verificação** (`verify=False`).

## Atualização diária

`.github/workflows/atualiza.yml` roda às 6h30 (horário de Brasília) e também pode ser disparado à mão
(*Actions → Atualização diária → Run workflow*). Ele:

1. restaura o `cache/` da rodada anterior (cache do Actions);
2. pergunta às APIs só o que mudou desde a última rodada e regera os JSON;
3. faz commit de `site/dados/` e `entradas/` ("Dados de AAAA-MM-DD") — o histórico do git é o
   registro auditável de cada mudança;
4. publica `site/` no GitHub Pages.

Checagem de sanidade falhou → o passo sai com erro, nada é commitado nem publicado, e o site continua
com os dados do dia anterior. Para publicar pela primeira vez: *Settings → Pages → Source: GitHub Actions*.

## Limitações conhecidas

- **Votação nominal é rara.** De 2023 a 2026, só 164 votações finais de texto-base foram nominais;
  tudo o que depende do voto individual em proposições (apoio cruzado, adesão por faixa) vale para elas.
- **82 dos 513 deputados não têm posição no espectro**, 52 deles da União Brasil. Ficam fora das
  médias por faixa, e o site declara quanto isso pesa nas votações.
- **Relatorias na Câmara** não têm fonte por deputado na API, e aparecem como "—". No Senado existem.
- **Obras**: por ora só São Paulo e só contrato federal, cerca de 1,3% das obras de SP cadastradas no
  ObrasGov. Obras contratadas por estado ou prefeitura não aparecem.
- **Pessoas documentadas (obras)**: a cobertura é parcial por construção, e a página diz onde. Cerca de 40% dos contratos não têm
  arquivo algum anexado no Contratos.gov.br; PDF digitalizado não é lido (sem OCR); alguns PDFs perdem letras na extração e o nome
  fica de fora quando não dá para reconciliar. O aviso de licitação traz o nome do responsável em quase todas as licitações até 2024: nas de 2025 em
  diante o Compras.gov.br quase não traz o nome (a licitação consta do PNCP, que publica o edital e não foi consultado para nomes). "Responsável no aviso" é o cargo que a fonte grava — nem sempre
  pregoeiro —, e a precisão dessa leitura só foi conferida em dois casos. Não há homologação, ratificação, dirigente do órgão na data,
  nem o quadro de autoridades: o Diário Oficial não permite coleta automatizada (`robots.txt`). Amostra de validação manual pequena
  (16 casos de PDF): a precisão não está provada em 95%.
- **Contexto econômico**: só a Câmara e só dois temas oficiais (Economia e Finanças Públicas e Orçamento). PL ordinário entra apenas nas sancionadas dos
  últimos 12 meses; apensadas e proposições sem situação na API ficam fora das abas e são contadas. Antes de 2003 a Câmara não atribuiu tema a todas as
  proposições, então há anos sem marcos. O "objetivo declarado" é citação literal da justificação, nunca paráfrase de IA.
- **De onde vêm as leis**: só a legislatura atual, e só leis ordinárias e complementares (as emendas constitucionais ficam à
  parte). A lei só entra numa categoria se a ligação com a proposição for por identificador exato; do contrário fica "Não
  classificada", e uma divergência de número entre as Casas a deixa fora da conta. O universo é o do registro do Senado, e uma lei
  que só a Câmara registra entra com a marca "data não confirmada pelo Senado". As leis da ficha de um deputado, as de "Leis
  votadas" e as desta página são conjuntos diferentes.
- **Mais indicadores (economia)**: séries da PNAD Contínua em trimestre móvel (cada ponto é a média de três meses, nomeado pelo último mês), reponderadas
  pelo IBGE em nov/2021 e em 31/7/2025; os valores reais mudam de base todo mês (reais do último trimestre móvel), por isso a série inteira é baixada de novo a cada
  rodada. A informalidade só existe desde out–dez/2015, e a subutilização tem dois meses sem dado na fonte (out e nov/2015), mostrados como "Dados indisponíveis".
  Renda per capita e Gini são anuais e, segundo o IBGE, parte dos temas anuais só é reponderada no ciclo de 2026. A "mediana" é o limite superior da classe P50 que o
  IBGE publica; não é calculada de microdados. A renda nacional disponível bruta é nominal (o IBGE não publica série real no SIDRA) e o site não a deflaciona. As
  transações correntes seguem o manual BPM6 (desde abril de 2015); a proporção do PIB usa o PIB em dólares. Produtividade não é calculada aqui.
- **Exportações (economia)**: valor exportado em US$ FOB, em dólares correntes: sem corrigir pela inflação e sem converter para reais. O destino é o país registrado
  na exportação e pode ser um intermediário, não o consumidor final. Os produtos são as posições SH4 do MDIC; o site não cria categorias ("agro", "indústria"…). Países e produtos abaixo de 0,5%
  do total entram em "Demais". O ano corrente mostra só os meses fechados, e a comparação com outro ano usa os mesmos meses. Os dados gerais começam em 1997; os de 1989 a 1996 usam outra
  metodologia (NBM) e não entram. A soma por posição SH4 fica poucos milhares de dólares abaixo do total (a fonte não explica a diferença), e a página diz quanto. A API do MDIC limita os
  pedidos, então a coleta é semanal e leva cerca de 20 minutos.
- **Categorias de tema** são classificação deste site por palavras-chave da ementa, com o termo que
  definiu cada uma sempre visível. A precisão ainda não foi medida. No perfil do senador as mesmas regras valem só sobre a ementa do Senado (sem a
  indexação oficial, que o Senado não entrega junto da lista de autoria); a ementa que não casa com nenhuma regra fica "sem categoria", e a página
  diz quantas.
- **Símbolos dos temas.** Cada um dos 7 blocos e das 27 categorias tem um desenho, nas páginas que mostram tema (ficha por tema do deputado, Leis
  votadas, "Esta semana", Quem vota com quem, o perfil por tema dos partidos, o perfil do senador e o Contexto econômico, onde os dois temas oficiais da Câmara
  emprestam o desenho de duas categorias). São desenhos originais deste projeto (`site/temas.js`), em uma
  tinta só, a do texto, nos dois temas: ajudam a achar o tema, não dizem qual é. O nome fica sempre escrito ao lado, e sem o módulo as páginas
  mostram só o nome. O símbolo é um auxílio e não carrega ideologia, posição política nem importância.

## Licença

Código sob a [licença MIT](LICENSE). Os dados são públicos e pertencem às fontes citadas.

Encontrou um erro? Abra uma [issue](https://github.com/paulopottermarchi/Senado_Move/issues) com o
link da página e o que está errado.
