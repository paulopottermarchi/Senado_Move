// Câmara Aberta — modelo DECORATIVO do topo da página dos senadores: uma cúpula convexa simplificada, evocando o Senado do Congresso
// Nacional. Não carrega dado nenhum e não significa nada: é enfeite. Geometria gerada por código (sem arquivo GLB), monocromática
// (cinzas e petróleo), em linhas de baixa opacidade, sobre um palco escuro. Fica na coluna da direita do topo, nunca atrás de texto,
// de gráfico ou de tabela. Oscila devagar (cerca de 15 graus para cada lado, sem giro completo) e para com movimento reduzido, aba
// oculta ou fora da tela (as duas últimas regras são da cena3d.js). As cores do espectro NÃO entram aqui: são só da ideologia.
//
// Carregado por import('./cupula3d.js') depois da primeira pintura (requestIdleCallback), só se o navegador tem importmap e WebGL,
// a conexão não pede economia e o movimento não está reduzido; senão fica a imagem estática (img/cupula-senado.svg), gerada por
// svgEstatico() a partir deste mesmo modelo.
import { criarCena, corDoToken } from './cena3d.js';

const R = 1.55;            // raio da cúpula
const BASE_Y = 0.5;        // altura onde a cúpula assenta (em cima do tambor)
const OSCILACAO = 0.2618;  // 15 graus em radianos
const PERIODO = 0.42;      // rad/s do seno: uma ida e volta em ~15 s

/** As linhas do modelo, como listas de pontos [x,y,z]: { petroleo: [...], cinza: [...] }. */
export function polilinhas() {
  const petroleo = [], cinza = [];
  const anel = (raio, y, n = 64) => { const l = []; for (let i = 0; i <= n; i++) { const a = i / n * Math.PI * 2; l.push([Math.cos(a) * raio, y, Math.sin(a) * raio]); } return l; };
  // cúpula: paralelos a cada 1/8 do quarto de esfera e meridianos a cada 20 graus
  for (let k = 1; k <= 8; k++) {
    const fi = k / 8 * Math.PI / 2;
    petroleo.push(anel(R * Math.sin(fi), BASE_Y + R * Math.cos(fi)));
  }
  for (let m = 0; m < 18; m++) {
    const a = m / 18 * Math.PI * 2, l = [];
    for (let k = 0; k <= 16; k++) { const fi = k / 16 * Math.PI / 2; l.push([Math.cos(a) * R * Math.sin(fi), BASE_Y + R * Math.cos(fi), Math.sin(a) * R * Math.sin(fi)]); }
    petroleo.push(l);
  }
  // lanterna no alto
  petroleo.push([[0, BASE_Y + R, 0], [0, BASE_Y + R + 0.42, 0]]);
  petroleo.push(anel(0.07, BASE_Y + R + 0.3, 16));
  // tambor sob a cúpula
  cinza.push(anel(R * 1.04, 0.2)); cinza.push(anel(R * 1.04, BASE_Y));
  for (let m = 0; m < 36; m++) { const a = m / 36 * Math.PI * 2, c = Math.cos(a) * R * 1.04, s = Math.sin(a) * R * 1.04; cinza.push([[c, 0.2, s], [c, BASE_Y, s]]); }
  // pódio em dois níveis (só as arestas)
  const caixa = (w, h, d, y) => {
    const x = w / 2, z = d / 2, y0 = y - h / 2, y1 = y + h / 2;
    const p = [[-x, y0, -z], [x, y0, -z], [x, y0, z], [-x, y0, z]], q = p.map(([a, , c]) => [a, y1, c]);
    return [[...p, p[0]], [...q, q[0]], ...p.map((v, i) => [v, q[i]])];
  };
  cinza.push(...caixa(6.6, 0.2, 3.5, -0.1), ...caixa(4.4, 0.2, 2.7, 0.1));
  return { petroleo, cinza };
}

function segmentos(listas) {
  const v = [];
  for (const l of listas) for (let i = 0; i < l.length - 1; i++) v.push(...l[i], ...l[i + 1]);
  return new Float32Array(v);
}

/** Monta o grupo 3D (linhas + um véu translúcido na cúpula). */
export function construirGrupo(THREE) {
  const grupo = new THREE.Group();
  const { petroleo, cinza } = polilinhas();
  const linhas = (listas, cor, opacidade) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(segmentos(listas), 3));
    return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: cor, transparent: true, opacity: opacidade, depthWrite: false }));
  };
  grupo.add(linhas(cinza, 0x94a3b8, 0.34));
  grupo.add(linhas(petroleo, 0x2dd4bf, 0.55));
  const veu = new THREE.Mesh(new THREE.SphereGeometry(R * 0.996, 48, 24, 0, Math.PI * 2, 0, Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: 0x0f766e, transparent: true, opacity: 0.1, side: THREE.DoubleSide, depthWrite: false }));
  veu.position.y = BASE_Y;
  grupo.add(veu);
  return grupo;
}

export function camaraDoModelo(THREE, aspecto = 4 / 3) {
  const cam = new THREE.PerspectiveCamera(26, aspecto, 0.1, 100);
  cam.position.set(0, 2.2, 12);
  cam.lookAt(0, 0.9, 0);
  return cam;
}

/** Liga o modelo ao `contentor`. Devolve a cena (com destruir()) ou null. `aoPronto` roda depois do primeiro quadro. */
export function iniciar({ THREE, contentor, aoPronto = null }) {
  const camera = camaraDoModelo(THREE);
  const grupo = construirGrupo(THREE);
  const cena = criarCena({ THREE, contentor, camera, animado: true,
    aoQuadro: t => { grupo.rotation.y = Math.sin(t * PERIODO) * OSCILACAO; } });
  if (!cena) return null;
  cena.canvas.setAttribute('aria-hidden', 'true');
  cena.canvas.setAttribute('role', 'presentation');
  cena.scene.add(grupo);
  cena.pedirQuadro();
  if (aoPronto) requestAnimationFrame(() => requestAnimationFrame(aoPronto));
  return cena;
}

/**
 * A imagem estática de reserva: as mesmas linhas, projetadas pela mesma câmera, com o modelo parado. Usada uma vez para gerar
 * img/cupula-senado.svg (largura x altura em px do viewBox); refazer se o modelo mudar.
 */
export function svgEstatico(THREE, larg = 480, alt = 360) {
  const cam = camaraDoModelo(THREE, larg / alt);
  cam.updateMatrixWorld(); cam.updateProjectionMatrix();
  const { petroleo, cinza } = polilinhas();
  const v = new THREE.Vector3();
  const caminho = listas => listas.map(l => 'M' + l.map(p => {
    v.set(p[0], p[1], p[2]).project(cam);
    return ((v.x + 1) / 2 * larg).toFixed(1) + ' ' + ((1 - v.y) / 2 * alt).toFixed(1);
  }).join('L')).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${larg} ${alt}" width="${larg}" height="${alt}" fill="none" stroke-linejoin="round" stroke-linecap="round">`
    + `<path d="${caminho(cinza)}" stroke="#94a3b8" stroke-opacity=".34" stroke-width="1"/>`
    + `<path d="${caminho(petroleo)}" stroke="#2dd4bf" stroke-opacity=".55" stroke-width="1"/></svg>`;
}

export { corDoToken };
