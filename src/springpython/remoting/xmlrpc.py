# -*- coding: utf-8 -*-

# stdlib
import httplib
import logging
import socket
import ssl
import sys
import traceback

from SimpleXMLRPCServer import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler
from xmlrpclib import ServerProxy, Error, Transport

# Spring Python
from springpython.remoting.http import CAValidatingHTTPS

__all__ = ["VerificationException", "SSLServer", "SSLClient"]

class VerificationException(Exception):
    """ Raised when the verification of a certificate's fields fails.
    """

# ##############################################################################
# Server
# ##############################################################################

class RequestHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/", "/RPC2",)

    def setup(self):
        self.connection = self.request # for doPOST
        self.rfile = socket._fileobject(self.request, "rb", self.rbufsize)
        self.wfile = socket._fileobject(self.request, "wb", self.wbufsize)

class SSLServer(object, SimpleXMLRPCServer):
    def __init__(self, host=None, port=None, ca_certs=None, keyfile=None, certfile=None,
                 cert_reqs=ssl.CERT_OPTIONAL, ssl_version=ssl.PROTOCOL_TLSv1,
                 do_handshake_on_connect=True, suppress_ragged_eofs=True, ciphers=None, **kwargs):

        SimpleXMLRPCServer.__init__(self, (host, port), requestHandler=RequestHandler)
        self.logger = logging.getLogger(self.__class__.__name__)

        self.ca_certs = ca_certs
        self.keyfile = keyfile
        self.certfile = certfile
        self.cert_reqs = cert_reqs
        self.ssl_version = ssl_version
        self.do_handshake_on_connect = do_handshake_on_connect
        self.suppress_ragged_eofs = suppress_ragged_eofs
        self.ciphers = ciphers

        # 'verify_fields' is taken from kwargs to allow for adding more keywords
        # in future versions.
        self.verify_fields = kwargs.get("verify_fields")

        self.register_functions()

    def get_request(self):
        """ Overridden from Socket.TCPServer.get_request, wraps the socket in
        an SSL context.
        """
        sock, from_addr = self.socket.accept()

        # 'ciphers' argument is new in 2.7 and we must support 2.6 so add it
        # to kwargs conditionally, depending on the Python version.

        kwargs = {"keyfile":self.keyfile, "certfile":self.certfile,
                    "server_side":True, "cert_reqs":self.cert_reqs, "ssl_version":self.ssl_version,
                    "ca_certs":self.ca_certs, "do_handshake_on_connect":self.do_handshake_on_connect,
                    "suppress_ragged_eofs":self.suppress_ragged_eofs}

        if sys.version_info >= (2, 7):
            kwargs["ciphers"] = self.ciphers

        sock  = ssl.wrap_socket(sock, **kwargs)
        return sock, from_addr

    def verify_request(self, sock, from_addr):
        """ Overridden from Socket.TCPServer.verify_request, adds validation of the
        other side's certificate fields.
        """
        try:
            if self.verify_fields:

                cert = sock.getpeercert()
                if not cert:
                    msg = "Couldn't verify fields, peer didn't send the certificate, from_addr='%s'" % (from_addr,)
                    raise VerificationException(msg)

                allow_peer, reason = self.verify_peer(cert)
                if not allow_peer:
                    self.logger.error(reason)
                    sock.close()
                    return False

        except Exception, e:

            # It was either an error on our side or the client didn't send the
            # certificate even though self.cert_reqs was CERT_OPTIONAL (it couldn't
            # have been CERT_REQUIRED because we wouldn't have got so far, the
            # session would've been terminated much earlier in ssl.wrap_socket call).
            # Regardless of the reason we cannot accept the client in that case.

            msg = "Verification error='%s', cert='%s', from_addr='%s'" % (
                traceback.format_exc(e), sock.getpeercert(), from_addr)
            self.logger.error(msg)

            sock.close()
            return False

        return True

    def verify_peer(self, cert):
        """ Verifies the other side's certificate. May be overridden in subclasses
        if the verification process needs to be customized.
        """

        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug("verify_peer cert='%s'" % (cert))

        subject = cert.get("subject")
        if not subject:
            msg = "Peer certificate doesn't have the 'subject' field, cert='%s'" % cert
            raise VerificationException(msg)

        subject = dict(elem[0] for elem in subject)

        for verify_field in self.verify_fields:

            expected_value = self.verify_fields[verify_field]
            cert_value = subject.get(verify_field, None)

            if not cert_value:
                reason = "Peer didn't send the '%s' field, subject fields received '%s'" % (
                    verify_field, subject)
                return False, reason

            if expected_value != cert_value:
                reason = "Expected the subject field '%s' to have value '%s' instead of '%s'" % (
                    verify_field, expected_value, subject)
                return False, reason

        return True, None

    def register_functions(self):
        raise NotImplementedError("Must be overridden by subclasses")

# ##############################################################################
# Client
# ##############################################################################

class SSLClientTransport(Transport):
    """ Handles an HTTPS transaction to an XML-RPC server.
    """
    def __init__(self, keyfile=None, certfile=None, ca_certs=None, cert_reqs=None,
                 ssl_version=None, timeout=None, strict=None):
        self.keyfile = keyfile
        self.certfile = certfile
        self.ca_certs = ca_certs
        self.cert_reqs = cert_reqs
        self.ssl_version = ssl_version
        self.timeout = timeout
        self.strict = strict

        Transport.__init__(self)

    def make_connection(self, host):
        return CAValidatingHTTPS(host, strict=self.strict, keyfile=self.keyfile,
                certfile=self.certfile, ca_certs=self.ca_certs, cert_reqs=self.cert_reqs,
                ssl_version=self.ssl_version, timeout=self.timeout)

class SSLClient(ServerProxy):
    def __init__(self, uri=None, ca_certs=None, keyfile=None, certfile=None,
                 cert_reqs=ssl.CERT_OPTIONAL, ssl_version=ssl.PROTOCOL_TLSv1,
                 transport=None, encoding=None, verbose=0, allow_none=0, use_datetime=0,
                 timeout=socket._GLOBAL_DEFAULT_TIMEOUT, strict=None):

        if not transport:
            transport=SSLClientTransport(keyfile, certfile, ca_certs, cert_reqs,
                                         ssl_version, timeout, strict)

        ServerProxy.__init__(self, uri, transport, encoding, verbose,
                        allow_none, use_datetime)

        self.logger = logging.getLogger(self.__class__.__name__)