#!/usr/bin/env python3
# Checks the peers' logs (logs/logfile*.log) after a round: each log must have exactly
# N*nMsgs entries, without duplicates, with every pair (s, k), s in 0..N-1, k in 0..nMsgs-1.
# Order is not checked. Exit code 0 if all logs are OK, 1 otherwise.
#
# Usage: python3 tests/check_logs.py N nMsgs [logs_dir]   (logs_dir defaults to ./logs)
import ast
import glob
import os
import sys
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


def main():
	if len(sys.argv) not in (3, 4):
		sys.exit('Usage: python3 tests/check_logs.py N nMsgs [logs_dir]')
	n = int(sys.argv[1])
	n_msgs = int(sys.argv[2])
	logs_dir = sys.argv[3] if len(sys.argv) == 4 else 'logs'

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
		problems = check_log(entries, n, n_msgs)
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
