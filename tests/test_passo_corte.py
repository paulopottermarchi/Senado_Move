"""Passo cortado no meio: o teto do passo (passo.py --minutos), o orçamento da rodada (RODADA_FIM) e o que fica em site/.

Caso real: a rodada de 25/10/2026 foi cortada no limite do job (360 min) com um passo opcional em 317 min, e nada foi publicado.
Agora o passo opcional é encerrado no teto e a rodada segue; o que ele deixou pela metade, restaurar_parciais.py desfaz antes do
commit e da publicação. O teste roda passo.py de verdade (um processo filho) e a restauração num repositório git temporário.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import passo  # noqa: E402
import restaurar_parciais as R  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40 + b"IEND\xaeB`\x82"


def rodar_passo(*args, env_extra=None, timeout=120):
    env = {k: v for k, v in os.environ.items() if k != "RODADA_FIM"}
    env.update(env_extra or {})
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "passo.py"), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return p.returncode, p.stdout, time.monotonic() - t0


class Corte(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.T = Path(self.tmp.name)
        self.alvo = self.T / "saida.json"
        # um script que grava SEM ser atômico, devagar: cortado no meio, o arquivo fica pela metade
        self.lento = self.T / "lento.py"
        self.lento.write_text(textwrap.dedent(f"""
            import sys, time
            print("comecei", flush=True)
            with open({str(self.alvo)!r}, "w", encoding="utf-8") as f:
                f.write('{{"itens": [')
                f.flush()
                for i in range(600):
                    f.write(f'{{i}},')
                    f.flush()
                    time.sleep(0.1)
                f.write('0]}}')
            print("terminei", flush=True)
        """), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_opcional_cortado_no_teto_segue_a_rodada(self):
        codigo, out, seg = rodar_passo("--opcional", "--minutos", "0.05", str(self.lento))
        self.assertEqual(codigo, 0, "passo opcional cortado não derruba a rodada")
        self.assertIn("::warning title=", out)
        self.assertIn("cortado depois de", out)
        self.assertIn("comecei", out)
        self.assertNotIn("terminei", out)
        self.assertLess(seg, 3 + passo.FOLGA_KILL)
        with self.assertRaises(ValueError):
            json.loads(self.alvo.read_text(encoding="utf-8"))   # é exatamente o que restaurar_parciais.py desfaz

    def test_obrigatorio_cortado_falha(self):
        codigo, out, _ = rodar_passo("--minutos", "0.05", str(self.lento))
        self.assertEqual(codigo, 1)
        self.assertIn("::error title=", out)

    def test_orcamento_esgotado_pula_o_opcional_sem_rodar(self):
        codigo, out, seg = rodar_passo("--opcional", str(self.lento), env_extra={"RODADA_FIM": str(time.time() + 20)})
        self.assertEqual(codigo, 0)
        self.assertIn("pulado: orçamento da rodada esgotado", out)
        self.assertNotIn("comecei", out)
        self.assertFalse(self.alvo.exists())

    def test_orcamento_limita_o_teto_do_passo(self):
        antes = os.environ.get("RODADA_FIM")
        try:
            os.environ["RODADA_FIM"] = str(1000 + 30 * 60)
            seg, motivo = passo.teto(70, True, agora=1000)
            self.assertEqual(seg, 30 * 60)
            self.assertIn("orçamento da rodada", motivo)
            self.assertEqual(passo.teto(70, False, agora=1000), (70 * 60, "teto do passo (70 min)"), "obrigatório não entra no orçamento")
            self.assertEqual(passo.teto(None, True, agora=1000)[0], passo.TETO_OPCIONAL * 60)
            os.environ["RODADA_FIM"] = str(1000 + 30)
            self.assertEqual(passo.teto(10, True, agora=1000)[0], 0)
        finally:
            if antes is None:
                os.environ.pop("RODADA_FIM", None)
            else:
                os.environ["RODADA_FIM"] = antes

    def test_sem_teto_o_obrigatorio_termina(self):
        rapido = self.T / "rapido.py"
        rapido.write_text("print('ok')\n", encoding="utf-8")
        codigo, out, _ = rodar_passo(str(rapido))
        self.assertEqual(codigo, 0)
        self.assertNotIn("cortado", out)


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


class Restaurar(unittest.TestCase):
    def test_desfaz_o_que_ficou_pela_metade(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "teste@exemplo.invalid")
            git(repo, "config", "user.name", "teste")
            git(repo, "config", "core.autocrlf", "false")
            dados = repo / "site" / "dados"
            (dados / "emendas").mkdir(parents=True)
            (repo / "entradas").mkdir()
            arquivos = {
                "site/dados/a.json": b'{"a": 1}', "site/dados/b.json": b'{"b": 1}', "site/dados/c.json": b'{"c": 1}',
                "site/dados/apagado.json": b'{"x": 1}', "site/dados/emendas/indice.json": b'{"i": 1}',
                "site/deputado/1-x/og.png": PNG, "site/deputado/1-x/index.html": b"<html><body>x</body></html>\n",
                "entradas/e.json": b'{"e": 1}',
            }
            for rel, b in arquivos.items():
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_bytes(b)
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "rodada anterior")

            # o que uma rodada com passos cortados deixa
            (dados / "a.json").write_bytes(b'{"a": [1, 2, ')                          # pela metade, rastreado
            (dados / "b.json").write_bytes(b'{"b": 2}')                               # mudou e está inteiro: fica
            (dados / "novo.json").write_bytes(b'{"n": [')                             # pela metade, novo
            (dados / "novo_ok.json").write_bytes(b'{"n": 1}')                         # novo e inteiro: fica
            (dados / "apagado.json").unlink()                                         # apagado pelo script: continua apagado
            (repo / "site/deputado/1-x/og.png").write_bytes(PNG[:30])                 # imagem cortada
            (repo / "site/deputado/1-x/index.html").write_bytes(b"<html><body>")      # página cortada
            (repo / "entradas/e.json").write_bytes(b"{")                              # entradas também
            (dados / "c.json.tmp").write_bytes(b'{"c": 2')                            # temporário de gravação atômica
            os.replace(dados / "emendas", dados / "emendas.velho")                    # troca de pasta morreu entre os dois renames
            (dados / "emendas.novo").mkdir()
            (dados / "emendas.novo" / "indice.json").write_bytes(b'{"i": 2}')
            (dados / "outra.novo").mkdir()                                            # transitória com o destino ausente: só apagada

            feito, _ = R.restaurar(repo, simular=True)
            self.assertTrue(feito)
            self.assertEqual((dados / "a.json").read_bytes(), b'{"a": [1, 2, ', "--simular não mexe")

            feito, conferidos = R.restaurar(repo)
            self.assertEqual((dados / "a.json").read_bytes(), arquivos["site/dados/a.json"])
            self.assertEqual((dados / "b.json").read_bytes(), b'{"b": 2}')
            self.assertFalse((dados / "novo.json").exists())
            self.assertEqual((dados / "novo_ok.json").read_bytes(), b'{"n": 1}')
            self.assertFalse((dados / "apagado.json").exists())
            self.assertEqual((repo / "site/deputado/1-x/og.png").read_bytes(), PNG)
            self.assertEqual((repo / "site/deputado/1-x/index.html").read_bytes(), arquivos["site/deputado/1-x/index.html"])
            self.assertEqual((repo / "entradas/e.json").read_bytes(), arquivos["entradas/e.json"])
            self.assertFalse((dados / "c.json.tmp").exists())
            self.assertEqual((dados / "emendas" / "indice.json").read_bytes(), b'{"i": 1}', "a pasta anterior volta")
            for d in ("emendas.velho", "emendas.novo", "outra.novo"):
                self.assertFalse((dados / d).exists(), d)
            # e o que vai para o commit: só o que mudou de verdade
            status = git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
            self.assertEqual(sorted(status), sorted([" M site/dados/b.json", " D site/dados/apagado.json", "?? site/dados/novo_ok.json"]))
            # rodar de novo não acha nada
            self.assertEqual(R.restaurar(repo)[0], [])

    def test_conferidor_por_tipo(self):
        with tempfile.TemporaryDirectory() as t:
            T = Path(t)
            casos = {"a.json": (b"[1,2]", True), "b.json": (b"[1,", False), "c.png": (PNG, True), "d.png": (PNG[:-1], False),
                     "e.jpg": (b"\xff\xd8xx\xff\xd9", True), "f.jpg": (b"\xff\xd8xx", False), "g.html": (b"<html></HTML>\r\n", True),
                     "h.html": (b"<html><p>", False), "i.xml": (b"<a><b/></a>", True), "j.xml": (b"<a><b/>", False),
                     "k.txt": (b"qualquer", None)}
            for nome, (b, esperado) in casos.items():
                (T / nome).write_bytes(b)
                self.assertEqual(R.inteiro(T / nome), esperado, nome)


if __name__ == "__main__":
    unittest.main()
