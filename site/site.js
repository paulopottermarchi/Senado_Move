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
