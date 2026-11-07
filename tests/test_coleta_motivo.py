"""coleta.get: quando a API da Câmara falha em todas as tentativas, a mensagem diz POR QUÊ (tempo esgotado, HTTP 503…).

Em 7/11/2026 a API não respondeu ao GitHub por ~19 min e a rodada parou com "falhou após 6 tentativas", sem o motivo.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import coleta  # noqa: E402


class Resposta:
    def __init__(self, status, texto="", dados=None):
        self.status_code, self.text, self._dados = status, texto, dados

    def json(self):
        return self._dados

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Motivo(unittest.TestCase):
    def rodar(self, efeitos):
        with mock.patch.object(coleta.sessao, "get", side_effect=efeitos), mock.patch.object(coleta.time, "sleep"), \
                mock.patch.object(coleta.limitador, "esperar"):
            return coleta.get("https://dadosabertos.camara.leg.br/api/v2/deputados", {"itens": 100}, tentativas=2)

    def test_tempo_esgotado(self):
        with self.assertRaises(RuntimeError) as c:
            self.rodar([coleta.requests.ReadTimeout("read timed out"), coleta.requests.ReadTimeout("read timed out")])
        self.assertIn("falhou após 2 tentativas (ReadTimeout: read timed out)", str(c.exception))

    def test_http_503_com_pagina_html(self):
        html = "<html><style>body{}</style><h1>Service Unavailable</h1><p>tente mais tarde</p></html>"
        with self.assertRaises(RuntimeError) as c:
            self.rodar([Resposta(503, html), Resposta(503, html)])
        self.assertIn("(HTTP 503, 'Service Unavailable tente mais tarde')", str(c.exception))

    def test_segunda_tentativa_funciona(self):
        self.assertEqual(self.rodar([Resposta(502, ""), Resposta(200, dados={"dados": []})]), {"dados": []})

    def test_404_continua_sendo_none(self):
        self.assertIsNone(self.rodar([Resposta(404)]))


if __name__ == "__main__":
    unittest.main()
