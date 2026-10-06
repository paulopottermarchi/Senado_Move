# Câmara Aberta

O que cada deputado e senador propôs, o que virou lei e como votou, a partir de dados públicos
oficiais, organizados para qualquer pessoa entender.

Site estático (HTML, CSS e JavaScript puros, sem build e sem npm), atualizado todos os dias por um
workflow do GitHub Actions e publicado no GitHub Pages. Tudo o que o navegador baixa de terceiros para
funcionar está versionado em `site/vendor/`, em versão fixa e sem CDN: o Motion 14.0.0 (animação, MIT; `site/motion-init.js`), o
Three.js 0.180.0 (3D, MIT; o modelo decorativo do topo das páginas, depois da primeira pintura, e o plenário dos senadores, só por clique; sempre sob demanda, por importmap e `site/cena3d.js`) e a fonte Barlow Condensed
(SIL OFL, só para títulos grandes e números de destaque). Tudo é melhoria progressiva: sem JavaScript, sem WebGL ou com erro,
a página fica completa (o SVG do plenário e uma imagem estática no topo). As animações respeitam `prefers-reduced-motion`; o
tema escuro vale por `prefers-color-scheme` ou pelo alternador da barra, e só nas páginas que declaram `data-escuro-ok`
(hoje, deputados e senadores). O visual editorial (grade larga, título e números em condensada, um modelo 3D por página, camadas escuras e claras) vale nas páginas com `data-ed`. Site independente, sem vínculo com a Câmara
dos Deputados, o Senado Federal ou o TSE.

## O que o site mostra

| Página | O que responde |
|---|---|
| **Início** (`index.html`) | A porta de entrada: busca de deputado, um cartão para cada página, "Esta semana na Câmara" (o que virou lei, andou ou foi apresentado nos últimos 7 dias, a pauta do Plenário e as notícias) e o espectro político em cada região do país. |
| **Deputados** (`deputados.html`) | Onde cada um dos 513 deputados está no espectro político e quanto propôs — com alternância entre proposições *apresentadas* e as que *viraram lei*. Ranking (com votação, votos, conversão, orientação e alinhamento ao Governo), partidos e as votações mais disputadas e mais consensuais. |
| **Partidos** (aba da página inicial) | As proposições de cada partido, por legislatura desde 1991 e por tema: proposições distintas, conversão, quem concentra o volume e perfil por tema; alinhamento ao Governo governo a governo, desde 2003; na legislatura atual, também coesão e posição no espectro. |
| **Perfil do deputado** (`deputado.html?id=…`) | Todas as PEC e PL de autoria, por tema, com o tempo e o caminho de cada uma na Câmara; com quem vota; leituras na Wikipédia; obras com emenda dele. |
| **Senadores** (`senadores.html`) | Os 81 senadores no mesmo gráfico: autoria, leis, relatorias e votos no Plenário, com composição da esquerda à direita, alinhamento ao Governo e filtro de legislatura desde 2003 (só entre os senadores de hoje). |
| **Leis votadas** (`leis.html`) | As votações finais nominais desde 2023: placar, posição média de quem votou sim e não, adesão por faixa do espectro, quem votou diferente da orientação da própria bancada, enquete da Câmara e consulta do Senado. |
| **Quem vota com quem** (`blocos.html`) | Os blocos que se formam pela semelhança dos votos, sem olhar o partido. |
| **Entre as casas** (`fluxo.html`) | Proposições aprovadas numa casa e remetidas à outra (Câmara → Senado e Senado → Câmara): quantas viraram lei, quanto tempo levaram na casa revisora e em que órgão estão paradas. A unidade é a proposição, nunca o parlamentar. |
| **Obras** (`obras.html`) | Obras federais em São Paulo com contrato no Contratos.gov.br: quanto o contrato cresceu, termo a termo, com os documentos oficiais. |

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
  tinta neutra ou cores fora dessa paleta.

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
| ObrasGov.br, Contratos.gov.br e CGU | obras, contratos, termos aditivos, emendas parlamentares, sanções (CEIS/CNEP) |
| Enquetes e Agência Câmara (RSS) | enquete de cada lei votada; notícias ligadas às proposições |
| Wikipédia (API de leituras) | leituras do artigo de cada deputado, ligado por identificador |
| Logo oficial da Câmara e Wikimedia Commons | logos dos partidos, só referenciados (nenhuma imagem é copiada), com crédito e licença na aba Partidos |

## Estrutura do repositório

```
site/                 o que vai ao ar — o GitHub Pages publica a pasta inteira, e só ela
  index.html          página inicial; deputados.html, a dos deputados (prototipo.html só redireciona)
  deputado.html  leis.html  blocos.html  fluxo.html  obras.html  senadores.html
  estilo.css  site.js  motion-init.js  favicon.ico
  cena3d.js  modelos3d.js  cupula3d.js  plenario3d.js   3D sob demanda: base comum, modelos do topo, plenário dos senadores
  img/                imagem estática de cada modelo (a mesma câmera do 3D, para quem não tem WebGL)
  vendor/             Motion, Three.js e a fonte condensada, em versão fixa
  dados/              os JSON que as páginas leem, gerados pelos scripts
    temas/            um arquivo por deputado, a taxonomia e o resumo
scripts/              coleta e processamento, em Python
entradas/             tabelas fixas e resultados pagos, versionados e não publicados
                      (ideologia.json, eleicao2022.json, governos.json, logos_partidos.json, resumos por IA)
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
→ ecidadania → resumos → obras → senado → tramitacoes → temas → partidos → governo → logos → fluxo → semana
```

Para ver o site, sirva a pasta `site/` (o navegador não carrega os JSON de arquivos abertos direto
do disco) e abra `http://localhost:8000`:

```bash
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Scripts que não entram na rodada diária:

- `scripts/eleicao.py` — gera `entradas/eleicao2022.json` uma vez. O portal de dados abertos do TSE
  recusa acesso automatizado, e o projeto não contorna esse bloqueio; por isso a tabela é fixa.
- `scripts/signatarios.py` — piloto que lê os PDFs dos contratos de obra para achar quem assinou.
  Não publica nada; precisa de `pymupdf` (fora do `requirements.txt`).

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
- **Categorias de tema** são classificação deste site por palavras-chave da ementa, com o termo que
  definiu cada uma sempre visível. A precisão ainda não foi medida.

## Licença

Código sob a [licença MIT](LICENSE). Os dados são públicos e pertencem às fontes citadas.

Encontrou um erro? Abra uma [issue](https://github.com/paulopottermarchi/Senado_Move/issues) com o
link da página e o que está errado.
