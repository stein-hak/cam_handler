#!/usr/bin/python3

import sys

sys.path.append('/opt/lib/')
import os
import gi
from ffprobe import ffprobe

gi.require_version('Gst', '1.0')
from gi.repository import Gst, Gio, GObject
from datetime import datetime, timedelta
import time
import os
import redis
from multiprocessing import Process, Event, Value
import json
import shutil
from collections import OrderedDict
import camctl
from http_server_fifo import base_server
from urllib import parse as urlparse
import socket
import ftplib
from copy import copy
from functools import partial
import argparse
from uuid import uuid4
from native_ping import ping

Gst.init(None)
GObject.threads_init()


# def keep_ratio(width, height, width1):
#     rate = float(width1) / width
#     ret = int(height * rate)
#
#     while ret % 8 != 0:
#         ret += 1
#
#     return ret


class record_format():

    def __init__(self, time, format={0: {'hybrid': {'days': '1111111', 'hours': '000000000111111111111111'},
                                         'event': {'days': '1111111', 'hours': '111111111111111111111111'},
                                         'persistent': {'days': '1111111', 'hours': '111111111111111111111111'}}},
                 event_seconds_pre=0, event_seconds_post=15, calendar={}):
        self.time = time
        self.format = format
        self.event_seconds_pre = event_seconds_pre
        self.event_seconds_post = event_seconds_post
        self.calendar = calendar

    def get_record_type(self, now):
        is_active = False
        num_final = 0
        record_format_final = None
        day = now.strftime('%m%d')

        if self.calendar.get(day) == 0:
            return is_active, num_final, record_format_final

        weekday = int(now.strftime('%w')) - 1

        if weekday < 0:  # change us weekdays to russian
            weekday = 6
        hour = int(now.strftime('%H'))

        # print(weekday)
        # print(hour)

        for num in self.format.keys():
            for record_format in self.format[num]:
                days = self.format[num][record_format]['days']
                hours = self.format[num][record_format]['hours']
                # print(record_format)
                # print(int(days[weekday]))
                # print(int(hours[hour]))
                if int(days[weekday]) == 1 and int(hours[hour]) == 1 and not is_active:
                    is_active = True
                    num_final = num
                    record_format_final = record_format
                    break
            else:
                continue

        return is_active, num_final, record_format_final


def upload_file(file, host='localhost', user='usrsokrat', password='mtt'):
    session = ftplib.FTP(host, user, password)
    with open(file, 'rb') as f:
        session.storbinary('STOR %s' % os.path.basename(file), f)
    session.quit()
    os.remove(file)


def parse_addr(url):
    args = None
    if len(url.split('?')) > 1:
        args = url.split('?')[1]
        url = url.split('?')[0]

    parsed = urlparse.urlparse(url)
    proto = parsed.scheme

    if parsed.username or parsed.password:
        username = parsed.username
        password = parsed.password
        location = parsed.scheme + '://' + parsed.netloc.split('@')[1] + parsed.path
        if args:
            location = location + '?' + args

        host = parsed.netloc.split('@')[1].split(':')[0]

    else:

        username = None
        password = None
        location = url
        if args:
            location = location + '?' + args
        host = parsed.netloc.split(':')[0]

    # print location
    return host, proto, location, username, password


class Splitter(Process):
    def __init__(self, host=None, name='default', user='admin', password='admin', overrides={}, publish_rtsp=False,
                 redis_host='127.0.0.1', ftp_host='localhost', min_detector_width=360, encoder_resolutions=[]):
        Process.__init__(self)
        #self.state = Value('i',0)  # {-2:ping timeout, -1:rtsp pipeline failed, 1: standby, 2:broadcasting, 3:recording}
        self.name = name
        self.host = host
        self.user = user
        self.password = password
        self.state = None
        self.pipelines = OrderedDict()
        self.vcodecs = OrderedDict()
        self.fifos = {}
        self.overrides = overrides
        self.publish_rtsp = publish_rtsp
        self.locations = []
        self.processes = []
        self.redis_host = redis_host
        self.ftp_host = ftp_host
        self.min_detector_width = min_detector_width
        self.split_lock = None
        self.exit = Event()
        self.watchdog = Event()


        self.pool = redis.ConnectionPool(host=self.redis_host, port=6379, db=0)
        self.redis_queue = redis.StrictRedis(connection_pool=self.pool)
        self.start_times = {}
        self.record_format = record_format(time=120, event_seconds_pre=0, event_seconds_post=60)
        self.watchdog_timeout = self.record_format.time * 2
        self.parent_path = '/run/videoserver'
        self.base_path = os.path.join(self.parent_path, self.name)
        self.fifo_path = os.path.join(self.base_path, 'fifo')
        self.http_port = None
        self.encoder_resolutions = encoder_resolutions # example [('MJPEG', 420, 15, 1), ('H264', 240, 15, 0.5)]
        self.encoder_sinks = []
        self.sinks = []
        self.native_sinks = []
        self.params = {}

        # recorder

        self.alarm_timeout = 300
        self.alarm = [False, False, None, self.alarm_timeout]
        self.is_alarmed = False
        self.was_alarmed = False
        self.last_alarm_time = None
        self.alarm_timeout = 600
        self.files_to_export = []
        self.file_list = {}

        # detector

        self.detect_motion = True
        self.encoders = {}

        if not self.parent_path:
            os.makedirs(self.parent_path)

        if os.path.exists(self.base_path):
            shutil.rmtree(self.base_path)

        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path)

        if not os.path.exists(self.fifo_path):
            os.makedirs(self.fifo_path)

    def tryPort(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = False
        try:
            sock.bind(("0.0.0.0", port))
            result = True
        except:
            pass
            # print("Port is in use")
        sock.close()
        return result

    def setup_onvif(self, host, user, passwd):

        onvif = None

        if user and passwd:
            try:
                onvif = camctl.cam_control(host, user=user, password=passwd)
            except:
                pass
        else:
            try:
                onvif = camctl.cam_control(host)
            except:
                pass

        return onvif

    def get_cam_options(self):
        video_uri = []
        snap_uri = None
        cam_options = OrderedDict()
        if self.onvif:
            try:
                video_uri = self.onvif.get_video_uri()
            except:
                # video_uri = []
                print('%s: unable to get video_uri by onvif' % self.name)

            try:
                snap_uri = self.onvif.get_snapshot_uri()
            except:
                print('%s: unable to get snap_uri by onvif' % self.name )
                snap_uri = None

            try:
                video_opt = self.onvif.get_video_options()

                if video_uri and video_opt:
                    for i in video_uri:
                        try:
                            cam_options[i] = video_opt[video_uri.index(i)]
                        except:
                            pass

                if cam_options:
                    s = sorted(cam_options.items(), key=lambda x: [x[1][1] * x[1][2]], reverse=True)
                    for item in s:
                        cam_options[item[0]] = item[1]


            except:
                print('%s: unable to get video options by onvif' % self.name)


        return video_uri, snap_uri, cam_options


    def keep_ratio(self, width, height, width1):
        rate = float(width1) / width
        ret = int(height * rate)

        while ret % 8 != 0:
            ret += 1

        return ret


    def start_record(self, num=0):
        try:
            pipeline, bus = list(self.pipelines.values())[num]
            location = list(self.pipelines.keys())[num]
        except:
            pipeline, bus = list(self.pipelines.values())[0]
            location = list(self.pipelines.keys())[0]

        if not self.recording:
            print('Start recording')
            while pipeline.get_state(1 * Gst.SECOND)[1] != Gst.State.PLAYING:
                pipeline.set_state(Gst.State.PLAYING)
                time.sleep(0.1)
            self.start_times[pipeline] = datetime.now()

            vcodec = self.vcodecs.get(location)

            self.fd_num = os.listdir(self.fd_dir)

            self.recorder = self.create_recorder_pipeline(location='rtsp',
                                                          file_location=os.path.join(self.base_path, str(num)),
                                                          vcodec=vcodec)

            pipeline.add(self.recorder)

            tee = pipeline.get_by_name('video_tee')
            tee.link(self.recorder)
            self.recorder.set_state(Gst.State.PLAYING)
            while self.recorder.get_state(Gst.SECOND)[1] != Gst.State.PLAYING:
                time.sleep(0.1)
            print('Recorder started')
            self.recording = True


    def stop_record(self):
        if self.recording:
            self.split_now()
            time.sleep(5)
            self.recording = False
            ghostpad = self.recorder.get_static_pad("sink")
            teepad = ghostpad.get_peer()
            teepad.add_probe(Gst.PadProbeType.BLOCK, self.blocking_pad_recorder)


    def stop_detector(self):
        if self.detector:
            self.detecting = False
            ghostpad = self.detector.get_static_pad("sink")
            teepad = ghostpad.get_peer()
            teepad.add_probe(Gst.PadProbeType.BLOCK, self.blocking_pad_detector)


    def blocking_pad_recorder(self, pad, info):
        # Function to remove bin from pipeline

        print('Stoping recorder')
        self.recorder.set_state(Gst.State.NULL)
        pipeline, bus = list(self.pipelines.values())[0]

        # tee = self.detector.get_by_name('tee_video')
        tee = pipeline.get_by_name('video_tee')
        ghostpad = self.recorder.get_static_pad("sink")
        teepad = ghostpad.get_peer()

        # print('Removing encoder bin')
        pipeline.remove(self.recorder)
        # print("Releasing Tee-Pad")
        tee.release_request_pad(teepad)

        del self.recorder
        return Gst.PadProbeReturn.REMOVE


    def blocking_pad_detector(self, pad, info):
        # Function to remove bin from pipeline

        print('Stoping recorder')
        self.detector.set_state(Gst.State.NULL)
        pipeline, bus = list(self.pipelines.values())[-1]

        # tee = self.detector.get_by_name('tee_video')
        tee = pipeline.get_by_name('video_tee')
        ghostpad = self.detector.get_static_pad("sink")
        teepad = ghostpad.get_peer()

        # print('Removing encoder bin')
        pipeline.remove(self.detector)
        # print("Releasing Tee-Pad")
        tee.release_request_pad(teepad)

        del self.recorder
        return Gst.PadProbeReturn.REMOVE


    def start_detector(self, num=4):
        try:
            pipeline, bus = list(self.pipelines.values())[num]
            location = list(self.pipelines.keys())[num]
        except:
            pipeline, bus = list(self.pipelines.values())[-1]
            location = list(self.pipelines.keys())[-1]

        if not self.detecting:
            print('Start detecting')
            while pipeline.get_state(1 * Gst.SECOND)[1] != Gst.State.PLAYING:
                pipeline.set_state(Gst.State.PLAYING)
                time.sleep(0.1)
            self.start_times[pipeline] = datetime.now()
            vcodec = self.vcodecs.get(location)

            self.detector = self.create_detector_pipeline(vcodec=vcodec)

            pipeline.add(self.detector)

            tee = pipeline.get_by_name('video_tee')
            tee.link(self.detector)

            self.detector.set_state(Gst.State.PLAYING)

            while self.detector.get_state(0.1 * Gst.SECOND)[1] != Gst.State.PLAYING:
                time.sleep(0.1)
            print('Detector started')
            self.detecting = True

        # pipeline, bus = list(self.pipelines.values())[self.encoder_num]
        #
        # options = list(self.cam_options.values())[self.encoder_num]
        #
        # width = options[1]
        # height = options[2]
        #
        #
        # if not self.detecting:
        #     if pipeline.get_state(1 * Gst.SECOND)[1] != Gst.State.PLAYING:
        #         pipeline.set_state(Gst.State.PLAYING)
        #         time.sleep(0.3)
        #
        #
        #     self.detecting = True
        #     self.detector = Decoder(name=self.name, num=self.encoder_num,redis_host=self.redis_host,record_format=self.record_format,fifo_path=self.fifo_path,width=width,height=height,detector_width=self.min_detector_width,encoder_sinks=self.encoder_sinks)
        #     self.detector.start()


    def check_schedule(self):
        # print((datetime.now() - self.begin_time).seconds)
        #
        # if datetime.now() - self.begin_time <= timedelta(seconds=30):
        #     if not self.recording:
        #         self.start_record()
        # else:
        #     self.stop_record()
        # if not self.detecting:
        #    self.start_record()
        #    self.start_detector()

        ret = self.record_format.get_record_type(datetime.now())
        ret1 = self.record_format.get_record_type(datetime.now() + timedelta(seconds=120))

        if ret[0] or ret1[0]:
            if ret[0]:
                num = ret[1]
                format = ret[2]
            else:
                num = ret1[1]
                format = ret1[2]

            if format == 'hybrid' or format == 'event' or self.encoder_sinks:
                if not self.detecting:
                    self.start_detector()
            else:
                if self.detecting:
                    self.stop_detector()

            if not self.recording:
                self.start_record(num)

        else:
            if self.recording:
                self.stop_record()
            if self.detecting:
                self.stop_detector()

    def update_watchdog(self):
        ret = self.record_format.get_record_type(datetime.now())
        ret1 = self.record_format.get_record_type(datetime.now() + timedelta(seconds=120))

        if ret or ret1 and self.pipelines:
            if self.watchdog.is_set():
                self.watchdog_timeout = self.record_format.time * 2
                self.watchdog.clear()
            else:
                self.watchdog_timeout -= 1

            if self.watchdog_timeout == 0:
                self.exit.set()






    def create_base_pipeline(self, location, user, passwd, tcp=False):
        pipeline = Gst.Pipeline()
        index = self.locations.index(location)
        pipeline.set_property('name', location)
        print('Creating pipeline for %s' % location)
        source = Gst.ElementFactory.make('rtspsrc', 'source')
        source.set_property('location', location)
        source.set_property('latency', 0)
        source.set_property('user-id', user)
        source.set_property('user-pw', passwd)
        source.connect("pad-added", self.on_rtspsrc_pad_added)
        pipeline.add(source)
        depay264 = Gst.ElementFactory.make('rtph264depay', 'depay264')
        pipeline.add(depay264)
        depay265 = Gst.ElementFactory.make('rtph265depay', 'depay265')
        pipeline.add(depay265)
        parse264 = Gst.ElementFactory.make('h264parse', 'parse264')
        pipeline.add(parse264)
        parse265 = Gst.ElementFactory.make('h265parse', 'parse265')
        pipeline.add(parse265)
        depay_jpeg = Gst.ElementFactory.make('rtpjpegdepay', 'jpegdepay')
        pipeline.add(depay_jpeg)
        parse_jpeg = Gst.ElementFactory.make('jpegparse', 'jpegparse')
        pipeline.add(parse_jpeg)
        multipart_mux = Gst.ElementFactory.make('multipartmux', 'muxer_mulripart')
        pipeline.add(multipart_mux)
        muxer_stream = Gst.ElementFactory.make('mpegtsmux', 'muxer_stream')
        pipeline.add(muxer_stream)
        video_tee = Gst.ElementFactory.make('tee', 'video_tee')
        # video_tee.set_property("allow-not-linked", True)
        pipeline.add(video_tee)
        mux_queue = Gst.ElementFactory.make('queue', 'mux_queue')
        pipeline.add(mux_queue)
        multisink = Gst.ElementFactory.make('multifdsink', 'multisink')
        multisink.set_property('timeout', 2 * Gst.SECOND)
        multisink.connect('client-added', self.add_client)
        multisink.connect('client-removed', self.remove_client)
        multisink.set_property('sync-method', 'latest-keyframe')
        pipeline.add(multisink)
        if self.publish_rtsp:
            rtsp_queue = Gst.ElementFactory.make('queue', 'rtsp_queue')
            pipeline.add(rtsp_queue)
            rtspclient = Gst.ElementFactory.make('rtspclientsink', 'rtspclient')
            rtspclient.set_property('location', 'rtsp://usrsokrat:mtt@127.0.0.1:8554/' + self.name + '_' + str(index))
            pipeline.add(rtspclient)

            pipeline.set_state(Gst.State.PLAYING)
            bus = pipeline.get_bus()
            bus.add_signal_watch()

        else:
            pipeline.set_state(Gst.State.READY)
            bus = pipeline.get_bus()
            bus.add_signal_watch()

        return (pipeline, bus)


    def create_recorder_pipeline(self, location, file_location, vcodec='h265'):
        # "queue ! tsdemux ! h265parse ! splitmuxsink"
        if not os.path.exists(file_location):
            os.makedirs(file_location)

        self.file_location = file_location
        self.recorder = Gst.Bin.new()
        self.recorder.set_property('name', 'recorder')
        queue = Gst.ElementFactory.make('queue', 'raw_queue')
        queue.set_property('max-size-time', int(0.1 * Gst.SECOND))
        self.recorder.add(queue)

        parse265 = Gst.ElementFactory.make('h265parse', 'parse265')
        self.recorder.add(parse265)

        parse264 = Gst.ElementFactory.make('h264parse', 'parse264')
        self.recorder.add(parse264)

        persist = Gst.ElementFactory.make('splitmuxsink', 'persist')
        persist.set_property('location', os.path.join(file_location, '%05d.mp4'))
        persist.set_property('max_size_time', (self.record_format.time + 2) * Gst.SECOND)
        persist.set_property('async-handling', False)
        persist.connect('format-location-full', self.on_new_file)
        persist.set_property('muxer', Gst.ElementFactory.make('matroskamux'))
        self.recorder.add(persist)
        if vcodec == 'h265':
            queue.link(parse265)
            parse265.link(persist)
        elif vcodec == 'h264':
            queue.link(parse264)
            parse264.link(persist)
        sink_pad = queue.get_static_pad("sink")
        ghost_pad = Gst.GhostPad.new("sink", sink_pad)
        self.recorder.add_pad(ghost_pad)
        self.recorder.set_state(Gst.State.READY)
        for f in os.listdir(file_location):
            os.remove(os.path.join(file_location, f))

        self.last_buffer_time = None
        self.last_file_name = None
        self.offset = 1
        self.timestamp = None

        self.last_present_time = None

        return self.recorder


    def create_encoder_pipeline(self, name, encoder, resolution=(0, 0), fps=15, bitrate=1, caps=None):
        pipeline = Gst.Bin.new()
        pipeline.set_property('name', 'Encoder_for_%s_%s' % (name, encoder))
        queue = Gst.ElementFactory.make('queue', 'raw_queue')
        queue.set_property('max-size-time', int(0.1 * Gst.SECOND))
        pipeline.add(queue)
        convert = Gst.ElementFactory.make('videoconvert', 'encoder_convert')
        pipeline.add(convert)
        rate = Gst.ElementFactory.make('videorate', 'rate')
        rate.set_property('drop-only', True)
        pipeline.add(rate)
        scale = Gst.ElementFactory.make('videoscale', 'scale')
        pipeline.add(scale)
        if resolution[0] and resolution[1]:
            caps = Gst.Caps.from_string(
                "video/x-raw,width=%d,height=%d,framerate=%d/1" % (resolution[0], resolution[1], fps))
        elif fps:
            caps = Gst.Caps.from_string("video/x-raw,framerate=%d/1" % (fps))
        else:
            caps = None

        if encoder == 'mjpeg':
            encoder = Gst.ElementFactory.make('avenc_mjpeg', 'encoder')
            # encoder.set_property('max-threads', 1)
            encoder.set_property('bitrate', bitrate * 1024 * 1024)
            encoder.set_property('gop-size', fps)
            mux = Gst.ElementFactory.make('multipartmux', 'mux')
            pipeline.add(encoder)
            pipeline.add(mux)

        elif encoder == 'h264':
            encoder = Gst.ElementFactory.make('x264enc', 'encoder')
            encoder.set_property('threads', 1)
            encoder.set_property('bitrate', bitrate * 1024)
            encoder.set_property('cabac', 0)
            encoder.set_property('speed-preset', 'ultrafast')
            encoder.set_property('tune', 'zerolatency')
            encoder.set_property('key-int-max', fps)

            mux = Gst.ElementFactory.make('mpegtsmux', 'mux')
            pipeline.add(encoder)
            pipeline.add(mux)

        multisink = Gst.ElementFactory.make('multifdsink', 'multisink_encoded')
        multisink.set_property('sync-method', 'latest-keyframe')
        multisink.set_property('timeout', 2 * Gst.SECOND)
        multisink.connect('client-added', self.add_client)
        multisink.connect('client-removed', self.remove_client)
        pipeline.add(multisink)

        queue.link(convert)
        convert.link(scale)
        scale.link(rate)

        print(caps)
        if caps:
            rate.link_filtered(encoder, caps)
        else:
            rate.link(encoder)

        encoder.link(mux)
        mux.link(multisink)
        sink_pad = queue.get_static_pad("sink")
        ghost_pad = Gst.GhostPad.new("sink", sink_pad)
        pipeline.add_pad(ghost_pad)

        return pipeline


    def create_detector_pipeline(self, vcodec):
        # 'fdsrc ! tsdemux ! decodebin ! videoparse ! videorate ! videoscale ! motioncells ! jpegenc ! multipartmux !  multifdsink'
        self.detector = Gst.Bin.new()
        self.detector.set_property('name', 'detector')
        queue = Gst.ElementFactory.make('queue', 'raw')
        queue.set_property('max-size-time', int(0.1 * Gst.SECOND))
        self.detector.add(queue)

        decode = Gst.ElementFactory.make('decodebin', 'decoder')
        decode.set_property('async-handling', False)
        self.detector.add(decode)
        decode.connect('pad-added', self.on_decodebin_pad_added)
        queue.link(decode)

        tee = Gst.ElementFactory.make('tee', 'tee_video')
        self.detector.add(tee)

        queue_motion = Gst.ElementFactory.make('queue', 'motion_queue')
        self.detector.add(queue_motion)
        convert = Gst.ElementFactory.make('videoconvert', 'convert')
        self.detector.add(convert)
        rate = Gst.ElementFactory.make('videorate', 'rate')
        rate.set_property('drop-only', True)
        self.detector.add(rate)
        scale = Gst.ElementFactory.make('videoscale', 'scale')
        self.detector.add(scale)
        # #self.detector_height = keep_ratio(self.width,self.height,self.detector_width)
        #
        motioncells = Gst.ElementFactory.make('motioncells', 'motion_detector')
        motioncells.set_property('threshold', 0.03)
        motioncells.set_property('gap', self.record_format.event_seconds_post)
        self.detector.add(motioncells)
        motioncells.set_property('display', 'true')
        textoverlay = Gst.ElementFactory.make('opencvtextoverlay', 'textoverlay')
        textoverlay.set_property('text', 'No motion')
        textoverlay.set_property('ypos', 30)
        textoverlay.set_property('xpos', 288 - 100)
        textoverlay.set_property('colorR', 255)
        self.detector.add(textoverlay)
        # #
        motion_tee = Gst.ElementFactory.make('tee', 'motion_tee')
        self.detector.add(motion_tee)
        #

        app_queue = Gst.ElementFactory.make('queue', 'app_queue')
        self.detector.add(app_queue)
        self.appsink = Gst.ElementFactory.make('appsink', 'raw_sink')
        self.appsink.set_property('max-buffers', 1)
        self.appsink.set_property('drop', True)
        self.detector.add(self.appsink)
        #
        fakesink = Gst.ElementFactory.make('fakesink', 'fakesink')
        fakesink.set_property('async', False)
        self.detector.add(fakesink)
        #
        if self.detect_motion:
            motioncells.set_property('calculatemotion', True)
        else:
            motioncells.set_property('calculatemotion', False)

        sink_pad = queue.get_static_pad("sink")
        ghost_pad = Gst.GhostPad.new("sink", sink_pad)
        self.detector.add_pad(ghost_pad)

        # self.detector.set_state(Gst.State.READY)

        return self.detector

    def create_face_detector(self):
        pass
        # qeueue name=face !
    def on_rtspsrc_pad_added(self, rtspsrc, pad, *user_data):
        parent = rtspsrc.get_property('parent')

        location = rtspsrc.get_property('location')

        pad_caps = pad.get_current_caps()
        pad_struct = pad_caps.get_structure(0)
        pad_type = pad_struct.get_name()

        encoding = pad_struct.get_value('encoding-name')

        media = pad_struct.get_value('media')

        # print(pad_struct.to_string())
        if media == 'video':
            if encoding == "H264":
                self.vcodecs[location] = encoding.lower()
                depay = parent.get_by_name('depay264')
                parse = parent.get_by_name('parse264')

                tee = parent.get_by_name('video_tee')
                mux_queue = parent.get_by_name('mux_queue')
                mux = parent.get_by_name('muxer_stream')
                multisink = parent.get_by_name('multisink')

                new_pad = depay.get_static_pad('sink')
                pad.link(new_pad)

                depay.link(parse)
                parse.link(tee)
                tee.link(mux_queue)
                mux_queue.link(mux)
                mux.link(multisink)
                if self.publish_rtsp:
                    rtsp_queue = parent.get_by_name('rtsp_queue')
                    rtsp_client = parent.get_by_name('rtspclient')
                    tee.link(rtsp_queue)
                    rtsp_queue.link(rtsp_client)


            elif encoding == "H265":
                self.vcodecs[location] = encoding.lower()
                depay = parent.get_by_name('depay265')
                parse = parent.get_by_name('parse265')
                tee = parent.get_by_name('video_tee')
                mux_queue = parent.get_by_name('mux_queue')
                mux = parent.get_by_name('muxer_stream')
                multisink = parent.get_by_name('multisink')

                new_pad = depay.get_static_pad('sink')
                pad.link(new_pad)
                depay.link(parse)
                parse.link(tee)
                tee.link(mux_queue)
                mux_queue.link(mux)
                mux.link(multisink)
                if self.publish_rtsp:
                    rtsp_queue = parent.get_by_name('rtsp_queue')
                    rtsp_client = parent.get_by_name('rtspclient')
                    tee.link(rtsp_queue)
                    rtsp_queue.link(rtsp_client)


            elif encoding == 'JPEG':
                self.vcodecs[location] = encoding.lower()
                depay = parent.get_by_name('jpegdepay')
                parse = parent.get_by_name('jpegparse')
                tee = parent.get_by_name('video_tee')
                mux_queue = parent.get_by_name('mux_queue')
                mux = parent.get_by_name('muxer_mulripart')
                multisink = parent.get_by_name('multisink')
                new_pad = depay.get_static_pad('sink')
                pad.link(new_pad)
                depay.link(parse)
                parse.link(tee)
                tee.link(mux_queue)
                mux_queue.link(mux)
                mux.link(multisink)
                if self.publish_rtsp:
                    rtsp_queue = parent.get_by_name('rtsp_queue')
                    rtsp_client = parent.get_by_name('rtspclient')
                    tee.link(rtsp_queue)
                    rtsp_queue.link(rtsp_client)


    def on_decodebin_pad_added(self, decoder, pad, *user_data):
        parent = decoder.get_property('parent')
        tee = parent.get_by_name('tee_video')
        queue = parent.get_by_name('motion_queue')
        convert = parent.get_by_name('convert')
        detector = parent.get_by_name('motion_detector')
        textoverlay = parent.get_by_name('textoverlay')
        rate = parent.get_by_name('rate')
        scale = parent.get_by_name('scale')
        app_queue = parent.get_by_name('app_queue')
        appsink = parent.get_by_name('raw_sink')

        motion_tee = parent.get_by_name('motion_tee')
        fakesink = parent.get_by_name('fakesink')

        # print('Pad created for decoder')
        pad.link(tee.get_static_pad('sink'))

        tee.link(queue)

        queue.link(convert)

        convert.link(rate)

        rate.link(scale)
        # caps = Gst.Caps.from_string("video/x-raw,width=%d,height=%d,framerate=%d/1" % (self.detector_width,self.detector_height,self.detector_fps))
        # scale.link_filtered(detector,caps)
        scale.link(detector)

        detector.link(textoverlay)
        textoverlay.link(motion_tee)
        motion_tee.link(fakesink)

        tee.link(app_queue)
        app_queue.link(appsink)


    def get_fd_count(self):
        print(len(os.listdir(self.fd_dir)))


    def add_client(self, sink, fd):
        sink.get_property('parent').set_state(Gst.State.PLAYING)
        self.start_times[sink.get_property('parent')] = datetime.now()


    def remove_client(self, sink, fd, status):
        # print("Removing client")
        # print(self.fifos.get(fd))
        os.close(fd)
        if self.fifos.get(fd):
            os.unlink(self.fifos.get(fd))
            del self.fifos[fd]


    def update_param(self):
        params = {}
        params['name'] = self.name
        params['pid'] = os.getpid()
        params['port'] = self.http_port
        params['ip'] = self.pod_ip
        if self.cam_options:
            params['direct_streams'] = []
            i = 0
            for location in self.cam_options.keys():
                try:
                    vcodec = self.vcodecs[location]
                    options = self.cam_options.get(location)
                    stream = (i, location, options[1], options[2], vcodec)
                    params['direct_streams'].append(stream)
                    i += 1
                except:
                    pass

            params['main_distributor'] = []

            for sink in self.sinks:
                params['main_distributor'].append(sink)

            params['native_sinks'] = []

            for sink in self.native_sinks:
                params['native_sinks'].append(sink)

            params['encoder_sinks'] = []

            for sink in self.encoder_sinks:
                params['encoder_sinks'].append(sink)

        if self.params != params:
            self.params = params

        self.redis_queue.set('videoserver-multifd:%s:config' % self.id, json.dumps(params), ex=5)
        # self.redis_queue.expire('videoserve-mulrifd:%s:config' % self.id, 1)


    def parse_incomming(self):
        m = self.message_bus.get_message()
        if m:
            if m.get('type') == 'message':
                data = json.loads(m.get('data'))
                name = data.get('event').lower()

                print(name)
                print(data)

                if name == 'new_fd':
                    id = data.get('fd')
                    num = data.get('num')
                    if num != 9:
                        try:
                            sink = self.sinks[num]
                        except:
                            sink = self.sinks[-1]

                        if sink in self.native_sinks:

                            m = self.native_sinks.index(sink)

                            pipeline, bus = list(self.pipelines.values())[m]

                            sink_name = 'multisink'

                        else:

                            sink_name = 'multisink_encoded'
                            width = sink[1]
                            if not self.encoders.get(width):
                                pipeline = self.create_encoder_pipeline('encoder',
                                                                        encoder=sink[0].lower(),
                                                                        resolution=(sink[1], sink[2]),
                                                                        fps=sink[3])

                                self.detector.add(pipeline)
                                tee = self.detector.get_by_name('tee_video')
                                print(tee.link(pipeline))
                                self.encoders[width] = pipeline
                            else:
                                pipeline = self.encoders[width]
                    else:
                        sink_name = 'multisink_encoded'
                        if not self.encoders.get('motion'):

                            pipeline = self.create_encoder_pipeline('motion-debug', encoder='h264', fps=15)
                            self.detector.add(pipeline)

                            tee = self.detector.get_by_name('motion_tee')
                            print(tee.link(pipeline))

                            self.encoders['motion'] = pipeline
                        else:
                            pipeline = self.encoders['motion']

                    while pipeline.get_state(0.1 * Gst.SECOND)[1] != Gst.State.PLAYING:
                        pipeline.set_state(Gst.State.PLAYING)
                        time.sleep(0.1)

                    if not os.path.exists(id):
                        os.mkfifo(id)
                    time.sleep(0.1)
                    w = os.open(id, os.O_WRONLY | os.O_NONBLOCK)
                    self.fifos[w] = id
                    multisink = pipeline.get_by_name(sink_name)
                    multisink.emit('add', w)

                elif name == 'start_record':
                    num = data.get('num')
                    self.start_record(num)

                elif name == 'stop_record':

                    if self.recording:
                        self.stop_record()

                elif name == 'new_file':
                    self.watchdog.set()
                    files = data.get('files')
                    for path in sorted(list(files.keys())):
                        timestamp = files[path].get('timestamp')
                        begin_time = datetime.fromtimestamp(timestamp)
                        ret = self.record_format.get_record_type(begin_time)
                        if ret[0]:
                            if ret[2] == 'hybrid' or ret[2] == 'persistent':
                                if self.ftp_host:
                                    try:
                                        upload_file(path, host=self.ftp_host)
                                    except:
                                        print('%s: Unable to upload file to %s' % (self.name, self.ftp_host))
                                        os.remove(path)
                                else:
                                    print('%s: No ftp archive to upload' % self.name)
                                    os.remove(path)


                            elif '#' in path:
                                if self.ftp_host:
                                    try:
                                        upload_file(path, host=self.ftp_host)
                                    except:
                                        print('%s: Unable to upload file to %s' % (self.name,self.ftp_host))
                                        os.remove(path)
                                else:
                                    print('%s: No ftp archive to upload' % self.name)
                                    os.remove(path)
                            else:
                                os.remove(path)

                        else:
                            os.remove(path)
                        time.sleep(0.1)


                elif name == 'alarm_active':
                    timeout = data.get('timeout')
                    num = data.get('num')

                    self.start_record()

                    message = {'event': 'start_motion', 'timeout': timeout}
                    self.redis_queue.publish(self.name + '_' + 'recorder', json.dumps(message))

                elif name == 'alarm_stop':

                    message = {'event': 'stop_motion'}
                    self.redis_queue.publish(self.name + '_' + 'recorder', json.dumps(message))

                elif name == 'start_detector':
                    num = data.get('num')
                    self.start_detector(num)

                elif name == 'new_job':
                    self.name = data.get('name')
                    self.host = data.get('host')
                    self.username = data.get('user')
                    self.password = data.get('password')
                    if self.state == 'standby':
                        self.configure_cam(self.name, self.host, self.username, self.password)

                elif name == 'new_archive':
                    self.ftp_host = data.get('ftp_host')


    def test_multisink(self):
        if not self.publish_rtsp:
            for pipeline, bus in list(self.pipelines.values()):

                sink = pipeline.get_by_name('multisink')
                handles = sink.get_property('num-handles')

                if handles == 0:
                    if self.start_times.get(pipeline):
                        state = pipeline.get_state(5 * Gst.SECOND)
                        if state[1] == Gst.State.PLAYING:
                            if pipeline.get_by_name('recorder') == None and pipeline.get_by_name('decoder') == None:
                                if (datetime.now() - self.start_times.get(pipeline)).seconds > 60:
                                    pipeline.send_event(Gst.Event.new_eos())
                                    del self.start_times[pipeline]

            for width in self.encoders.keys():
                bin = self.encoders[width]
                sink = bin.get_by_name('multisink_encoded')
                handles = sink.get_property('num-handles')

                if handles == 0:
                    if self.start_times.get(bin):
                        state = bin.get_state(5 * Gst.SECOND)
                        if state[1] == Gst.State.PLAYING:
                            if (datetime.now() - self.start_times.get(bin)).seconds > 30:
                                ghostpad = bin.get_static_pad("sink")
                                teepad = ghostpad.get_peer()
                                pad_probe = partial(self.blocking_pad_probe, width)
                                teepad.add_probe(Gst.PadProbeType.BLOCK, pad_probe)
                                del self.start_times[bin]


    def blocking_pad_probe(self, width, pad, info):
        # Function to remove bin from pipeline
        parent = pad.get_property('parent')
        bin = self.encoders[width]
        name = bin.get_property('name')
        print('Stoping bin %s ' % name)
        bin.set_state(Gst.State.NULL)
        # tee = self.detector.get_by_name('tee_video')
        tee = self.detector.get_by_name(parent.get_property('name'))
        ghostpad = bin.get_static_pad("sink")
        teepad = ghostpad.get_peer()
        # print('Removing encoder bin')
        self.detector.remove(bin)
        # print("Releasing Tee-Pad")
        tee.release_request_pad(teepad)

        del self.encoders[width]
        return Gst.PadProbeReturn.REMOVE


    # def split_now(self, timeout=15):
    #     if not self.split_lock or datetime.now() - self.split_lock >= timedelta(seconds=timeout):
    #         self.split_lock = datetime.now()
    #         persist = self.recorder.get_by_name('persist')
    #         print('splitting file')
    #         persist.emit('split-now')
    #
    #     else:
    #         pass

    def split_now(self, timeout=15):
        print('Manual split')
        if not self.split_lock:
            beging_time = datetime.now()
            persist = self.recorder.get_by_name('persist')
            try:
                persist.emit('split-after')
            except:
                persist.emit('split-now')
            self.split_lock = True

            while self.split_lock:
                if datetime.now() - beging_time > timedelta(seconds=timeout):
                    break
                time.sleep(1)


    def handle_splits(self):
        if not self.split_lock and self.last_buffer_time:
            record_type = self.record_format.get_record_type(datetime.now())
            if datetime.now() - self.last_buffer_time > timedelta(seconds=15):

                if self.last_buffer_time.strftime('%H') != (datetime.now() - timedelta(seconds=2)).strftime('%H'):
                    self.split_now()

                if record_type[-1] == 'hybrid' or record_type[-1] == 'event':
                    if not self.alarm[0] and not self.alarm[1]:
                        if self.record_format.event_seconds_pre:
                            if datetime.now() - self.last_buffer_time >= timedelta(
                                    seconds=self.record_format.event_seconds_pre):
                                self.split_now()

                if self.alarm[0]:
                    if not self.alarm[1]:
                        # self.split_now()
                        pass
                        # print('Alarm recieved, initial split')
                        # self.alarm[1] = True
                        # self.split_now()

                    elif datetime.now() - self.alarm[2] > timedelta(seconds=self.alarm[3]):
                        print('Alarm timeout, final split')
                        self.alarm[0] = False
                        self.split_now()

                else:
                    if self.alarm[1]:
                        # print('Alarm stoped')
                        self.split_now()
                        # if record_type[-1] == 'hybrid' or record_type[-1] == 'event':
                        #     if self.record_format.event_seconds_post:
                        #         if datetime.now() - self.alarm[2] >= timedelta(seconds=self.record_format.event_seconds_post):
                        #             self.split_now()
                        #     else:
                        #         self.split_now()
                        # else:
                        #     self.split_now()


    def on_new_file(self, sink, id, sample):
        location = sink.get_property('location')

        file_name = location % id

        buff = sample.get_buffer()

        present_time = float(buff.dts) / Gst.SECOND

        print(present_time)
        print(float(buff.pts) / Gst.SECOND)

        if id == 0:
            self.last_buffer_time = datetime.now()
            present_time = float(buff.dts) / Gst.SECOND

            self.record_begin_time = datetime.now() - timedelta(seconds=present_time)

            print(self.record_begin_time)

            self.last_pts_time = float(buff.pts) / Gst.SECOND
            self.last_present_time = present_time

            self.last_file_name = file_name


        else:

            present_time = float(buff.dts) / Gst.SECOND
            self.last_present_time = present_time
            duration = (present_time - self.last_present_time)
            if duration > (self.record_format.time * 5) or duration <= 0:
                if id == 1:
                    print('Invalid dts, using pts')
                duration = float(buff.pts) / Gst.SECOND - self.last_pts_time
                self.last_pts_time = float(buff.pts) / Gst.SECOND

            begin = datetime.now()

            begin_time = begin - timedelta(seconds=duration)

            begin = datetime.now()
            print(begin_time)
            print(begin)

            new_name = self.name + '=' + begin_time.strftime('%y%m%d') + '=' + begin_time.strftime(
                '%H') + '=' + begin_time.strftime('%M%S') + '-' + '%05d' % int(round(duration)) + '.mp4'

            final_name = os.path.join(self.file_location, new_name)

            shutil.move(self.last_file_name, final_name)

            self.file_list[final_name] = (begin_time, begin)

            files_to_export = []

            if not self.alarm[0] and not self.alarm[1]:
                # If not alarmed - handle expired files
                if self.record_format.event_seconds_pre:
                    for file in sorted(list(self.file_list.keys())):
                        begin, end = self.file_list[file]
                        if datetime.now() - end > timedelta(seconds=self.record_format.event_seconds_pre):
                            files_to_export.append((file, begin, False))
                            del self.file_list[file]
                else:
                    for file in sorted(list(self.file_list.keys())):
                        begin, end = self.file_list[file]

                        files_to_export.append((file, begin, False))
                        del self.file_list[file]


            elif self.alarm[0]:
                # If alarmed - set was_alarmed
                self.alarm[1] = True

                # if self.record_format.event_seconds_pre:
                for file in sorted(list(self.file_list.keys())):
                    begin, end = self.file_list[file]
                    alarm_begin = self.alarm[2] - timedelta(seconds=self.record_format.event_seconds_pre)
                    alarm_end = self.alarm[2] + timedelta(seconds=self.record_format.event_seconds_post)

                    if (begin <= alarm_begin <= end) or (begin <= alarm_end <= end):
                        files_to_export.append((file, begin, True))
                        del self.file_list[file]

                    else:
                        files_to_export.append((file, begin, False))
                        del self.file_list[file]

                # else:
                #     for file in self.file_list:
                #         files_to_export.append((file[0], file[1], True))
                #         self.file_list.remove(file)


            elif self.alarm[1]:

                for file in sorted(list(self.file_list.keys())):
                    begin, end = self.file_list[file]
                    alarm_begin = self.alarm[2] - timedelta(seconds=self.record_format.event_seconds_pre)
                    alarm_end = self.alarm[2] + timedelta(seconds=self.record_format.event_seconds_post)

                    # print(begin,end)
                    # print(alarm_begin)
                    # print(alarm_end)
                    if (begin <= alarm_begin <= end) or (begin <= alarm_end <= end):

                        files_to_export.append((file, begin, True))
                        del self.file_list[file]

                    else:
                        files_to_export.append((file, begin, False))
                        del self.file_list[file]

                self.alarm[1] = False
                self.alarm[2] = None

            if files_to_export:
                files = {}

                for file in files_to_export:
                    if file[2]:
                        if not '#' in file[0]:
                            basename = os.path.basename(file[0])
                            new_basename = basename.replace('.mp4', '#.mp4')
                            # print(file[0])
                            # print(os.path.join(os.path.dirname(file[0]),new_basename))
                            shutil.move(file[0], os.path.join(os.path.dirname(file[0]), new_basename))

                            files[os.path.join(os.path.dirname(file[0]), new_basename)] = {
                                'timestamp': datetime.timestamp(file[1])}
                        else:
                            files[file[0]] = {'timestamp': datetime.timestamp(file[1])}
                    else:
                        files[file[0]] = {'timestamp': datetime.timestamp(file[1])}

                message = {'event': 'new_file', 'files': files}
                print(message)
                self.redis_queue.publish(self.id + '_multifd', json.dumps(message))

            self.last_buffer_time = self.last_buffer_time + timedelta(seconds=duration)
            self.last_file_name = file_name

            self.split_lock = False

            if len(os.listdir(self.file_location)) > 5:
                print('Main process hang?')
                print(len(os.listdir(self.file_location)))
                self.exit.set()


    def update_sinks(self):
        self.native_sinks = []
        for location in self.cam_options.keys():
            sink = list(self.cam_options[location])
            if self.vcodecs.get(location):
                sink[0] = self.vcodecs.get(location)
            self.native_sinks.append(sink)

        self.sinks = []
        self.sinks = copy(self.native_sinks)
        self.sinks.extend(self.encoder_sinks)
        self.sinks = sorted(self.sinks, key=lambda x: x[1], reverse=True)


    def configure_cam(self, name, host, user, password):

        if ping(host):
            self.onvif = self.setup_onvif(host=host, user=user, passwd=password)

            if self.onvif.state == 1:

                self.locations, self.snap_uri, self.cam_options = self.get_cam_options()
                # print(self.cam_options)

                encoder_sinks = []

                self.encoder_num = None
                print(self.cam_options)
                # print(self.encoder_resolutions)

                if not len(self.encoder_resolutions):
                    for item in list(self.cam_options.items()):
                        if self.min_detector_width <= item[1][1]:
                            self.encoder_num = list(self.cam_options.keys()).index(item[0])
                else:
                    for item in list(self.cam_options.items()):
                        if self.encoder_resolutions[0][1] <= item[1][1]:
                            self.encoder_num = list(self.cam_options.keys()).index(item[0])

                if self.encoder_resolutions:
                    encoder_options = list(self.cam_options.values())[self.encoder_num]
                    for encoder_resolution in self.encoder_resolutions:
                        height = self.keep_ratio(encoder_options[1], encoder_options[2], encoder_resolution[1])
                        encoder_sink = [encoder_resolution[0], encoder_resolution[1], height, encoder_resolution[2],
                                        encoder_resolution[3]]
                        self.encoder_sinks.append(encoder_sink)

            # print(self.encoder_sinks)

            n = 0
            for location in self.cam_options.keys():
                pipeline, bus = self.create_base_pipeline(location, self.user, self.password)

                self.pipelines[location] = (pipeline, bus)
                sink = list(self.cam_options[location])

                self.native_sinks.append(sink)
                n += 1

            #
            # for vcodec in list(self.vcodecs.values()):
            #     index = list(self.vcodec.values()).index(vcodec)
            #     self.native_sinks[index][0] = vcodec

            self.sinks = copy(self.native_sinks)
            self.sinks.extend(self.encoder_sinks)
            self.sinks = sorted(self.sinks, key=lambda x: x[1], reverse=True)

            if not self.locations:
                print('Onvif failed')
                print(self.name)
                self.exit.set()

            if self.pipelines:
                self.recording = False
                self.detecting = False

                self.http_server = base_server(self.id, port=8888, redis_host=self.redis_host, fifo_path=self.fifo_path)
                self.http_port = self.http_server.port
                print('Starting http server on %d' % self.http_port)
                self.http_server.start()

            self.get_fd_count()
            self.begin_time = datetime.now()


    def stop_all(self):
        # if self.recording:
        #     self.stop_record()
        # if self.detecting:
        #     self.stop_record()
        for pipeline, bus in list(self.pipelines.values()):
            pipeline.set_state(Gst.State.NULL)
            del pipeline
            del bus


    def announce_state(self):
        if self.mode == 'manual':
            state = {'id': self.id, 'name': self.name, 'host': self.host, 'pid': os.getpid()}
        elif self.mode == 'kubernetes':
            state = {'id': self.id, 'name': self.name, 'host': self.host, 'pod_ip': self.pod_ip}

        self.redis_queue.set('videoserve-mulrifd:workers:%s' % self.id, json.dumps(state), ex=10)
        # self.redis_queue.expire('videoserve-mulrifd:workers:%s' % self.id,10)


    def run(self):

        self.fd_dir = '/proc/%d/fd' % os.getpid()

        self.pod_name = os.getenv('MY_POD_NAME')
        self.namespace = os.getenv('MY_POD_NAMESPACE')
        self.pod_ip = os.getenv('MY_POD_IP')
        self.redis_host_env = os.getenv('REDIS_HOST')
        self.ftp_host_env = os.getenv('FTP_HOST')

        if self.pod_name:
            self.mode = 'kubernetes'
            self.id = self.pod_name.split('-')[-1]
            if self.redis_host_env:
                self.redis_host = self.redis_host_env
            if self.ftp_host_env:
                self.ftp_host = self.ftp_host_env


        else:
            self.mode = 'manual'
            self.id = str(uuid4()).split('-')[-1]

        print(self.mode)

        print(self.id)

        self.message_bus = self.redis_queue.pubsub(ignore_subscribe_messages=True)
        print(self.id+'_multifd')
        self.message_bus.subscribe(self.id + '_multifd')

        if self.host:
            print(self.host)
            self.configure_cam(self.name, self.host, self.user, self.password)
            self.state = 'active'
        else:
            print('No camera specified, worker in standby mode')
            self.state = 'standby'

        while not self.exit.is_set():
            if self.pipelines:
                for pipeline, bus in list(self.pipelines.values()):
                    message = bus.timed_pop_filtered(0.1 * Gst.SECOND,
                                                     Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.ELEMENT)

                    if message:
                        msgType = message.type

                        struct = message.get_structure()
                        if msgType == Gst.MessageType.ELEMENT:
                            if (struct.has_field("motion_begin")):
                                print('Motion started on %s' % self.name)
                                textoverlay = self.detector.get_by_name('textoverlay')
                                if textoverlay:
                                    textoverlay.set_property('colorR', 0)
                                    textoverlay.set_property('colorG', 255)
                                    textoverlay.set_property('text', 'Motion')

                                # self.redis_queue.publish(self.name + '_multifd', json.dumps(message))

                                self.alarm[0] = True
                                self.alarm[2] = datetime.now()
                                self.alarm[3] = self.alarm_timeout

                            elif (struct.has_field("motion_finished")):
                                print('Motion finished on %s' % self.name)
                                textoverlay = self.detector.get_by_name('textoverlay')
                                if textoverlay:
                                    textoverlay.set_property('text', 'No motion')
                                    textoverlay.set_property('colorR', 255)
                                    textoverlay.set_property('colorG', 0)

                                self.alarm[0] = False
                                self.alarm[1] = True

                        if msgType == Gst.MessageType.ERROR:
                            print('Err on main pipeline')
                            err, debug = message.parse_error()
                            print(err)
                            self.exit.set()

                        if msgType == Gst.MessageType.EOS:
                            source = pipeline.get_by_name('source')
                            location = source.get_property('location')
                            print('EOS on %s' % location)
                            if pipeline.get_by_name('recorder') or pipeline.get_by_name('detector'):
                                self.exit.set()
                            else:
                                pipeline.set_state(Gst.State.NULL)
                                if not self.publish_rtsp:
                                    pipeline.set_state(Gst.State.READY)
                                else:
                                    pipeline.set_state(Gst.State.PLAYING)
                                self.begin_time = datetime.now()

                    self.check_schedule()
                    self.handle_splits()
                    self.test_multisink()
                    self.update_sinks()
                    self.update_param()
            self.parse_incomming()
            self.announce_state()
            self.update_watchdog()
            time.sleep(1)

        self.stop_all()
        self.http_server.exit.set()
        print('Cam handler exiting')


        sys.exit(-1)


if __name__ == '__main__':
    #s = Splitter(name='in_office', redis_host='localhost', ftp_host='192.168.10.152', host='192.168.1.133')
    # s.start()
    # while s.is_alive():
    #     time.sleep(1)
    redis_host = os.getenv('REDIS_HOST')
    ftp_host = os.getenv('FTP_HOST')
    s = Splitter(redis_host=redis_host,ftp_host=ftp_host)
    s.run()