// Câmara Aberta — comportamento comum a todas as páginas (carregado com defer).
(() => {
  // No celular a barra rola de lado: abre já mostrando o item da página atual.
  const atual = document.querySelector('.nav a[aria-current]');
  if (atual) {
    const nav = atual.parentElement;
    nav.scrollLeft += atual.getBoundingClientRect().left - nav.getBoundingClientRect().left - 32;
  }

  // Entrada suave dos blocos fixos da página (não das listas que os filtros redesenham).
  // Sem JS, ou com movimento reduzido, nada fica escondido: a classe só entra aqui.
  if (!('IntersectionObserver' in window) ||
      matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const alvos = [...document.querySelectorAll(
    '.capa > *, .painel, .semana, .votos, .mapa, .rodape-site .wrap, .rg > *')];
  const io = new IntersectionObserver(entradas => {
    let atraso = 0;
    for (const e of entradas) {
      if (!e.isIntersecting) continue;
      const el = e.target;
      io.unobserve(el);
      el.style.animationDelay = `${atraso}ms`;
      atraso = Math.min(atraso + 60, 300);
      el.classList.add('visivel');
      // terminada a entrada, a peça volta ao normal (hover, sticky e transform livres)
      el.addEventListener('animationend', () => {
        el.classList.remove('revela', 'visivel');
        el.style.animationDelay = '';
      }, { once: true });
    }
  }, { rootMargin: '0px 0px -6% 0px' });
  for (const el of alvos) { el.classList.add('revela'); io.observe(el); }
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
