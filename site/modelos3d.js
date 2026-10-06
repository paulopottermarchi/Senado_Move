// Câmara Aberta — modelos DECORATIVOS do topo de cada página: um por assunto, todos da mesma família (cupula3d.js é o do Senado).
// Não carregam dado nenhum e não significam nada: é enfeite que evoca o assunto da página. Geometria gerada por código (sem arquivo
// GLB), monocromática (cinza e petróleo), em linhas de baixa opacidade, sobre um palco escuro. Ficam na coluna da direita do topo,
// nunca atrás de texto, de gráfico ou de tabela. Oscilam devagar (cerca de 15 graus para cada lado, sem giro completo) e param com
// movimento reduzido, aba oculta ou fora da tela (as duas últimas regras são da cena3d.js). As cores do espectro NÃO entram aqui.
//
//   bacia   — a Câmara: bacia côncava, aberta para cima, sobre o pódio      (início, deputados)
//   folhas  — leis votadas: pilha de folhas com o texto e o selo
//   aneis   — quem vota com quem: dois anéis entrelaçados
//   casas   — entre as casas: cúpula e bacia ligadas por um arco
//   obras   — obras: estrutura em etapas, com guindaste
//   assento — ficha do deputado: um assento sobre os degraus, com o anel de foco
//
// Carregado por import('./modelos3d.js') depois da primeira pintura (site.js, Modelo3D), só se o navegador tem importmap e WebGL, a
// conexão não pede economia e o movimento não está reduzido; senão fica a imagem estática de cada um (img/modelo-<tipo>.svg), gerada
// por svgEstatico() a partir deste mesmo modelo e da mesma câmera.
import { criarCena } from './cena3d.js';

const OSCILACAO = 0.2618;  // 15 graus em radianos
const PERIODO = 0.42;      // rad/s do seno: uma ida e volta em ~15 s
const PI = Math.PI;

const anel = (raio, y, n = 36, cx = 0, cz = 0) => {
  const l = [];
  for (let i = 0; i <= n; i++) { const a = i / n * PI * 2; l.push([cx + Math.cos(a) * raio, y, cz + Math.sin(a) * raio]); }
  return l;
};
const caixa = (w, h, d, y, cx = 0, cz = 0) => {
  const x = w / 2, z = d / 2, y0 = y - h / 2, y1 = y + h / 2;
  const p = [[cx - x, y0, cz - z], [cx + x, y0, cz - z], [cx + x, y0, cz + z], [cx - x, y0, cz + z]];
  const q = p.map(([a, , c]) => [a, y1, c]);
  return [[...p, p[0]], [...q, q[0]], ...p.map((v, i) => [v, q[i]])];
};

// Cúpula convexa (a do Senado, em escala menor) e bacia côncava (a da Câmara): as duas casas.
function cupula({ R = 1.55, base = 0.5, cx = 0, cz = 0, ring = 28, mer = 12 } = {}) {
  const pe = [], ci = [];
  for (let k = 1; k <= 7; k++) { const fi = k / 7 * PI / 2; pe.push(anel(R * Math.sin(fi), base + R * Math.cos(fi), ring, cx, cz)); }
  for (let m = 0; m < mer; m++) {
    const a = m / mer * PI * 2, l = [];
    for (let k = 0; k <= 8; k++) { const fi = k / 8 * PI / 2; l.push([cx + Math.cos(a) * R * Math.sin(fi), base + R * Math.cos(fi), cz + Math.sin(a) * R * Math.sin(fi)]); }
    pe.push(l);
  }
  pe.push([[cx, base + R, cz], [cx, base + R + 0.4, cz]]);
  ci.push(anel(R * 1.04, base - 0.3, ring, cx, cz), anel(R * 1.04, base, ring, cx, cz));
  return { pe, ci };
}
function bacia({ R = 1.95, base = 0.55, escY = 0.6, cx = 0, cz = 0, ring = 32, mer = 14 } = {}) {
  const pe = [], ci = [];
  for (let k = 1; k <= 7; k++) { const fi = k / 7 * PI / 2; pe.push(anel(R * Math.sin(fi), base + escY * R * (1 - Math.cos(fi)), ring, cx, cz)); }
  for (let m = 0; m < mer; m++) {
    const a = m / mer * PI * 2, l = [];
    for (let k = 0; k <= 8; k++) { const fi = k / 8 * PI / 2; l.push([cx + Math.cos(a) * R * Math.sin(fi), base + escY * R * (1 - Math.cos(fi)), cz + Math.sin(a) * R * Math.sin(fi)]); }
    pe.push(l);
  }
  ci.push(anel(R * 1.06, base + escY * R, ring, cx, cz), anel(R * 0.5, base - 0.02, 20, cx, cz));
  return { pe, ci };
}
const toro = (R, r, cx, cy, plano, n = 26, m = 10) => {
  const pt = (u, v) => {
    const a = u / n * PI * 2, b = v / m * PI * 2;
    const x = (R + r * Math.cos(b)) * Math.cos(a), y = r * Math.sin(b), z = (R + r * Math.cos(b)) * Math.sin(a);
    return plano === 'v' ? [cx + x, cy + z, y] : [cx + x, cy + y, z];
  };
  const ls = [];
  for (let u = 0; u < n; u += 2) { const l = []; for (let v = 0; v <= m; v++) l.push(pt(u, v)); ls.push(l); }
  const a = [], b = [];
  for (let u = 0; u <= n; u++) { a.push(pt(u, 0)); b.push(pt(u, m / 2)); }
  ls.push(a, b);
  return ls;
};

/** Como cada modelo se monta: as linhas (petróleo e cinza), a câmera e, só na bacia, o véu. */
const MODELOS = {
  bacia: {
    camera: { pos: [0, 4.3, 13.6], alvo: [0, 1.0, 0], fov: 26 },
    linhas() {
      const { pe, ci } = bacia();
      ci.push(...caixa(7.2, 0.2, 3.7, -0.1), ...caixa(4.8, 0.2, 2.9, 0.1), anel(2.0, 0.2, 32), anel(2.0, 0.55, 32));
      return { petroleo: pe, cinza: ci };
    },
    veu: { R: 1.95, base: 0.55, escY: 0.6 }
  },
  folhas: {
    camera: { pos: [2.2, 4.6, 8.2], alvo: [0, 0.4, 0], fov: 30 },
    linhas() {
      const pe = [], ci = [];
      for (let i = 0; i < 6; i++) {
        const ang = (i - 2.5) * 0.045, c = Math.cos(ang), s = Math.sin(ang), w = 3.0, d = 4.0;
        const cx = 0.04 * i, cz = 0, y = 0.12 * i;
        const pts = [[cx + (-w / 2) * c - (-d / 2) * s, y, cz + (-w / 2) * s + (-d / 2) * c], [cx + (w / 2) * c - (-d / 2) * s, y, cz + (w / 2) * s + (-d / 2) * c],
          [cx + (w / 2) * c - (d / 2) * s, y, cz + (w / 2) * s + (d / 2) * c], [cx + (-w / 2) * c - (d / 2) * s, y, cz + (-w / 2) * s + (d / 2) * c]];
        ci.push([...pts, pts[0]]);
        if (i < 5) { ci.push([pts[3], [pts[3][0], y + 0.12, pts[3][2]]]); ci.push([pts[2], [pts[2][0], y + 0.12, pts[2][2]]]); }
      }
      const y = 0.12 * 5;
      for (let k = 0; k < 7; k++) { const z = -1.45 + k * 0.32; pe.push([[-1.2, y + 0.005, z], [k % 3 ? 1.0 : 0.2, y + 0.005, z]]); }   // as linhas do texto
      pe.push(anel(0.38, y + 0.005, 24, 0.9, 1.45), anel(0.2, y + 0.005, 20, 0.9, 1.45));                                                 // o selo
      return { petroleo: pe, cinza: ci };
    }
  },
  aneis: {
    camera: { pos: [0, 3.4, 9.5], alvo: [0, 1.5, 0], fov: 30 },
    linhas: () => ({ petroleo: toro(1.45, 0.2, -0.95, 1.5, 'v'), cinza: toro(1.45, 0.2, 0.95, 1.5, 'h') })
  },
  casas: {
    camera: { pos: [0, 4.0, 12.5], alvo: [0, 1.2, 0], fov: 28 },
    linhas() {
      const a = cupula({ R: 0.95, base: 0.4, cx: -2.2, ring: 24, mer: 10 }), b = bacia({ R: 1.2, base: 0.4, cx: 2.2, ring: 24, mer: 10 });
      const arco = [];
      for (let i = 0; i <= 16; i++) { const t = i / 16; arco.push([-2.2 + 4.4 * t, 2.35 + 1.0 * Math.sin(PI * t), 0]); }
      const seta = [[2.0, 2.65, 0], arco[arco.length - 1], [1.95, 2.3, 0.25]];
      return { petroleo: [...a.pe, ...b.pe, arco, seta], cinza: [...a.ci, ...b.ci, ...caixa(7.4, 0.2, 2.6, -0.1)] };
    }
  },
  obras: {
    camera: { pos: [6.0, 5.2, 10.5], alvo: [0.2, 1.9, 0], fov: 32 },
    linhas() {
      const pe = [], ci = [];
      const niveis = [[5.0, 3.0, 0.0], [4.2, 2.6, 0.85], [3.4, 2.2, 1.7], [2.6, 1.8, 2.55]];
      for (const [w, d, y] of niveis) {
        ci.push(...caixa(w, 0.08, d, y));
        for (const sx of [-1, 1]) for (const sz of [-1, 1]) ci.push([[sx * w / 2 * 0.92, y, sz * d / 2 * 0.92], [sx * w / 2 * 0.92, y + 0.85, sz * d / 2 * 0.92]]);
      }
      const [w, d, y] = niveis[niveis.length - 1];
      for (let k = 1; k < 3; k++) ci.push([[-w / 2 + k * w / 3, y + 0.04, -d / 2], [-w / 2 + k * w / 3, y + 0.04, d / 2]]);
      // guindaste
      pe.push([[2.4, 0, 1.2], [2.4, 4.6, 1.2]], [[2.55, 0, 1.2], [2.55, 4.6, 1.2]]);
      for (let k = 0; k < 9; k++) { const yy = k * 0.575; pe.push([[2.4, yy, 1.2], [2.55, yy + 0.575, 1.2]]); }
      pe.push([[-2.6, 4.7, 1.2], [3.2, 4.7, 1.2]], [[-2.6, 4.9, 1.2], [3.2, 4.9, 1.2]], [[-0.6, 4.7, 1.2], [-0.6, 3.6, 1.2]]);
      return { petroleo: pe, cinza: ci };
    }
  },
  assento: {
    camera: { pos: [2.6, 4.2, 7.4], alvo: [0, 0.9, 0], fov: 28 },
    linhas() {
      const pe = [], ci = [];
      [0.0, 0.5, 1.0].forEach((y, f) => ci.push(...caixa(3.4 - f * 0.9, 0.5, 2.0, y + 0.25)));
      pe.push(anel(0.62, 1.5, 32), anel(0.62, 1.32, 32));
      for (let k = 0; k < 16; k++) { const a = k / 16 * PI * 2; pe.push([[0.62 * Math.cos(a), 1.32, 0.62 * Math.sin(a)], [0.62 * Math.cos(a), 1.5, 0.62 * Math.sin(a)]]); }
      pe.push(anel(0.86, 1.52, 40));
      return { petroleo: pe, cinza: ci };
    }
  }
};

export const TIPOS = Object.keys(MODELOS);

function segmentos(listas) {
  const v = [];
  for (const l of listas) for (let i = 0; i < l.length - 1; i++) v.push(...l[i], ...l[i + 1]);
  return new Float32Array(v);
}

/** Monta o grupo 3D do modelo (linhas e, na bacia, um véu translúcido). */
export function construirGrupo(THREE, tipo) {
  const M = MODELOS[tipo];
  const grupo = new THREE.Group();
  const { petroleo, cinza } = M.linhas();
  const linhas = (listas, cor, opacidade) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(segmentos(listas), 3));
    return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: cor, transparent: true, opacity: opacidade, depthWrite: false }));
  };
  grupo.add(linhas(cinza, 0x94a3b8, 0.34));
  grupo.add(linhas(petroleo, 0x2dd4bf, 0.55));
  if (M.veu) {
    const { R, base, escY } = M.veu, perfil = [];
    for (let k = 0; k <= 24; k++) { const fi = k / 24 * PI / 2; perfil.push(new THREE.Vector2(R * Math.sin(fi) * 0.996, base + escY * R * (1 - Math.cos(fi)))); }
    const veu = new THREE.Mesh(new THREE.LatheGeometry(perfil, 48),
      new THREE.MeshBasicMaterial({ color: 0x0f766e, transparent: true, opacity: 0.1, side: THREE.DoubleSide, depthWrite: false }));
    grupo.add(veu);
  }
  return grupo;
}

export function camaraDoModelo(THREE, tipo, aspecto = 4 / 3) {
  const { pos, alvo, fov } = MODELOS[tipo].camera;
  const cam = new THREE.PerspectiveCamera(fov, aspecto, 0.1, 100);
  cam.position.set(...pos);
  cam.lookAt(...alvo);
  return cam;
}

/** Liga o modelo `tipo` ao `contentor`. Devolve a cena (com destruir()) ou null. `aoPronto` roda depois do primeiro quadro. */
export function iniciar({ THREE, contentor, tipo, aoPronto = null }) {
  if (!MODELOS[tipo]) return null;
  const camera = camaraDoModelo(THREE, tipo);
  const grupo = construirGrupo(THREE, tipo);
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
 * A imagem estática de reserva: as mesmas linhas, projetadas pela mesma câmera, com o modelo parado. Usada para gerar
 * img/modelo-<tipo>.svg (largura x altura em px do viewBox); refazer se o modelo mudar.
 */
export function svgEstatico(THREE, tipo, larg = 480, alt = 360) {
  const cam = camaraDoModelo(THREE, tipo, larg / alt);
  cam.updateMatrixWorld(); cam.updateProjectionMatrix();
  const { petroleo, cinza } = MODELOS[tipo].linhas();
  const v = new THREE.Vector3();
  const caminho = listas => listas.map(l => 'M' + l.map(p => {
    v.set(p[0], p[1], p[2]).project(cam);
    return ((v.x + 1) / 2 * larg).toFixed(0) + ' ' + ((1 - v.y) / 2 * alt).toFixed(0);
  }).join('L')).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${larg} ${alt}" width="${larg}" height="${alt}" fill="none" stroke-linejoin="round" stroke-linecap="round">`
    + `<path d="${caminho(cinza)}" stroke="#94a3b8" stroke-opacity=".34" stroke-width="1"/>`
    + `<path d="${caminho(petroleo)}" stroke="#2dd4bf" stroke-opacity=".55" stroke-width="1"/></svg>`;
}
