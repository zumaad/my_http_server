import pathlib
from typing import Any, List, Dict, Union, Sequence, Tuple, Callable, Generator
import socket
import time
import random
from utils.general_utils import HttpRequest, HttpResponse, Range, SocketTasks, read_all, send_all, async_send_all
import selectors
from abc import ABC, abstractmethod
from event_loop.event_loop import ResourceTask


class HttpBaseHandler(ABC):
    def __init__(self, match_criteria: Dict[str, List], context: Dict, server_obj):
        self.http_request_match_criteria = match_criteria
        self.context = context
        self.server_obj = server_obj

    def should_handle(self, http_request: HttpRequest) -> bool:
        """ 
        Determines whether the handler for a certain task (like serving static files) should handle
        the given request. It does this by looking at the attributes of the http request like the 
        its headers, the request url, etc.
        """
        for target_http_request_attribute, required_attribute_values in self.http_request_match_criteria.items():
            actual_request_attribute_value = http_request[target_http_request_attribute]
            
            
            if target_http_request_attribute == 'url':
                #url matching is different because you are not checking for the simple existance of the
                #request value in the required values.
                if not actual_request_attribute_value.startswith(tuple(required_attribute_values)):
                    return False
            else:
                if actual_request_attribute_value not in required_attribute_values:
                    return False
        return True
    
    @abstractmethod
    def handle_request(self, http_request: HttpRequest) -> HttpResponse:
        pass

class HealthCheckHandler(HttpBaseHandler):
    def handle_request(self, http_request: HttpRequest) -> HttpResponse:  
        return HttpResponse(body="I'm Healthy!")

class StaticAssetHandler(HttpBaseHandler):
    def __init__(self, match_criteria: Dict[str, List], context: Dict, server_obj):
        super().__init__(match_criteria, context, server_obj)
        self.static_directory_path = context['staticRoot']
        self.all_files = set(pathlib.Path(self.static_directory_path).glob('**/*')) #get all files in the static directory
        self.file_extension_mime_type = {
            '.jpg':'image/jpeg',
            '.jpeg':'image/jpeg',
            '.jfif':'image/jpeg',
            '.pjpeg':'image/jpeg',
            '.pjp':'image/jpeg',
            '.png':'image/png',
            '.css':'text/css',
            '.html':'text/html',
            '.js':'text/javascript',
            '.mp4':'video/mp4',
            '.flv':'video/x-flv',
            '.m3u8':'application/x-mpegURL',
            '.ts':'video/MP2T',
            '.3gp':'video/3gpp',
            '.mov':'video/quicktime',
            '.avi':'video/x-msvideo',
            '.wmv':'video/x-ms-wmv'
        }

    def not_found_error_response(self, absolute_path: str) -> str:
        return (f'<pre> the file requested was searched for in {absolute_path} and it does not exist.\n'
                f'A proper request for a static resource is any of the strings the request should start with (as defined\n'
                f'in your settings.json file) + the relative path to your resource starting from the static_root (defined in\n' 
                f'settings.py). </pre>')

    def remove_url_prefix(self, http_request: HttpRequest) -> str:
        for required_beginning in self.http_request_match_criteria['url']:
            if http_request.requested_url.startswith(required_beginning):
                return http_request.requested_url[len(required_beginning):]
        raise Exception("somehow the requested url doesn't begin with the required beginning path")
        
    def handle_request(self, http_request: HttpRequest) -> HttpResponse:
        file_extension = '.' + http_request.requested_url.split('.')[-1] #probably a better way
        absolute_path = self.static_directory_path + self.remove_url_prefix(http_request) 
        content_type = self.file_extension_mime_type.get(file_extension,'text/html') #get mime type and default to text/html
        if pathlib.Path(absolute_path) in self.all_files:
            static_file_contents = open(absolute_path,'rb').read()
            return HttpResponse(body=static_file_contents, additional_headers={'Content-Type':content_type})
        else:
            return HttpResponse(response_code=404, body=self.not_found_error_response(absolute_path))

class ReverseProxyHandler(HttpBaseHandler):
    def __init__(self, match_criteria: Dict[str, List], context: Dict, server_obj):
        super().__init__(match_criteria, context, server_obj)
        self.remote_host, self.remote_port = context['send_to']
        
    def connect_and_send(self, remote_host: str, remote_port: int, http_request: HttpRequest) -> HttpResponse:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as remote_server:
            remote_server.settimeout(15)
            remote_server.connect((remote_host,int(remote_port)))
            remote_server.sendall(http_request.raw_http_request)
            data = remote_server.recv(1024)
            http_response = HttpResponse.from_bytes(data)
            return http_response

    def handle_request(self, http_request: HttpRequest) -> HttpResponse:
        return self.connect_and_send(self.remote_host, self.remote_port, http_request)

class LoadBalancingHandler(ReverseProxyHandler):
    def __init__(self, match_criteria: Dict[str, List], context: Dict, server_obj):
        HttpBaseHandler.__init__(self, match_criteria, context, server_obj)
        self.strategy = self.context['strategy']
        self.remote_servers = self.context['send_to']
        self.server_index = 0
        self.strategy_mapping = {
            "round_robin":self.round_robin_strategy,
            "weighted":self.weighted_strategy
        }
        
    def round_robin_strategy(self) -> Tuple[str,int]:
        server_to_send_to = self.remote_servers[self.server_index % len(self.remote_servers)]
        self.server_index +=1
        return server_to_send_to
    
    def weighted_strategy(self) -> Tuple[str,int]:
        random_num = random.random()
        for host, port, weight_range in self.remote_servers:
            if random_num in weight_range:
                return (host, port)
        raise Exception("random number generated was not in any range")

    def handle_request(self, http_request: HttpRequest) -> HttpResponse:
        strategy_func = self.strategy_mapping[self.strategy]
        remote_host, remote_port = strategy_func()
        return self.connect_and_send(remote_host, remote_port, http_request)    


class AsyncReverseProxyHandler(ReverseProxyHandler):

    def connect_and_send(self, remote_host: str, remote_port: int, http_request: HttpRequest) -> Generator:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as remote_server:
            remote_server.setblocking(False)
            try:
                remote_server.connect((remote_host,int(remote_port)))
            except BlockingIOError:
                yield ResourceTask(remote_server,'writable')
            yield from async_send_all(remote_server, http_request.raw_http_request)
            yield ResourceTask(remote_server, 'readable')
            data = read_all(remote_server)
            http_response = HttpResponse.from_bytes(data)
            return http_response

    def handle_request(self, http_request: HttpRequest) -> Generator:
        http_response = yield from self.connect_and_send(self.remote_host, self.remote_port, http_request)
        return http_response

class AsyncLoadBalancingHandler(AsyncReverseProxyHandler, LoadBalancingHandler):
    
    def __init__(self, match_criteria: Dict[str, List], context: Dict, server_obj):
        super().__init__(match_criteria, context, server_obj)
        LoadBalancingHandler.__init__(self, match_criteria, context, server_obj)
    
    def handle_request(self, http_request: HttpRequest) -> Generator:
        strategy_func = self.strategy_mapping[self.strategy]
        remote_host, remote_port = strategy_func()
        http_response = yield from self.connect_and_send(remote_host, remote_port, http_request)   
        return http_response
