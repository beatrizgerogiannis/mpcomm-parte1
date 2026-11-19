import os
import sys
import unittest

# Allow importing reliability.py from the repository root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from reliability import (RoundState, PendingSends, pending_key, ack_seq, make_ack, key_from_ack,
						 classify_round, should_give_up)


class TestRoundState(unittest.TestCase):

	def test_duplicate_data_ignored(self):
		st = RoundState(2)
		self.assertEqual(st.on_data(1, 0), [(1, 0)])
		self.assertEqual(st.on_data(1, 0), [])
		self.assertEqual(st.log, [(1, 0)])
		self.assertEqual(st.duplicates, 1)

	def test_duplicate_ready_and_end_ignored(self):
		st = RoundState(2)
		self.assertTrue(st.on_ready(0))
		self.assertFalse(st.on_ready(0))
		self.assertTrue(st.on_end(0, 3))
		self.assertFalse(st.on_end(0, 3))
		self.assertEqual(st.duplicates, 2)

	def test_all_ready(self):
		st = RoundState(2)
		st.on_ready(0)
		self.assertFalse(st.all_ready())
		st.on_ready(0)
		self.assertFalse(st.all_ready())
		st.on_ready(1)
		self.assertTrue(st.all_ready())

	def test_gap_detected(self):
		st = RoundState(2)
		st.on_data(1, 0)
		self.assertEqual(st.next_deliver[1], 1)
		self.assertEqual(st.highest(1), 0)
		st.on_data(1, 3)
		self.assertEqual(st.highest(1), 3)
		self.assertEqual(st.gaps(1), [1, 2])
		st.on_data(1, 2)
		self.assertEqual(st.gaps(1), [1])
		self.assertEqual(st.gaps(0), [])

	def test_fifo_reorders_same_sender(self):
		# Replaces test_log_keeps_arrival_order: the log is now in FIFO order per sender
		st = RoundState(2)
		self.assertEqual(st.on_data(1, 0), [(1, 0)])
		self.assertEqual(st.on_data(1, 2), [])          # waits for 1
		self.assertEqual(st.held(1), [2])
		self.assertEqual(st.on_data(1, 1), [(1, 1), (1, 2)])
		self.assertEqual(st.log, [(1, 0), (1, 1), (1, 2)])
		self.assertEqual(st.held(1), [])
		self.assertEqual(st.held_back, 1)

	def test_fifo_reverse_arrival(self):
		st = RoundState(2)
		self.assertEqual(st.on_data(0, 2), [])
		self.assertEqual(st.on_data(0, 1), [])
		self.assertEqual(st.log, [])                    # nothing delivered before 0
		self.assertEqual(st.gaps(0), [0])
		self.assertEqual(st.on_data(0, 0), [(0, 0), (0, 1), (0, 2)])
		self.assertEqual(st.log, [(0, 0), (0, 1), (0, 2)])
		self.assertEqual(st.held_back, 2)
		self.assertEqual(st.next_deliver[0], 3)

	def test_duplicates_of_delivered_and_held_discarded(self):
		st = RoundState(2)
		st.on_data(0, 0)
		st.on_data(0, 2)                                # held
		self.assertTrue(st.seen(0, 0))
		self.assertTrue(st.seen(0, 2))
		self.assertFalse(st.seen(0, 1))
		self.assertEqual(st.on_data(0, 0), [])          # duplicate of a delivered message
		self.assertEqual(st.on_data(0, 2), [])          # duplicate of a held message
		self.assertEqual(st.duplicates, 2)
		self.assertEqual(st.held_back, 1)
		self.assertEqual(st.on_data(0, 1), [(0, 1), (0, 2)])
		self.assertEqual(st.log, [(0, 0), (0, 1), (0, 2)])   # each message only once

	def test_fifo_interleaving_between_senders_is_free(self):
		# FIFO only orders within each sender: the arrival interleaving between senders is kept
		st = RoundState(2)
		for (s, k) in [(1, 0), (0, 1), (0, 0), (1, 2), (1, 1), (0, 2)]:
			st.on_data(s, k)
		self.assertEqual(st.log, [(1, 0), (0, 0), (0, 1), (1, 1), (1, 2), (0, 2)])
		self.assertEqual([k for (s, k) in st.log if s == 0], [0, 1, 2])
		self.assertEqual([k for (s, k) in st.log if s == 1], [0, 1, 2])

	def test_not_complete_while_holdback_not_empty(self):
		st = RoundState(1)
		st.on_end(0, 2)
		st.on_data(0, 1)                                # held: 0 missing
		st.on_data(0, 5)                                # beyond total, also held
		self.assertFalse(st.is_complete())
		st.on_data(0, 0)                                # delivers 0, 1; 5 still held
		self.assertEqual(st.log, [(0, 0), (0, 1)])
		self.assertEqual(st.held(0), [5])
		self.assertFalse(st.is_complete())

	def test_complete_after_holdback_drains(self):
		st = RoundState(1)
		st.on_data(0, 1)
		st.on_end(0, 2)
		self.assertFalse(st.is_complete())
		st.on_data(0, 0)
		self.assertTrue(st.is_complete())

	def test_is_complete(self):
		st = RoundState(2)
		st.on_data(0, 0)
		st.on_data(0, 1)
		st.on_data(1, 0)
		st.on_end(0, 2)
		self.assertFalse(st.is_complete())   # END from peer 1 missing
		st.on_end(1, 2)
		self.assertFalse(st.is_complete())   # message (1, 1) missing
		st.on_data(1, 1)
		self.assertTrue(st.is_complete())

	def test_is_complete_with_zero_messages(self):
		st = RoundState(1)
		self.assertFalse(st.is_complete())
		st.on_end(0, 0)
		self.assertTrue(st.is_complete())


class TestPendingSends(unittest.TestCase):

	def test_ack_removes_pending(self):
		p = PendingSends()
		msg = ('DATA', 1, 0, 5)
		key = pending_key('10.0.0.2', msg)
		self.assertEqual(key, ('10.0.0.2', 'DATA', 1, 0, 5))
		p.add(key, b'x', 0.0)
		self.assertFalse(p.empty())
		self.assertTrue(p.ack(key))
		self.assertTrue(p.empty())
		self.assertFalse(p.ack(key))   # duplicate ACK

	def test_ack_key_matches_ack_message(self):
		# The ACK built by the receiver must remove exactly the key the sender registered
		for msg in [('READY', 2, 0), ('DATA', 2, 0, 7), ('END', 2, 0, 20)]:
			ack = make_ack(msg, 3)
			self.assertEqual(ack[0], 'ACK')
			self.assertEqual(ack[2], 3)
			self.assertEqual(key_from_ack(ack, 'h'), pending_key('h', msg))
		self.assertEqual(make_ack(('DATA', 2, 0, 7), 3), ('ACK', 2, 3, 'DATA', 0, 7))
		self.assertEqual(ack_seq(('READY', 2, 0)), -1)
		self.assertEqual(ack_seq(('END', 2, 0, 20)), -1)

	def test_due_returns_only_expired(self):
		p = PendingSends()
		p.add('old', b'a', 0.0)
		p.add('new', b'b', 0.15)
		self.assertEqual(p.due(0.1, 0.2), [])
		self.assertEqual(p.due(0.2, 0.2), [('old', b'a', 1)])
		self.assertEqual(sorted(k for (k, _, _) in p.due(0.4, 0.2)), ['new', 'old'])

	def test_mark_resent_and_drop(self):
		p = PendingSends()
		p.add('k', b'a', 0.0)
		p.mark_resent('k', 0.3)
		self.assertEqual(p.due(0.4, 0.2), [])
		self.assertEqual(p.due(0.5, 0.2), [('k', b'a', 2)])
		p.drop('k')
		self.assertTrue(p.empty())


class TestRoundRules(unittest.TestCase):

	def test_classify_round(self):
		self.assertEqual(classify_round(3, 3), 'current')
		self.assertEqual(classify_round(2, 3), 'past')
		self.assertEqual(classify_round(4, 3), 'future')

	def test_classify_round_before_first_round(self):
		# No round started yet: everything is "future" (no ACK, sender keeps retransmitting)
		self.assertEqual(classify_round(1, None), 'future')

	def test_should_give_up(self):
		# sends = first send + retransmissions: give up after max_retries retransmissions
		self.assertFalse(should_give_up(1, 3))
		self.assertFalse(should_give_up(3, 3))
		self.assertTrue(should_give_up(4, 3))
		self.assertTrue(should_give_up(1, 0))


if __name__ == '__main__':
	unittest.main()
