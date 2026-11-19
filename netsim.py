"""
netsim.py — Network condition simulator for MPComm peers.

Intercepts UDP sends and applies configurable packet loss and artificial
delay/jitter, controlled entirely via environment variables.
When all variables are at their defaults (0), behaviour is identical to a
plain sendto() call, so the peer code is unaffected by this module.

Environment variables
---------------------
LOSS_RATE  float [0, 1]   Probability of silently dropping each packet.
                           0 → never drop (default). 1 → always drop.
DELAY_MS   int   >= 0     Fixed additional latency in milliseconds before
                           each send. 0 → no delay (default).
JITTER_MS  int   >= 0     Random ±jitter added on top of DELAY_MS.
                           Actual delay per packet is uniform in
                           [DELAY_MS - JITTER_MS, DELAY_MS + JITTER_MS],
                           clamped to >= 0. 0 → no jitter (default).

tc netem (Linux / Docker alternative)
--------------------------------------
The same effects can be achieved at the kernel level with tc netem inside
the peer containers (they already have NET_ADMIN and iproute2):

  # 20 % random loss + 50 ms delay ± 10 ms jitter on eth0
  tc qdisc add dev eth0 root netem loss 20% delay 50ms 10ms

  # Remove the rule
  tc qdisc del dev eth0 root

  # Show current rules
  tc qdisc show dev eth0

Using LOSS_RATE / DELAY_MS / JITTER_MS in netsim.py is easier for quick
experiments because it requires no root privileges beyond what is already
in the container, and works on any OS (including macOS / Windows for local
testing without Docker).
"""

import logging
import os
import random
import time

# ---------------------------------------------------------------------------
# Configuration — read once at import time
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    value = float(raw)
    if not (0.0 <= value <= 1.0):
        raise ValueError(f'netsim: {name}={raw!r} must be in [0, 1]')
    return value

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f'netsim: {name}={raw!r} must be >= 0')
    return value


LOSS_RATE: float = _env_float('LOSS_RATE', 0.0)
DELAY_MS:  int   = _env_int('DELAY_MS',  0)
JITTER_MS: int   = _env_int('JITTER_MS', 0)

# Fast-path flag: if everything is zero, skip all logic and call sendto directly.
_PASSTHROUGH: bool = (LOSS_RATE == 0.0 and DELAY_MS == 0 and JITTER_MS == 0)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    format='%(asctime)s [netsim] %(message)s',
    datefmt='%H:%M:%S',
    level=logging.INFO,
)
_log = logging.getLogger('netsim')

if not _PASSTHROUGH:
    _log.info(
        'Active — LOSS_RATE=%.2f  DELAY_MS=%d  JITTER_MS=%d',
        LOSS_RATE, DELAY_MS, JITTER_MS,
    )
else:
    _log.info('Passthrough mode (all env vars at defaults). No simulation.')

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send(sock, data: bytes, addr: tuple) -> None:
    """
    Drop-in replacement for sock.sendto(data, addr).

    If LOSS_RATE > 0, each call has a LOSS_RATE probability of being
    silently discarded (the packet is logged and not sent).
    If DELAY_MS or JITTER_MS > 0, the thread sleeps before sending.

    When all env vars are 0, calls sock.sendto() directly with no overhead.
    """
    if _PASSTHROUGH:
        sock.sendto(data, addr)
        return

    # --- Loss simulation ---
    if LOSS_RATE > 0.0 and random.random() < LOSS_RATE:
        _log.info('DROPPED  %d bytes  dest=%s:%d', len(data), addr[0], addr[1])
        return   # packet discarded — do NOT call sendto

    # --- Delay + jitter simulation ---
    if DELAY_MS > 0 or JITTER_MS > 0:
        jitter_range = random.randint(-JITTER_MS, JITTER_MS) if JITTER_MS > 0 else 0
        delay_ms = max(0, DELAY_MS + jitter_range)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    sock.sendto(data, addr)
