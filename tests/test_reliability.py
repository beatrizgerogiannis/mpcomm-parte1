import os
import sys
import unittest

# Allow importing reliability.py from the repository root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from reliability import RoundState, PendingSends, pending_key, ack_seq


class TestRoundState(unittest.TestCase):

	def test_duplicate_data_ignored(self):
		st = RoundState(2)
		self.assertTrue(st.on_data(1, 0))
		self.assertFalse(st.on_data(1, 0))
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
		self.assertEqual(st.next_expected(1), 1)
		st.on_data(1, 3)
		self.assertEqual(st.gaps(1), [1, 2])
		st.on_data(1, 2)
		self.assertEqual(st.gaps(1), [1])
		self.assertEqual(st.gaps(0), [])

	def test_log_keeps_arrival_order(self):
		st = RoundState(2)
		for (s, k) in [(1, 1), (0, 0), (1, 0)]:
			st.on_data(s, k)
		self.assertEqual(st.log, [(1, 1), (0, 0), (1, 0)])

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
		# The receiver builds the key from ('ACK', round, acker, kind, sender, seq)
		for msg in [('READY', 2, 0), ('DATA', 2, 0, 7), ('END', 2, 0, 20)]:
			ack = ('ACK', msg[1], 3, msg[0], msg[2], ack_seq(msg))
			self.assertEqual(pending_key('h', msg), ('h',) + ack[3:4] + (ack[1],) + ack[4:])
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


if __name__ == '__main__':
	unittest.main()
