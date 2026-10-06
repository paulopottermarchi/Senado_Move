// Câmara Aberta — comportamento comum a todas as páginas (carregado com defer).
(() => {
  // No celular a barra rola de lado: abre já mostrando o item da página atual.
  const atual = document.querySelector('.nav a[aria-current]');
  if (atual) {
    const nav = atual.parentElement;
    nav.scrollLeft += atual.getBoundingClientRect().left - nav.getBoundingClientRect().left - 32;
  }

  // Entrada suave dos blocos fixos da página (não das listas que os filtros redesenham), com inView e stagger do Motion
  // (window.Motion, de motion-init.js). Sem JS, sem Motion ou com movimento reduzido nada fica escondido: o CSS só esconde
  // enquanto html não tem .m-ok, e a animação m-seguranca do estilo.css solta tudo depois de 3,5 s se ninguém assumir.
  const html = document.documentElement, M = window.Motion;
  const soltar = () => html.classList.add('m-ok');
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !M || !M.animate || !M.inView || !M.stagger) { soltar(); return; }
  try {
    // os mesmos alvos que o estilo.css esconde; a abertura da página inicial (.hero) se anima sozinha
    const alvos = [...document.querySelectorAll(
      '.capa > :not(.hero):not(.explora), .explora > li, .painel, .semana, .votos, .mapa, .rodape-site .wrap, .rg > *')];
    // o estado inicial passa para estilo em linha antes de o CSS soltar: nenhum quadro mostra o bloco pronto e depois o esconde
    for (const el of alvos) { el.dataset.m = ''; el.style.opacity = '0'; el.style.transform = 'translateY(12px)'; }
    if (!document.querySelector('.hero')) soltar();       // com abertura animada, quem solta é ela, depois de pôr o estado inicial dela
    const base = M.stagger(0.06);
    let fila = [], agendado = false;
    const revelar = () => {
      agendado = false;
      const lote = fila; fila = [];
      // terminada a entrada, a peça volta ao normal (hover, sticky e transform livres)
      const limpar = () => lote.forEach(el => { el.style.removeProperty('opacity'); el.style.removeProperty('transform'); el.removeAttribute('data-m'); });
      try {
        M.animate(lote, { opacity: [0, 1], y: [12, 0] },
          { duration: 0.6, ease: [0.4, 0, 0.2, 1], delay: (i, n) => Math.min(base(i, n), 0.3) }).finished.then(limpar, limpar);
      } catch (e) { limpar(); }
    };
    // o que entra na tela no mesmo instante vai junto, em cascata (60 ms entre um e outro, até 300 ms)
    M.inView(alvos, el => { fila.push(el); if (!agendado) { agendado = true; queueMicrotask(revelar); } },
      { margin: '0px 0px -6% 0px' });
  } catch (e) {
    document.querySelectorAll('[data-m]').forEach(el => { el.style.removeProperty('opacity'); el.style.removeProperty('transform'); el.removeAttribute('data-m'); });
    soltar();
  }
})();

// Logos dos partidos (dados/partidos/logos.json, gerado por logos.py). A página só referencia o endereço da
// imagem, como faz com as fotos dos deputados; nenhuma cópia fica neste site. Sigla sem logo seguro não mostra nada.
window.Bandeiras = (() => {
  let dados = null, pronta = null;
  const carregar = () => pronta || (pronta = fetch('dados/partidos/logos.json', { cache: 'no-cache' })
    .then(r => (r.ok ? r.json() : null)).then(j => (dados = (j && j.partidos) || {}))
    .catch(() => (dados = {})));
  // leg: id da legislatura (49 a 57) ou 'todas'. Em 'todas' só vale o logo que não tem limite de época.
  const entrada = (sigla, leg) => {
    const es = dados && dados[sigla];
    if (!es) return null;
    if (leg === 'todas' || leg == null) return es.length === 1 && es[0].de == null && es[0].ate == null ? es[0] : null;
    return es.find(e => (e.de == null || leg >= e.de) && (e.ate == null || leg <= e.ate)) || null;
  };
  const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // px = altura do logo; sem logo devolve um espaço do mesmo tamanho (alinha a coluna) ou '' com {vazio:false}
  // O logo fica num espaço de largura fixa: o nome do partido começa no mesmo ponto em todas as linhas, e um logo
  // largo (um nome escrito) encolhe dentro do espaço em vez de empurrar o texto.
  const img = (sigla, leg, px = 20, opcoes = {}) => {
    const e = entrada(sigla, leg), w = Math.round(px * 1.5);
    const slot = `<span class="bandeira-slot" style="width:${w}px;height:${px}px">`;
    if (!e) return opcoes.vazio === false ? '' : slot + '</span>';
    return slot + `<img class="bandeira" src="${esc(e.u)}" alt="" title="Logo do ${esc(sigla)}" loading="lazy" decoding="async" onerror="this.remove()"></span>`;
  };
  const creditos = () => Object.entries(dados || {}).flatMap(([sigla, es]) =>
    es.filter(e => e.f === 'commons').map(e => ({ sigla, arquivo: e.a, pagina: e.p, licenca: e.l })))
    .sort((a, b) => a.sigla.localeCompare(b.sigla));
  return { carregar, entrada, img, creditos };
})();

// Espectro político: as cores, os nomes das faixas, as regiões e a geometria do hemiciclo, comuns às páginas que
// mostram a composição (senadores) ou o espectro por região (inicial). A cor é CONTÍNUA ao longo do score: as cinco
// faixas servem só à legenda e aos filtros, nunca para colorir nem para rotular uma pessoa (CLAUDE.md).
window.Espectro = (() => {
  const CORES = { 'esquerda': '#d85a30', 'centro-esquerda': '#b88f5a', 'centro': '#888780',
                  'centro-direita': '#4a7a9f', 'direita': '#0c447c', 'sem-classificacao': '#c8cace' };
  const NOMES = { 'esquerda': 'Esquerda', 'centro-esquerda': 'Centro-esquerda', 'centro': 'Centro',
                  'centro-direita': 'Centro-direita', 'direita': 'Direita', 'sem-classificacao': 'Sem classificação' };
  const PARADAS = [[1, [216, 90, 48]], [3.25, [184, 143, 90]], [5.5, [136, 135, 128]], [7.75, [74, 122, 159]], [10, [12, 68, 124]]];
  const corDoScore = s => {
    if (s == null) return CORES['sem-classificacao'];
    s = Math.max(1, Math.min(10, s));
    for (let i = 0; i < PARADAS.length - 1; i++) {
      const [a, ca] = PARADAS[i], [b, cb] = PARADAS[i + 1];
      if (s <= b) {
        const t = (s - a) / (b - a);
        return '#' + ca.map((v, j) => Math.round(v + (cb[j] - v) * t).toString(16).padStart(2, '0')).join('');
      }
    }
    return '#0c447c';
  };
  const REGIOES = { N: ['AC', 'AP', 'AM', 'PA', 'RO', 'RR', 'TO'], NE: ['AL', 'BA', 'CE', 'MA', 'PB', 'PE', 'PI', 'RN', 'SE'],
                    CO: ['DF', 'GO', 'MT', 'MS'], SE: ['ES', 'MG', 'RJ', 'SP'], S: ['PR', 'RS', 'SC'] };
  const REG_NOME = { N: 'Norte', NE: 'Nordeste', CO: 'Centro-Oeste', SE: 'Sudeste', S: 'Sul' };
  const UF_REG = {};
  for (const r in REGIOES) REGIOES[r].forEach(u => { UF_REG[u] = r; });
  // Assentos num hemiciclo, em ordem da esquerda para a direita: fileiras de raio crescente, cada uma com uma cota de
  // assentos proporcional ao raio (a maior fica por último).
  const assentos = (total, fileiras, r0, r1) => {
    const raios = [], pesos = [];
    for (let i = 0; i < fileiras; i++) { const r = r0 + (r1 - r0) * (i / (fileiras - 1)); raios.push(r); pesos.push(r); }
    const soma = pesos.reduce((a, b) => a + b, 0);
    const cotas = pesos.map(p => Math.floor(total * p / soma));
    const resto = total - cotas.reduce((a, b) => a + b, 0);
    const sobras = pesos.map((p, i) => [total * p / soma - cotas[i], i]).sort((a, b) => b[0] - a[0]);
    for (let k = 0; k < resto; k++) cotas[sobras[k % sobras.length][1]]++;
    const pts = [];
    cotas.forEach((q, i) => {
      const r = raios[i];
      for (let j = 0; j < q; j++) {
        const t = q === 1 ? 0.5 : j / (q - 1), ang = Math.PI - t * Math.PI;      // pi = esquerda, 0 = direita
        pts.push({ ang, r, x: 150 + r * Math.cos(ang), y: 150 - r * Math.sin(ang) });
      }
    });
    return pts.sort((a, b) => b.ang - a.ang);
  };
  return { CORES, NOMES, PARADAS, corDoScore, REGIOES, REG_NOME, UF_REG, assentos };
})();
