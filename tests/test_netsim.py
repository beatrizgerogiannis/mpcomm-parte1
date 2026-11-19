"""
Teste isolado do netsim.py — roda sem Docker, sem rede real.
Simula o envio de pacotes e verifica o comportamento do netsim.

Execute com:
    python tests/test_netsim.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

# Adiciona a raiz do projeto ao path para importar netsim
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_netsim(loss=0.0, delay=0, jitter=0):
    """Importa (ou reimporta) netsim com as env vars fornecidas."""
    import importlib
    env = {
        'LOSS_RATE': str(loss),
        'DELAY_MS':  str(delay),
        'JITTER_MS': str(jitter),
    }
    with patch.dict(os.environ, env, clear=False):
        import netsim
        importlib.reload(netsim)   # força releitura das env vars
        return netsim


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

class TestNetsimPassthrough(unittest.TestCase):
    """Com LOSS_RATE=0, DELAY_MS=0, JITTER_MS=0 — tudo deve passar sem mudança."""

    def setUp(self):
        self.netsim = _load_netsim(loss=0.0, delay=0, jitter=0)

    def test_passthrough_flag_is_true(self):
        self.assertTrue(self.netsim._PASSTHROUGH)

    def test_sendto_is_called_always(self):
        sock = MagicMock()
        data = b'hello'
        addr = ('127.0.0.1', 6789)

        for _ in range(20):   # 20 tentativas; nenhuma deve ser dropada
            self.netsim.send(sock, data, addr)

        self.assertEqual(sock.sendto.call_count, 20)

    def test_sendto_receives_correct_args(self):
        sock = MagicMock()
        data = b'\x80\x04\x95'   # dados pickled fictícios
        addr = ('10.0.0.5', 6789)

        self.netsim.send(sock, data, addr)

        sock.sendto.assert_called_once_with(data, addr)


class TestNetsimLoss(unittest.TestCase):
    """Com LOSS_RATE > 0, alguns pacotes devem ser descartados."""

    def test_loss_rate_100_drops_all(self):
        netsim = _load_netsim(loss=1.0)
        sock = MagicMock()
        for _ in range(50):
            netsim.send(sock, b'data', ('127.0.0.1', 6789))
        # LOSS_RATE=1.0 → nunca envia
        sock.sendto.assert_not_called()

    def test_loss_rate_0_drops_none(self):
        netsim = _load_netsim(loss=0.0)
        sock = MagicMock()
        for _ in range(50):
            netsim.send(sock, b'data', ('127.0.0.1', 6789))
        self.assertEqual(sock.sendto.call_count, 50)

    def test_loss_rate_50_drops_roughly_half(self):
        """Com 500 pacotes e LOSS_RATE=0.5, esperamos ~250 ± 60 enviados."""
        netsim = _load_netsim(loss=0.5)
        sock = MagicMock()
        N = 500
        for _ in range(N):
            netsim.send(sock, b'data', ('127.0.0.1', 6789))
        sent = sock.sendto.call_count
        # Tolerância estatística: ±60 (> 5 desvios-padrão seria improvável)
        self.assertGreater(sent, 190, f'Muito poucos enviados: {sent}/{N}')
        self.assertLess(sent, 310,    f'Poucos demais dropados: {N - sent}/{N}')

    def test_passthrough_false_when_loss_set(self):
        netsim = _load_netsim(loss=0.3)
        self.assertFalse(netsim._PASSTHROUGH)


class TestNetsimDelay(unittest.TestCase):
    """Com DELAY_MS > 0, o send deve demorar ao menos o delay configurado."""

    def test_delay_applied(self):
        import time
        netsim = _load_netsim(delay=50, jitter=0)   # 50 ms fixo
        sock = MagicMock()

        t0 = time.monotonic()
        netsim.send(sock, b'data', ('127.0.0.1', 6789))
        elapsed_ms = (time.monotonic() - t0) * 1000

        self.assertGreaterEqual(elapsed_ms, 40,
            f'Delay muito curto: {elapsed_ms:.1f} ms (esperado ~50 ms)')
        sock.sendto.assert_called_once()

    def test_no_delay_when_zero(self):
        import time
        netsim = _load_netsim(delay=0, jitter=0, loss=0.0)
        sock = MagicMock()

        t0 = time.monotonic()
        for _ in range(10):
            netsim.send(sock, b'data', ('127.0.0.1', 6789))
        elapsed_ms = (time.monotonic() - t0) * 1000

        self.assertLess(elapsed_ms, 20,
            f'Delay inesperado em passthrough: {elapsed_ms:.1f} ms')


class TestNetsimEnvValidation(unittest.TestCase):
    """Valores inválidos nas env vars devem levantar ValueError."""

    def test_loss_rate_above_1_raises(self):
        import importlib
        with patch.dict(os.environ, {'LOSS_RATE': '1.5'}):
            import netsim
            with self.assertRaises(ValueError):
                importlib.reload(netsim)

    def test_negative_delay_raises(self):
        import importlib
        with patch.dict(os.environ, {'DELAY_MS': '-10', 'LOSS_RATE': '0'}):
            import netsim
            with self.assertRaises(ValueError):
                importlib.reload(netsim)


if __name__ == '__main__':
    unittest.main(verbosity=2)
