import os
import sys
import unittest

# Allow importing comparisonServer.py from the repository root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from comparisonServer import count_unordered_rounds, check_fifo_and_completeness


class TestCountUnorderedRounds(unittest.TestCase):

	def test_identical_logs(self):
		log = [(0, 0), (1, 0), (0, 1), (1, 1)]
		self.assertEqual(count_unordered_rounds([list(log), list(log), list(log)]), 0)

	def test_difference_only_in_last_position(self):
		# The original loop stopped at N_MSGS-2 and missed this case
		logs = [
			[(0, 0), (1, 0), (0, 1), (1, 1)],
			[(0, 0), (1, 0), (0, 1), (1, 1)],
			[(0, 0), (1, 0), (0, 1), (9, 9)],
		]
		self.assertEqual(count_unordered_rounds(logs), 1)

	def test_difference_only_in_last_peer(self):
		# The original loop stopped at peer N-2 and missed this case
		logs = [
			[(0, 0), (1, 0), (0, 1)],
			[(0, 0), (1, 0), (0, 1)],
			[(0, 0), (1, 0), (0, 1)],
			[(1, 0), (1, 0), (0, 1)],
		]
		self.assertEqual(count_unordered_rounds(logs), 1)

	def test_shorter_log_counts_missing_positions(self):
		logs = [
			[(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)],
			[(0, 0), (1, 0), (0, 1)],
			[(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)],
		]
		self.assertEqual(count_unordered_rounds(logs), 2)

	def test_three_differences_in_different_peers(self):
		base = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
		p1 = list(base); p1[0] = (1, 0)
		p2 = list(base); p2[2] = (0, 1)
		p3 = list(base); p3[5] = (1, 1)
		self.assertEqual(count_unordered_rounds([base, p1, p2, p3]), 3)


class TestCheckFifoAndCompleteness(unittest.TestCase):
	"""
	Tests for check_fifo_and_completeness (Issue #6 criteria):
	  - Correct log
	  - Loss (missing message / gaps)
	  - Duplication (repeated message)
	  - Out-of-order delivery from the same sender (FIFO violation)
	  - Proof that FIFO valid does not imply Total Order
	"""

	def test_correct_log(self):
		# 2 peers, 3 messages each (all 0..2 delivered in order)
		log = [
			(0, 0), (1, 0),
			(0, 1), (1, 1),
			(0, 2), (1, 2)
		]
		res = check_fifo_and_completeness([log], n_msgs=3, n_peers=2)
		self.assertEqual(len(res), 1)
		self.assertTrue(res[0]['ok'])
		self.assertEqual(res[0]['problems'], [])

	def test_loss_missing_message(self):
		# Sender 0 sent 0 and 2; message 1 was lost
		log = [
			(0, 0), (1, 0),
			(0, 2), (1, 1),
			(1, 2)
		]
		res = check_fifo_and_completeness([log], n_msgs=3, n_peers=2)
		self.assertFalse(res[0]['ok'])
		self.assertTrue(any('missing seqs from sender 0: [1]' in p for p in res[0]['problems']))

	def test_duplicate_message(self):
		# Sender 0 message 1 delivered twice
		log = [
			(0, 0), (1, 0),
			(0, 1), (0, 1),
			(0, 2), (1, 1), (1, 2)
		]
		res = check_fifo_and_completeness([log], n_msgs=3, n_peers=2)
		self.assertFalse(res[0]['ok'])
		self.assertTrue(any('duplicate messages from sender 0' in p for p in res[0]['problems']))

	def test_out_of_order_same_sender(self):
		# Sender 0 delivered seq 2 before seq 1
		log = [
			(0, 0), (1, 0),
			(0, 2), (0, 1),  # out-of-order for sender 0
			(1, 1), (1, 2)
		]
		res = check_fifo_and_completeness([log], n_msgs=3, n_peers=2)
		self.assertFalse(res[0]['ok'])
		self.assertTrue(any('FIFO violations from sender 0' in p for p in res[0]['problems']))

	def test_fifo_does_not_imply_total_order(self):
		# Two peers receive messages from senders 0 and 1.
		# Peer 0 receives: 0,0 then 1,0 then 0,1 then 1,1
		# Peer 1 receives: 1,0 then 0,0 then 1,1 then 0,1
		# Both satisfy FIFO per sender! But their global interleaving is different (not total order).
		log_peer0 = [(0, 0), (1, 0), (0, 1), (1, 1)]
		log_peer1 = [(1, 0), (0, 0), (1, 1), (0, 1)]

		# Check FIFO for both:
		res = check_fifo_and_completeness([log_peer0, log_peer1], n_msgs=2, n_peers=2)
		self.assertTrue(res[0]['ok'])
		self.assertTrue(res[1]['ok'])

		# Check total order:
		unordered_rounds = count_unordered_rounds([log_peer0, log_peer1])
		self.assertGreater(unordered_rounds, 0,
			"FIFO is satisfied, but total order must report unordered rounds due to distinct interleaving!")


if __name__ == '__main__':
	unittest.main()
