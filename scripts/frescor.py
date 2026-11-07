"""
Câmara Aberta — confere se os dados publicados estão FRESCOS (site/dados/*.json), não só bem formados.

Uso:
    python scripts/frescor.py              # tabela; cada arquivo velho demais vira aviso (::warning:: no GitHub Actions)
    python scripts/frescor.py --estrito    # idem, e sai com código 1 se algum estiver velho demais: a rodada fica vermelha e o GitHub avisa por e-mail
    python scripts/frescor.py --teste      # testes sintéticos

Por que existe: a validação por esquema (validar_jsons_reais.py) confere o FORMATO, não a data. Um arquivo de dois dias atrás continua válido, e a rodada
diária terminava "com sucesso" com os indicadores da economia parados (21 a 23/10/2026: a API de agregados do IBGE não responde fora do Brasil) e as páginas
dos senadores sem atualizar. Os passos de coleta são opcionais de propósito (uma fonte fora do ar não derruba o site), então a falha de um deles só
aparecia como aviso no meio de dezenas de outros.

Cada arquivo usa o campo de data que o próprio script dele grava, e o limite em dias vem da frequência com que ele deve mudar: 2 dias para o que
roda todo dia, 8 para o que é semanal e 7 para o que só é regravado com a coleta completa. A conferência roda no FIM da rodada, depois de cada script
gravar o seu arquivo: o que é diário tem de estar com a data de hoje; com 1 dia de limite, uma rodada perdida passa e duas seguidas viram alarme.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DADOS = Path(__file__).resolve().parent.parent / "site" / "dados"
BRASILIA = timezone(timedelta(hours=-3))

# (arquivo em site/dados, campo de data, limite em dias, o que é)
LIMITES = [
    ("semana.json", "geradoEm", 1, "Esta semana na Câmara (último passo da rodada)"),
    ("inicio.json", "geradoEm", 1, "números da página inicial"),
    ("noticias.json", "_conferidoEm", 1, "notícias da Agência Câmara"),
    ("paginas.json", "geradoEm", 1, "páginas de perfil e sitemap"),
    ("blocos.json", "_gerado", 1, "Quem vota com quem (Câmara)"),
    ("blocos_senado.json", "_gerado", 1, "Quem vota com quem (Senado)"),
    ("notoriedade.json", "_gerado", 1, "notoriedade das leis"),
    ("origem_leis.json", "_gerado", 1, "De onde vêm as leis"),
    ("partidos/indice.json", "_gerado", 1, "aba Partidos"),
    ("partidos/governo.json", "_gerado", 1, "alinhamento ao Governo por governo"),
    ("senadores/resumo.json", "geradoEm", 1, "página de cada senador"),
    ("obras.json", "_geradoEm", 1, "obras e contratos"),
    ("obras_pessoas.json", "_geradoEm", 1, "pessoas documentadas das obras"),
    ("contexto/agenda.json", "geradoEm", 1, "economia: agenda legislativa"),
    ("contexto/indicadores.json", "geradoEm", 1, "economia: IPCA, PIB, dívida, resultado"),
    ("contexto/trabalho.json", "geradoEm", 1, "economia: trabalho, renda e setor externo"),
    ("contexto/abertura.json", "geradoEm", 1, "economia: abertura comercial"),
    ("contexto/tributos.json", "geradoEm", 8, "economia: tributos (semanal)"),
    ("comex/indice.json", "verificadoEm", 8, "exportações e balança (semanal)"),
    ("fluxo.json", "_gerado", 7, "Entre as casas (só é regravado com a coleta completa)"),
]


def hoje():
    return datetime.now(BRASILIA).date()


def conferir(dados=DADOS, limites=LIMITES, dia=None):
    """[(arquivo, data ou None, idade em dias ou None, limite, situação, descrição)]; situação é "ok", "velho", "sem data" ou "falta"."""
    dia = dia or hoje()
    out = []
    for arq, campo, limite, desc in limites:
        p = dados / arq
        if not p.exists():
            out.append((arq, None, None, limite, "falta", desc))
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            v = d.get(campo) if isinstance(d, dict) else None
            data = date.fromisoformat(str(v)[:10]) if v else None
        except (ValueError, OSError):
            data = None
        if data is None:
            out.append((arq, None, None, limite, "sem data", desc))
            continue
        idade = (dia - data).days
        out.append((arq, data.isoformat(), idade, limite, "ok" if idade <= limite else "velho", desc))
    return out


def relatorio(linhas):
    """A tabela em Markdown (vai para o resumo da rodada no GitHub Actions)."""
    ruins = [x for x in linhas if x[4] != "ok"]
    cab = (f"### Frescor dos dados publicados: {'tudo em dia' if not ruins else f'{len(ruins)} arquivo(s) velho(s) demais'}\n\n"
           "| arquivo | data | idade | limite | situação | o que é |\n|---|---|---:|---:|---|---|\n")
    return cab + "".join(f"| {a} | {d or '—'} | {'' if i is None else f'{i} d'} | {lim} d | {'✅ ok' if s == 'ok' else '⚠️ ' + s} | {desc} |\n"
                         for a, d, i, lim, s, desc in linhas)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if "--teste" in sys.argv[1:]:
        testar()
        return
    linhas = conferir()
    ruins = [x for x in linhas if x[4] != "ok"]
    for a, d, i, lim, s, desc in linhas:
        print(f"  {'ok   ' if s == 'ok' else s.upper():9} {a:28} {d or '—':10} {'' if i is None else f'{i} d':>5} (limite {lim} d)  {desc}")
    for a, d, i, lim, s, desc in ruins:
        txt = (f"{a} ({desc}): " + (f"último dado de {d}, há {i} dias (limite {lim})" if s == "velho" else s)
               + ". Veja o passo que gera este arquivo nos avisos desta rodada.")
        print(f"::warning title=Dados velhos::{txt}" if os.environ.get("GITHUB_ACTIONS") else f"AVISO: {txt}", flush=True)
    resumo = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(relatorio(linhas))
    print(f"{len(linhas) - len(ruins)} de {len(linhas)} arquivos em dia.")
    if ruins and "--estrito" in sys.argv[1:]:
        sys.exit(1)


def testar():
    import tempfile
    ok = 0

    def igual(a, b, m):
        nonlocal ok
        assert a == b, f"{m}: {a!r} != {b!r}"
        ok += 1
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "sub").mkdir()
        (t / "a.json").write_text(json.dumps({"geradoEm": "2026-10-23T11:23-03:00"}), encoding="utf-8")
        (t / "b.json").write_text(json.dumps({"_gerado": "2026-10-20"}), encoding="utf-8")
        (t / "sub" / "c.json").write_text(json.dumps({"outro": "x"}), encoding="utf-8")
        (t / "d.json").write_text("isto não é JSON", encoding="utf-8")
        (t / "e.json").write_text(json.dumps([1, 2]), encoding="utf-8")
        lim = [("a.json", "geradoEm", 2, "A"), ("b.json", "_gerado", 2, "B"), ("sub/c.json", "geradoEm", 2, "C"), ("falta.json", "geradoEm", 2, "F"),
               ("d.json", "geradoEm", 2, "D"), ("e.json", "geradoEm", 2, "E"), ("b.json", "_gerado", 3, "B no limite")]
        r = {x[5]: x for x in conferir(t, lim, date(2026, 10, 23))}
        igual((r["A"][4], r["A"][2]), ("ok", 0), "do dia: em dia, com a hora no campo")
        igual((r["B"][4], r["B"][2]), ("velho", 3), "três dias com limite de dois: velho")
        igual(r["B no limite"][4], "ok", "no limite exato: em dia")
        igual(r["C"][4], "sem data", "sem o campo de data")
        igual(r["F"][4], "falta", "arquivo que falta")
        igual(r["D"][4], "sem data", "JSON quebrado não derruba a conferência")
        igual(r["E"][4], "sem data", "lista no lugar de objeto")
        md = relatorio(list(r.values()))
        igual(("velho" in md, md.count("\n| ") >= 7), (True, True), "a tabela do resumo traz todos")
    igual(len({a for a, *_ in LIMITES}), len(LIMITES), "nenhum arquivo repetido na tabela")
    # todos os arquivos da tabela existem no site e têm o campo de data (pega erro de digitação na tabela)
    faltam = [x[0] for x in conferir() if x[4] in ("falta", "sem data")]
    igual(faltam, [], f"arquivos e campos da tabela existem em site/dados ({faltam})")
    print(f"{ok} verificações passaram")


if __name__ == "__main__":
    main()
