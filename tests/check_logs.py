#!/usr/bin/env python3
# Checks the peers' logs (logs/logfile*.log) after a round: each log must have exactly
# N*nMsgs entries, without duplicates, with every pair (s, k), s in 0..N-1, k in 0..nMsgs-1,
# and in FIFO order per sender: for each sender s, the subsequence of its entries (s, k) must be
# exactly 0, 1, ..., nMsgs-1. The order between different senders is free (FIFO is not total
# order). Exit code 0 if all logs are OK, 1 otherwise.
#
# For each log it prints the modification time and the number of FIFO violations: consecutive
# entries of the same sender with the later seq smaller than the earlier one.
#
# --since EPOCH: a log modified before EPOCH (seconds since 1970, e.g. `date +%s` taken before
# the experiment) is FAILED with "log antigo: <file>", so an old log never gives a false OK.
#
# Usage: python3 tests/check_logs.py [--since EPOCH] N nMsgs [logs_dir]   (logs_dir defaults to ./logs)
import argparse
import ast
import glob
import os
import sys
import time
from collections import Counter


def check_log(entries, n, n_msgs):
	# Returns a list of problems (empty if the log is OK)
	problems = []
	expected = {(s, k) for s in range(n) for k in range(n_msgs)}
	counts = Counter(entries)
	if len(entries) != n * n_msgs:
		problems.append('has ' + str(len(entries)) + ' entries, expected ' + str(n * n_msgs))
	dups = sorted(e for e, c in counts.items() if c > 1)
	if dups:
		problems.append('duplicates: ' + str(dups))
	missing = sorted(expected - counts.keys())
	if missing:
		problems.append('missing: ' + str(missing))
	extra = sorted(counts.keys() - expected)
	if extra:
		problems.append('unexpected: ' + str(extra))
	return problems


def fifo_violations(entries):
	# Number of consecutive pairs (s, a), (s, b) of the same sender with b < a.
	# Returns (count, examples), examples = up to 5 tuples (sender, a, b)
	last = {}
	count = 0
	examples = []
	for (s, k) in entries:
		if s in last and k < last[s]:
			count += 1
			if len(examples) < 5:
				examples.append((s, last[s], k))
		last[s] = k
	return count, examples


def fifo_ok(entries, n, n_msgs):
	# True if, for every sender, its subsequence is exactly 0, 1, ..., n_msgs-1
	for s in range(n):
		if [k for (t, k) in entries if t == s] != list(range(n_msgs)):
			return False
	return True


def main():
	parser = argparse.ArgumentParser(description='Checks loss, duplicates and FIFO order in the peers\' logs')
	parser.add_argument('n', type=int, metavar='N', help='number of peers')
	parser.add_argument('n_msgs', type=int, metavar='nMsgs', help='messages per peer in the last round')
	parser.add_argument('logs_dir', nargs='?', default='logs', help='directory of logfile*.log (default: logs)')
	parser.add_argument('--since', type=float, metavar='EPOCH',
						help='logs modified before this epoch are FAILED ("log antigo")')
	args = parser.parse_args()
	n = args.n
	n_msgs = args.n_msgs
	logs_dir = args.logs_dir

	files = sorted(glob.glob(os.path.join(logs_dir, 'logfile*.log')))
	ok = True
	if len(files) != n:
		print('ERROR: found ' + str(len(files)) + ' log files in ' + logs_dir + ', expected ' + str(n))
		ok = False
	for path in files:
		with open(path) as f:
			try:
				entries = [tuple(e) for e in ast.literal_eval(f.read())]
			except (ValueError, SyntaxError) as e:
				print(path + ': ERROR: cannot parse log: ' + str(e))
				ok = False
				continue
		mtime_epoch = os.path.getmtime(path)
		mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime_epoch))
		print(path + ': modified ' + mtime)
		problems = check_log(entries, n, n_msgs)
		if args.since is not None and mtime_epoch < args.since:
			problems.insert(0, 'log antigo: ' + path)
		violations, examples = fifo_violations(entries)
		print(path + ': ' + str(violations) + ' FIFO violations' +
			  (' (sender, earlier seq, later seq): ' + str(examples) if examples else ''))
		if violations or not fifo_ok(entries, n, n_msgs):
			problems.append('not in FIFO order per sender')
		if problems:
			ok = False
			for p in problems:
				print(path + ': ' + p)
		else:
			print(path + ': OK (' + str(len(entries)) + ' entries)')

	print('OK' if ok else 'FAILED')
	sys.exit(0 if ok else 1)


if __name__ == '__main__':
	main()
