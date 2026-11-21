import os
import sys
import threading
import unittest
import xmlrpc.client

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from comparisonServer import (
	ComparisonService,
	ReusableSimpleXMLRPCServer,
	count_unordered_rounds,
	restore_log,
)


class TestRestoreLog(unittest.TestCase):

	def test_lists_from_xmlrpc_become_tuples(self):
		restored = restore_log([[0, 1], [2, 3]])
		self.assertEqual(restored, [(0, 1), (2, 3)])
		self.assertIsInstance(restored[0], tuple)


class TestSubmitLogRpc(unittest.TestCase):

	def test_large_log_arrives_whole(self):
		# N=6 and nMsgs=500 is 3000 messages, past the old recv(32768) limit.
		server = ReusableSimpleXMLRPCServer(
			('127.0.0.1', 0), allow_none=True, logRequests=False)
		service = ComparisonService()
		server.register_instance(service)
		thread = threading.Thread(target=server.handle_request, daemon=True)
		thread.start()
		url = 'http://127.0.0.1:%d' % server.server_address[1]
		proxy = xmlrpc.client.ServerProxy(url, allow_none=True)
		log = [(i % 6, i) for i in range(3000)]
		try:
			self.assertTrue(proxy.submitLog(log))
		finally:
			thread.join(timeout=5)
			server.server_close()
		self.assertFalse(thread.is_alive())
		self.assertEqual(len(service.logs), 1)
		self.assertEqual(len(service.logs[0]), 3000)
		self.assertEqual(service.logs[0][0], (0, 0))
		self.assertEqual(service.logs[0][-1], (5, 2999))
		self.assertIsInstance(service.logs[0][0], tuple)
		self.assertEqual(count_unordered_rounds([service.logs[0], list(service.logs[0])]), 0)


if __name__ == '__main__':
	unittest.main()
