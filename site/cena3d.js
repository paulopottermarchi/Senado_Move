// Câmara Aberta — base comum das cenas 3D (Three.js 0.180.0, versionado em vendor/three/; nada de CDN).
// Módulo ES carregado SÓ sob demanda, por import('./cena3d.js'), depois de a página ter sido pintada. Quem usa (cupula3d.js, o modelo
// decorativo do topo, e plenario3d.js, o plenário com os dados) fica com a geometria; aqui ficam as regras que valem para qualquer
// cena do site:
//   · renderização SOB DEMANDA: só desenha quando alguém pede (pedirQuadro) ou quando a cena é animada e está à vista;
//   · pausa fora da tela (IntersectionObserver) e com a aba oculta (visibilitychange): nada de GPU em segundo plano;
//   · pixelRatio no máximo 1,5 e no máximo 30 quadros por segundo no laço contínuo;
//   · movimento reduzido: o laço contínuo nunca começa (a cena é desenhada parada, sob demanda);
//   · liberação dos recursos (geometrias, materiais, texturas, renderer, contexto WebGL) em destruir().
// Melhoria progressiva: tudo aqui é opcional. Sem importmap, sem WebGL ou com erro, a página continua completa (SVG e imagem
// estática de reserva) — por isso cada função devolve null/false em vez de lançar quando o ambiente não serve.

const PIXEL_RATIO_MAX = 1.5;
const FPS_MAX = 30;

/** O navegador entende importmap? (o `three` é importado pelo nome, resolvido pelo importmap do HTML). */
export const temImportmap = () => !!(window.HTMLScriptElement && HTMLScriptElement.supports && HTMLScriptElement.supports('importmap'));

let _webgl = null;
/** Dá para criar um contexto WebGL? Testado uma vez; o contexto de teste é solto na hora. */
export function webglDisponivel() {
  if (_webgl !== null) return _webgl;
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    _webgl = !!gl;
    if (gl) { const ext = gl.getExtension('WEBGL_lose_context'); if (ext) ext.loseContext(); }
  } catch (e) { _webgl = false; }
  return _webgl;
}

/** Tudo o que o 3D precisa existe neste navegador? */
export const suporta3D = () => temImportmap() && webglDisponivel();

export const movimentoReduzido = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Conexão que pede economia (Save-Data ou 2G): nada de 3D decorativo. */
export function conexaoLenta() {
  const c = navigator.connection;
  return !!c && (c.saveData === true || /(^|-)2g$/.test(c.effectiveType || ''));
}

/** Importa o Three.js (pelo importmap). null se não der. */
export async function carregarThree() {
  try { return await import('three'); } catch (e) { return null; }
}

/** Cor de um token CSS (ex.: '--linha-forte') como THREE.Color, para os neutros acompanharem o tema. */
export function corDoToken(THREE, nome, reserva = '#808080') {
  const v = getComputedStyle(document.documentElement).getPropertyValue(nome).trim() || reserva;
  const c = new THREE.Color();
  try { c.setStyle(v); } catch (e) { c.set(reserva); }
  return c;
}

/** Libera geometrias, materiais e texturas de tudo o que está sob `raiz`. */
export function liberar(raiz) {
  raiz.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    for (const m of ms) {
      for (const k of Object.keys(m)) { const v = m[k]; if (v && v.isTexture) v.dispose(); }
      m.dispose();
    }
    if (o.isInstancedMesh && o.dispose) o.dispose();
  });
}

/**
 * Cria a cena dentro de `contentor` (um elemento com tamanho próprio: a cena preenche o que ele tiver).
 * opcoes: THREE (obrigatório), camera, animado (laço contínuo, ex.: oscilação), aoQuadro(t, dt) antes de cada desenho do laço,
 * aoErro(e) se o contexto WebGL se perder, rotulo (aria-label do canvas, se não for decorativo).
 * Devolve { renderer, scene, canvas, pedirQuadro, setAnimado, destruir } ou null se o WebGL falhar.
 */
export function criarCena({ THREE, contentor, camera, animado = false, aoQuadro = null, aoErro = null }) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
  } catch (e) { return null; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, PIXEL_RATIO_MAX));
  renderer.setClearColor(0x000000, 0);
  const canvas = renderer.domElement;
  canvas.style.cssText = 'display:block;width:100%;height:100%';
  contentor.appendChild(canvas);
  const scene = new THREE.Scene();

  let visivel = true, abaVisivel = document.visibilityState !== 'hidden', perdido = false, destruida = false;
  let sujo = true, pedido = 0, laco = 0, ultimo = 0, quer = animado;
  const animando = () => quer && !movimentoReduzido();

  const desenhar = () => { if (destruida || perdido) return; sujo = false; renderer.render(scene, camera); };

  // Sob demanda: um único quadro, só se a cena está à vista; senão fica marcada como suja e é desenhada ao voltar.
  const pedirQuadro = () => {
    sujo = true;
    if (pedido || laco || destruida || !visivel || !abaVisivel) return;
    pedido = requestAnimationFrame(() => { pedido = 0; if (sujo) desenhar(); });
  };

  // Laço contínuo (só cenas animadas): no máximo FPS_MAX quadros por segundo.
  const passo = 1000 / FPS_MAX;
  const volta = t => {
    laco = requestAnimationFrame(volta);
    if (t - ultimo < passo) return;
    const dt = ultimo ? (t - ultimo) / 1000 : 0;
    ultimo = t - ((t - ultimo) % passo);
    if (aoQuadro) aoQuadro(t / 1000, dt);
    desenhar();
  };
  const parar = () => { if (laco) { cancelAnimationFrame(laco); laco = 0; } };
  const atualizarLaco = () => {
    const deve = animando() && visivel && abaVisivel && !perdido && !destruida;
    if (deve && !laco) { ultimo = 0; laco = requestAnimationFrame(volta); }
    else if (!deve) { parar(); if (sujo && visivel && abaVisivel) pedirQuadro(); }
  };

  const ro = new ResizeObserver(entradas => {
    const r = entradas[0].contentRect, w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    renderer.setSize(w, h, false);
    if (camera.isPerspectiveCamera) { camera.aspect = w / h; camera.updateProjectionMatrix(); }
    pedirQuadro();
  });
  ro.observe(contentor);

  const io = new IntersectionObserver(es => { visivel = es[es.length - 1].isIntersecting; atualizarLaco(); if (visivel && sujo) pedirQuadro(); });
  io.observe(contentor);
  const aoVisibilidade = () => { abaVisivel = document.visibilityState !== 'hidden'; atualizarLaco(); if (abaVisivel && sujo) pedirQuadro(); };
  document.addEventListener('visibilitychange', aoVisibilidade);
  const mq = matchMedia('(prefers-reduced-motion: reduce)');
  const aoMovimento = () => atualizarLaco();
  if (mq.addEventListener) mq.addEventListener('change', aoMovimento);
  const aoPerder = e => { e.preventDefault(); perdido = true; parar(); if (aoErro) aoErro(new Error('contexto WebGL perdido')); };
  canvas.addEventListener('webglcontextlost', aoPerder);

  atualizarLaco();
  pedirQuadro();

  return {
    renderer, scene, canvas,
    pedirQuadro,
    setAnimado(b) { quer = !!b; atualizarLaco(); },
    get visivel() { return visivel && abaVisivel; },
    destruir() {
      if (destruida) return;
      destruida = true; parar();
      if (pedido) cancelAnimationFrame(pedido);
      ro.disconnect(); io.disconnect();
      document.removeEventListener('visibilitychange', aoVisibilidade);
      if (mq.removeEventListener) mq.removeEventListener('change', aoMovimento);
      canvas.removeEventListener('webglcontextlost', aoPerder);
      liberar(scene);
      renderer.dispose();
      try { renderer.forceContextLoss(); } catch (e) { /* já perdido */ }
      canvas.remove();
    }
  };
}
