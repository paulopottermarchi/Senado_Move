/* Símbolos dos temas: um desenho para cada bloco (7) e para cada categoria (27) da classificação de temas (scripts/temas.py).
   Desenhos originais deste projeto: grade de 24 × 24, área útil de 20 × 20, traço de 1,75 com pontas e quinas arredondadas.
   Regras (CLAUDE.md, "Símbolos dos temas"):
   - UMA TINTA SÓ: o desenho usa a cor do texto (currentColor), no tema claro e no escuro. Nunca as cores do espectro, de partido ou de
     bloco de voto: tema não tem ideologia.
   - O NOME VAI JUNTO: o símbolo ajuda a achar o tema, quem diz qual é o tema é o texto. Por isso o desenho é decorativo (aria-hidden).
   - A PARTIR DE 16 px para categoria; o bloco usa um carimbo (quadrado arredondado na cor invertida) e a categoria fica sem fundo.
   Reconhece o nome oficial da categoria ("Mulheres e violência de gênero", de taxonomia.json) e o nome curto que vem em proposicoes.json e
   semana.json ("mulheres", o CURTO de temas.py), sem acento e sem diferença de caixa. Nome desconhecido não quebra nada: sai só o texto.
   Uso: TemaIcone.cat(nome, px) · TemaIcone.bloco(nome, px) · TemaIcone.rotulo(nome, texto, px) · TemaIcone.rotuloBloco(nome, texto, px) · TemaIcone.oficial(nome, px) e TemaIcone.rotuloOficial(nome, texto, px) para os 2 temas oficiais da Câmara. Carregar com <script src="temas.js"> (sem
   defer) antes do script da página, que pode desenhar assim que os dados chegam. O conjunto é um sprite inserido uma vez por página. */
window.TemaIcone=(function(){
  'use strict';
  // [área com tom de 16%, traço, cheio]: cada um é o `d` de um <path> (vários trechos juntos)
  var BLOCOS=[
    ['Políticas sociais',['M12 3L19.5 6V11.3C19.5 16 16.3 19.3 12 21C7.7 19.3 4.5 16 4.5 11.3V6Z','','M12 15.2C12 15.2 8.4 12.8 8.4 10.3A2 2 0 0 1 12 9.1A2 2 0 0 1 15.6 10.3C15.6 12.8 12 15.2 12 15.2Z']],
    ['Grupos e direitos',['','M9.5 8.2a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0Z M7 20V18.8A5 5 0 0 1 17 18.8V20 M2.9 10a1.9 1.9 0 1 0 3.8 0a1.9 1.9 0 1 0-3.8 0Z M2 19V18A3.4 3.4 0 0 1 5.2 14.6 M17.3 10a1.9 1.9 0 1 0 3.8 0a1.9 1.9 0 1 0-3.8 0Z M22 19V18A3.4 3.4 0 0 0 18.8 14.6','']],
    ['Estado, justiça e segurança',['M3.5 9.2L12 4L20.5 9.2Z','M7 12V17.5M12 12V17.5M17 12V17.5 M4 20.5H20','']],
    ['Economia e mercado',['M4.5 6.5H19.5A2 2 0 0 1 21.5 8.5V15.5A2 2 0 0 1 19.5 17.5H4.5A2 2 0 0 1 2.5 15.5V8.5A2 2 0 0 1 4.5 6.5Z','M9.5 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0Z M5.8 12h.01M18.2 12h.01','']],
    ['Infraestrutura e território',['','M2 16H22 M7 5.5V20.5M17 5.5V20.5 M2 16L7 5.5Q12 13 17 5.5L22 16 M10 8.6V16M14 8.6V16','']],
    ['Tecnologia',['M8 6H16A2 2 0 0 1 18 8V16A2 2 0 0 1 16 18H8A2 2 0 0 1 6 16V8A2 2 0 0 1 8 6Z','M9.8 9.8H14.2V14.2H9.8Z M9.5 3V6M14.5 3V6M9.5 18V21M14.5 18V21M3 9.5H6M3 14.5H6M18 9.5H21M18 14.5H21','']],
    ['Cultura e homenagens',['','M10 20.5C5.5 19 3.3 14.5 4.4 9.2 M14 20.5C18.5 19 20.7 14.5 19.6 9.2 M6.6 18.4L8.6 16.4M4.83 15.67L6.7 14.2M4.13 12.27L6 10.8M4.4 9.2L6.2 7.7 M17.4 18.4L15.4 16.4M19.17 15.67L17.3 14.2M19.87 12.27L18 10.8M19.6 9.2L17.8 7.7','M12 8.4L12.91 10.95L15.61 11.03L13.47 12.68L14.23 15.27L12 13.75L9.77 15.27L10.53 12.68L8.39 11.03L11.09 10.95Z']]
  ];
  // [nome oficial (taxonomia.json), nome curto (CURTO de temas.py), índice do bloco, desenho]
  var CATS=[
    ['Saúde','saúde',0,['M9.5 3.5H14.5V9.5H20.5V14.5H14.5V20.5H9.5V14.5H3.5V9.5H9.5Z','','']],
    ['Educação','educação',0,['M12 4.5L22 9.5L12 14.5L2 9.5Z','M6 12.2V16.3C6 17.7 8.7 19.2 12 19.2C15.3 19.2 18 17.7 18 16.3V12.2 M22 9.5V15','']],
    ['Trabalho, emprego e profissões','trabalho e emprego',0,['M10.5 11.3H13.5V14.3H10.5Z','M4.5 7.5H19.5A2 2 0 0 1 21.5 9.5V17.5A2 2 0 0 1 19.5 19.5H4.5A2 2 0 0 1 2.5 17.5V9.5A2 2 0 0 1 4.5 7.5Z M9 7.5V6A1.5 1.5 0 0 1 10.5 4.5H13.5A1.5 1.5 0 0 1 15 6V7.5 M2.5 12.8H21.5','']],
    ['Previdência e assistência social','previdência e assistência social',0,['M3 12.5A9 9 0 0 1 21 12.5A3 3 0 0 1 15 12.5A3 3 0 0 1 9 12.5A3 3 0 0 1 3 12.5Z','M12 15.5V19.5A2 2 0 0 1 8 19.5','']],
    ['Pessoa idosa e pessoa com deficiência','pessoas idosas e com deficiência',1,['','M7.5 4.8a2 2 0 1 0 4 0a2 2 0 1 0-4 0Z M6.8 21L8.8 14.2L9.5 8.2M8.8 14.2L11.8 21 M9.4 9.6L14.2 12.2 M14.5 21V11A2 2 0 0 1 18.5 11','']],
    ['Mulheres e violência de gênero','mulheres',1,['','M7.5 8.5a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0-9 0Z M12 13V21.5M8.5 17.5H15.5','']],
    ['Crianças e adolescentes','crianças e adolescentes',1,['','M4.5 13.5a7.5 7.5 0 1 0 15 0a7.5 7.5 0 1 0-15 0Z M12 6.2C12 4 13.6 2.7 15.6 3.2 M4.9 11.6A1.9 1.9 0 0 0 4.9 15.4M19.1 11.6A1.9 1.9 0 0 1 19.1 15.4 M9.3 12.4h.01M14.7 12.4h.01 M9.5 16.2Q12 18.4 14.5 16.2','']],
    ['Direitos humanos e igualdade','direitos humanos',1,['','M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0Z M8 10H16M8 14H16','']],
    ['Direito penal e segurança pública','segurança pública e direito penal',2,['M5.5 17.5H18.5A1 1 0 0 1 19.5 18.5V19.5A1 1 0 0 1 18.5 20.5H5.5A1 1 0 0 1 4.5 19.5V18.5A1 1 0 0 1 5.5 17.5Z','M7 17.5V13.5A5 5 0 0 1 17 13.5V17.5 M12 3V5.2M4.9 6.1L6.5 7.7M19.1 6.1L17.5 7.7 M10 14A2 2 0 0 1 12 12','']],
    ['Administração pública e servidores','administração pública',2,['','M5 5.5H19A2 2 0 0 1 21 7.5V16.5A2 2 0 0 1 19 18.5H5A2 2 0 0 1 3 16.5V7.5A2 2 0 0 1 5 5.5Z M6.8 10a1.7 1.7 0 1 0 3.4 0a1.7 1.7 0 1 0-3.4 0Z M5.8 16A2.7 2.7 0 0 1 11.2 16 M13.5 10H18M13.5 13.5H16.5','']],
    ['Direito civil, processo e justiça','justiça e direito civil',2,['','M12 3.5V20.5M8 20.5H16M4 7H20 M4 7L1.8 14M4 7L6.2 14M20 7L17.8 14M20 7L22.2 14 M1.8 14A2.2 1.8 0 0 0 6.2 14M17.8 14A2.2 1.8 0 0 0 22.2 14','']],
    ['Política, eleições e processo legislativo','eleições e processo legislativo',2,['M4.5 11.5H19.5A1.5 1.5 0 0 1 21 13V19.5A1.5 1.5 0 0 1 19.5 21H4.5A1.5 1.5 0 0 1 3 19.5V13A1.5 1.5 0 0 1 4.5 11.5Z','M8.5 11.5V4.5A1 1 0 0 1 9.5 3.5H14.5A1 1 0 0 1 15.5 4.5V11.5 M10.4 7.4L11.6 8.6L13.8 6 M9 15.5H15','']],
    ['Relações exteriores e soberania','relações exteriores',2,['','M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0Z M3 12H21 M12 3C9.5 5.2 7.5 8.3 7.5 12S9.5 18.8 12 21M12 3C14.5 5.2 16.5 8.3 16.5 12S14.5 18.8 12 21','']],
    ['Defesa, Forças Armadas e militares','defesa e militares',2,['','M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0Z','M12 7.1L13.23 10.6L16.95 10.69L14 12.95L15.06 16.51L12 14.4L8.94 16.51L10 12.95L7.05 10.69L10.77 10.6Z']],
    ['Economia, indústria, comércio e empreendedorismo','economia e empresas',3,['M4.5 20.5V11.5L9.8 14.5V11.5L15.1 14.5V8.5H19.5V20.5Z','M3 20.5H21 M8 18h.01M12 18h.01M17.3 18h.01','']],
    ['Sistema financeiro, crédito e pagamentos','crédito e sistema financeiro',3,['M4.5 5.5H19.5A2 2 0 0 1 21.5 7.5V16.5A2 2 0 0 1 19.5 18.5H4.5A2 2 0 0 1 2.5 16.5V7.5A2 2 0 0 1 4.5 5.5Z','M2.5 10H21.5 M6 15H10','']],
    ['Tributação e finanças públicas','tributos e contas públicas',3,['','M5 7.2a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0Z M14.6 16.8a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0Z M18.5 5.5L5.5 18.5','']],
    ['Consumidor','consumidor',3,['M5.5 8H18.5A1 1 0 0 1 19.5 8.9L20.4 19.5A1.5 1.5 0 0 1 18.9 21H5.1A1.5 1.5 0 0 1 3.6 19.5L4.5 8.9A1 1 0 0 1 5.5 8Z','M8.5 11V7A3.5 3.5 0 0 1 15.5 7V11','']],
    ['Propriedade intelectual','propriedade intelectual',3,['','M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0Z M15.2 9.6A3.6 3.6 0 1 0 15.2 14.4','']],
    ['Transporte, trânsito e mobilidade','transporte e trânsito',4,['M4.5 5.5H19.5A2 2 0 0 1 21.5 7.5V16H2.5V7.5A2 2 0 0 1 4.5 5.5Z','M5 8H9V11.5H5Z M10.5 8H14.5V11.5H10.5Z M16 8H19V11.5H16Z','M4.8 18.2a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0Z M14.8 18.2a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0Z']],
    ['Meio ambiente e animais','meio ambiente e animais',4,['M4.5 19.5C4 10 9.5 4.5 19.5 4C19 13.5 14 19.5 4.5 19.5Z','M4.5 19.5L13 11','M17 19.9a2 1.6 0 1 0 4 0a2 1.6 0 1 0-4 0Z M15.9 17.7a.85 .85 0 1 0 1.7 0a.85 .85 0 1 0-1.7 0Z M18.2 16.6a.85 .85 0 1 0 1.7 0a.85 .85 0 1 0-1.7 0Z M20.4 17.7a.85 .85 0 1 0 1.7 0a.85 .85 0 1 0-1.7 0Z']],
    ['Cidades, habitação e desenvolvimento urbano','cidades e habitação',4,['','M4.5 20.5V6A1 1 0 0 1 5.5 5H11.5A1 1 0 0 1 12.5 6V20.5 M12.5 20.5V10.5H18.5A1 1 0 0 1 19.5 11.5V20.5 M3 20.5H21 M8.5 9h.01M8.5 12.5h.01M8.5 16h.01M16 14h.01M16 17.5h.01','']],
    ['Energia, mineração, água e saneamento','energia, mineração e saneamento',4,['M9 3L3.5 13H8L6.8 21L12.5 10.5H8.4Z M17.5 7.5C17.5 7.5 13.5 12 13.5 15A4 4 0 0 0 21.5 15C21.5 12 17.5 7.5 17.5 7.5Z','','']],
    ['Agro e desenvolvimento rural','agropecuária',4,['M12 8.2C10.4 7 10.4 4.8 12 3C13.6 4.8 13.6 7 12 8.2Z M12 12.8C9 13.2 7.4 11.5 7.2 9C10.2 8.7 11.8 10.3 12 12.8Z M12 12.8C15 13.2 16.6 11.5 16.8 9C13.8 8.7 12.2 10.3 12 12.8Z M12 17.2C9 17.6 7.4 15.9 7.2 13.4C10.2 13.1 11.8 14.7 12 17.2Z M12 17.2C15 17.6 16.6 15.9 16.8 13.4C13.8 13.1 12.2 14.7 12 17.2Z','M12 21.5V9.5','']],
    ['Tecnologia, telecom e dados','tecnologia e dados',5,['','M9.2 14.7A4 4 0 0 1 14.8 14.7M6.3 11.8A8 8 0 0 1 17.7 11.8M3.5 9A12 12 0 0 1 20.5 9 M12 17.5h.01','']],
    ['Cultura, esporte e turismo','cultura, esporte e turismo',6,['M4.5 6.5H19.5A1.5 1.5 0 0 1 21 8V10A2 2 0 0 0 21 14V16A1.5 1.5 0 0 1 19.5 17.5H4.5A1.5 1.5 0 0 1 3 16V14A2 2 0 0 0 3 10V8A1.5 1.5 0 0 1 4.5 6.5Z','M14.5 8V9.2M14.5 11.4V12.6M14.5 14.8V16','']],
    ['Homenagens, datas e títulos','homenagens e datas comemorativas',6,['M6.8 9a5.2 5.2 0 1 0 10.4 0a5.2 5.2 0 1 0-10.4 0Z','M8.9 13.4L7 21L12 18.7L17 21L15.1 13.4 M10.3 9a1.7 1.7 0 1 0 3.4 0a1.7 1.7 0 1 0-3.4 0Z','']]
  ];

  // sem acento, sem caixa, espaços únicos: "Mulheres" e "mulheres" e "MULHERES" são a mesma coisa
  function norm(s){ return String(s==null?'':s).normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/\s+/g,' ').trim(); }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  var POR_CAT={}, POR_BLOCO={};
  CATS.forEach(function(c,i){ POR_CAT[norm(c[0])]=i; POR_CAT[norm(c[1])]=i; });
  BLOCOS.forEach(function(b,i){ POR_BLOCO[norm(b[0])]=i; });

  function caminhos(t){
    var a=' stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"', s='';
    if(t[0]) s+='<path d="'+t[0]+'" fill="currentColor" fill-opacity=".16"'+a+'/>';
    if(t[1]) s+='<path d="'+t[1]+'" fill="none"'+a+'/>';
    if(t[2]) s+='<path d="'+t[2]+'" fill="currentColor"/>';
    return s;
  }
  // o sprite entra uma vez por página, no começo do <body>: 0 × 0, fora do fluxo e escondido dos leitores de tela
  function sprite(){
    if(document.getElementById('ti-sprite')) return true;
    if(!document.body) return false;
    var s='<svg id="ti-sprite" width="0" height="0" style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true" focusable="false">';
    BLOCOS.forEach(function(b,i){ s+='<symbol id="ti-b'+i+'" viewBox="0 0 24 24">'+caminhos(b[1])+'</symbol>'; });
    CATS.forEach(function(c,i){ s+='<symbol id="ti-c'+i+'" viewBox="0 0 24 24">'+caminhos(c[3])+'</symbol>'; });
    document.body.insertAdjacentHTML('afterbegin',s+'</svg>');
    return true;
  }
  function desenho(tipo,i,px){
    var id='ti-'+tipo+i, dados=tipo==='b'?BLOCOS[i][1]:CATS[i][3];
    var interior=sprite()?'<use href="#'+id+'"></use>':caminhos(dados);      // sem <body> ainda (script muito cedo): o desenho vai inteiro
    return '<svg class="ti" viewBox="0 0 24 24" width="'+px+'" height="'+px+'" style="--ti:'+px+'px" aria-hidden="true" focusable="false">'+interior+'</svg>';
  }

  // O símbolo de uma categoria (16 px por padrão). Nome desconhecido: texto vazio.
  function cat(nome,px){
    var i=POR_CAT[norm(nome)]; return i==null?'':desenho('c',i,px||16);
  }
  // O carimbo de um bloco: quadrado arredondado na cor invertida do texto, com o desenho em negativo (28 px por padrão).
  function bloco(nome,px){
    var i=POR_BLOCO[norm(nome)]; if(i==null) return '';
    px=px||28;
    return '<span class="ti-carimbo" style="--tc:'+px+'px" aria-hidden="true">'+desenho('b',i,Math.round(px*.62))+'</span>';
  }
  // Símbolo + nome, o nome quebrando em linhas sem passar por baixo do desenho. `texto` é texto puro (aqui é escapado); sem ele vale o nome.
  // Categoria desconhecida: sai só o texto escapado, como era antes.
  function rotulo(nome,texto,px){
    var t=esc(texto==null?nome:texto), ic=cat(nome,px);
    return ic?'<span class="ti-rot">'+ic+'<span>'+t+'</span></span>':t;
  }

  // Os temas OFICIAIS da Câmara (contexto.html: Economia e Finanças Públicas e Orçamento) não são as 27 categorias deste site, mas têm o
  // mesmo assunto de duas delas: emprestam o desenho pelo nome, sem fundo. Economia usa o desenho do bloco "Economia e mercado" (a nota de
  // dinheiro, mais fiel ao tema amplo que a fábrica); Finanças Públicas e Orçamento, o de "Tributação e finanças públicas". É escolha do
  // site para o desenho, não equivalência entre as classificações; o nome oficial vai sempre ao lado.
  var OFICIAIS={}; OFICIAIS[norm('Economia')]=['b',3]; OFICIAIS[norm('Finanças Públicas e Orçamento')]=['c',16];
  function oficial(nome,px){
    var o=OFICIAIS[norm(nome)]; return o?desenho(o[0],o[1],px||16):'';
  }
  function rotuloOficial(nome,texto,px){
    var t=esc(texto==null?nome:texto), ic=oficial(nome,px);
    return ic?'<span class="ti-rot">'+ic+'<span>'+t+'</span></span>':t;
  }

  // Carimbo do bloco + nome (para títulos de seção). Bloco desconhecido: só o texto escapado.
  function rotuloBloco(nome,texto,px){
    var t=esc(texto==null?nome:texto), ic=bloco(nome,px);
    return ic?'<span class="ti-rot ti-rot-b">'+ic+'<span>'+t+'</span></span>':t;
  }

  // ---------- botão "Ver as proposições por tema" (estilo.css: .btn-tema) ----------
  // A porta de entrada da página por tema de um parlamentar. Os símbolos empilhados são os das 3 categorias em que ele mais tem proposições
  // (só auxílio: o texto do botão diz tudo e o desenho é aria-hidden). Sem as categorias (ainda não carregadas), o botão sai sem os símbolos.
  var SETA='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
  function pilha(nomes){
    return (nomes||[]).map(function(n){ var s=cat(n,18); return s?'<span class="bt-ic">'+s+'</span>':''; }).join('');
  }
  // [{c:[[índice, termo, fonte]]}] → nomes das `n` categorias com mais proposições (empate: a primeira da taxonomia)
  function maisFrequentes(props,categorias,n){
    var k=categorias.map(function(){ return 0; });
    (props||[]).forEach(function(p){ (p.c||[]).forEach(function(c){ k[c[0]]++; }); });
    return k.map(function(v,i){ return [v,i]; }).filter(function(x){ return x[0]>0; })
      .sort(function(a,b){ return b[0]-a[0]||a[1]-b[1]; }).slice(0,n||3).map(function(x){ return categorias[x[1]]; });
  }
  function resumoDoBotao(props,categorias){
    var n=(props||[]).length, usadas={};
    (props||[]).forEach(function(p){ (p.c||[]).forEach(function(c){ usadas[c[0]]=1; }); });
    var t=Object.keys(usadas).length;
    return n+(n===1?' proposição':' proposições')+(t?' · '+t+(t===1?' tema':' temas'):'');
  }
  // {href, titulo, sub, nomes, id}: o HTML do botão. `nomes` pode vir vazio e ser preenchido depois com preencherBotao.
  function botao(o){
    return '<a class="btn-tema"'+(o.id?' id="'+esc(o.id)+'"':'')+' href="'+esc(o.href)+'">'+
      '<span class="bt-ics" aria-hidden="true">'+pilha(o.nomes)+'</span>'+
      '<span class="bt-txt"><b>'+esc(o.titulo||'Ver as proposições por tema')+'</b>'+(o.sub?'<small>'+esc(o.sub)+'</small>':'')+'</span>'+
      '<span class="bt-seta" aria-hidden="true">'+SETA+'</span></a>';
  }
  // Preenche os símbolos e o total de um botão já na página, a partir do arquivo de proposições do parlamentar (dados/temas/{id}.json ou
  // dados/senadores/{id}.json: as duas têm props[].c). Falha em silêncio: o botão continua funcionando sem os símbolos.
  var _tax=null;
  function taxonomia(){
    if(!_tax) _tax=fetch('dados/temas/taxonomia.json',{cache:'no-cache'}).then(function(r){ return r.ok?r.json():null; }).catch(function(){ return null; });
    return _tax;
  }
  function preencherBotao(el,urlProps){
    if(!el) return Promise.resolve();
    // o mesmo botão serve a fichas de parlamentares diferentes: volta ao estado sem símbolos e ignora resposta de outro arquivo que chegue atrasada
    el.dataset.props=urlProps;
    var ics0=el.querySelector('.bt-ics'); if(ics0) ics0.innerHTML='';
    return Promise.all([taxonomia(),fetch(urlProps,{cache:'no-cache'}).then(function(r){ return r.ok?r.json():null; }).catch(function(){ return null; })])
      .then(function(r){
        var tax=r[0], d=r[1]; if(!tax||!d||!d.props||!el.isConnected||el.dataset.props!==urlProps) return;
        var ics=el.querySelector('.bt-ics'), sub=el.querySelector('.bt-txt small');
        if(ics) ics.innerHTML=pilha(maisFrequentes(d.props,tax.categorias,3));
        if(sub) sub.textContent=resumoDoBotao(d.props,tax.categorias);
      });
  }

  return {cat:cat,bloco:bloco,rotulo:rotulo,rotuloBloco:rotuloBloco,oficial:oficial,rotuloOficial:rotuloOficial,
          botao:botao,pilha:pilha,maisFrequentes:maisFrequentes,resumoDoBotao:resumoDoBotao,preencherBotao:preencherBotao,
          categorias:CATS.map(function(c){ return c[0]; }), blocos:BLOCOS.map(function(b){ return b[0]; })};
})();
