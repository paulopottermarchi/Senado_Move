#!/usr/bin/env python3
"""Orquestrador central da rodada diária do projeto Câmara Aberta.

Uso:
    python scripts/rodada.py --grupo critico
    python scripts/rodada.py --grupo pesado
    python scripts/rodada.py --grupo critico --step atualiza
    python scripts/rodada.py --grupo critico --dry-run

O objetivo é concentrar a ordem da rotina, o tratamento de falhas, as métricas
por etapa e os alertas de desempenho em um único arquivo.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PASSO = BASE / "scripts" / "passo.py"

CRITICAL_LIMITS = {
    "atualiza": 420,
    "coleta": 1800,
    "votos": 900,
    "blocos": 900,
    "temas": 600,
    "semana": 300,
}
OPTIONAL_LIMITS = {
    "notoriedade": 600,
    "wikipedia": 600,
    "noticias": 600,
    "youtube": 900,
    "enquetes": 600,
    "ecidadania": 600,
    "contexto": 900,
    "resumos": 1800,
    "objetivos": 1800,
    "contexto_montar": 900,
    "comex": 1200,
    "receita": 900,
    "obras": 1800,
    "pessoas": 1800,
    "senado": 1200,
    "tramitacoes": 1800,
    "senador_paginas": 600,
    "blocos_senado": 900,
    "partidos": 600,
    "governo": 600,
    "logos": 600,
    "fluxo": 1800,
    "origem_leis": 1200,
    "paginas": 900,
}


@dataclass(frozen=True)
class Step:
    name: str
    script: str
    args: tuple[str, ...] = ()
    critical: bool = True


def build_steps(grupo: str) -> list[Step]:
    if grupo == "critico":
        return [
            Step("atualiza", "atualiza.py"),
            Step("coleta", "coleta.py", ("--limite", "513")),
            Step("votos", "votos.py"),
            Step("blocos", "blocos.py", ("--atualizar",)),
            Step("temas", "temas.py"),
            Step("semana", "semana.py"),
        ]
    if grupo == "pesado":
        return [
            Step("notoriedade", "notoriedade.py", critical=False),
            Step("wikipedia", "wikipedia.py", critical=False),
            Step("noticias", "noticias.py", critical=False),
            Step("youtube", "youtube.py", critical=False),
            Step("enquetes", "enquetes.py", critical=False),
            Step("ecidadania", "ecidadania.py", critical=False),
            Step("contexto", "contexto.py", critical=False),
            Step("resumos", "resumos.py", ("--escopo", "leis,recentes,contexto", "--limite", "200", "--esperar", "60"), critical=False),
            Step("objetivos", "objetivos.py", ("--limite", "250", "--esperar", "60"), critical=False),
            Step("contexto_montar", "contexto.py", ("--so-montar",), critical=False),
            Step("comex", "comex.py", critical=False),
            Step("receita", "receita.py", critical=False),
            Step("obras", "obras.py", critical=False),
            Step("pessoas", "pessoas.py", ("--publicar", "--vias", "e2,e8"), critical=False),
            Step("senado", "senado.py", critical=False),
            Step("tramitacoes", "tramitacoes.py", ("--limite", "12000"), critical=False),
            Step("senador_paginas", "senador_paginas.py", critical=False),
            Step("blocos_senado", "blocos_senado.py", critical=False),
            Step("partidos", "partidos.py", critical=False),
            Step("governo", "governo.py", critical=False),
            Step("logos", "logos.py", critical=False),
            Step("fluxo", "fluxo.py", ("--limite", "9000"), critical=False),
            Step("origem_leis", "origem_leis.py", critical=False),
            Step("paginas", "paginas.py", critical=False),
        ]
    raise ValueError(f"Grupo desconhecido: {grupo!r}")


def step_command(step: Step) -> list[str]:
    cmd = [sys.executable, str(PASSO)]
    if not step.critical:
        cmd.append("--opcional")
    cmd.append(step.script)
    cmd.extend(step.args)
    return cmd


def get_limit(step: Step) -> float:
    if step.critical:
        return CRITICAL_LIMITS.get(step.name, 600)
    return OPTIONAL_LIMITS.get(step.name, 900)


def emit_github_summary(grupo: str, metrics: list[tuple[str, float, str]]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path or os.getenv("GITHUB_ACTIONS") != "true":
        return

    total = sum(tempo for _, tempo, _ in metrics)
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(f"## Resumo da rodada `{grupo}`\n\n")
        f.write(f"- Tempo total: {total:.1f}s\n")
        f.write(f"- Etapas: {len(metrics)}\n\n")
        f.write("| Etapa | Tempo | Status |\n")
        f.write("|---|---:|---|\n")
        for nome, tempo, status in metrics:
            f.write(f"| {nome} | {tempo:.1f}s | {status} |\n")
        f.write("\n")


def emit_github_annotation(step: Step, tempo: float, ok: bool) -> None:
    if os.getenv("GITHUB_ACTIONS") != "true":
        return

    limite = get_limit(step)
    if tempo > limite:
        severidade = "warning" if not step.critical else "error"
        print(f"::{severidade} title={step.name}::Tempo de execução excedeu o limite: {tempo:.1f}s > {limite:.0f}s")


def print_final_summary(grupo: str, metrics: list[tuple[str, float, str]], failed: list[str], warnings: list[str]) -> None:
    total = sum(tempo for _, tempo, _ in metrics)
    print(f"\n{'=' * 80}")
    print(f"Resumo da rodada: {grupo}")
    print(f"Tempo total: {total:.1f}s")
    print(f"Etapas: {len(metrics)} | Falhas: {len(failed)} | Avisos: {len(warnings)}")
    print("\nDetalhes:")
    for nome, tempo, status in metrics:
        print(f"  - {nome:<20} {tempo:>7.1f}s  {status}")
    print("=" * 80)

    if failed:
        print("FALHAS CRÍTICAS:")
        for nome in failed:
            print(f"  - {nome}")
    if warnings:
        print("AVISOS DE DESEMPENHO:")
        for nome in warnings:
            print(f"  - {nome}")


def run_step(step: Step, *, dry_run: bool) -> tuple[bool, float]:
    print(f"\n=== {step.name} :: {'DRY-RUN' if dry_run else 'EXEC'} ===")
    if dry_run:
        print("CMD:", " ".join(step_command(step)))
        return True, 0.0

    inicio = time.monotonic()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        step_command(step),
        cwd=str(BASE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    codigo = proc.wait()
    tempo = time.monotonic() - inicio
    print(f"[tempo] {step.name}: {tempo:.1f}s")
    return codigo == 0, tempo


def run_group(grupo: str, *, dry_run: bool, step_name: str | None) -> int:
    steps = build_steps(grupo)
    if step_name:
        steps = [s for s in steps if s.name == step_name]
        if not steps:
            raise SystemExit(f"Passo {step_name!r} não existe em {grupo}.")

    failed = []
    warnings = []
    metrics: list[tuple[str, float, str]] = []

    for index, step in enumerate(steps, 1):
        print(f"\n[{index}/{len(steps)}] Grupo: {grupo} | Passo: {step.name}")
        ok, tempo = run_step(step, dry_run=dry_run)
        status = "ok" if ok else "falhou"
        metrics.append((step.name, tempo, status))
        emit_github_annotation(step, tempo, ok)

        if not ok:
            if step.critical:
                failed.append(step.name)
                print(f"\nERRO CRÍTICO: passo '{step.name}' falhou.")
                break
            warnings.append(step.name)
            print(f"\nAVISO: passo opcional '{step.name}' falhou, mas a rodada continua.")

    emit_github_summary(grupo, metrics)
    print_final_summary(grupo, metrics, failed, warnings)

    if failed:
        print("\nResumo: falha crítica em:", ", ".join(failed))
        return 1

    if warnings:
        print("\nResumo: avisos em passos opcionais:", ", ".join(warnings))

    print(f"\nGrupo '{grupo}' concluído com sucesso.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grupo", choices=("critico", "pesado"), required=True)
    parser.add_argument("--step", help="Executa apenas este passo dentro do grupo")
    parser.add_argument("--dry-run", action="store_true", help="Mostra os comandos sem executá-los")
    args = parser.parse_args()

    try:
        return run_group(args.grupo, dry_run=args.dry_run, step_name=args.step)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
