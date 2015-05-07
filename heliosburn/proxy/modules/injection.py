from module import AbstractModule
from twisted.python import log
from traffic_eval.traffic_evaluator import TrafficEvaluator


drop_test = {
    "action": {
        "type": "drop",
        "payload": "Intercepted by HeliosBurn"
    }}

reset_test = {
    "action": {
        "type": "reset",
        "payload": "Intercepted by HeliosBurn"
    }}

null_test = None

modify_test = {
    "action": {
        "type": "modify",
        "method": "PUT",
        "setHeaders": [
            {
                "key": "X-Auth-Token",
                "value": "k54l3b6k6b43l56b346"
            }
        ],
        "deleteHeaders": [
            {
                "key": "User-Agent"
            }
        ]
    }}

new_request_test = {
    "action": {
        "type": "newRequest",
        "httpProtocol": "HTTP/1.1",
        "statusCode": 400,
        "statusDescription": "Bad Request",
        "headers": [
            {
                "key": "E-Tag",
                "value": "9384253245"
            },
            {
                "key": "Server",
                "value": "HeliosBurn"
            }
        ],
        "payload": "Intercepted by HeliosBurn"
    }}

new_response_test = {
    "action": {
        "type": "newResponse",
        "httpProtocol": "HTTP/1.1",
        "statusCode": 400,
        "statusDescription": "Bad Request",
        "headers": [
            {
                "key": "E-Tag",
                "value": "9384253245"
            },
            {
                "key": "Server",
                "value": "HeliosBurn"
            }
        ],
        "payload": "Intercepted by HeliosBurn"
    }}

config = {
    "config": {
        "redis": {
            "db": 0,
            "port": 6379,
            "host": "localhost"
        },
        "mongodb": {
            "host": "localhost",
            "db": {
                "production": "heliosburn",
                "test": "heliosburng_test"
            },
            "port": 27017
        }
    }
}


class InjectionAction(object):

    def __init__(self, action_dict, request=None, response=None):
        self.action_dict = action_dict
        self.request = request
        self.response = response
        self.injection_engine = TrafficEvaluator(config)

        self.element_map = {
            "httpProtocol": "clientproto",
            "method": "method",
            "url": "uri",
            "statusCode": "code",
            "payload": "content",
            "statusDescription": "code_message"
        }

    def execute(self):
        pass

    def _add_headers(self, obj, headers):
        for header in headers:
            if obj.responseHeaders:
                obj.responseHeaders.addRawHeader(
                    header['key'], header['value']
                )
            if obj.requestHeaders:
                obj.requestHeaders.addRawHeader(
                    header['key'], header['value']
                )

    def _delete_headers(self, obj, headers):
        for header in headers:
            if obj.responseHeaders:
                obj.responseHeaders.removeHeader(header['key'])
            if obj.requestHeaders:
                obj.requestHeaders.removeHeader(header['key'])

    def _modify(self, obj):
        action = self.action_dict['action']
        for element in self.element_map:
            if element in action:
                setattr(obj, self.element_map[element], action[element])

        if 'headers' in action:
            self._delete_headers(obj, obj.getAllRawHeaders())
            self._add_headers(obj, action['header'])

        if 'setHeaders' in action:
            self._add_headers(obj, action['setHeaders'])

        if 'deleteHeaders' in action:
            self._delete_headers(obj, action['deleteHeaders'])


class ModifyAction(InjectionAction):

    def execute(self):
        if self.request:
            self._modify(self.request)
            log.msg("request after modify: " + str(self.request))
            return self.request
        else:
            self._modify(self.response)
            log.msg("response after modify: " + str(self.response))
            return self.response


class NewResponseAction(InjectionAction):
    def execute(self):
        self._modify(self.response)
        return self.response


class NewRequestAction(InjectionAction):
    def execute(self):
        self._modify(self.request)
        return self.request


class DropAction(InjectionAction):
    def execute(self):
        if self.request:
            self.request.drop_connection = True
            return self.request
        else:
            self.response.drop_connection = True
            return self.response


class ResetAction(InjectionAction):
    def execute(self):
        if self.request:
            self.request.reset_connection = True
            return self.request
        else:
            self.response.reset_connection = True
            return self.response


class NullAction(InjectionAction):
    def execute(self):
        if self.request:
            return self.request
        else:
            return self.response


class Injection(AbstractModule):
    actions = {
        'modify': ModifyAction,
        'newResponse': NewResponseAction,
        'newRequest': NewRequestAction,
        'drop': DropAction,
        'reset': ResetAction,
        'null': NullAction
    }

    def __init__(self):
        AbstractModule.__init__(self)
        self.injection_engine = TrafficEvaluator(config)

    def _process_request(self, http_metadata, session):
        injection_engine = TrafficEvaluator(config)
        log.msg("calling traffic evaluator with:\n" +
                "http_metadata: " + str(http_metadata) + "\n"
                "      session: " + str(session))
#        self.injection_engine.get_action(http_metadata, session)
        action = null_test

        return action

    # Dummy function used to test until engine exists
    def _process_response(self, http_metadata, session):
        injection_engine = TrafficEvaluator(config)
        log.msg("calling traffic evaluator with:\n" +
                "http_metadata: " + str(http_metadata) + "\n"
                "      session: " + str(session))
#        self.injection_engine.get_action(http_metadata, session)

        action = modify_test

        return action

    def handle_request(self, request):
        request_headers = [[k, v] for (k, v)
                           in request.requestHeaders.getAllRawHeaders()]
        http_metadata = {
            "request": {
                "url": request.uri,
                "httpProtocol": request.clientproto,
                "method": request.method,
                "headers": request_headers
            }
        }

        action_dict = self._process_request(http_metadata, 1)
        if action_dict:
            action_type = action_dict['action']['type']
        else:
            action_type = 'null'

        action = self.actions[action_type](action_dict=action_dict,
                                           request=request)
        result = action.execute()
        if result:
            return request

    def handle_response(self, response):
        response_headers = [[k, v] for (k, v)
                            in response.responseHeaders.getAllRawHeaders()]

        request_headers = [[k, v] for (k, v)
                           in response.requestHeaders.getAllRawHeaders()]

        http_metadata = {
            "request": {
                "url": response.uri,
                "httpProtocol": response.clientproto,
                "method": response.method,
                "headers": request_headers
            },
            "response": {
                "url": response.uri,
                "httpProtocol": response.clientproto,
                "stausCode": response.code,
                "stausDescription": response.code_message,
                "headers": response_headers
            }
        }

        action_dict = self._process_response(http_metadata, 1)
        if action_dict:
            action_type = action_dict['action']['type']
        else:
            action_type = 'null'

        action = self.actions[action_type](action_dict=action_dict,
                                           response=response)
        result = action.execute()
        if result:
            return response

    def reset(self):
        pass

    def reload(self):
        pass

    def start(self):
        pass

    def stop(self):
        pass

injection = Injection()
