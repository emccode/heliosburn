#!/usr/bin/env python

# proxy_core provides ReverseProxy functionality to HeliosBurn
# If invoked with the single command line parameter 'unittests',
# it discards all modules from config.yaml, and loads
# only the 'unittest_module.py' module, necessary for unit tests.
# To run unit tests against proxy_core.py, execute `python -m unittest tests`

from os.path import dirname, abspath
from inspect import getsourcefile

import yaml
import sys
import argparse
import json
from twisted.internet import reactor
from twisted.internet import defer
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.web import proxy
from twisted.web import server
from twisted.python import log
from txredis.client import RedisClientFactory
from module import Registry
from protocols import HBProxyClient
from protocols import HBProxyClientFactory
from protocols import HBReverseProxyRequest
from protocols import HBReverseProxyResource
from protocols import HBProxyMgmtRedisSubscriber
from protocols import HBProxyMgmtRedisSubscriberFactory
from protocols import HBProxyMgmtProtocol
from protocols import HBProxyMgmtProtocolFactory


class OperationResponse(object):

    def __init__(self, code, message, key):

        self.deferred = defer.Deferred()
        self.response = {'code': code,
                         'message': message,
                         'key': key
                         }

    def get_code(self):
        return self.response['code']

    def get_message(self):
        return self.response['message']

    def set_code(self, code):
        self.response['code'] = code

    def set_message(self, message):
        self.response['message'] = message

    def send(self):
        pass

    def getDeferred(self):
        return self.deferred


class RedisOperationResponse(OperationResponse):

    def __init__(self, code, message, key, redis_endpoint=None,
                 response_channel=None):

        OperationResponse.__init__(self, code, message, key)

        self.response_channel = response_channel

        self.redis_conn = redis_endpoint.connect(RedisClientFactory())
        self.redis_conn.addCallback(self.set_redis_client)

    def set_redis_client(self, redis_client):
        self.redis_client = redis_client

    def _send(self, result):
        self.redis_client.publish(self.response_channel,
                                  json.dumps(self.response))
        self.redis_client.set(self.response['key'], self.response)

    def send(self):
        self.redis_conn.addCallback(self._send)


class OperationResponseFactory(object):

    def get_response(self, code, message, key):
        response = OperationResponse(code,
                                     message,
                                     key)
        return response


class RedisOperationResponseFactory(OperationResponseFactory):

    def __init__(self, redis_endpoint, response_channel):
        self.reactor = reactor
        self.response_channel = response_channel
        self.redis_endpoint = redis_endpoint

    def get_response(self, code, message, key):
        response = RedisOperationResponse(code, message, key,
                                          self.redis_endpoint,
                                          self.response_channel)
        return response


class TcpOperationResponseFactory(OperationResponseFactory):

    def get_response(self, code, message, key):
        pass


class OperationFactory(object):

    def __init__(self, controller):
        self.controller = controller
        self.response_factory = OperationResponseFactory()

    def get_operation(self, message):
        op_string = json.loads(message)
        operation = None

        if "stop" == op_string['operation']:
            operation = StopProxy(self.controller,
                                  self.response_factory,
                                  op_string['key'])

        if "start" == op_string['operation']:
            operation = StartProxy(self.controller,
                                   self.response_factory,
                                   op_string['key'])

        if "stop_recording" == op_string['operation']:
            operation = StopRecording(self.controller,
                                      self.response_factory,
                                      op_string['key'],
                                      recording_id=op_string['param'])

        if "start_recording" == op_string['operation']:
            operation = StartRecording(self.controller,
                                       self.response_factory,
                                       op_string['key'],
                                       recording_id=op_string['param'])

        if "reload" == op_string['operation']:
            operation = ReloadPlugins(self.controller,
                                      self.response_factory,
                                      op_string['key'])

        if "reset" == op_string['operation']:
            operation = ResetPlugins(self.controller,
                                     self.response_factory,
                                     op_string['key'])

        if "upstream_port" == op_string['operation']:
            self.controller.upstream_port = op_string['param']
            operation = ChangeUpstreamPort(self.controller,
                                           self.response_factory,
                                           op_string['key'])

        if "upstream_host" == op_string['operation']:
            self.controller.upstream_host = op_string['param']
            operation = ChangeUpstreamHost(self.controller,
                                           self.response_factory,
                                           op_string['key'])

        if "bind_address" == op_string['operation']:
            self.controller.bind_address = op_string['param']
            operation = ChangeBindAddress(self.controller,
                                          self.response_factory,
                                          op_string['key'])

        if "bind_port" == op_string['operation']:
            self.controller.protocol = op_string['param']
            operation = ChangeBindPort(self.controller,
                                       self.response_factory,
                                       op_string['key'])

        return operation


class RedisOperationFactory(OperationFactory):

    def __init__(self, proxy, redis_endpoint, response_channel):
        OperationFactory.__init__(self, proxy)
        self.response_factory = RedisOperationResponseFactory(redis_endpoint,
                                                              response_channel)


class TcpOperationFactory(OperationFactory):

    def __init__(self, hb_proxy, response_channel):
        OperationFactory.__init__(hb_proxy)
        self.repsponse_factory = TcpOperationResponseFactory()


class ControllerOperation(object):

    def __init__(self, controller, response_factory, key):
        self.controller = controller
        self.operation = defer.Deferred()
        self.response = response_factory.get_response(200,
                                                      "execution successful",
                                                      key)
        self.key = key

    def execute(self):
        return self.operation.callback(self.response)

    def respond(self, result):
        self.response.send()

    def addCallback(self, callback):
        return self.operation.addCallback(callback)


class StopProxy(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)

        self.addCallback(self.stop)
        self.addCallback(self.respond)

    def stop(self, result):
        deferred = self.controller.proxy.stopListening()
        self.response.set_message("stop " + self.response.get_message())

        return deferred


class StartProxy(ControllerOperation):

    def __init__(self, controller, response_factory, key):

        ControllerOperation.__init__(self, controller, response_factory, key)

        self.addCallback(self.start)
        self.addCallback(self.respond)

    def start(self, result):
        resource = HBReverseProxyResource(self.controller.upstream_host,
                                          self.controller.upstream_port, '',
                                          self.controller.module_registry)
        f = server.Site(resource)
        f.requestFactory = HBReverseProxyRequest
        protocol = self.controller.protocol
        bind_address = self.controller.bind_address
        self.controller.proxy = reactor.listenTCP(protocol, f,
                                                  interface=bind_address)
        self.response.set_message("start " + self.response.get_message())

        return self.controller.proxy


class ChangeUpstreamPort(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)
        stop_op = StopProxy(controller, response_factory, key)
        self.start_op = StartProxy(controller, response_factory, key)

        d = self.addCallback(stop_op.stop).addCallback(self.start_op.start)
        d.addCallback(self.update_message)
        d.addCallback(self.respond)

    def update_message(self, result):
        self.response.set_message({'Change_upstream_port':
                                  [self.response.get_message()]
                                   })


class ChangeUpstreamHost(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)
        stop_op = StopProxy(controller, response_factory, key)
        self.start_op = StartProxy(controller, response_factory, key)

        d = self.addCallback(stop_op.stop).addCallback(self.start_op.start)
        d.addCallback(self.update_message)
        d.addCallback(self.respond)

    def update_message(self, result):
        self.response.set_message({'Change_upstream_host':
                                  [self.response.get_message()]
                                   })


class ChangeBindAddress(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)
        stop_op = StopProxy(controller, response_factory, key)
        self.start_op = StartProxy(controller, response_factory, key)

        d = self.addCallback(stop_op.stop).addCallback(self.start_op.start)
        d.addCallback(self.update_message)
        d.addCallback(self.respond)

    def update_message(self, result):
        self.response.set_message({'Change_bind_address':
                                  [self.response.get_message()]
                                   })


class ChangeBindPort(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)
        stop_op = StopProxy(controller, response_factory, key)
        self.start_op = StartProxy(controller, response_factory, key)

        d = self.addCallback(stop_op.stop).addCallback(self.start_op.start)
        d.addCallback(self.update_message)
        d.addCallback(self.respond)

    def update_message(self, result):
        self.response.set_message({'Change_bind_port':
                                  [self.response.get_message()]
                                   })


class StopRecording(ControllerOperation):

    def __init__(self, controller, response_factory, key, **params):
        ControllerOperation.__init__(self, controller, response_factory, key)

        self.params = params

        d = self.addCallback(self.stop_recording)
        d.addCallback(self.respond)

    def stop_recording(self, result):
        self.controller.module_registry.stop(module_name='TrafficRecorder',
                                             **self.params)
        self.response.set_message("Stopped Recording: { "
                                  + self.response.get_message() + ", "
                                  + "}")


class StartRecording(ControllerOperation):

    def __init__(self, controller, response_factory, key, **params):
        ControllerOperation.__init__(self, controller, response_factory, key)

        self.params = params

        d = self.addCallback(self.start_recording)
        d.addCallback(self.respond)

    def start_recording(self, result):
        self.controller.module_registry.start(module_name='TrafficRecorder',
                                              **self.params)
        self.response.set_message({'Started Recording':
                                   [self.response.get_message()]
                                   })


class ResetPlugins(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)
        stop_op = StopProxy(controller, response_factory, key)
        self.start_op = StartProxy(controller, response_factory, key)

        d = self.addCallback(stop_op.stop)
        d = d.addCallback(self.start_op.start)
        d.addCallback(self.reset_plugins)
        d.addCallback(self.update_message)
        d.addCallback(self.respond)

    def reset_plugins(self, result):
        self.controller.module_registry.reset()

    def update_message(self, result):
        self.response.set_message({'Reset_plugins':
                                  [self.response.get_message()]
                                   })


class ReloadPlugins(ControllerOperation):

    def __init__(self, controller, response_factory, key):
        ControllerOperation.__init__(self, controller, response_factory, key)
        stop_op = StopProxy(controller, response_factory, key)
        self.start_op = StartProxy(controller, response_factory, key)

        d = self.addCallback(stop_op.stop)
        d = d.addCallback(self.start_op.start)
        d.addCallback(self.reload_plugins)
        d.addCallback(self.update_message)
        d.addCallback(self.respond)

    def reload_plugins(self, result):
        self.controller.module_registry.reload()

    def update_message(self, result):
        self.response.set_message({'Reload_plugins':
                                  [self.response.get_message()]
                                   })


class HBProxyController(object):

    def __init__(self, bind_address, protocols, upstream_host, upstream_port,
                 redis_mgmt, redis_host, redis_port, request_channel,
                 response_channel, tcp_mgmt, tcp_mgmt_address, tcp_mgmt_port,
                 plugins):

        self.bind_address = bind_address
        self._start_logging()
        self.protocols = protocols
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.request_channel = request_channel
        self.response_channel = response_channel
        self.tcp_mgmt_address = tcp_mgmt_address
        self.tcp_mgmt_port = tcp_mgmt_port

        self.module_registry = Registry(plugins)
        self.site = server.Site(proxy.ReverseProxyResource(self.upstream_host,
                                self.upstream_port, ''))

        self.mgmt_protocol_factory = HBProxyMgmtProtocolFactory(self)
        reactor.listenTCP(self.tcp_mgmt_port, self.mgmt_protocol_factory,
                          interface=self.tcp_mgmt_address)

        redis_endpoint = TCP4ClientEndpoint(reactor, self.redis_host,
                                            self.redis_port)

        op_factory = RedisOperationFactory(self, redis_endpoint,
                                           self.response_channel)
        self.redis_conn = redis_endpoint.connect(
            HBProxyMgmtRedisSubscriberFactory(self.request_channel,
                                              op_factory))
        self.redis_conn.addCallback(self.subscribe).addCallback(
            self.start_proxy)

    def _start_logging(self):

        setStdout = True
#        log.startLogging(sys.stdout)

    def start_proxy(self, result):
        resource = HBReverseProxyResource(self.upstream_host,
                                          self.upstream_port, '',
                                          self.module_registry)
        f = server.Site(resource)
        f.requestFactory = HBReverseProxyRequest
        self.protocol = self.protocols['http']
        self.proxy = reactor.listenTCP(self.protocol, f,
                                       interface=self.bind_address)
        return self.proxy

    def subscribe(self, redis):
        return redis.subscribe()

    def run(self):
        reactor.run()

    def _stop_test(self, result):
        reactor.stop()

    def test(self):
        self.tests = self.module_registry.test()
        self.tests.addCallback(self._stop_test)
        self.tests.callback("start test")
        reactor.run()


def get_config(config_path):
    with open(config_path, 'r+') as config_file:
        config = yaml.load(config_file.read())

    return config


def get_arg_parser():

    description = " Helios Burn Proxy Server:  \n\n"

    parser = argparse.ArgumentParser(description=description)

    parser.add_argument('--config_path',
                        default="./config.yaml",
                        dest='config_path',
                        help='Path to output config file. Default: '
                        + ' ./config.yaml')

    parser.add_argument('--tcp_mgmt',
                        action="store_true",
                        dest='tcp_mgmt',
                        help='If set will listen for proxy mgmt commands on'
                        + ' a TCP port as well as subscribe to the request'
                        + ' channel. Default: false')

    parser.add_argument('--tcp_address',
                        dest='tcp_mgmt_address',
                        help='If the proxy mgmt interface is listening '
                        + ' on a tcp socket, this option will set the address'
                        + ' on which to listen.')

    parser.add_argument('--tcp_port',
                        dest='tcp_mgmt_port',
                        help='If the proxy mgmt interface is listening '
                        + ' on a tcp socket, this option will set the port'
                        + ' on which to listen.')

    parser.add_argument('--redis_mgmt',
                        action="store_true",
                        dest='redis_mgmt',
                        help='If set will subscribe to a given REDIS'
                        + ' channel for proxy mgmt commands'
                        + ' Default: true')

    parser.add_argument('--redis_address',
                        dest='redis_address',
                        help='If the proxy mgmt interface is listening '
                        + ' on a tcp socket, this option will set the address'
                        + ' on which to listen.')

    parser.add_argument('--redis_port',
                        dest='redis_port',
                        help='If the proxy mgmt interface is listening '
                        + ' on a tcp socket, this option will set the port'
                        + ' on which to listen.')

    parser.add_argument('--request_channel',
                        dest='request_channel',
                        help='If the proxy mgmt interface is set to use redis'
                        + ', this option will set the channel to which it '
                        + ' should subscribe.')

    parser.add_argument('--response_channel',
                        dest='response_channel',
                        help='If the proxy mgmt interface is set to use redis'
                        + ', this option will set the channel to which it '
                        + ' should publish.')

    parser.add_argument('--test',
                        action="store_true",
                        dest='run_tests',
                        help='If set will run all proxy tests'
                        + ' Default: false')

    return parser


def main():
    """
    Entry point for starting the proxy
    """
    args = get_arg_parser().parse_args()
    config_path = args.config_path

    config = get_config(config_path)
    proxy_config = config['proxy']
    mgmt_config = config['mgmt']

    bind_address = proxy_config['bind_address']
    protocols = proxy_config['protocols']
    upstream_host = proxy_config['upstream']['address']
    upstream_port = proxy_config['upstream']['port']
    tcp_mgmt_address = mgmt_config['tcp']['address']
    tcp_mgmt_port = mgmt_config['tcp']['port']
    redis_address = mgmt_config['redis']['address']
    redis_port = mgmt_config['redis']['port']
    request_channel = mgmt_config['redis']['request_channel']
    response_channel = mgmt_config['redis']['response_channel']

    if args.tcp_mgmt:
        if args.tcp_mgmt_address:
            tcp_mgmt_address = args.tcp_mgmt_address

        if args.tcp_mgmt_port:
            tcp_mgmt_port = args.tcp_mgmt_port

    if args.redis_mgmt:
        if args.redis_address:
            redis_address = args.redis_address
        if args.redis_port:
            redis_port = args.redis_address
        if args.request_channel:
            request_channel = args.request_channel
        if args.response_channel:
            response_channel = args.response_channel

    plugins = get_config("./modules.yaml")
    proxy_controller = HBProxyController(bind_address, protocols,
                                         upstream_host, upstream_port,
                                         args.redis_mgmt, redis_address,
                                         redis_port, request_channel,
                                         response_channel, args.tcp_mgmt,
                                         tcp_mgmt_address, tcp_mgmt_port,
                                         plugins)
    if args.run_tests:
        proxy_controller.test()
    else:
        proxy_controller.run()

if __name__ == "__main__":
    main()
