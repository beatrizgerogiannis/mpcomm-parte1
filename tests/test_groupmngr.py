import os
import sys
import threading
import unittest
import xmlrpc.client

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from GroupMngr import (
	GroupManagerService,
	ReusableSimpleXMLRPCServer,
	list_peer_ips,
	register_peer,
	serve,
	unregister_peer,
)


class TestMembership(unittest.TestCase):

	def test_register_adds_peer_and_list_keeps_order(self):
		membership = []
		self.assertEqual(register_peer(membership, '10.0.0.1', 6789), 'registered')
		self.assertEqual(register_peer(membership, '10.0.0.2', '6789'), 'registered')
		self.assertEqual(membership, [('10.0.0.1', 6789), ('10.0.0.2', 6789)])
		self.assertEqual(list_peer_ips(membership), ['10.0.0.1', '10.0.0.2'])

	def test_register_same_pair_is_idempotent(self):
		membership = []
		register_peer(membership, '10.0.0.1', 6789)
		self.assertEqual(register_peer(membership, '10.0.0.1', 6789), 'unchanged')
		self.assertEqual(len(membership), 1)
		self.assertEqual(list_peer_ips(membership), ['10.0.0.1'])

	def test_register_same_ip_replaces_port_without_a_second_entry(self):
		membership = []
		register_peer(membership, '10.0.0.1', 6789)
		self.assertEqual(register_peer(membership, '10.0.0.1', 6790), 'updated')
		self.assertEqual(membership, [('10.0.0.1', 6790)])
		self.assertEqual(list_peer_ips(membership), ['10.0.0.1'])

	def test_unregister_removes_the_matching_pair(self):
		membership = [('10.0.0.1', 6789), ('10.0.0.2', 6789)]
		self.assertTrue(unregister_peer(membership, '10.0.0.1', 6789))
		self.assertEqual(list_peer_ips(membership), ['10.0.0.2'])

	def test_unregister_missing_pair_is_idempotent(self):
		membership = [('10.0.0.2', 6789)]
		self.assertFalse(unregister_peer(membership, '10.0.0.1', 6789))
		self.assertFalse(unregister_peer(membership, '10.0.0.2', 6790))
		self.assertEqual(membership, [('10.0.0.2', 6789)])


class TestGroupManagerRpc(unittest.TestCase):

	def test_register_list_unregister_and_stop_over_xmlrpc(self):
		server = ReusableSimpleXMLRPCServer(
			('127.0.0.1', 0), allow_none=True, logRequests=False)
		service = GroupManagerService()
		thread = threading.Thread(target=serve, args=(server, service), daemon=True)
		thread.start()
		url = 'http://127.0.0.1:%d' % server.server_address[1]
		proxy = xmlrpc.client.ServerProxy(url, allow_none=True)
		try:
			self.assertTrue(proxy.register('10.0.0.1', 6789))
			self.assertTrue(proxy.register('10.0.0.1', 6789))
			self.assertEqual(proxy.list(), ['10.0.0.1'])
			self.assertTrue(proxy.register('10.0.0.2', 6789))
			self.assertTrue(proxy.register('10.0.0.2', 6790))
			self.assertEqual(proxy.list(), ['10.0.0.1', '10.0.0.2'])
			self.assertTrue(proxy.unregister('10.0.0.2', 6789))
			self.assertEqual(proxy.list(), ['10.0.0.1', '10.0.0.2'])
			self.assertTrue(proxy.unregister('10.0.0.2', 6790))
			self.assertTrue(proxy.unregister('10.0.0.1', 6789))
			self.assertEqual(proxy.list(), [])
			self.assertTrue(proxy.stop())
		finally:
			service._stopped = True
			if thread.is_alive():
				try:
					xmlrpc.client.ServerProxy(url, allow_none=True).list()
				except (OSError, xmlrpc.client.Error):
					pass
			thread.join(timeout=3)
		self.assertFalse(thread.is_alive())


if __name__ == '__main__':
	unittest.main()
