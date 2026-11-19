# Reliable delivery for the peer-to-peer UDP multicast (positive ACK + retransmission).
# Pure logic, no sockets: used by peerCommunicatorUDP.py and tested in tests/test_reliability.py.
#
# UDP messages (pickled tuples):
#   ('READY', round, sender)
#   ('DATA',  round, sender, seq)
#   ('END',   round, sender, total)
#   ('ACK',   round, acker, ackedKind, sender, seq)   # seq = -1 for READY and END


def ack_seq(msg):
  # Sequence number used to identify a READY/DATA/END message in its ACK
  return msg[3] if msg[0] == 'DATA' else -1


def pending_key(dest, msg):
  # Key of a sent message waiting for an ACK: (destination, kind, round, sender, seq)
  return (dest, msg[0], msg[1], msg[2], ack_seq(msg))


def make_ack(msg, acker):
  # ACK sent by `acker` for a READY/DATA/END message
  return ('ACK', msg[1], acker, msg[0], msg[2], ack_seq(msg))


def key_from_ack(ack, dest):
  # Pending key acknowledged by `ack`; dest = address the ACK came from (the original destination).
  # Same format as pending_key(dest, msg) for the acknowledged msg.
  (_, rnd, _acker, acked_kind, sender, seq) = ack
  return (dest, acked_kind, rnd, sender, seq)


def classify_round(rnd, current):
  # Round of a received message relative to the current round number (None = no round yet).
  # "current": process it; "past": ACK and discard; "future": discard WITHOUT ACK
  if current is None or rnd > current:
    return 'future'
  if rnd < current:
    return 'past'
  return 'current'


def should_give_up(sends, max_retries):
  # sends = number of sends so far (first send + retransmissions)
  return sends > max_retries


class RoundState:
  # What this peer has received in the current round.
  # DATA messages are delivered (appended to the log) in FIFO order per sender, using a hold-back
  # queue: a message that arrives before a lower seq of the same sender waits in holdback until
  # every lower seq has been delivered. The order between different senders is free.

  def __init__(self, n_peers):
    self.n_peers = n_peers
    self.ready = set()      # IDs of the peers whose handshake was received
    self.received = {}      # sender -> set of received seqs, delivered or held (to discard duplicates)
    self.next_deliver = {}  # sender -> lowest seq not yet delivered (0 at the start)
    self.holdback = {}      # sender -> set of seqs received but waiting for a lower seq
    self.totals = {}        # sender -> total of messages announced in its END
    self.log = []           # delivered messages (sender, seq), in delivery (FIFO) order
    self.duplicates = 0     # duplicates discarded (READY, DATA and END)
    self.held_back = 0      # DATA messages that went through the holdback

  def on_ready(self, sender):
    # True if this handshake is new
    if sender in self.ready:
      self.duplicates += 1
      return False
    self.ready.add(sender)
    return True

  def seen(self, sender, seq):
    # True if (sender, seq) was already received (delivered or in the holdback)
    return seq in self.received.get(sender, ())

  def on_data(self, sender, seq):
    # Returns the list of messages (sender, seq) delivered by this call, in order: empty if the
    # message is a duplicate or was put in the holdback
    seqs = self.received.setdefault(sender, set())
    if seq in seqs:
      self.duplicates += 1
      return []
    seqs.add(seq)
    nxt = self.next_deliver.get(sender, 0)
    held = self.holdback.setdefault(sender, set())
    if seq != nxt:
      # seq > nxt (seq < nxt would already be in received): wait for the missing ones
      held.add(seq)
      self.held_back += 1
      return []
    delivered = [(sender, seq)]
    nxt += 1
    while nxt in held:
      held.remove(nxt)
      delivered.append((sender, nxt))
      nxt += 1
    self.next_deliver[sender] = nxt
    self.log.extend(delivered)
    return delivered

  def on_end(self, sender, total):
    # True if this END is new
    if sender in self.totals:
      self.duplicates += 1
      return False
    self.totals[sender] = total
    return True

  def all_ready(self):
    return len(self.ready) >= self.n_peers

  def highest(self, sender):
    # Highest seq received from sender (delivered or held), -1 if nothing was received yet
    held = self.holdback.get(sender)
    return max(self.next_deliver.get(sender, 0) - 1, max(held) if held else -1)

  def gaps(self, sender):
    # Missing seqs that block the holdback: from next_deliver up to the highest held seq
    held = self.holdback.get(sender)
    if not held:
      return []
    return [k for k in range(self.next_deliver.get(sender, 0), max(held)) if k not in held]

  def held(self, sender):
    # Seqs of sender waiting in the holdback, sorted
    return sorted(self.holdback.get(sender, ()))

  def is_complete(self):
    # END from all N peers, all messages 0..total-1 of each of them DELIVERED, holdback empty
    if len(self.totals) < self.n_peers:
      return False
    for sender, total in self.totals.items():
      if self.next_deliver.get(sender, 0) < total:
        return False
    return not any(self.holdback.values())


class PendingSends:
  # Sent messages waiting for an ACK, keyed by (destination, kind, round, sender, seq)

  def __init__(self):
    self._items = {}   # key -> [message, time of last send, number of sends]

  def add(self, key, msg, now):
    self._items[key] = [msg, now, 1]

  def ack(self, key):
    # True if key was pending (duplicate ACKs return False)
    return self._items.pop(key, None) is not None

  def due(self, now, timeout):
    # Items whose last send was at least `timeout` seconds ago: list of (key, msg, sends)
    return [(key, item[0], item[2]) for key, item in self._items.items()
            if now - item[1] >= timeout]

  def mark_resent(self, key, now):
    item = self._items.get(key)
    if item is not None:
      item[1] = now
      item[2] += 1

  def drop(self, key):
    # Give up on key (no more retransmissions)
    self._items.pop(key, None)

  def empty(self):
    return not self._items

  def __len__(self):
    return len(self._items)
