/* Mapa de assentos do plenário do Senado, em 3D, para senador.html (window.MapaAssentos).

   De onde vem a disposição: o "Mapa de Assentos no Plenário" do perfil de cada senador no site do Senado
   (www25.senado.leg.br/web/senadores/senador/-/perfil/{código}), conferido em 15/10/2026. O mapa oficial é um desenho
   fixo de 84 cadeiras e a Mesa; em cada perfil ele só destaca as 3 cadeiras do ESTADO (os três senadores de um estado
   veem o mesmo destaque), e a API de Dados Abertos do Senado não tem nada sobre assento. Por isso este módulo marca a
   BANCADA DO ESTADO e nunca uma cadeira individual: a cadeira de cada senador não é publicada, e inventá-la seria dado falso.

   A disposição, tirada do desenho oficial: 6 fileiras de 14 cadeiras, com um corredor no meio (7 de cada lado). Os estados
   vêm em ordem alfabética, 3 cadeiras cada, em serpentina: a fileira 1 da esquerda para a direita, a 2 da direita para a
   esquerda, e assim por diante. As 3 últimas cadeiras (fileira de trás, à esquerda) não têm estado no desenho. Se o Senado
   refizer o mapa, é a tabela UFS e a serpentina de CADEIRAS que mudam.

   Como desenha: projeção em perspectiva feita aqui mesmo e pintada em SVG (algoritmo do pintor), sem Three.js, sem WebGL e
   sem baixar nada: a imagem parada e a animada são a mesma, não há troca de uma pela outra. No 3D a câmera faz um vaivém leve
   (±8° em 18 s) que para fora da tela, com a aba oculta, com "reduzir movimento" e em aparelho fraco; na planta (vista de
   cima) a imagem fica parada. A cor é de interface (turquesa e neutros): nunca as cores do espectro, porque o mapa não é ideologia. */
(function () {
  'use strict';

  const UFS = [['AC', 'Acre', 'do Acre'], ['AL', 'Alagoas', 'de Alagoas'], ['AP', 'Amapá', 'do Amapá'], ['AM', 'Amazonas', 'do Amazonas'],
    ['BA', 'Bahia', 'da Bahia'], ['CE', 'Ceará', 'do Ceará'], ['DF', 'Distrito Federal', 'do Distrito Federal'],
    ['ES', 'Espírito Santo', 'do Espírito Santo'], ['GO', 'Goiás', 'de Goiás'], ['MA', 'Maranhão', 'do Maranhão'],
    ['MT', 'Mato Grosso', 'de Mato Grosso'], ['MS', 'Mato Grosso do Sul', 'de Mato Grosso do Sul'], ['MG', 'Minas Gerais', 'de Minas Gerais'],
    ['PA', 'Pará', 'do Pará'], ['PB', 'Paraíba', 'da Paraíba'], ['PR', 'Paraná', 'do Paraná'], ['PE', 'Pernambuco', 'de Pernambuco'],
    ['PI', 'Piauí', 'do Piauí'], ['RJ', 'Rio de Janeiro', 'do Rio de Janeiro'], ['RN', 'Rio Grande do Norte', 'do Rio Grande do Norte'],
    ['RS', 'Rio Grande do Sul', 'do Rio Grande do Sul'], ['RO', 'Rondônia', 'de Rondônia'], ['RR', 'Roraima', 'de Roraima'],
    ['SC', 'Santa Catarina', 'de Santa Catarina'], ['SP', 'São Paulo', 'de São Paulo'], ['SE', 'Sergipe', 'de Sergipe'], ['TO', 'Tocantins', 'do Tocantins']];
  const NOME = {}, DE = {};
  UFS.forEach(x => { NOME[x[0]] = x[1]; DE[x[0]] = x[2]; });

  // 84 cadeiras: fileira r (0 = a mais perto da Mesa), coluna c (0 a 13), posição u (−7…−1 à esquerda do corredor, 1…7 à direita)
  const CADEIRAS = [];
  for (let s = 0; s < 84; s++) {
    const r = Math.floor(s / 14), p = s % 14;
    const c = (r % 2 === 0) ? p : 13 - p;
    const g = Math.floor(s / 3);
    CADEIRAS.push({ r, u: c < 7 ? c - 7 : c - 6, uf: g < 27 ? UFS[g][0] : 'SEM' });
  }
  const zDe = (r, u) => 5.3 + r * 1.92 - 0.0238 * Math.pow(Math.abs(u) - 1, 2);
  const yDe = r => 0.32 + r * 0.42;

  /** Onde ficam as 3 cadeiras de um estado, em palavras (da própria geometria). null se a sigla não é de um estado. */
  function descrever(uf) {
    if (!NOME[uf]) return null;
    const as = CADEIRAS.filter(a => a.uf === uf).sort((a, b) => a.r - b.r || a.u - b.u);
    const filas = [];
    as.forEach(a => { if (filas.indexOf(a.r + 1) < 0) filas.push(a.r + 1); });
    const esq = as.some(a => a.u < 0), dir = as.some(a => a.u > 0);
    let contiguas = filas.length === 1;
    for (let i = 1; i < as.length && contiguas; i++) if (as[i].u - as[i - 1].u !== 1) contiguas = false;
    const fila = filas.length === 1 ? 'na ' + filas[0] + 'ª fileira a partir da Mesa' : 'nas fileiras ' + filas.join(' e ') + ' a partir da Mesa';
    const lado = esq && dir ? 'dos dois lados do corredor central' : (esq ? 'à esquerda do corredor central' : 'à direita do corredor central');
    return { nome: NOME[uf], de: DE[uf], fila, lado, contiguas, frase: 'As 3 cadeiras do estado ficam ' + (contiguas ? 'lado a lado, ' : '') + fila + ', ' + lado + '.' };
  }

  const SEG = '#0a121b';
  const COR = {
    neutro: ['#33465a', '#223244', '#182533'], semUf: ['#222f3e', '#182330', '#121b26'], acento: ['#5eead4', '#2dd4bf', '#0f9488'],
    foco: ['#a9bbcf', '#7f93a9', '#5b6e84'], mesa: ['#41566d', '#2b3d50', '#1f2e3e'], tampo: ['#566e89', '#3d5169', '#2b3c50']
  };
  const ALVO = [0, 0.9, 7.6];
  const EL3D = 31, ELPLANTA = 89, D3D = 44, DPLANTA = 400;
  const AMP = 8, PERIODO = 18, TROCA = 0.55;   // vaivém: ±8° a cada 18 s; 3D ⇄ planta em 0,55 s

  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = a => { const l = Math.sqrt(dot(a, a)) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

  /** A cena inteira numa câmera: polígonos já projetados (em unidades da tela, sem o ajuste à caixa), do fundo para a frente. */
  function cena(yawDeg, mix, est) {
    const el = (EL3D + (ELPLANTA - EL3D) * mix) * Math.PI / 180;
    const D = 1 / (1 / D3D + (1 / DPLANTA - 1 / D3D) * mix);
    const yw = yawDeg * Math.PI / 180;
    const cam = [ALVO[0] + D * Math.sin(yw) * Math.cos(el), ALVO[1] + D * Math.sin(el), ALVO[2] + D * Math.cos(yw) * Math.cos(el)];
    const fw = norm(sub(ALVO, cam)), rt = norm(cross(fw, [0, 1, 0])), up2 = cross(rt, fw);
    const proj = q => { const v = sub(q, cam), k = D / dot(v, fw); return [dot(v, rt) * k, -dot(v, up2) * k]; };
    const polys = [];
    const quad = (v, f, o, uf, semBorda) => { polys.push({ v, f, o: o === undefined ? 1 : o, uf: uf || '', s: semBorda ? 'none' : SEG }); };
    const box = (x0, x1, y0, y1, z0, z1, col, uf) => {
      [{ n: [0, 1, 0], c: col[0], v: [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]] },
        { n: [0, 0, 1], c: col[1], v: [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]] },
        { n: [0, 0, -1], c: col[1], v: [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]] },
        { n: [1, 0, 0], c: col[2], v: [[x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]] },
        { n: [-1, 0, 0], c: col[2], v: [[x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [x0, y1, z0]] }].forEach(fa => {
        const ctr = [(fa.v[0][0] + fa.v[2][0]) / 2, (fa.v[0][1] + fa.v[2][1]) / 2, (fa.v[0][2] + fa.v[2][2]) / 2];
        if (dot(fa.n, sub(cam, ctr)) > 0.0001) quad(fa.v, fa.c, 1, uf);
      });
    };
    // chão e Mesa
    quad([[-8.8, 0, -1.8], [8.8, 0, -1.8], [8.8, 0, 16.4], [-8.8, 0, 16.4]], '#0e1a25');
    box(-4.1, 4.1, 0, 0.38, -1.0, 2.2, COR.mesa);
    [-2.28, -1.14, 0, 1.14, 2.28].forEach(x => box(x - 0.4, x + 0.4, 0.38, 1.2, -0.35, 0.35, COR.mesa));
    box(-3.37, 3.37, 0.38, 1.0, 0.5, 1.5, COR.tampo);
    // fileiras: degrau, brilho no chão da bancada em foco e cadeiras
    const sel = [], hov = [];
    for (let r = 0; r < 6; r++) {
      const y0 = yDe(r);
      [[-7.75, -0.45], [0.45, 7.75]].forEach(rg => {
        const frente = [], fundo = [], base = [];
        for (let i = 0; i <= 8; i++) {
          const u = rg[0] + (rg[1] - rg[0]) * i / 8, z = zDe(r, u);
          frente.push([u, y0, z - 0.95]); fundo.push([u, y0, z + 0.95]); base.push([u, 0, z + 0.95]);
        }
        quad(frente.concat(fundo.slice().reverse()), '#162536');
        if (r === 5) quad(fundo.concat(base.slice().reverse()), '#101c29');
      });
      const linha = CADEIRAS.filter(a => a.r === r).map(a => ({ r: a.r, u: a.u, uf: a.uf, z: zDe(r, a.u), y0 }));
      linha.forEach(a => {
        const halo = f => quad([[a.u - 0.62, y0, a.z - 0.62], [a.u + 0.62, y0, a.z - 0.62], [a.u + 0.62, y0, a.z + 0.62], [a.u - 0.62, y0, a.z + 0.62]], f, 0.28, a.uf, true);
        if (a.uf === est.uf) { sel.push(a); halo('#2dd4bf'); } else if (a.uf === est.hov) { hov.push(a); halo('#a9bbcf'); }
        a.dz = dot(sub([a.u, y0 + 0.4, a.z], cam), fw);
      });
      linha.sort((a, b) => b.dz - a.dz).forEach(a => {
        const s = a.uf === est.uf, h = !s && a.uf === est.hov;
        const lift = s ? 0.12 : (h ? 0.06 : 0);
        box(a.u - 0.37, a.u + 0.37, y0 + lift, y0 + lift + (s ? 0.9 : 0.78), a.z - 0.28, a.z + 0.28,
          s ? COR.acento : (h ? COR.foco : (a.uf === 'SEM' ? COR.semUf : COR.neutro)), a.uf);
      });
    }
    polys.forEach(p => { p.p = p.v.map(proj); });
    return { polys, sel, hov, proj };
  }

  /** Encaixe na caixa W×H: a escala e o deslocamento são os da UNIÃO das câmeras do vaivém, para a imagem não "respirar" enquanto gira. */
  function ajustar(W, H, mix) {
    const amp = AMP * (1 - mix);
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    [-amp, 0, amp].forEach(y => cena(y, mix, { uf: '', hov: '' }).polys.forEach(p => p.p.forEach(q => {
      if (q[0] < x0) x0 = q[0]; if (q[0] > x1) x1 = q[0]; if (q[1] < y0) y0 = q[1]; if (q[1] > y1) y1 = q[1];
    })));
    const padX = 14, padT = 38, padB = 52;                      // em cima cabe o rótulo da bancada; embaixo, o seletor 3D/Planta
    const esc = Math.min((W - 2 * padX) / (x1 - x0), (H - padT - padB) / (y1 - y0));
    return { esc, tx: padX + ((W - 2 * padX) - (x1 - x0) * esc) / 2 - x0 * esc, ty: padT + ((H - padT - padB) - (y1 - y0) * esc) / 2 - y0 * esc };
  }

  const NS = 'http://www.w3.org/2000/svg';
  const ARQ = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M8 7h9v9"/></svg>';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /** Texto alternativo do mapa. */
  function alternativo(uf) {
    const d = descrever(uf);
    const base = 'Mapa de assentos do plenário do Senado, com 84 cadeiras em 6 fileiras e a Mesa ao fundo.';
    return d ? base + ' As três cadeiras da bancada ' + d.de + ' estão destacadas, ' + d.fila + ', ' + d.lado + '.'
      : base + ' Nenhuma cadeira está destacada.';
  }

  /** Legenda de baixo do mapa, em HTML. uf vazio = sem destaque (quem já deixou o Senado). */
  function legenda(op) {
    op = op || {};
    const d = descrever(op.uf);
    const sw = c => '<i class="mapa-sw"><b style="background:' + c + '"></b></i>';
    const link = op.url ? '<a class="mapa-leg-a" href="' + esc(op.url) + '" target="_blank" rel="noopener">Ver o mapa no site do Senado' + ARQ + '</a>' : '';
    if (!d) {
      return '<div class="mapa-leg"><div class="mapa-leg-k">Mapa de assentos no plenário</div>'
        + '<div class="mapa-leg-t">Sem cadeira no plenário de hoje</div>'
        + '<p class="mapa-leg-p">Quem já deixou o Senado não tem cadeira no plenário de hoje. O mapa aparece sem destaque, em vez de mostrar a bancada atual do estado como se fosse da pessoa.</p>'
        + '<div class="mapa-leg-l"><span>' + sw('#33465a') + 'Cadeiras de estados (81)</span><span>' + sw('#222f3e') + 'Sem estado no mapa (3)</span></div>' + link + '</div>';
    }
    return '<div class="mapa-leg"><div class="mapa-leg-k">Mapa de assentos no plenário</div>'
      + '<div class="mapa-leg-t">Bancada ' + esc(d.de) + '</div>'
      + '<p class="mapa-leg-p">' + esc(d.frase) + '</p>'
      + '<p class="mapa-leg-n">O Senado marca no mapa as cadeiras do estado, não a de cada senador: o destaque é o mesmo para os três senadores ' + esc(d.de) + '.</p>'
      + '<div class="mapa-leg-l"><span>' + sw('#2dd4bf') + 'Bancada de ' + esc(op.uf) + ' (3)</span><span>' + sw('#33465a') + 'Outros estados (78)</span><span>' + sw('#222f3e') + 'Sem estado no mapa (3)</span></div>'
      + link + '</div>';
  }

  /** Monta o mapa dentro de <figure>. op: { uf: sigla do estado, ou '' para nenhum destaque; aviso: texto no pé da caixa }. */
  function montar(fig, op) {
    op = op || {};
    const uf = NOME[op.uf] ? op.uf : '';
    const est = { uf, hov: '' };
    const mq = matchMedia('(prefers-reduced-motion: reduce)');
    const fraco = (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2) || (navigator.deviceMemory && navigator.deviceMemory <= 2);
    const semMovimento = () => mq.matches || !!fraco;

    fig.textContent = '';
    fig.classList.add('mapa-fig');
    fig.removeAttribute('aria-hidden');
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'mapa-svg');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', alternativo(uf));
    const tri = [0, 1].map(() => { const t = document.createElementNS(NS, 'polygon'); t.setAttribute('class', 'mapa-tri'); svg.appendChild(t); return t; });
    fig.appendChild(svg);
    const chip = (cls) => { const d = document.createElement('div'); d.className = 'mapa-chip ' + cls; d.hidden = true; fig.appendChild(d); return d; };
    const chipSel = chip('mapa-chip-sel'), chipHov = chip('mapa-chip-hov'), chipAviso = chip('mapa-chip-ex');
    const mesaEl = document.createElement('div'); mesaEl.className = 'mapa-mesa'; mesaEl.textContent = 'MESA'; fig.appendChild(mesaEl);
    if (op.aviso) { chipAviso.textContent = op.aviso; chipAviso.hidden = false; }

    const grupo = document.createElement('div');
    grupo.className = 'mapa-vista'; grupo.setAttribute('role', 'group'); grupo.setAttribute('aria-label', 'Tipo de vista');
    const b3d = document.createElement('button'), bPl = document.createElement('button');
    b3d.type = bPl.type = 'button'; b3d.textContent = '3D'; bPl.textContent = 'Planta';
    grupo.appendChild(b3d); grupo.appendChild(bPl); fig.appendChild(grupo);

    const pool = [];
    let W = 0, H = 0, mix = 0, mixAlvo = 0, t = 0, yawBase = 0, ultimo = 0, raf = 0, visivel = true, aj = null, ajChave = '', tt = 0, vivo = true;

    function ajuste() {
      const k = W + 'x' + H + ':' + mix.toFixed(3);
      if (k !== ajChave) { aj = ajustar(W, H, mix); ajChave = k; }
      return aj;
    }

    function rotular(el, ponta, lista, txt, altCad, c, tf) {
      if (!lista.length) { el.hidden = true; ponta.setAttribute('points', ''); return; }
      const topos = lista.map(a => tf(c.proj([a.u, a.y0 + altCad, a.z])));
      const bases = lista.map(a => tf(c.proj([a.u, a.y0, a.z + 0.3])));
      const cx = topos.reduce((s, q) => s + q[0], 0) / topos.length;
      const topo = Math.min.apply(null, topos.map(q => q[1])), fundo = Math.max.apply(null, bases.map(q => q[1]));
      const acima = topo - 36 >= 6;
      if (el.textContent !== txt) el.textContent = txt;
      el.hidden = false;
      el.classList.toggle('abaixo', !acima);
      el.style.left = Math.max(56, Math.min(W - 56, cx)).toFixed(1) + 'px';
      el.style.top = (acima ? topo - 12 : fundo + 12).toFixed(1) + 'px';
      const y = acima ? [topo - 4, topo - 12] : [fundo + 4, fundo + 12];
      ponta.setAttribute('points', [[cx, y[0]], [cx - 4.5, y[1]], [cx + 4.5, y[1]]].map(q => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join(' '));
      ponta.setAttribute('fill', el.classList.contains('mapa-chip-sel') ? '#2dd4bf' : '#e2e8f0');
    }

    function pintar() {
      if (!W || !H) return;
      const yaw = yawBase * (1 - mix);
      const c = cena(yaw, mix, est), a = ajuste();
      const tf = q => [q[0] * a.esc + a.tx, q[1] * a.esc + a.ty];
      const n = c.polys.length;
      for (let i = 0; i < n; i++) {
        const p = c.polys[i];
        let el = pool[i];
        if (!el) { el = document.createElementNS(NS, 'polygon'); svg.insertBefore(el, tri[0]); pool[i] = el; }
        let pts = '';
        for (let j = 0; j < p.p.length; j++) { const r = tf(p.p[j]); pts += r[0].toFixed(1) + ',' + r[1].toFixed(1) + ' '; }
        el.setAttribute('points', pts);
        if (el._f !== p.f) { el.setAttribute('fill', p.f); el._f = p.f; }
        if (el._s !== p.s) { el.setAttribute('stroke', p.s); el._s = p.s; }
        if (el._o !== p.o) { el.setAttribute('opacity', p.o); el._o = p.o; }
        if (el._u !== p.uf) { if (p.uf) el.setAttribute('data-uf', p.uf); else el.removeAttribute('data-uf'); el._u = p.uf; }
        if (el._h) { el.removeAttribute('display'); el._h = false; }
      }
      for (let i = n; i < pool.length; i++) if (!pool[i]._h) { pool[i].setAttribute('display', 'none'); pool[i]._h = true; }
      rotular(chipSel, tri[0], uf ? c.sel : [], uf + ' · 3 cadeiras', 1.0, c, tf);
      rotular(chipHov, tri[1], c.hov, est.hov === 'SEM' ? 'Sem estado no mapa oficial' : (NOME[est.hov] || ''), 0.84, c, tf);
      const mp = tf(c.proj([0, 1.3, -0.9]));
      mesaEl.style.left = mp[0].toFixed(1) + 'px'; mesaEl.style.top = (mp[1] - 4).toFixed(1) + 'px';
    }

    const precisaLaco = () => vivo && visivel && !document.hidden && (mix !== mixAlvo || (mixAlvo === 0 && !semMovimento()));
    function pedir() { if (!raf && vivo) raf = requestAnimationFrame(laco); }
    function laco(ts) {
      raf = 0;
      if (!vivo) return;
      if (!precisaLaco()) { ultimo = 0; pintar(); return; }
      if (!ultimo) ultimo = ts - 33;                                                   // o primeiro quadro pinta na hora
      if (ts - ultimo < 32) { raf = requestAnimationFrame(laco); return; }          // ~30 quadros por segundo
      const dt = Math.min(ts - ultimo, 100) / 1000;
      ultimo = ts;
      if (mix !== mixAlvo) mix = mixAlvo > mix ? Math.min(mixAlvo, mix + dt / TROCA) : Math.max(mixAlvo, mix - dt / TROCA);
      else if (mixAlvo === 0 && !semMovimento()) {
        t += dt;
        yawBase = AMP * Math.min(1, t / 2) * Math.sin(2 * Math.PI * t / PERIODO);    // entra devagar nos 2 primeiros segundos
      }
      pintar();
      if (precisaLaco()) raf = requestAnimationFrame(laco); else ultimo = 0;
    }

    function vista(v) {
      const antes = mixAlvo;
      mixAlvo = v === 'planta' ? 1 : 0;
      b3d.setAttribute('aria-pressed', String(mixAlvo === 0)); bPl.setAttribute('aria-pressed', String(mixAlvo === 1));
      if (mixAlvo === 0 && antes === 1) { t = 0; yawBase = 0; }                       // volta à câmera de frente e retoma o vaivém do zero
      if (mq.matches) { mix = mixAlvo; if (mixAlvo === 1) yawBase = 0; ultimo = 0; }
      pedir();
    }
    b3d.addEventListener('click', () => vista('3d'));
    bPl.addEventListener('click', () => vista('planta'));
    b3d.setAttribute('aria-pressed', 'true'); bPl.setAttribute('aria-pressed', 'false');

    // passar o mouse (ou tocar) numa bancada mostra o nome do estado
    const ufDe = e => { const x = e.target && e.target.getAttribute && e.target.getAttribute('data-uf'); return x && x !== est.uf ? x : ''; };
    const foco = u => { if (u === est.hov) return; est.hov = u; pedir(); };
    svg.addEventListener('pointermove', e => { if (e.pointerType !== 'touch') foco(ufDe(e)); });
    svg.addEventListener('pointerleave', () => foco(''));
    svg.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'touch') return;
      foco(ufDe(e)); clearTimeout(tt); tt = setTimeout(() => foco(''), 2500);
    });

    const medir = () => { W = fig.clientWidth; H = fig.clientHeight; svg.setAttribute('width', W); svg.setAttribute('height', H); pedir(); };
    const ro = new ResizeObserver(medir); ro.observe(fig);
    const io = new IntersectionObserver(es => { visivel = es[es.length - 1].isIntersecting; if (visivel) pedir(); }, { threshold: 0.01 });
    io.observe(fig);
    const aoVisibilidade = () => { ultimo = 0; if (!document.hidden) pedir(); };
    document.addEventListener('visibilitychange', aoVisibilidade);
    const aoMovimento = () => { if (semMovimento()) { yawBase = 0; t = 0; mix = mixAlvo; } ultimo = 0; pedir(); };
    mq.addEventListener ? mq.addEventListener('change', aoMovimento) : mq.addListener(aoMovimento);
    medir();

    return {
      destruir() {
        vivo = false; cancelAnimationFrame(raf); clearTimeout(tt); ro.disconnect(); io.disconnect();
        document.removeEventListener('visibilitychange', aoVisibilidade);
        mq.removeEventListener ? mq.removeEventListener('change', aoMovimento) : mq.removeListener(aoMovimento);
      }
    };
  }

  /** [fileira, posição] das cadeiras de um estado ('SEM' = as 3 sem estado), fileira 0 = a mais perto da Mesa. Serve à conferência contra o mapa do Senado. */
  const cadeiras = uf => CADEIRAS.filter(a => a.uf === uf).map(a => [a.r, a.u]);

  window.MapaAssentos = { montar, legenda, descrever, alternativo, cadeiras, UFS: UFS.map(x => x[0]) };
})();
