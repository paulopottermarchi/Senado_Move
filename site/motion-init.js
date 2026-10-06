// Câmara Aberta — carrega o Motion (motion.dev) e o expõe como window.Motion.
// O arquivo é versionado em vendor/ (Motion 14.0.0, licença MIT em vendor/motion-LICENSE.md): nada de CDN em produção.
// Carregado como <script type="module" src="motion-init.js">, antes do site.js (a ordem dos scripts adiados é a do HTML).
// Se este módulo ou o arquivo não carregarem, window.Motion fica indefinido e o site segue sem animação: toda página que
// o usa testa `window.Motion` antes, e a <script> tem onerror="…classList.add('m-ok')" para soltar o que o CSS escondeu.
import './vendor/motion-14.0.0.js';

if (globalThis.Motion && typeof globalThis.Motion.animate === 'function') {
  document.documentElement.dataset.motion = '14.0.0';
}
