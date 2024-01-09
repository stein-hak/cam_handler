#!/usr/bin/env python

import sys

sys.path.append('/opt/lib')

import time
import os
import posixpath
import urllib

import socket
import logging
from http.server import SimpleHTTPRequestHandler,HTTPServer
from socketserver import ThreadingMixIn, ForkingMixIn
from multiprocessing import Process, Event
from datetime import datetime, timedelta
from  urllib import parse as urlparse
import redis
import json

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        # surpress socket/ssl related errors
        cls, e = sys.exc_info()[:2]
        if cls is socket.error:  # or cls is ssl.SSLError:
            pass
        else:
            return HTTPServer.handle_error(self, request, client_address)


class ProcessHTTPServer(ForkingMixIn, HTTPServer):

    def handle_error(self, request, client_address):
        # surpress socket/ssl related errors
        cls, e = sys.exc_info()[:2]
        if cls is socket.error:  # or cls is ssl.SSLError:
            pass
        else:
            return HTTPServer.handle_error(self, request, client_address)

    """Handle requests in a separate Process."""


class RequestHandler(SimpleHTTPRequestHandler):
    def basic_auth(self):
        if self.username and self.password:
            self.realm = "AuthHTTPServer"
            # self.username=None
            # self.password=None
            auth_hed = self.headers.getheader('Authorization')

            # print self.clients

            if self.client_address[0] in self.clients.keys():

                if self.clients[self.client_address[0]] == None:
                    return True
                elif datetime.now() - self.clients[self.client_address[0]] < timedelta(seconds=120):
                    self.clients[self.client_address[0]] = datetime.now()
                    return True


            elif auth_hed:
                method, auth = auth_hed.split(' ', 1)
                if method.lower() == 'basic':
                    self.username, self.password = auth.decode('base64').split(':', 1)
                    self.clients[self.client_address[0]] = datetime.now()
                    return True
            else:
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm=\"%s\"' % self.realm)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                return False

    def do_REDIRECT(self, redirect_path):
        self.send_response(301)
        self.send_header('Location', redirect_path)
        self.end_headers()


    def translate_path(self, path):
        print(path)
        """translate path given routes"""

        # set default root to cwd
        # look up routes and set root directory accordingly
        #        for pattern, rootdir in routes:
        #            if path.startswith(pattern):
        # found match!
        #                path = path[len(pattern):]  # consume path up to pattern len
        #                root = rootdir
        #                break

        # normalize path and prepend root directory
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        path = posixpath.normpath(urllib.unquote(path))
        words = path.split('/')
        words = filter(None, words)

        path = self.root
        for word in words:
            drive, word = os.path.splitdrive(word)
            head, word = os.path.split(word)
            if word in (os.curdir, os.pardir):
                continue
            path = os.path.join(path, word)

        return path

    def log_message(self, format, *args):
        return


class VideoHandler(RequestHandler):
    protocol_version = 'HTTP/1.1'
    jobs = None
    archive = None
    timeout = 20

    def code_to_type(self, code):
        if int(code) == 0:
            return "video/webm"
        elif int(code) == 1:
            return "video/mpeg"
        elif int(code) == 2:
            return 'multipart/x-mixed-replace'
        elif int(code) == 3:
            return 'video/mp4'

    def parse_args(self, args):
        client = {'action': 'options', 'res': 0}
        if 'action' in args.keys():
            if args['action'] == 'reboot':
                client['action'] = 'reboot'

            elif args['action'] == 'live':
                client['action'] = 'live'
                if 'res' in args.keys():
                    if args['res'] == 'full' or args['res'] == '0':
                        client['res'] = 0  # Get CGI params for client
                    elif args['res'] == 'half' or args['res'] == '1':
                        client['res'] = 1
                    else:
                        try:
                            client['res'] = int(args['res'])
                        except:
                            client['res'] = 0

            elif args['action'] == 'snapshot':
                client['action'] = 'snapshot'

            elif args['action'] == 'streams' or args['action'] == 'options':
                client['action'] = 'options'

            elif args['action'] == 'storage':
                client['action'] = 'storage'

        else:
            client['action'] = 'live'
            if 'res' in args.keys():
                if args['res'] == 'full' or args['res'] == '0':
                    client['res'] = 0  # Get CGI params for client
                elif args['res'] == 'half' or args['res'] == '1':
                    client['res'] = 1
                else:
                    try:
                        client['res'] = int(args['res'])
                    except:
                        client['res'] = 0

        return client

    def do_GET(self):
        # self.basic_auth()
        map_jobs = {}
        map_backup_jobs = {}
        # print(self.client_address[0])
        # print(self.path)
        self.redis_queue = redis.Redis(connection_pool=self.pool)

        params = json.loads(self.redis_queue.get('videoserver-multifd:%s:config' % self.name))

        sinks = params.get('main_distributor')

        native_sinks = params.get('native_sinks')

        encoder_sinks = params.get('encoder_sinks')


        parsed_path = urlparse.urlparse(self.path)

        args = dict(urlparse.parse_qsl(parsed_path.query))
        new_path = parsed_path.path[1:]

        client = self.parse_args(args)
        if new_path == self.name or new_path == 'live':
            if not 'action' in args.keys():
                if 'res' in args.keys():
                    try:
                        res = int(args['res'])
                    except:
                        res = 100
                else:
                    res = 0

                # if res != 9 :
                #     try:
                #         sink = sinks[res]
                #     except:
                #         sink = sinks[-1]
                #
                #     if sink in native_sinks:
                #         type = 'native'
                #         m = native_sinks.index(sink)
                #
                #     elif sink in encoder_sinks:
                #         type = 'encoder'
                #         m = encoder_sinks.index(sink)
                # else:
                #     type = 'encoder'
                #     m = 9

                # print(type,m)

                self.send_response(200)
                # self.send_header("Content-type", self.code_to_type(int(http_code)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Vary", "*")
                self.send_header("Etag", str(time.time()).replace(".", ""))
                self.send_header("Connection", "keep-alive")
                self.end_headers()


                #fifo = os.path.join('/run/videoserver/fifo/', str(os.getpid()))
                fifo = os.path.join(self.fifo_path,'distributor-' + str(os.getpid()))
                message = {'event': 'new_fd', 'fd': fifo,'num':res}
                #
                print(message)
                #if type == 'native':
                self.redis_queue.publish(self.name + '_multifd', json.dumps(message))
                # elif type == 'encoder':
                #     self.redis_queue.publish(self.name + '_detector', json.dumps(message))


                while not os.path.exists(fifo):
                    time.sleep(0.01)

                r = os.open(fifo, os.O_RDONLY)

                fr = os.fdopen(r,'rb')

                data = ''
                interrupt = False
                while not interrupt:
                    try:
                        self.wfile.write(fr.read(188))
                        self.wfile.flush()
                    except:
                        break

            #
            # elif args.get('action') == 'motion':
            #     self.send_response(200)
            #     # self.send_header("Content-type", self.code_to_type(int(http_code)))
            #     self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            #     self.send_header("Pragma", "no-cache")
            #     self.send_header("Expires", "0")
            #     self.send_header("Vary", "*")
            #     self.send_header("Etag", str(time.time()).replace(".", ""))
            #     self.send_header("Connection", "keep-alive")
            #     self.end_headers()
            #
            #     fifo = os.path.join(self.fifo_path, 'distributor-' + str(os.getpid()))
            #     message = {'event': 'new_fd', 'fd': fifo}
            #     #
            #     #print(message)
            #
            #     self.redis_queue.publish(self.name + '_detector', json.dumps(message))
            #     while not os.path.exists(fifo):
            #         time.sleep(0.01)
            #
            #     r = os.open(fifo, os.O_RDONLY)
            #
            #     fr = os.fdopen(r, 'rb')
            #
            #     data = ''
            #     interrupt = False
            #     while not interrupt:
            #         try:
            #             self.wfile.write(fr.read(188))
            #             self.wfile.flush()
            #         except:
            #             break




class proxy_handler(RequestHandler):
    def do_GET(self):
        self.do_PROXY(self.wfile, 'http://192.168.11.102')


class base_server(Process):
    def __init__(self, name, handler=VideoHandler, port=8888, redis_host='localhost',fifo_path='/run/videoserver/fifo'):
        Process.__init__(self)

        self.handler = handler
        self.name = name
        self.daemon = True
        self.port = port
        self.blocksize = 65536
        self.handler.name = name
        self.handler.username = 'usrsokrat'
        self.handler.password = 'mtt'
        self.handler.clients = {}
        self.pool = redis.ConnectionPool(host=redis_host, port=6379, db=0)
        self.handler.pool = self.pool
        self.handler.fifo_path = fifo_path

        self.handler.blocksize = self.blocksize
        self.interrupt = False
        socket.setdefaulttimeout(5.0)
        self.exit = Event()

        # self.server = ThreadedHTTPServer((0.0.0.0, self.port), self.handler)
        is_running = False
        while not is_running:
            try:
                self.server = ProcessHTTPServer(('0.0.0.0', self.port), self.handler)
            except:
                self.port += 1
                time.sleep(0.1)
            else:
                break
        self.port = self.server.server_port
        self.server.timeout = 10

    # self.server=Server(self.host,self.port,self.handler)

    def run(self):
        while not self.exit.is_set():
            try:
                self.server.handle_request()
            except:
                pass
        print('Stopping ' + self.name)

    # asyncore.loop(timeout=2)

    def stop(self):
        self.server.stop()


if __name__ == '__main__':
    server = base_server(port=8884)

    server.run()
#    while 1:
#        time.sleep(1)
