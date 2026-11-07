"""O passo "Versionar os dados e o site gerado" do atualiza.yml leva para o commit as exclusões da rodada.

Caso real: um deputado perde o último vínculo de emenda e o emendas_obras.py apaga o arquivo dele em site/dados/emendas/. Se o commit
não levasse a exclusão, o arquivo antigo ficaria publicado. O teste lê o comando `git add` do próprio workflow (não uma cópia) e o roda
num repositório git temporário, depois da troca de pasta que o script faz de verdade (gravar_pasta).
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import emendas_obras  # noqa: E402


def comandos_git_add():
    """As linhas `git add` do passo "Versionar os dados e o site gerado", como estão no workflow."""
    linhas = (ROOT / ".github" / "workflows" / "atualiza.yml").read_text(encoding="utf-8").splitlines()
    ini = next(i for i, ln in enumerate(linhas) if "name: Versionar os dados e o site gerado" in ln)
    fim = next((i for i in range(ini + 1, len(linhas)) if re.match(r"\s*- (name|uses|id):", linhas[i])), len(linhas))
    return [ln.strip() for ln in linhas[ini:fim] if ln.strip().startswith("git add")]


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


class VersionarDados(unittest.TestCase):
    def test_exclusao_de_deputado_vai_para_o_commit(self):
        cmds = comandos_git_add()
        self.assertEqual(len(cmds), 1, cmds)
        self.assertRegex(cmds[0], r"^git add -A(\s+\S+)*\s+site(\s|$)", "o passo tem de usar git add -A e incluir site/")
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "teste@exemplo.invalid")
            git(repo, "config", "user.name", "teste")
            git(repo, "config", "core.autocrlf", "false")
            shutil.copy(ROOT / ".gitignore", repo / ".gitignore")
            (repo / "entradas").mkdir()
            (repo / "entradas" / "x.json").write_text("{}", encoding="utf-8")
            pasta = repo / "site" / "dados" / "emendas"
            item = lambda o: {"id": f"{o}:202400010001", "obra": o}
            indice = lambda ids: {"_versao": "emendas-2", "deputados": {i: 1 for i in ids}, "obrasDoSite": {}}
            arquivo = lambda i, o: {"deputado": int(i), "itens": [item(o)]}
            emendas_obras.gravar_pasta(pasta, indice(["10", "30"]), {"10": arquivo("10", "1.35-01"), "30": arquivo("30", "2.35-01")})
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "rodada anterior")

            # rodada seguinte: o deputado 30 perdeu o vínculo; e uma troca interrompida deixou uma pasta transitória para trás
            emendas_obras.gravar_pasta(pasta, indice(["10"]), {"10": arquivo("10", "1.35-01")})
            (repo / "site" / "dados" / "emendas.velho").mkdir()
            (repo / "site" / "dados" / "emendas.velho" / "30.json").write_text("{}", encoding="utf-8")
            subprocess.run(cmds[0], cwd=repo, shell=True, check=True, capture_output=True)
            status = git(repo, "diff", "--cached", "--name-status").split("\n")

            self.assertIn("D\tsite/dados/emendas/30.json", status, "a exclusão do deputado 30 tem de ir para o commit")
            self.assertIn(f"M\tsite/dados/emendas/{emendas_obras.INDICE}", status)
            self.assertFalse(any("emendas.velho" in s or "emendas.novo" in s for s in status), f"pasta transitória no commit: {status}")
            self.assertEqual(json.loads((pasta / emendas_obras.INDICE).read_text(encoding="utf-8"))["deputados"], {"10": 1})


if __name__ == "__main__":
    unittest.main()
