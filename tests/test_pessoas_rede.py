"""pessoas.py com a API do Contratos.gov.br lenta e instável: teto da rodada, tempo por pedido, tentativas limitadas e disjuntor.

Caso real: a rodada de 25/10/2026 foi cortada no limite do job (360 min) com o passo "Pessoas documentadas das obras" em 317 min; em
26/10 o mesmo passo levou 10,8 min. Sem rede: a sessão HTTP é trocada por uma falsa e o relógio também (os "45 minutos" correm em
milissegundos). O cache e a saída vão para uma pasta temporária; o arquivo "publicado" de lá tem de sair byte a byte igual de toda
rodada cortada.
"""

import contextlib
import functools
import io
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import obras  # noqa: E402
import pessoas as PE  # noqa: E402

REDE_ORIGINAL = PE.Rede


class Relogio:
    def __init__(self):
        self.t = 1000.0
        self.esperas = []

    def agora(self):
        return self.t

    def dormir(self, s):
        self.esperas.append(s)
        self.t += max(0.0, s)


class Resp:
    def __init__(self, relogio, status=200, corpo=b"", headers=None, bloco=1 << 16, por_bloco=0.0):
        self.relogio, self.status_code, self.corpo = relogio, status, corpo
        self.headers = headers or {}
        self.bloco, self.por_bloco = bloco, por_bloco

    def iter_content(self, _n):
        for i in range(0, len(self.corpo), self.bloco):
            self.relogio.t += self.por_bloco
            yield self.corpo[i:i + self.bloco]

    def close(self):
        pass


class Sessao:
    """`comportamento(url, n)` devolve uma Resp ou levanta uma exceção do requests; n = quantas vezes esta url já foi pedida."""

    def __init__(self, relogio, comportamento):
        self.relogio, self.comportamento = relogio, comportamento
        self.pedidos = []
        self.por_url = Counter()

    def get(self, url, params=None, timeout=None, stream=False):
        assert timeout == (PE.CONEXAO, PE.LEITURA) and stream, "todo pedido tem de ter conexão e leitura curtas e ler em partes"
        self.pedidos.append(url)
        n = self.por_url[url]
        self.por_url[url] += 1
        return self.comportamento(url, n)


def esperas_ate_o_disjuntor():
    """Segundos de espera entre as FALHAS_SEGUIDAS_MAX tentativas que abrem o disjuntor (5, 10, 20, 30, 30…)."""
    return sum(min(PE.ESPERA_MAX, 5 * 2 ** (k - 1)) for k in range(1, PE.FALHAS_SEGUIDAS_MAX))


def json_resp(rel, obj, espera=0.0):
    rel.t += espera
    return Resp(rel, 200, json.dumps(obj).encode())


def tempo_esgotado(rel):
    rel.t += PE.LEITURA
    raise requests.exceptions.ReadTimeout("leitura")


def fim_do_caminho(url):
    """('arquivos', cid) etc. para as urls do Contratos.gov.br; ('pdf', nome) para os PDFs."""
    if url.endswith(".pdf"):
        return "pdf", url.rsplit("/", 1)[-1]
    partes = url.split("/")
    if partes[-2] == "id":
        return "registro", partes[-1]
    return partes[-1], partes[-2]


class Ambiente:
    """Cache, entrada (obras.json) e saída em pasta temporária; Rede com o relógio e a sessão falsos."""

    def __init__(self, n_contratos, comportamento, publicado=None):
        self.tmp = tempfile.TemporaryDirectory()
        T = Path(self.tmp.name)
        self.rel = Relogio()
        self.sessao = Sessao(self.rel, comportamento)
        self.ids = [str(5000 + i) for i in range(n_contratos)]
        (T / "obras.json").write_text(json.dumps({"obras": [{"id": "1.35-01", "contratos": [
            {"id": int(c), "numero": f"{c}/2024", "uasg": 158154, "processo": None} for c in self.ids]}]}), encoding="utf-8")
        self.saida = T / "site" / "obras_pessoas.json"
        self.saida.parent.mkdir()
        self.publicado = (publicado or '{"_versao": "rodada-anterior", "contratos": {}}').encode()
        self.saida.write_bytes(self.publicado)
        self.troca = {
            (obras, "SAIDA"): T / "obras.json", (obras, "CACHE_O"): T / "cache_obras",
            (PE, "CACHE_P"): T / "pessoas", (PE, "VAZIOS"): T / "pessoas" / "arquivos_vazios.json",
            (PE, "ARESTAS"): T / "arestas.jsonl", (PE, "CANDIDATO"): T / "candidato.json", (PE, "RELATORIO"): T / "relatorio.txt",
            (PE, "ESTADO"): T / "pessoas" / "estado.json", (PE, "AMOSTRA"): T / "amostra.csv", (PE, "SUPRESSOES"): T / "nao_existe.json",
            (PE, "SAIDA"): self.saida,
            (PE, "Rede"): functools.partial(REDE_ORIGINAL, sessao=self.sessao, relogio=self.rel.agora, dormir=self.rel.dormir),
        }
        self.antes = {k: getattr(*k) for k in self.troca}

    def __enter__(self):
        for (mod, nome), v in self.troca.items():
            setattr(mod, nome, v)
        return self

    def __exit__(self, *exc):
        for (mod, nome), v in self.antes.items():
            setattr(mod, nome, v)
        self.tmp.cleanup()

    def rodar(self, *argv):
        """(código de saída, texto do relatório, minutos no relógio falso)."""
        t0 = self.rel.t
        self.log = io.StringIO()
        try:
            with contextlib.redirect_stdout(self.log):
                PE.main(list(argv))
            codigo = 0
        except SystemExit as e:
            codigo = e.code if isinstance(e.code, int) else 1
        rel = PE.RELATORIO.read_text(encoding="utf-8") if PE.RELATORIO.exists() else ""
        return codigo, rel, (self.rel.t - t0) / 60


def contrato_ok(rel, tipo, cid, arquivos=None, espera=0.0):
    """Registro e listas de um contrato que responde; `arquivos` = a lista de anexos."""
    if tipo == "registro":
        return json_resp(rel, [{"id": int(cid)}], espera)
    if tipo == "arquivos":
        return json_resp(rel, arquivos if arquivos is not None else [], espera)
    return json_resp(rel, [], espera)


class Teto(unittest.TestCase):
    def test_api_lenta_corta_no_teto_e_nao_publica(self):
        def lenta(url, n):        # cada resposta leva 50 s (cabeçalho em 25 s, corpo em mais 25 s): abaixo do total por pedido
            tipo, cid = fim_do_caminho(url)
            r = contrato_ok(amb.rel, tipo, cid, arquivos=[{"id": 1}], espera=25)     # há anexo (sem PDF): a lista vai para o cache
            r.bloco, r.por_bloco = max(1, -(-len(r.corpo) // 2)), 12.5         # dois pedaços de 12,5 s
            return r
        with Ambiente(300, lenta) as amb:
            codigo, rel, minutos = amb.rodar("--publicar", "--minutos", "45")
            self.assertNotEqual(codigo, 0)
            self.assertIn("RODADA CORTADA", rel)
            self.assertIn("teto de 45 min", rel)
            self.assertLessEqual(minutos, 45 + 1.5, "o corte tem de vir no teto (mais, no máximo, um pedido)")
            self.assertGreaterEqual(minutos, 45)
            self.assertEqual(amb.saida.read_bytes(), amb.publicado, "rodada cortada não publica")
            self.assertFalse(PE.CANDIDATO.exists(), "rodada cortada não grava candidato")
            self.assertFalse(PE.ESTADO.exists(), "rodada cortada não avança o estado")
            pedidos = len(amb.sessao.pedidos)
            self.assertLess(pedidos, 60)
            # o que ficou para a próxima rodada está escrito no log
            self.assertRegex(rel, r"listas de arquivos não consultadas (\d+)")
            n_adiadas = int(__import__("re").search(r"listas de arquivos não consultadas (\d+)", rel)[1])
            self.assertGreater(n_adiadas, 250)
            # e o que veio ficou no cache: a próxima rodada, com a API boa, não pede de novo o que já tem
            ja = len(list((obras.CACHE_O / "arquivos").glob("*.json")))
            self.assertGreater(ja, 0)
            amb.sessao.comportamento = lambda url, n: contrato_ok(amb.rel, *fim_do_caminho(url), arquivos=[{"id": 1}])
            amb.sessao.pedidos.clear()
            codigo2, rel2, _ = amb.rodar("--dry-run")
            self.assertEqual(codigo2, 0, rel2[-2000:])
            self.assertEqual(sum(1 for u in amb.sessao.pedidos if u.endswith("/arquivos")), 300 - ja)

    def test_sem_teto_com_zero(self):
        rede = REDE_ORIGINAL(0, sessao=None, relogio=lambda: 1e12, dormir=lambda s: None)
        self.assertTrue(rede.aberta())


class Disjuntor(unittest.TestCase):
    def test_tempo_esgotado_seguido_abre_o_disjuntor_em_minutos(self):
        with Ambiente(300, lambda url, n: tempo_esgotado(amb.rel)) as amb:
            codigo, rel, minutos = amb.rodar("--publicar")
            self.assertNotEqual(codigo, 0)
            self.assertIn("disjuntor", rel)
            self.assertEqual(len(amb.sessao.pedidos), PE.FALHAS_SEGUIDAS_MAX, "depois do disjuntor, nenhum pedido")
            limite = (PE.FALHAS_SEGUIDAS_MAX * PE.LEITURA + esperas_ate_o_disjuntor()) / 60
            self.assertLessEqual(minutos, limite + 0.5, "tempos esgotados de 30 s e as esperas: minutos, não horas")
            self.assertGreaterEqual(minutos, esperas_ate_o_disjuntor() / 60, "um soluço de segundos não abre o disjuntor")
            self.assertLess(minutos, 10)
            self.assertEqual(amb.saida.read_bytes(), amb.publicado)

    def test_resposta_que_pinga_e_cortada_pelo_total_do_pedido(self):
        def pinga(url, n):        # um byte a cada 29 s: o timeout de leitura (30 s) nunca estoura, o total por pedido sim
            return Resp(amb.rel, 200, b"[" + b" " * 200 + b"]", bloco=1, por_bloco=29)
        with Ambiente(50, pinga) as amb:
            codigo, rel, minutos = amb.rodar("--publicar")
            self.assertNotEqual(codigo, 0)
            self.assertIn("disjuntor", rel)
            self.assertIn("lento", rel)
            self.assertLessEqual(minutos, (PE.FALHAS_SEGUIDAS_MAX * (PE.TOTAL_JSON + 29) + esperas_ate_o_disjuntor()) / 60 + 0.5)
            self.assertEqual(amb.saida.read_bytes(), amb.publicado)

    def test_sem_arquivo_em_sequencia_abre_o_disjuntor(self):
        with Ambiente(100, lambda url, n: contrato_ok(amb.rel, *fim_do_caminho(url))) as amb:   # toda lista vem vazia
            codigo, rel, _ = amb.rodar("--publicar")
            self.assertNotEqual(codigo, 0)
            self.assertIn(f"{PE.SEQUENCIA_VAZIA_SUSPEITA} contratos seguidos sem arquivo", rel)
            listas = [u for u in amb.sessao.pedidos if u.endswith("/arquivos")]
            self.assertEqual(len(listas), PE.SEQUENCIA_VAZIA_SUSPEITA, "para na 15ª lista vazia seguida, sem 2ª consulta")
            self.assertFalse(json.loads((PE.VAZIOS).read_text(encoding="utf-8")) if PE.VAZIOS.exists() else {},
                             "nenhum vazio da sequência suspeita é confirmado")
            self.assertEqual(amb.saida.read_bytes(), amb.publicado)


class Instavel(unittest.TestCase):
    def test_falha_intermitente_termina_com_tentativas_limitadas(self):
        def instavel(url, n):     # uma em cada três tentativas dá HTTP 503 com Retry-After de uma hora
            instavel.k += 1
            if instavel.k % 3 == 0:
                return Resp(amb.rel, 503, headers={"retry-after": "3600"})
            return contrato_ok(amb.rel, *fim_do_caminho(url), arquivos=[{"id": 1}])   # há anexo (sem PDF): nunca "sem arquivo"
        instavel.k = 0
        with Ambiente(200, instavel) as amb:
            codigo, rel, minutos = amb.rodar("--dry-run")
            self.assertEqual(codigo, 0, rel[-3000:])
            self.assertNotIn("RODADA CORTADA", rel)
            m = __import__("re").search(r"rede: (\d+) tentativas · (\d+) novas tentativas", rel)
            self.assertIsNotNone(m, rel[-2000:])
            self.assertLessEqual(int(m[2]), PE.NOVAS_TENTATIVAS_MAX)
            # Retry-After de 3.600 s limitado a ESPERA_MAX, e a rodada inteira dentro do teto
            self.assertLessEqual(max(amb.rel.esperas), PE.ESPERA_MAX)
            self.assertLess(minutos, PE.MINUTOS_PADRAO)
            self.assertEqual(amb.saida.read_bytes(), amb.publicado, "--dry-run nunca escreve em site/")


class SegundaConsulta(unittest.TestCase):
    """A 2ª consulta de "sem arquivo" (PR #46) com a API lenta: quanto custa."""

    def _rede(self, rel, sessao):
        return REDE_ORIGINAL(45, sessao=sessao, relogio=rel.agora, dormir=rel.dormir)

    def test_ja_confirmado_e_vazio_de_novo_nao_pede_duas_vezes(self):
        import os
        import time
        rel = Relogio()
        s = Sessao(rel, lambda url, n: json_resp(rel, [], espera=50))      # API lenta: 50 s por lista
        with tempfile.TemporaryDirectory() as t:
            antes = obras.CACHE_O
            obras.CACHE_O = Path(t)
            try:
                (Path(t) / "arquivos").mkdir()
                rede, est, consultas = self._rede(rel, s), Counter(), [("x", "cheio")]
                vazios = {}
                velho = time.time() - 40 * 86400
                for c in ("1", "2", "3"):                           # já confirmados, cache vencido
                    PE.coleta.gravar_cache(Path(t) / "arquivos" / f"{c}.json", [])
                    os.utime(Path(t) / "arquivos" / f"{c}.json", (velho, velho))
                    vazios[c] = "2026-10-01T00:00:00-03:00"
                prov = [c for c in ("1", "2", "3", "4", "5") if PE.listar_arquivos(c, False, vazios, consultas, est, rede) == PE.VAZIO_PROVISORIO]
                consultas.append(("y", "cheio"))
                self.assertEqual(prov, ["1", "2", "3", "4", "5"])
                self.assertEqual(len(s.pedidos), 5)
                out = PE.confirmar_vazios(prov, consultas, vazios, est, rede)
                self.assertEqual(out, {c: [] for c in prov})
                self.assertEqual(len(s.pedidos), 7, "2ª consulta só para os 2 que não estavam confirmados")
                self.assertEqual(est["arquivos_vazio_reconfirmado"], 3)
                self.assertEqual(est["arquivos_vazio_confirmado"], 2)
            finally:
                obras.CACHE_O = antes

    def test_rede_cortada_deixa_a_confirmacao_pendente(self):
        rel = Relogio()
        s = Sessao(rel, lambda url, n: json_resp(rel, []))
        rede = self._rede(rel, s)
        with tempfile.TemporaryDirectory() as t:
            antes = obras.CACHE_O
            obras.CACHE_O = Path(t)
            try:
                est, consultas = Counter(), [("x", "cheio")]
                prov = [c for c in ("1", "2") if PE.listar_arquivos(c, False, {}, consultas, est, rede) == PE.VAZIO_PROVISORIO]
                consultas.append(("y", "cheio"))
                rede.cortar("teste")
                out = PE.confirmar_vazios(prov, consultas, {}, est, rede)
                self.assertEqual(out, {"1": None, "2": None})
                self.assertEqual(est["adiado_confirmacao"], 2)
                self.assertEqual(len(s.pedidos), 2)
            finally:
                obras.CACHE_O = antes


class PorPedido(unittest.TestCase):
    def test_pdf_grande_para_de_ler(self):
        rel = Relogio()
        corpo = b"%PDF" + b"0" * (PE.P.MAX_PDF + 10)
        s = Sessao(rel, lambda url, n: Resp(rel, 200, corpo, bloco=1 << 20))
        self.assertEqual(PE.baixar("https://h/x.pdf", REDE_ORIGINAL(45, sessao=s, relogio=rel.agora, dormir=rel.dormir)), (None, "grande"))

    def test_404_e_definitivo_e_nao_conta_como_falha(self):
        rel = Relogio()
        s = Sessao(rel, lambda url, n: Resp(rel, 404))
        rede = REDE_ORIGINAL(45, sessao=s, relogio=rel.agora, dormir=rel.dormir)
        for _ in range(PE.FALHAS_SEGUIDAS_MAX + 2):
            self.assertIsNone(rede.pedir_json("https://h/contrato/1/arquivos"))
        self.assertTrue(rede.aberta())
        self.assertEqual(len(s.pedidos), PE.FALHAS_SEGUIDAS_MAX + 2, "404 não tem nova tentativa")

    def test_espera_entre_tentativas_e_limitada(self):
        rel = Relogio()
        s = Sessao(rel, lambda url, n: Resp(rel, 429, headers={"retry-after": "86400"}) if n == 0 else json_resp(rel, {"ok": 1}))
        rede = REDE_ORIGINAL(45, sessao=s, relogio=rel.agora, dormir=rel.dormir)
        t0 = rel.t
        self.assertEqual(rede.pedir_json("https://h/a"), {"ok": 1})
        self.assertLessEqual(rel.t - t0, PE.ESPERA_MAX + PE.INTERVALO_PDF)

    def test_resposta_que_nao_e_json_e_falha(self):
        rel = Relogio()
        s = Sessao(rel, lambda url, n: Resp(rel, 200, b"<html>manutencao</html>"))
        rede = REDE_ORIGINAL(45, sessao=s, relogio=rel.agora, dormir=rel.dormir)
        with self.assertRaises(RuntimeError) as cm:
            rede.pedir_json("https://h/a")
        self.assertNotIsInstance(cm.exception, PE.Cortada)
        self.assertEqual(len(s.pedidos), PE.TENTATIVAS)


if __name__ == "__main__":
    unittest.main()
