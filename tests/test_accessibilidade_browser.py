import functools
import http.server
import re
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionAbortedError):
            pass


class AcessibilidadeNoNavegador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            functools.partial(QuietHandler, directory=str(SITE)),
        )
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)

    def abrir(self, pagina):
        context = self.browser.new_context(reduced_motion="reduce")
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.route("https://**/*", lambda route: route.abort())
        page.goto(f"{self.base_url}/{pagina}", wait_until="domcontentloaded")
        return page

    def pontos_e_tabela(self, page, grafico, tabela):
        page.locator(f"{grafico} circle[tabindex='0']").wait_for()
        page.locator(f"{tabela} tbody tr").first.wait_for(state="attached")
        page.locator(tabela).locator("xpath=..").locator("summary").click()
        ponto = page.locator(f"{grafico} circle[tabindex='0']")
        rotulo = ponto.get_attribute("aria-label")
        self.assertTrue(rotulo)
        nome = rotulo.split(", ", 1)[0]
        valor = re.search(r"; ([\d.]+) (?:proposições|PEC e PL)", rotulo)
        self.assertIsNotNone(valor, rotulo)
        linha = page.get_by_role("row", name=re.compile(rf"^{re.escape(nome)}\b")).first
        self.assertIn(nome, linha.inner_text())
        self.assertIn(valor.group(1), linha.inner_text())
        return ponto

    def test_inicio_tem_pontos_navegaveis_e_alternativa_com_valores(self):
        page = self.abrir("index.html")
        ponto = self.pontos_e_tabela(page, "#telaIni svg", "#dadosIniTabela")
        self.assertGreater(page.locator("#dadosIniTabela tbody tr").count(), 500)
        self.assertEqual(ponto.get_attribute("role"), "button")
        ponto.focus()
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.evaluate("document.activeElement.tagName"), "circle")
        self.assertEqual(page.evaluate("document.activeElement.getAttribute('tabindex')"), "0")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r".*/deputado\.html\?id="))

    def test_dialogo_dos_deputados_contem_foco_fecha_e_o_restaura(self):
        page = self.abrir("deputados.html")
        ponto = self.pontos_e_tabela(page, "#tela svg", "#dadosDepTabela")
        self.assertGreater(page.locator("#dadosDepTabela tbody tr").count(), 500)
        deputado_id = ponto.get_attribute("data-id")
        ponto.focus()
        page.keyboard.press("Enter")
        dialogo = page.locator(".ficha[role='dialog']")
        self.assertTrue(dialogo.is_visible())
        fechar = dialogo.get_by_role("button", name="Fechar a ficha")
        self.assertTrue(fechar.evaluate("(el) => el === document.activeElement"))
        page.evaluate("""() => {
          const d = document.querySelector('.ficha[role="dialog"]');
          const f = [...d.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
            .filter(el => !el.hidden && el.getClientRects().length);
          f[f.length - 1].dataset.testLast = '';
        }""")
        page.keyboard.press("Shift+Tab")
        ultimo = dialogo.locator("[data-test-last]")
        self.assertTrue(ultimo.evaluate("(el) => el === document.activeElement"))
        page.keyboard.press("Tab")
        self.assertTrue(fechar.evaluate("(el) => el === document.activeElement"))
        page.keyboard.press("Escape")
        self.assertFalse(dialogo.is_visible())
        page.wait_for_function(
            "(id) => document.activeElement?.getAttribute('data-id') === id",
            arg=deputado_id,
        )

    def test_grafico_de_senadores_tem_alternativa_e_dialogo_nativo_acessivel(self):
        page = self.abrir("senadores.html")
        ponto = self.pontos_e_tabela(page, "#grafico svg", "#dadosSenTabela")
        self.assertGreater(page.locator("#dadosSenTabela tbody tr").count(), 70)
        ponto.focus()
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.evaluate("document.activeElement.tagName"), "circle")
        senador_id = page.evaluate("document.activeElement.getAttribute('data-id')")
        page.keyboard.press("Enter")
        dialogo = page.locator("#dlgFicha")
        self.assertTrue(dialogo.evaluate("(el) => el.open"))
        fechar = dialogo.get_by_role("button", name="Fechar a ficha")
        self.assertTrue(fechar.evaluate("(el) => el === document.activeElement"))
        page.evaluate("""() => {
          const d = document.querySelector('#dlgFicha');
          const f = [...d.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
            .filter(el => !el.hidden && el.getClientRects().length);
          f[0].dataset.testFirst = '';
          f[f.length - 1].dataset.testLast = '';
        }""")
        primeiro = dialogo.locator("[data-test-first]")
        ultimo = dialogo.locator("[data-test-last]")
        primeiro.focus()
        page.keyboard.press("Shift+Tab")
        self.assertTrue(ultimo.evaluate("(el) => el === document.activeElement"))
        page.keyboard.press("Tab")
        self.assertTrue(primeiro.evaluate("(el) => el === document.activeElement"))
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('#dlgFicha').open")
        page.wait_for_function(
            "(id) => document.activeElement?.getAttribute('data-id') === id",
            arg=senador_id,
        )


if __name__ == "__main__":
    unittest.main()
