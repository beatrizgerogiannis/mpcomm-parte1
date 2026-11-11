import os
import sys
import unittest

# Allow importing comparisonServer.py from the repository root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from comparisonServer import count_unordered_rounds


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


if __name__ == '__main__':
	unittest.main()
