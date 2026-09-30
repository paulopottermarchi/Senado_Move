"""
Câmara Aberta — roda um passo da rodada diária e, se ele falhar, põe o motivo na página da rodada.

Uso (é o que o workflow faz):
    python scripts/passo.py atualiza.py
    python scripts/passo.py coleta.py --limite 513
    python scripts/passo.py --opcional notoriedade.py   # falha vira aviso e a rodada segue

Por que existe: no GitHub Actions o log completo só aparece para quem está logado; a página da
rodada mostra a todos só "Process completed with exit code 1". A 1ª rodada agendada (29/9/2026)
falhou assim, sem que desse para saber por quê. Aqui o script roda normalmente, com a saída ao vivo
no log, e se sair com erro as últimas linhas (o fim do traceback, a mensagem da checagem que abortou)
viram uma anotação ::error:: — ou ::warning::, se o passo for opcional —, visível no resumo da rodada.
"""

import collections
import os
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def anotacao(texto):
    # formato de comando do GitHub Actions: % e quebras de linha precisam de escape
    return texto.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main():
    # a saída dos scripts tem →, ×, acentos: no Windows o padrão (cp1252) quebraria aqui
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    opcional = bool(args) and args[0] == "--opcional"
    if opcional:
        args = args[1:]
    if not args:
        sys.exit(__doc__)
    script, resto = args[0], args[1:]

    ultimas = collections.deque(maxlen=12)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", str(AQUI / script), *resto],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            text=True, encoding="utf-8", errors="replace")
    for linha in proc.stdout:
        sys.stdout.write(linha)
        sys.stdout.flush()
        if linha.strip():
            ultimas.append(linha.rstrip())
    codigo = proc.wait()

    if codigo:
        nivel = "warning" if opcional else "error"
        motivo = "\n".join(ultimas)[-1500:]
        print(f"::{nivel} title={script}::{anotacao(f'saiu com código {codigo}. Fim da saída:' + chr(10) + motivo)}",
              flush=True)
    sys.exit(0 if opcional else codigo)


if __name__ == "__main__":
    main()
