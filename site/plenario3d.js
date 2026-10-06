// Câmara Aberta — o plenário do Senado em 3D, com os dados: um assento por senador, na MESMA ordem e com as MESMAS cores do plenário em
// 2D (Espectro.assentos e Espectro.corDoScore, ordenados pelo score do partido). Carregado por import() só quando o leitor clica em
// "Ver plenário em 3D" (o Three.js também só é pedido então) e liberado quando o diálogo fecha.
//
// Regras que mantêm o gráfico honesto:
//   · todo assento tem o MESMO tamanho: a altura dos degraus e a profundidade são só arquibancada, nunca dado;
//   · a cor do assento é a do espectro do partido (cor contínua do score), e é a face de cima, sem iluminação, que a carrega:
//     o sombreamento não altera o matiz; mesa, tribuna e degraus são neutros do tema;
//   · a ordem segue o espectro do partido, não a disposição real do plenário (a nota está no diálogo);
//   · quem não estava em exercício na legislatura escolhida fica apagado, como no SVG.
// Câmera quase frontal (elevação de 25° a 35°), FOV 30, rotação limitada a ±40°, zoom limitado, sem pan.
// Teclado: setas percorrem os assentos (esquerda/direita pelo espectro, cima/baixo entre fileiras), Enter abre a ficha, + e − dão zoom.
import { criarCena, corDoToken, movimentoReduzido } from './cena3d.js';

const K = 0.05;              // unidades do SVG (raio 140) → mundo (7)
const PASSO_FILEIRA = 0.5;   // cada fileira, para trás, sobe isto
const SEAT = { r: 0.21, h: 0.17 };
const FOV = 30;
const ELEV = 30 * Math.PI / 180;               // padrão; limites de 25° a 35°
const POLAR_MIN = (90 - 35) * Math.PI / 180, POLAR_MAX = (90 - 25) * Math.PI / 180;
const AZIMUTE = 40 * Math.PI / 180;
const ALVO = [0, 0.75, -2.7];

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/**
 * opcoes: THREE, OrbitControls, contentor (elemento com tamanho), assentos [{id,nome,partido,uf,score,ativo}] na ordem do espectro,
 * pts (Espectro.assentos, mesma ordem), corDe(score) → '#rrggbb', aoEscolher(id), aoErro(e).
 * Devolve { vistaFrontal, zoom, recolorir, destruir } ou null.
 */
export function iniciar({ THREE, OrbitControls, contentor, assentos, pts, corDe, aoEscolher, aoErro }) {
  const n = assentos.length;
  const camera = new THREE.PerspectiveCamera(FOV, 16 / 9, 0.1, 200);
  const cena = criarCena({ THREE, contentor, camera, animado: false, aoErro });
  if (!cena) return null;
  const { scene, canvas } = cena;
  const alvo = new THREE.Vector3(...ALVO);

  // ---------- geometria ----------
  const raios = [...new Set(pts.map(p => +p.r.toFixed(3)))].sort((a, b) => a - b);
  const fila = pts.map(p => raios.indexOf(+p.r.toFixed(3)));
  const passoR = raios.length > 1 ? raios[1] - raios[0] : 26;
  const pos = pts.map((p, i) => new THREE.Vector3((p.x - 150) * K, fila[i] * PASSO_FILEIRA + 0.08 + SEAT.h / 2, -(150 - p.y) * K));

  // luz só nos neutros (degraus, mesa, tribuna): os assentos são MeshBasic e mostram a cor exata
  scene.add(new THREE.AmbientLight(0xffffff, 1.9));
  const sol = new THREE.DirectionalLight(0xffffff, 1.6); sol.position.set(-4, 10, 7); scene.add(sol);

  const neutro = (token, reserva) => corDoToken(THREE, token, reserva);
  const escuro = () => !!(window.Tema && window.Tema.escuro());
  // neutros do tema; no escuro, mesa e tribuna ficam mais fundas que o texto (senão viram o ponto mais claro da cena)
  const corMesa = () => (escuro() ? new THREE.Color('#4a586f') : neutro('--tinta3', '#566376'));
  const corTribuna = () => (escuro() ? new THREE.Color('#364358') : neutro('--tinta2', '#334155'));
  const matLambert = cor => new THREE.MeshLambertMaterial({ color: cor });
  const mats = { degrau: matLambert(neutro('--linha-forte', '#cbd5e1')), mesa: matLambert(corMesa()),
                 tribuna: matLambert(corTribuna()), chao: matLambert(neutro('--linha', '#e2e8f0')) };
  const linhasMat = new THREE.LineBasicMaterial({ color: neutro('--tinta3', '#566376'), transparent: true, opacity: 0.4 });

  const chao = new THREE.Mesh(new THREE.CircleGeometry(8.6, 72), mats.chao);
  chao.rotation.x = -Math.PI / 2; chao.position.y = -0.02; scene.add(chao);

  // degraus: meia coroa por fileira, mais alta quanto mais atrás
  raios.forEach((r, f) => {
    const interno = (r - passoR / 2) * K, externo = (r + passoR / 2) * K, altura = f * PASSO_FILEIRA + 0.08;
    const forma = new THREE.Shape();
    forma.absarc(0, 0, externo, 0, Math.PI, false);
    forma.absarc(0, 0, interno, Math.PI, 0, true);
    const geo = new THREE.ExtrudeGeometry(forma, { depth: altura, bevelEnabled: false, curveSegments: 56 });
    geo.rotateX(-Math.PI / 2);                               // forma (x,y) → mundo (x,−z); a altura sobe em Y
    const degrau = new THREE.Mesh(geo, mats.degrau); scene.add(degrau);
    const arestas = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), linhasMat); scene.add(arestas);
  });

  // mesa diretora e tribuna, neutras, no centro
  const mesa = new THREE.Mesh(new THREE.BoxGeometry(2.9, 0.46, 0.95), mats.mesa); mesa.position.set(0, 0.23, -0.35); scene.add(mesa);
  const tribuna = new THREE.Mesh(new THREE.BoxGeometry(0.85, 0.82, 0.6), mats.tribuna); tribuna.position.set(0, 0.41, 1.15); scene.add(tribuna);
  const pulpito = new THREE.Mesh(new THREE.BoxGeometry(0.95, 0.1, 0.72), mats.tribuna); pulpito.position.set(0, 0.87, 1.15); pulpito.rotation.x = -0.18; scene.add(pulpito);

  // assentos: UM InstancedMesh, todos do mesmo tamanho; a cor por instância carrega o espectro
  const geoAssento = new THREE.CylinderGeometry(SEAT.r, SEAT.r, SEAT.h, 24);
  const lado = new THREE.MeshBasicMaterial({ color: 0x8c8c8c }), topo = new THREE.MeshBasicMaterial({ color: 0xffffff });
  const malha = new THREE.InstancedMesh(geoAssento, [lado, topo, topo], n);
  const m4 = new THREE.Matrix4();
  pos.forEach((p, i) => { m4.makeTranslation(p.x, p.y, p.z); malha.setMatrixAt(i, m4); });
  malha.instanceMatrix.needsUpdate = true;
  scene.add(malha);
  const cor = new THREE.Color();
  const recolorir = () => {
    const fundo = neutro('--papel', '#ffffff');
    assentos.forEach((a, i) => { cor.set(corDe(a.score)); if (!a.ativo) cor.lerp(fundo, 0.88); malha.setColorAt(i, cor); });
    if (malha.instanceColor) malha.instanceColor.needsUpdate = true;
    mats.degrau.color.copy(neutro('--linha-forte', '#cbd5e1')); mats.mesa.color.copy(corMesa());
    mats.tribuna.color.copy(corTribuna()); mats.chao.color.copy(neutro('--linha', '#e2e8f0'));
    linhasMat.color.copy(neutro('--tinta3', '#566376'));
    anel.material.color.copy(neutro('--tinta', '#0f172a'));
    cena.pedirQuadro();
  };
  // anel do assento em foco (não muda o tamanho do assento)
  const anel = new THREE.Mesh(new THREE.RingGeometry(SEAT.r + 0.05, SEAT.r + 0.12, 40),
    new THREE.MeshBasicMaterial({ color: neutro('--tinta', '#0f172a'), depthTest: false, transparent: true }));
  anel.rotation.x = -Math.PI / 2; anel.renderOrder = 10; anel.visible = false; scene.add(anel);
  recolorir();

  // ---------- câmera e controles ----------
  let dist0 = 16;
  const enquadrar = () => {
    const w = contentor.clientWidth || 800, h = contentor.clientHeight || 450;
    const tanV = Math.tan(FOV / 2 * Math.PI / 180), tanH = tanV * (w / h);
    // meia-largura da cena (7,7) vista de mais perto na frente (as pontas das fileiras ficam 2,7 antes do alvo): ~9,9 de folga
    const meia = w / h < 1.15 ? 8.9 : 9.9;           // palco estreito (celular): menos folga nas laterais, a cena não encolhe à toa
    return Math.max(meia / tanH, 3.9 / tanV);
  };
  const posicionar = (azimute, polar, dist) => {
    camera.position.set(alvo.x + dist * Math.sin(polar) * Math.sin(azimute), alvo.y + dist * Math.cos(polar), alvo.z + dist * Math.sin(polar) * Math.cos(azimute));
    camera.lookAt(alvo);
  };
  dist0 = enquadrar();
  posicionar(0, Math.PI / 2 - ELEV, dist0);
  const ctl = new OrbitControls(camera, canvas);
  ctl.target.copy(alvo);
  ctl.enablePan = false; ctl.enableDamping = false;
  ctl.minAzimuthAngle = -AZIMUTE; ctl.maxAzimuthAngle = AZIMUTE;
  ctl.minPolarAngle = POLAR_MIN; ctl.maxPolarAngle = POLAR_MAX;
  const limites = () => { ctl.minDistance = dist0 * 0.6; ctl.maxDistance = dist0 * 1.3; };
  limites();
  ctl.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
  canvas.style.touchAction = 'pan-y';                       // arrastar na vertical continua rolando a página; na horizontal, gira
  // a roda do mouse só dá zoom com Ctrl/⌘: sem isso, rolar a página por cima do canvas não pode ficar preso
  canvas.addEventListener('wheel', e => { if (!(e.ctrlKey || e.metaKey)) e.stopImmediatePropagation(); }, { capture: true, passive: true });
  ctl.update();
  ctl.addEventListener('change', () => { posDica(); cena.pedirQuadro(); });

  const esfericas = () => { const d = camera.position.clone().sub(alvo), r = d.length(); return { az: Math.atan2(d.x, d.z), po: Math.acos(d.y / r), dist: r }; };
  let tween = null;
  const irPara = (az, po, dist) => {
    if (tween) { tween.stop(); tween = null; }
    const de = esfericas(), M = window.Motion;
    if (movimentoReduzido() || !M || !M.animate) { posicionar(az, po, dist); ctl.update(); return; }
    tween = M.animate(0, 1, { duration: 0.5, ease: [0.22, 1, 0.36, 1],
      onUpdate: t => { posicionar(de.az + (az - de.az) * t, de.po + (po - de.po) * t, de.dist + (dist - de.dist) * t); ctl.update(); },
      onComplete: () => { tween = null; } });
  };
  const vistaFrontal = () => irPara(0, Math.PI / 2 - ELEV, dist0);
  const zoom = fator => { const e = esfericas(); irPara(e.az, e.po, Math.min(ctl.maxDistance, Math.max(ctl.minDistance, e.dist * fator))); };

  // reenquadra ao redimensionar, mantendo o ângulo e a proporção do zoom
  let ultimoW = contentor.clientWidth, ultimoH = contentor.clientHeight;
  const ro = new ResizeObserver(() => {
    const w = contentor.clientWidth, h = contentor.clientHeight; if (!w || !h || (w === ultimoW && h === ultimoH)) return;
    ultimoW = w; ultimoH = h;
    const e = esfericas(), fator = e.dist / dist0;
    dist0 = enquadrar(); limites();
    posicionar(e.az, e.po, Math.min(ctl.maxDistance, Math.max(ctl.minDistance, dist0 * fator))); ctl.update(); posDica();
  });
  ro.observe(contentor);

  // ---------- dica, foco e leitor de tela ----------
  const dica = document.createElement('div');
  dica.className = 'dica3d'; dica.setAttribute('aria-hidden', 'true');
  const sr = document.createElement('div');
  sr.className = 'so-leitor'; sr.setAttribute('aria-live', 'polite');
  contentor.appendChild(dica); contentor.appendChild(sr);
  canvas.setAttribute('aria-hidden', 'true');       // quem lê a cena é o contêiner (role=group + aria-live); o canvas, sozinho, não diz nada
  contentor.tabIndex = 0;
  contentor.setAttribute('role', 'group');
  contentor.setAttribute('aria-label', `Plenário do Senado em 3D: ${n} assentos, do mais à esquerda ao mais à direita pela posição do partido. Use as setas para percorrer os assentos, Enter para abrir a ficha e mais ou menos para o zoom.`);
  let ativo = -1, toque = false;
  const texto = i => { const a = assentos[i]; return `<b>${esc(a.nome)}</b><small>${esc(a.partido || 'sem partido')}-${esc(a.uf)}${a.ativo ? '' : ' · não estava em exercício nesta legislatura'}</small>`; };
  const posDica = () => {
    if (ativo < 0) return;
    camera.updateMatrixWorld();                      // o controle mexeu na câmera e o desenho ainda não aconteceu: a projeção precisa da matriz nova
    const v = pos[ativo].clone(); v.y += SEAT.h / 2; v.project(camera);
    const w = contentor.clientWidth, h = contentor.clientHeight;
    dica.style.left = Math.min(w - 8, Math.max(8, (v.x + 1) / 2 * w)) + 'px';
    dica.style.top = ((1 - v.y) / 2 * h) + 'px';
  };
  const definirAtivo = (i, { anunciar = false } = {}) => {
    ativo = i;
    if (i < 0) { dica.classList.remove('on'); anel.visible = false; canvas.style.cursor = ''; cena.pedirQuadro(); return; }
    anel.position.set(pos[i].x, pos[i].y + SEAT.h / 2 + 0.02, pos[i].z); anel.visible = true;
    dica.innerHTML = texto(i) + (toque ? '<button type="button" class="pino" data-abrir>Abrir a ficha</button>' : '');
    dica.classList.add('on'); dica.classList.toggle('toque', toque);
    posDica();
    if (anunciar) sr.textContent = `${assentos[i].nome}, ${assentos[i].partido || 'sem partido'}, ${assentos[i].uf}. Assento ${i + 1} de ${n} no espectro.${assentos[i].ativo ? '' : ' Não estava em exercício nesta legislatura.'}`;
    cena.pedirQuadro();
  };
  const escolher = i => { if (i >= 0 && aoEscolher) aoEscolher(assentos[i].id); };

  const raio = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const sob = e => {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    raio.setFromCamera(ndc, camera);
    const h = raio.intersectObject(malha, false)[0];
    return h ? h.instanceId : -1;
  };
  let baixo = null;
  canvas.addEventListener('pointerdown', e => { baixo = { x: e.clientX, y: e.clientY, t: e.pointerType }; toque = e.pointerType === 'touch'; });
  canvas.addEventListener('pointermove', e => {
    if (e.pointerType !== 'mouse' || e.buttons) return;
    const i = sob(e); canvas.style.cursor = i >= 0 ? 'pointer' : '';
    if (i !== ativo) definirAtivo(i);
  });
  canvas.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse' && document.activeElement !== contentor) definirAtivo(-1); });
  canvas.addEventListener('click', e => {
    if (baixo && Math.hypot(e.clientX - baixo.x, e.clientY - baixo.y) > 6) return;      // foi um arrasto (giro), não um clique
    const i = sob(e);
    if (i < 0) { if (toque) definirAtivo(-1); return; }
    if (!toque) { escolher(i); return; }                                                   // mouse: o clique abre a ficha
    if (i === ativo) escolher(i); else definirAtivo(i, { anunciar: true });                // toque: o 1º toque mostra quem é, o 2º abre
  });
  dica.addEventListener('click', e => { if (e.target.closest('[data-abrir]')) escolher(ativo); });

  const vizinho = (i, df) => {              // assento da fileira vizinha com o ângulo mais próximo
    const alvoF = fila[i] + df; if (alvoF < 0 || alvoF >= raios.length) return i;
    let melhor = i, d = Infinity;
    pts.forEach((p, j) => { if (fila[j] === alvoF) { const dd = Math.abs(p.ang - pts[i].ang); if (dd < d) { d = dd; melhor = j; } } });
    return melhor;
  };
  // só o foco de teclado abre o primeiro assento: clicar no palco com o mouse não deve acender uma dica que ninguém pediu
  contentor.addEventListener('focus', () => { if (!contentor.matches(':focus-visible')) return; toque = false; if (ativo < 0) definirAtivo(0, { anunciar: true }); });
  contentor.addEventListener('blur', () => definirAtivo(-1));
  contentor.addEventListener('keydown', e => {
    const k = e.key; let j = ativo;
    if (k === 'ArrowRight') j = Math.min(n - 1, ativo + 1);
    else if (k === 'ArrowLeft') j = Math.max(0, ativo - 1);
    else if (k === 'ArrowUp') j = vizinho(ativo, +1);
    else if (k === 'ArrowDown') j = vizinho(ativo, -1);
    else if (k === 'Home') j = 0;
    else if (k === 'End') j = n - 1;
    else if (k === 'Enter' || k === ' ') { e.preventDefault(); escolher(ativo); return; }
    else if (k === '+' || k === '=') { e.preventDefault(); zoom(0.85); return; }
    else if (k === '-' || k === '_') { e.preventDefault(); zoom(1 / 0.85); return; }
    else return;
    e.preventDefault(); toque = false;
    if (j !== ativo) definirAtivo(j, { anunciar: true });
  });

  return {
    vistaFrontal, zoom, recolorir,
    destruir() {
      if (tween) tween.stop();
      ro.disconnect(); ctl.dispose();
      dica.remove(); sr.remove();
      contentor.removeAttribute('tabindex'); contentor.removeAttribute('role'); contentor.removeAttribute('aria-label');
      cena.destruir();
    }
  };
}
