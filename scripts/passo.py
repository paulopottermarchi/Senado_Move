"""
Câmara Aberta — roda um passo da rodada diária e, se ele falhar, põe o motivo na página da rodada.

Uso (é o que o workflow faz):
    python scripts/passo.py atualiza.py
    python scripts/passo.py coleta.py --limite 513
    python scripts/passo.py --opcional notoriedade.py               # falha vira aviso e a rodada segue
    python scripts/passo.py --opcional --minutos 70 fluxo.py ...    # teto do passo, em minutos
    python scripts/passo.py --repetir 10 atualiza.py                # falhou: espera 10 min e tenta mais uma vez

Por que existe: no GitHub Actions o log completo só aparece para quem está logado; a página da
rodada mostra a todos só "Process completed with exit code 1". A 1ª rodada agendada (29/9/2026)
falhou assim, sem que desse para saber por quê. Aqui o script roda normalmente, com a saída ao vivo
no log, e se sair com erro as últimas linhas (o fim do traceback, a mensagem da checagem que abortou)
viram uma anotação ::error:: — ou ::warning::, se o passo for opcional —, visível no resumo da rodada.

Teto e orçamento (desde 26/10/2026; a rodada de 25/10 foi cortada no limite do job, 360 min, com um passo opcional em 317 min, e
nada foi publicado naquele dia):
  - --minutos N: passado o teto, o script é encerrado (terminate; 30 s depois, kill) e o passo vira aviso (opcional) ou erro.
    Passo opcional sem --minutos tem TETO_OPCIONAL;
  - RODADA_FIM (variável de ambiente, segundos desde 1970, gravada no começo da rodada pelo workflow): orçamento dos passos
    OPCIONAIS. O teto de um opcional nunca passa do que resta, e o passo é pulado se restar menos de um minuto. Assim o commit e
    a publicação, que vêm depois, sempre têm tempo; o que ficou para trás roda na rodada seguinte, a partir do cache.
Segunda tentativa (desde 7/11/2026; naquele dia a API da Câmara não respondeu ao GitHub por ~19 min e a rodada parou no
1º passo, que é obrigatório, sem publicar nada): --repetir N espera N minutos depois de uma falha e roda o script de novo, uma
vez. A 1ª falha vira aviso; só a 2ª vira erro. Não vale para o passo cortado pelo teto (ele já gastou o tempo dele).
O script encerrado no meio não deixa arquivo pela metade em site/: a gravação é atômica, e restaurar_parciais.py, antes do
commit, apaga o temporário que sobrar e devolve ao último commit o que não estiver inteiro.
"""

import collections
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
TETO_OPCIONAL = 20          # min, para o passo opcional que não diz o seu (os leves levam de segundos a 13 min, medido de 22 a 26/10/2026)
FOLGA_KILL = 30             # s entre o terminate e o kill
MINIMO = 60                 # s: com menos que isso no orçamento, o passo opcional nem começa


def anotacao(texto):
    # formato de comando do GitHub Actions: % e quebras de linha precisam de escape
    return texto.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def teto(minutos, opcional, agora=None):
    """(segundos, motivo) do teto deste passo, ou (None, None) sem teto. Orçamento esgotado num opcional: (0, motivo)."""
    agora = time.time() if agora is None else agora
    seg = minutos * 60 if minutos else (TETO_OPCIONAL * 60 if opcional else None)
    motivo = f"teto do passo ({seg / 60:g} min)" if seg else None
    fim = os.environ.get("RODADA_FIM")
    if opcional and fim:
        resta = float(fim) - agora
        if resta < MINIMO:
            return 0, "orçamento da rodada esgotado"
        if seg is None or resta < seg:
            seg, motivo = resta, f"orçamento da rodada ({resta / 60:.0f} min restantes)"
    return seg, motivo


def rodar(script, resto, seg):
    """Roda o script com a saída ao vivo; devolve (código, cortado pelo teto?, últimas linhas, segundos)."""
    ultimas = collections.deque(maxlen=12)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", str(AQUI / script), *resto],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            text=True, encoding="utf-8", errors="replace")
    cortado = threading.Event()

    def cortar():
        cortado.set()
        proc.terminate()
        try:
            proc.wait(FOLGA_KILL)
        except subprocess.TimeoutExpired:
            proc.kill()
    relogio = threading.Timer(seg, cortar) if seg else None
    if relogio:
        relogio.daemon = True
        relogio.start()
    t0 = time.monotonic()
    for linha in proc.stdout:
        sys.stdout.write(linha)
        sys.stdout.flush()
        if linha.strip():
            ultimas.append(linha.rstrip())
    codigo = proc.wait()
    if relogio:
        relogio.cancel()
    return codigo, cortado.is_set(), ultimas, time.monotonic() - t0


def main():
    # a saída dos scripts tem →, ×, acentos: no Windows o padrão (cp1252) quebraria aqui
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    opcional = minutos = repetir = None
    while args and args[0] in ("--opcional", "--minutos", "--repetir"):
        if args[0] == "--opcional":
            opcional, args = True, args[1:]
        elif args[0] == "--minutos":
            minutos, args = float(args[1]), args[2:]
        else:
            repetir, args = float(args[1]), args[2:]
    opcional = bool(opcional)
    if not args:
        sys.exit(__doc__)
    script, resto = args[0], args[1:]
    nivel = "warning" if opcional else "error"

    seg, motivo = teto(minutos, opcional)
    if seg == 0:
        print(f"::warning title={script}::{anotacao('pulado: ' + motivo + '. Os passos opcionais param a tempo de o commit e a publicação rodarem; este roda na próxima rodada, a partir do cache.')}",
              flush=True)
        sys.exit(0)

    codigo, cortado, ultimas, dur = rodar(script, resto, seg)
    if codigo and not cortado and repetir is not None:
        fim = chr(10).join(ultimas)[-1200:]
        print(f"::warning title={script}::{anotacao(f'saiu com código {codigo} (tentativa 1 de 2); nova tentativa em {repetir:g} min. Fim da saída:' + chr(10) + fim)}",
              flush=True)
        time.sleep(repetir * 60)
        if opcional:
            seg, motivo = teto(minutos, opcional)
            if seg == 0:
                print(f"::warning title={script}::{anotacao('2ª tentativa pulada: ' + motivo + '.')}", flush=True)
                sys.exit(0)
        print(f"--- {script}: 2ª tentativa ---", flush=True)
        codigo, cortado, ultimas, dur = rodar(script, resto, seg)
        if not codigo and not cortado:
            print(f"--- {script}: a 2ª tentativa deu certo ---", flush=True)

    if cortado:
        fim = "\n".join(ultimas)[-1200:]
        print(f"::{nivel} title={script}::{anotacao(f'cortado depois de {dur / 60:.1f} min: {motivo}. O que ele gravou de forma atômica fica; o resto restaurar_parciais.py desfaz antes do commit. Fim da saída:' + chr(10) + fim)}",
              flush=True)
        sys.exit(0 if opcional else 1)
    if codigo:
        motivo_saida = "\n".join(ultimas)[-1500:]
        print(f"::{nivel} title={script}::{anotacao(f'saiu com código {codigo}. Fim da saída:' + chr(10) + motivo_saida)}",
              flush=True)
    sys.exit(0 if opcional else codigo)


if __name__ == "__main__":
    main()
