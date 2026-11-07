"""
Câmara Aberta — antes de validar, versionar e publicar, desfaz o que um passo cortado deixou pela metade em site/ e entradas/.

Um passo da rodada pode ser interrompido no meio: pelo teto do próprio passo (passo.py --minutos), pelo orçamento da rodada
(RODADA_FIM) ou pelo timeout-minutes do workflow. Os scripts gravam de forma atômica (arquivo ao lado e troca de uma vez:
coleta.gravar_cache, gravar_texto, gravar_bytes, emendas_obras.gravar_pasta), então o normal é sobrar só um temporário. Este passo
garante o resto sem depender de cada script, e vale também para o que o GitHub Pages publica, que é a PASTA site/ (inclusive o que o
git ignora), não o commit:
  1. pasta transitória de troca (X.novo, X.velho): X.velho sem X volta a ser X (a troca morreu entre os dois renames); com X, X.velho
     e X.novo são apagados;
  2. arquivo temporário (*.tmp) é apagado;
  3. cada arquivo que a rodada criou ou mudou (git status) é conferido pelo tipo: .json e .xml/.svg lidos inteiros, .png e .jpg com o
     começo e o fim do formato, .html terminando em </html>. O que não passar volta ao do último commit (git checkout) ou, se é novo,
     é apagado. Cada um vira aviso no resumo da rodada.
Arquivo apagado pela rodada não é restaurado: apagar é decisão do script (o deputado que perdeu o último vínculo de emenda, a página
de quem saiu da Câmara). Conferir que o arquivo está INTEIRO não é conferir que está CERTO: isso é a validação por esquema, que roda
depois e continua sendo o portão do commit.

Uso:
    python scripts/restaurar_parciais.py            # o que a rodada mudou (o workflow)
    python scripts/restaurar_parciais.py --todos    # confere todos os arquivos de site/ e entradas/ (teste do conferidor)
    python scripts/restaurar_parciais.py --simular  # diz o que faria, sem mexer
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PASTAS = ("site", "entradas")
TRANSITORIAS = (".novo", ".velho")


def aviso(msg):
    print(f"::warning title=restaurar_parciais.py::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"AVISO: {msg}", flush=True)


def inteiro(p):
    """True/False para os tipos que se sabe conferir; None para os outros (não conferidos)."""
    suf = p.suffix.lower()
    try:
        b = p.read_bytes()
    except OSError:
        return False
    try:
        if suf == ".json":
            json.loads(b.decode("utf-8"))
            return True
        if suf in (".xml", ".svg"):
            ET.fromstring(b)
            return True
    except (ValueError, ET.ParseError):
        return False
    if suf == ".png":
        return b.startswith(b"\x89PNG\r\n\x1a\n") and b.endswith(b"IEND\xaeB`\x82")
    if suf in (".jpg", ".jpeg"):
        return b.startswith(b"\xff\xd8") and b.rstrip(b"\x00").endswith(b"\xff\xd9")
    if suf == ".html":
        return b.rstrip().lower().endswith(b"</html>")
    return None


def git(base, *args):
    return subprocess.run(["git", *args], cwd=base, check=True, capture_output=True).stdout


def mudados(base):
    """[(caminho relativo, rastreado?)] dos arquivos que a rodada criou ou mudou em site/ e entradas/ (os apagados ficam de fora)."""
    saida = git(base, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *PASTAS).decode("utf-8")
    itens, partes, i = [], saida.split("\0"), 0
    while i < len(partes):
        e = partes[i]
        i += 1
        if len(e) < 4:
            continue
        xy, cam = e[:2], e[3:]
        if xy[0] in "RC":
            i += 1                    # renomeado/copiado: o nome de origem vem no item seguinte
        if "D" in xy:
            continue
        itens.append((cam, xy != "??"))
    return itens


def restaurar(base=BASE, todos=False, simular=False):
    """Lista do que foi feito (ou seria, com simular)."""
    feito = []
    raizes = [base / p for p in PASTAS if (base / p).exists()]
    # 1. pastas transitórias de troca
    for raiz in raizes:
        for d in sorted(raiz.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if not d.is_dir() or d.suffix not in TRANSITORIAS:
                continue
            alvo = d.with_suffix("")
            rel = d.relative_to(base).as_posix()
            if d.suffix == ".velho" and not alvo.exists():
                feito.append(f"{rel} → {alvo.relative_to(base).as_posix()} (a troca da pasta foi interrompida: volta a anterior)")
                if not simular:
                    os.replace(d, alvo)
            else:
                feito.append(f"{rel} apagada (pasta transitória de uma troca)")
                if not simular:
                    shutil.rmtree(d)
    # 2. temporários
    for raiz in raizes:
        for f in sorted(raiz.rglob("*.tmp")):
            if f.is_file():
                feito.append(f"{f.relative_to(base).as_posix()} apagado (temporário de uma gravação interrompida)")
                if not simular:
                    f.unlink()
    # 3. arquivos que não estão inteiros
    if todos:
        candidatos = [(f.relative_to(base).as_posix(), None) for raiz in raizes for f in sorted(raiz.rglob("*")) if f.is_file()]
    else:
        candidatos = mudados(base)
    conferidos = 0
    for rel, rastreado in candidatos:
        p = base / rel
        if not p.is_file():
            continue
        ok = inteiro(p)
        if ok is None:
            continue
        conferidos += 1
        if ok:
            continue
        if rastreado is None:
            rastreado = bool(git(base, "ls-files", "--", rel).strip())
        if rastreado:
            feito.append(f"{rel} estava pela metade: volta ao do último commit")
            if not simular:
                git(base, "checkout", "HEAD", "--", rel)
        else:
            feito.append(f"{rel} estava pela metade e é novo: apagado")
            if not simular:
                p.unlink()
    return feito, conferidos


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--todos", action="store_true", help="confere todos os arquivos, não só os que a rodada mudou")
    ap.add_argument("--simular", action="store_true", help="diz o que faria, sem mexer")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    feito, conferidos = restaurar(todos=args.todos, simular=args.simular)
    for f in feito:
        aviso(("(simulação) " if args.simular else "") + f)
    print(f"{conferidos} arquivos conferidos ({'todos' if args.todos else 'os que a rodada criou ou mudou'}); "
          f"{len(feito)} {'seriam corrigidos' if args.simular else 'corrigidos'}.")


if __name__ == "__main__":
    main()
