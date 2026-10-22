#!/usr/bin/env python3
"""Restaura os dados gerados para o último estado válido.

Uso:
  python scripts/rollback_dados.py --sha <sha>
  python scripts/rollback_dados.py --sha HEAD~1 --push
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGETS = [
    "site/dados",
    "entradas",
    "site/deputado",
    "site/senador",
    "site/sitemap.xml",
    "site/robots.txt",
    "site/img",
]

for html in sorted((ROOT / "site").glob("*.html")):
    TARGETS.append(str(html.relative_to(ROOT)))


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True)
    if check and result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def resolve_sha(explicit_sha: str | None) -> str:
    if explicit_sha:
        result = run_git("rev-parse", "--verify", explicit_sha)
        return result.stdout.strip()

    candidates = [
        "site/dados",
        "entradas",
        "site/deputado",
        "site/senador",
        "site/sitemap.xml",
        "site/robots.txt",
        "site/img",
    ]
    result = run_git("log", "--format=%H", "--", *candidates)
    sha = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if sha:
        return sha

    result = run_git("rev-parse", "--verify", "HEAD~1")
    return result.stdout.strip()


def restore_paths(sha: str) -> None:
    paths = sorted({path for path in TARGETS})
    print(f"[rollback] Restaurando arquivos do SHA {sha}...")
    run_git("restore", "--source", sha, "--", *paths)


def validate_data() -> None:
    script = ROOT / "scripts" / "validar_jsons.py"
    if not script.exists():
        raise FileNotFoundError(f"script de validação não encontrado: {script}")
    result = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def ensure_git_user() -> None:
    run_git("config", "user.name", "github-actions[bot]", check=False)
    run_git("config", "user.email", "41898283+github-actions[bot]@users.noreply.github.com", check=False)


def commit_if_needed(sha: str) -> bool:
    paths = sorted({path for path in TARGETS})
    run_git("add", "--all", *paths)

    result = run_git("diff", "--cached", "--quiet", check=False)
    if result.returncode == 0:
        print("[rollback] Não há mudanças para confirmar.")
        return False

    ensure_git_user()
    message = f"Rollback de dados para {sha}"
    run_git("commit", "-m", message)
    print(f"[rollback] Commit criado: {message}")
    return True


def push_if_requested(branch: str | None) -> None:
    branch_name = branch or run_git("branch", "--show-current").stdout.strip() or "main"
    run_git("push", "origin", f"HEAD:{branch_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restaura dados para um SHA anterior e valida antes de confirmar.")
    parser.add_argument("--sha", help="SHA ou ref para restaurar. Se omitido, usa o último commit que alterou dados públicos.")
    parser.add_argument("--branch", help="Branch de destino para push, quando --push for usado.")
    parser.add_argument("--push", action="store_true", help="Envia o rollback para o remoto após validação.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sha = resolve_sha(args.sha)
    print(f"[rollback] SHA alvo: {sha}")

    restore_paths(sha)
    validate_data()

    committed = commit_if_needed(sha)
    if args.push and committed:
        push_if_requested(args.branch)
        print("[rollback] Rollback enviado ao remoto.")
    elif args.push and not committed:
        print("[rollback] Push não executado porque não havia mudança para registrar.")

    print("[rollback] Processo concluído com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
