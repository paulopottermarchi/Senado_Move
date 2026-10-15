// Câmara Aberta — "De onde vêm as leis": o que as três páginas que mostram a origem das leis compartilham (a barra de composição, o texto
// gerado dos números e a linha da ficha do deputado). TODO número vem de dados/origem_leis.json (scripts/origem_leis.py); nada aqui é escrito à
// mão, e o texto nunca traz conclusão pronta: se o número mudar, a frase acompanha. Carregado com defer.
window.OrigemLeis = (() => {
  'use strict';

  const FRASE_FIXA = 'A origem é a autoria da proposição que virou lei. Não indica quem a aprovou nem quem mais contribuiu para o texto final.';
  const nfInt = new Intl.NumberFormat('pt-BR');
  const nfPct = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const num = v => nfInt.format(v);
  const pct = (q, t) => (t ? nfPct.format(100 * q / t) + '%' : '—');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, um, varios) => (n === 1 ? um : varios);
  // "1º/2/2023" (texto corrido) e "07/01/2026" (tabelas)
  const dataLonga = iso => { if (!iso) return '—'; const [a, m, d] = iso.slice(0, 10).split('-'); return `${+d === 1 ? '1º' : +d}/${+m}/${a}`; };
  const dataCurta = iso => (iso ? iso.slice(0, 10).split('-').reverse().join('/') : '—');

  // Rótulo curto para dentro da barra (a legenda sempre traz o nome inteiro, que vem do JSON).
  const CURTO = {
    'Ministério Público, Defensoria e controle': 'MP, Defensoria e controle',
    'Órgãos colegiados da Câmara (Mesa e comissões)': 'Mesa e comissões da Câmara'
  };
  const CURTO_VIA = ['Projeto de lei', 'MP convertida', 'PLN'];          // mesma ordem de `vias` no JSON
  const TEXTO_DIRETO = new Set([0, 1, 3]);                                // cor lisa com contraste garantido para texto direto; as demais levam o rótulo numa pílula

  // Como a lei foi ligada à proposição (uma via por lei, a que decidiu). A descrição fala a língua de quem lê.
  const VIA = {
    ligacao_direta: { rot: 'Mesmo número nas duas casas', desc: 'O Senado registra a lei com o mesmo identificador da proposição na Câmara: projeto de lei e PLP desde a numeração única de 2019, MP e PLN sempre.' },
    fluxo: { rot: 'Número da Câmara guardado pelo Senado', desc: 'O processo do Senado guarda o número que a proposição tem na Câmara, ou a Câmara cita o número do projeto do Senado ao recebê-lo. É o mesmo vínculo da página “Entre as casas”.' },
    numero_lei_camara: { rot: 'Número da lei registrado pela Câmara', desc: 'O Senado não traz o número da Câmara, mas a própria Câmara registra, numa única proposição, que ela virou aquela lei.' },
    excecao_curada: { rot: 'Exceção curada', desc: 'A Câmara registra a lei com o número de outra; com o link do Planalto (número e data) e o da ficha da proposição (autoria), vale o número do Planalto e do Senado.' },
    so_camara: { rot: 'Só a Câmara registra a lei', desc: 'A lei não está no registro do Senado: entra com a data do registro da Câmara, marcada “data não confirmada pelo Senado”.' }
  };

  let carga = null;
  // JSON ou null: arquivo que falta, ou que não fecha as próprias contas, nunca derruba a página (só tira o que dependia dele).
  const valido = d => {
    try {
      const soma = o => Object.values(o.categorias).reduce((a, b) => a + b, 0);
      const c = d.composicao.comPLN, s = d.composicao.semPLN;
      if (!Array.isArray(d.categorias) || !Array.isArray(d.vias) || soma(c) !== c.total || soma(s) !== s.total || s.total > c.total) return null;
      return d;
    } catch (e) { return null; }
  };
  const carregar = () => carga || (carga = fetch('dados/origem_leis.json', { cache: 'no-cache' })
    .then(r => (r.ok ? r.json() : null)).catch(() => null).then(valido));

  // Os números que os textos usam, todos derivados do JSON.
  const resumo = d => {
    const c = d.composicao.comPLN, s = d.composicao.semPLN, cat = c.categorias;
    return {
      N: c.total, M: s.total, pln: c.total - s.total,
      dep: cat['Deputados'] || 0, dep2: s.categorias['Deputados'] || 0,
      nc: cat['Não classificado'] || 0, divergentes: (d.totais && d.totais.divergentes) || 0,
      inicio: d.legislatura.inicio, ultima: d.legislatura.ultimaLei, gerado: d._gerado
    };
  };

  // "Das N leis sancionadas nesta legislatura, X% nasceram em deputados. Sem as PLN (créditos e orçamento), Y%." Os dois totais, sem escolher
  // uma versão como manchete.
  const textoPrincipal = d => {
    const r = resumo(d);
    if (!r.N) return 'Ainda não há lei sancionada nesta legislatura.';
    let t = `Das <b>${num(r.N)}</b> ${plural(r.N, 'lei sancionada', 'leis sancionadas')} nesta legislatura, <b>${pct(r.dep, r.N)}</b> nasceram em deputados.`;
    if (r.pln > 0) t += ` Sem as <b>${num(r.pln)}</b> ${plural(r.pln, 'lei de PLN', 'leis de PLN')} (créditos e orçamento), são <b>${pct(r.dep2, r.M)}</b> das <b>${num(r.M)}</b>.`;
    return t;
  };

  // Os segmentos da barra: categorias com algo, o Executivo como um grupo de três caminhos. Misto, Outros e Não classificado só entram se > 0.
  const segmentos = d => {
    const c = d.composicao.comPLN, lista = [];
    d.categorias.forEach((nome, i) => {
      const q = c.categorias[nome] || 0;
      if (!q) return;
      if (nome === 'Executivo') {
        const filhos = d.vias.map((v, j) => ({ nome: v, curto: CURTO_VIA[j] || v, q: c.executivoPorVia[v] || 0, cls: 'or-e' + j, pilula: j > 0 })).filter(f => f.q > 0);
        lista.push({ grupo: true, nome, q, filhos });
      } else {
        lista.push({ nome, curto: CURTO[nome] || nome, q, cls: 'or-c' + i, pilula: !TEXTO_DIRETO.has(i) });
      }
    });
    return lista;
  };

  // Texto alternativo da barra, com todos os valores.
  const textoAlternativo = (d, segs) => {
    const t = d.composicao.comPLN.total, nc = (d.composicao.comPLN.categorias['Não classificado'] || 0);
    const leis = q => `${num(q)} ${plural(q, 'lei', 'leis')}, ${pct(q, t)}`;
    const partes = segs.map(s => (s.grupo
      ? `${s.nome}: ${leis(s.q)}, sendo ${s.filhos.map(f => `${num(f.q)} por ${f.curto === 'Projeto de lei' ? 'projeto de lei' : f.curto === 'MP convertida' ? 'medida provisória convertida' : 'PLN'}`).join(', ')}`
      : `${s.nome}: ${leis(s.q)}`));
    return `Composição das ${num(t)} leis sancionadas por origem. ${partes.join('. ')}.${nc ? '' : ' Não classificado: nenhum caso.'}`;
  };

  const tituloSeg = (s, total, pai) => `${pai ? pai + ' · ' : ''}${s.nome}: ${num(s.q)} ${plural(s.q, 'lei', 'leis')} (${pct(s.q, total)})`;
  const segHTML = (s, total, pai) =>
    `<span class="or-s ${s.cls}${s.pilula ? ' pilula' : ''}" style="--n:${s.q}" title="${esc(tituloSeg(s, total, pai))}">` +
    `<span class="or-r"><span class="rt">${esc(s.curto)}</span> <span class="rv">${pct(s.q, total)}</span></span></span>`;

  const barraHTML = (d, segs) => {
    const t = d.composicao.comPLN.total;
    const itens = segs.map(s => (s.grupo
      ? `<span class="or-g" style="--n:${s.q}"><span class="or-gt"><span class="gn">${esc(s.nome)}</span><span class="gv"> · ${num(s.q)} · ${pct(s.q, t)}</span></span>${s.filhos.map(f => segHTML(f, t, s.nome)).join('')}</span>`
      : segHTML(s, t))).join('');
    return `<div class="or-barra-box"><div class="or-barra" role="img" aria-label="${esc(textoAlternativo(d, segs))}">${itens}</div></div>`;
  };

  const legendaHTML = (d, segs) => {
    const t = d.composicao.comPLN.total;
    const item = (cls, nome, q, extra) =>
      `<li><i class="or-sw ${cls}" aria-hidden="true"></i><span><b>${esc(nome)}</b> <span class="or-n">${num(q)} ${plural(q, 'lei', 'leis')} · ${pct(q, t)}</span>${extra || ''}</span></li>`;
    return `<ul class="or-leg" aria-label="Legenda, com o número de leis de cada origem">` + segs.map(s => (s.grupo
      ? `<li><i class="or-sw vazio" aria-hidden="true"></i><span><b>${esc(s.nome)}</b> <span class="or-n">${num(s.q)} ${plural(s.q, 'lei', 'leis')} · ${pct(s.q, t)}</span></span>` +
        `<ul>${s.filhos.map(f => item(f.cls, f.nome, f.q)).join('')}</ul></li>`
      : item(s.cls, s.nome, s.q))).join('') + '</ul>';
  };

  // Legenda compacta (a faixa dos deputados e o achado da página inicial): uma linha corrida, o Executivo achatado nos três caminhos.
  const legendaCompacta = (d, segs) => {
    const t = d.composicao.comPLN.total;
    const item = (cls, nome, q) =>
      `<li><i class="or-sw ${cls}" aria-hidden="true"></i><span><b>${esc(nome)}</b> <span class="or-n">${num(q)} · ${pct(q, t)}</span></span></li>`;
    const itens = [];
    segs.forEach(s => {
      if (s.grupo) s.filhos.forEach(f => itens.push(item(f.cls, `${s.nome}: ${f.curto}`, f.q)));
      else itens.push(item(s.cls, s.curto, s.q));
    });
    return `<ul class="or-leg" aria-label="Legenda, com o número de leis de cada origem">${itens.join('')}</ul>`;
  };

  // Os rótulos dentro dos segmentos só aparecem onde cabem: rótulo e valor, depois só o valor, depois nada (a legenda e o `title` dizem tudo).
  const ajustar = raiz => {
    const barra = raiz.querySelector('.or-barra');
    if (!barra) return;
    const roda = () => {
      barra.querySelectorAll('.or-s').forEach(s => {
        s.classList.remove('so-valor', 'sem-rotulo');
        const r = s.firstElementChild, cabe = () => r.offsetWidth <= s.clientWidth - 1;
        if (!cabe()) { s.classList.add('so-valor'); if (!cabe()) s.classList.add('sem-rotulo'); }
      });
      // a legenda do grupo (Executivo) perde o valor, não o nome, quando o grupo é estreito
      // (e o nome também, se nem ele couber: fica o colchete, que liga os três caminhos, e a legenda diz o resto)
      barra.querySelectorAll('.or-gt').forEach(t => {
        t.classList.remove('curto', 'oculto');
        if (t.scrollWidth > t.clientWidth + 1) { t.classList.add('curto'); if (t.scrollWidth > t.clientWidth + 1) t.classList.add('oculto'); }
      });
    };
    roda();
    if (window.ResizeObserver && !raiz._ro) { raiz._ro = new ResizeObserver(roda); raiz._ro.observe(barra); }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(roda);
  };

  // Monta o componente inteiro em `el`: o texto gerado, a barra, a legenda, a nota de "nenhum caso" e a frase fixa.
  //   opc.compacta  legenda numa linha e rodapé curto (faixa dos deputados, achado da inicial); sem isso, a versão completa (origem.html)
  //   opc.nota      HTML de uma nota a mais (ex.: o escopo da faixa na página dos deputados)
  //   opc.link      endereço do detalhe (vira "Ver o detalhe" ao lado da frase fixa)
  const montar = (el, d, opc = {}) => {
    const r = resumo(d), segs = segmentos(d), c = !!opc.compacta;
    const semNC = r.nc ? '' : (c ? ' <b>Não classificado:</b> nenhum caso.'
      : '<p class="or-nota"><b>Não classificado:</b> nenhum caso. Toda lei tem a proposição de origem ligada por identificador exato e o autor na tabela de órgãos.</p>');
    const corpo = r.N ? barraHTML(d, segs) + (c ? legendaCompacta(d, segs) : legendaHTML(d, segs)) + (c ? '' : semNC) : '';
    const aviso = r.divergentes
      ? `<p class="or-nota">${num(r.divergentes)} ${plural(r.divergentes, 'lei ficou fora da conta', 'leis ficaram fora da conta')} por divergência entre as fontes${opc.link ? '; o detalhe lista' : ''}.</p>` : '';
    const link = opc.link ? ` <a href="${esc(opc.link)}">Ver o detalhe: por ano, por instituição e lei a lei →</a>` : '';
    el.innerHTML = `<div class="or${c ? ' compacta' : ''}" role="group" aria-label="De onde vêm as leis sancionadas nesta legislatura">` +
      `<p class="or-txt">${textoPrincipal(d)}</p>${corpo}${aviso}${opc.nota ? `<p class="or-nota">${opc.nota}</p>` : ''}` +
      `<p class="or-pe">${c ? semNC.trim() + (semNC ? ' ' : '') : ''}${esc(FRASE_FIXA)}${link}</p></div>`;
    ajustar(el);
  };

  // A linha da ficha do deputado: o fato do CONJUNTO, nunca uma média por deputado.
  const linhaFicha = (d, link) => {
    const r = resumo(d);
    if (!r.N) return '';
    return `Das ${num(r.N)} ${plural(r.N, 'lei sancionada', 'leis sancionadas')} nesta legislatura, ${pct(r.dep, r.N)} nasceram em deputados` +
      (r.pln > 0 ? ` (${pct(r.dep2, r.M)} sem as PLN)` : '') + '. É o conjunto de todos os deputados, não uma média por deputado.' +
      (link ? ` <a href="${esc(link)}">De onde vêm as leis</a>` : '');
  };

  return { FRASE_FIXA, VIA, carregar, valido, resumo, textoPrincipal, segmentos, montar, linhaFicha, ajustar, esc, num, pct, plural, dataLonga, dataCurta };
})();
