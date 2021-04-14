# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import twisted.internet.base
from twisted.internet.address import IPv6Address
from twisted.internet.testing import StringTransport

from synapse.app.homeserver import SynapseHomeServer

from tests.unittest import HomeserverTestCase


class SynapseRequestTestCase(HomeserverTestCase):
    def make_homeserver(self, reactor, clock):
        return self.setup_test_homeserver(homeserver_to_use=SynapseHomeServer)

    def test_large_request(self):
        """overlarge HTTP requests should be rejected"""
        self.hs.start_listening()

        # find the HTTP server which is configured to listen on port 0
        (port, factory, _backlog, interface) = self.reactor.tcpServers[0]
        self.assertEqual(interface, "::")
        self.assertEqual(port, 0)

        # as a control case, first send a regular request.

        # complete the connection and wire it up to a fake transport
        client_address = IPv6Address("TCP", "::1", "2345")
        protocol = factory.buildProtocol(client_address)
        transport = StringTransport()
        protocol.makeConnection(transport)

        protocol.dataReceived(
            b"POST / HTTP/1.1\r\n"
            b"Connection: close\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"0\r\n"
            b"\r\n"
        )

        while not transport.disconnecting:
            self.reactor.advance(1)

        # we should get a 404
        self.assertRegex(transport.value().decode(), r"^HTTP/1\.1 404 ")

        # now send an oversized request
        protocol = factory.buildProtocol(client_address)
        transport = StringTransport()
        protocol.makeConnection(transport)

        protocol.dataReceived(
            b"POST / HTTP/1.1\r\n"
            b"Connection: close\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
        )

        # we deliberately send all the data in one big chunk, to ensure that
        # twisted isn't buffering the data in the chunked transfer decoder.
        protocol.dataReceived(b"10000000\r\n")
        for i in range(0, 0x1000):
            protocol.dataReceived(b"\0" * 0x1000)

        self.assertTrue(transport.disconnected, "connection did not drop")

        # this is a bit of a hack. The problem is that the HTTPChannel sets a timeout
        # on receiving the request, which would normally be cleared once the entire
        # request is received. Of course, the entire request is *not* received, so
        # the timeout is never cancelled.
        #
        # Ideally then, we would tick the reactor past that timeout, to check what
        # happens. Unfortunately, twisted's `TimeoutMixin` (as used by HTTPChannel)
        # seems to be hardcoded to use the default twisted reactor, so the timeout is
        # set on the real reactor, which means we'd have to *actually* sleep here while
        # we wait for the timeout to elapse.
        #
        # So instead, let's just gut-wrench into twisted to cancel the timeout
        # ourselves.
        #
        # (Note that `protocol` here is actually a _GenericHTTPChannelProtocol - the
        # real HTTPChannel is `protocol._channel`)
        protocol._channel.setTimeout(None)
