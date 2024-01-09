#!/usr/bin/env python

from onvif import ONVIFCamera
import os
import sys
import argparse
import time
import logging
from datetime import datetime
from calendar import timegm
import time
from collections import OrderedDict
import json

logging.getLogger("onvif").setLevel(logging.CRITICAL)
logging.getLogger("suds.client").setLevel(logging.CRITICAL)
logging.getLogger("suds.xsd.schema").setLevel(logging.CRITICAL)
logging.getLogger("suds.wsdl").setLevel(logging.CRITICAL)


class cam_control:
	def __init__(self, host, port = None, user='admin', password='admin', path=None, wsdldir=None):
		self.state = 0

		if not wsdldir:
			wsdldir = os.path.join('wsdl')

		self.mycam = None

		if port:
			self.mycam = ONVIFCamera(host, port, user, password, wsdldir)

		else:
			try:
				port = 80
				self.mycam = ONVIFCamera(host, port, user, password, wsdldir)
			except:
				try:
					port = 2000
					self.mycam = ONVIFCamera(host, port, user, password, wsdldir)
				except:
					try:
						port = 8999
						self.mycam = ONVIFCamera(host, port, user, password, wsdldir)
					except:
						port = None
						pass

		if self.mycam:
			self.state = 1

	def reboot(self):
		try:
			self.mycam.devicemgmt.SystemReboot()
			return 0
		except:
			return 1
		# print 'Reboot signal send'

	def get_ntp(self):
		try:
			ntp = self.mycam.devicemgmt.GetNTP()
			print('From dhcp: ', ntp.FromDHCP)
			print('Manual: ', ntp.NTPManual)
		except:
			pass

	def set_ntp(self, host):
		#try:
		ntp = self.mycam.devicemgmt.create_type('SetNTP')
		ntp.FromDHCP = False
		ntp.NTPManual = {'Type': 'IPv4', 'IPv4Address': host}
		self.mycam.devicemgmt.SetNTP(ntp)
		self.set_date(dtype='ntp',set_tz=True)
		#except:
		#	pass

	def get_date(self):
		sync_type = None
		date = None
		try:
			time = self.mycam.devicemgmt.GetSystemDateAndTime()
			sync_type = time.DateTimeType
			d_save = time.DaylightSavings
			tz = time.TimeZone.TZ

			cam_year = time.UTCDateTime.Date.Year
			cam_month = time.UTCDateTime.Date.Month
			cam_day = time.UTCDateTime.Date.Day

			cam_hour = time.UTCDateTime.Time.Hour
			cam_minute = time.UTCDateTime.Time.Minute
			cam_second = time.UTCDateTime.Time.Second

			date = datetime(year=cam_year, month=cam_month, day=cam_day, hour=cam_hour, minute=cam_minute,
			                second=cam_second)
		except:
			pass

		return sync_type, date, tz, d_save

	def set_date(self, dtype='manual',set_tz=False):
		# time_params = mycam.devicemgmt.create_type('SetSystemDateAndTime')
		# time_params.DateTimeType = 'Manual'
		# time_params.DaylightSavings = False
		try:
			timezone = time.strftime("%z")
			tz = 'UTC' + timezone[:3] + ':' + timezone[3:]



			if dtype == 'manual':
				if not set_tz:
					now = datetime.now()
				else:
					now = datetime.utcnow()

				time_params = self.mycam.devicemgmt.create_type('SetSystemDateAndTime')
				time_params.DateTimeType = 'Manual'
				time_params.DaylightSavings = False

				if set_tz:
					time_params.TimeZone.TZ = tz

				time_params.UTCDateTime.Date.Year = now.year
				time_params.UTCDateTime.Date.Month = now.month
				time_params.UTCDateTime.Date.Day = now.day

				time_params.UTCDateTime.Time.Hour = now.hour
				time_params.UTCDateTime.Time.Minute = now.minute
				time_params.UTCDateTime.Time.Second = now.second

				self.mycam.devicemgmt.SetSystemDateAndTime(time_params)

			else:

				time_params = self.mycam.devicemgmt.create_type('SetSystemDateAndTime')
				time_params.DateTimeType = 'NTP'
				time_params.DaylightSavings = False
				time_params.TimeZone.TZ = tz
				self.mycam.devicemgmt.SetSystemDateAndTime(time_params)
		except:
			return 1

		else:
			return 0

	def get_snapshot_uri(self):
		uri = None
		try:
			media_service = self.mycam.create_media_service()
			profiles = media_service.GetProfiles()
			token = profiles[0].token
			snap = media_service.create_type('GetSnapshotUri')
			snap.ProfileToken = token
			uri = media_service.GetSnapshotUri(snap).Uri

		except:
			pass

		return uri

	def get_video_uri(self):
		uri = []
#				try:
		media_service = self.mycam.create_media_service()
		profiles = media_service.GetProfiles()
		for profile in profiles:
			token = profile.token
			url = media_service.create_type('GetStreamUri')
			url.ProfileToken = token
			url.StreamSetup = {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}}
			new = media_service.GetStreamUri(url)
			uri.append(new.Uri)
#		except:
#			pass

		return uri

	def get_video_options(self):
		res = []
		try:
			media_service = self.mycam.create_media_service()
			profiles = media_service.GetProfiles()
			configurations_list = media_service.GetVideoEncoderConfigurations()

			i = 0
			for profile in profiles:
				conf = configurations_list[i]
				encoding =  conf.Encoding
				width =  int(conf.Resolution.Width)
				height = int(conf.Resolution.Height)
				fps =  int(conf.RateControl.FrameRateLimit)
				bitrate =  int(conf.RateControl.BitrateLimit)

				if conf.Encoding == 'H264':
					gop = int(conf.H264.GovLength)
				else:
					gop = 1
				res.append((encoding,width,height,fps,bitrate,gop))
				i += 1
		except:
		 	pass

		return res





	def get_video_codec_options(self):
		ret =[]
		try:
			media_service = self.mycam.create_media_service()
			ret = media_service.GetVideoEncoderConfigurationOptions()
		except:
			pass
		return ret



	def set_video_options(self, num=0, enc='H264',fps=25,bitrate=4096, gop = None,width=None,height=None):
		self.enc = enc
		self.fps = fps
		self.bitrate = bitrate
		if not gop:
			self.gop = int(self.fps)
		else:
			self.gop = int(gop)

		media_service = self.mycam.create_media_service()
		profiles = media_service.GetProfiles()
		self.changed = False

		if num < len(profiles):
			conf = media_service.GetVideoEncoderConfigurations()[num]
			encoding = conf.Encoding
			if encoding != self.enc:
				self.changed = True

			fps = int(conf.RateControl.FrameRateLimit)

			if fps != self.fps:
				self.changed = True

			bitrate = int(conf.RateControl.BitrateLimit)

			if bitrate != self.bitrate:
				self.changed = True

			if encoding == 'H264':
				try:
					gop = int(conf.H264.GovLength)
					if gop != self.gop:
						self.changed = True
				except:
					pass
			if width and height:
				if width != conf.Resolution.Width or height != conf.Resolution.Height:
					self.changed = True


			if self.changed:
				conf.Encoding = self.enc
				conf.RateControl.FrameRateLimit = str(self.fps)
				conf.RateControl.BitrateLimit = str(self.bitrate)

				try:
					if conf.Encoding == 'H264':
						conf.H264.GovLength = str(self.gop)
				except:
					pass

				if width and height:
					conf.Resolution.Width = width
					conf.Resolution.Height = height

				request = media_service.create_type('SetVideoEncoderConfiguration')
				request.Configuration = conf
				request.ForcePersistence = True
				media_service.SetVideoEncoderConfiguration(request)

	def get_cam_details(self):
		details = OrderedDict()
		urls = self.get_video_uri()
		video_opt = self.get_video_options()
		try:
			snapshot_uri = self.get_snapshot_uri()
		except:
			snapshot_uri = None
		for i in urls:
			try:
				details[i] = video_opt[urls.index(i)]
			except:
				pass

		return details, snapshot_uri

if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Usage: camctl.py reboot -c 192.168.12.101')
	parser.add_argument('action', help='Action to preform: reboot,get_ntp,get_date,set_ntp,set_date')
	parser.add_argument('-s', '--server', help='IPv4 address of ntp server')
	parser.add_argument('-c', '--cam', help='Camera name')
	parser.add_argument('-u','--user',default='admin',help='User for cam')
	parser.add_argument('-p','--password',default='admin',help='Password for cam')

	parser.add_argument('-e', '--encoder', default='H264', help='Encoder for cam H264 or JPEG')
	parser.add_argument('-n', '--num', type=int, default=0, help='Num of encoder')
	parser.add_argument('-b', '--bitrate', type=int, default=4096, help='Bitrate for stream')
	parser.add_argument('-f', '--fps', type=int, default=25, help='Bitrate for stream')
	parser.add_argument('-g', '--gop', type=int, default=0, help='Gop for stream')
	parser.add_argument('-r', '--res',help='Resolution of stream eg: 640:360')




	args = parser.parse_args()

	user = args.user
	pwd = args.password
	width = None
	height = None

	encoder = args.encoder
	num = args.num
	bitrate = args.bitrate
	fps = args.fps
	gop = args.gop
	resolution = args.res
	if resolution:
		width = int(resolution.split(':')[0])
		height = int(resolution.split(':')[1])


	if args.action == 'reboot':
		try:
			mycam = cam_control(args.cam,user=user,password=pwd)
			if mycam.reboot() == 0:
				print('Cam rebooted')
		except:
			print('Cannot reboot cam')

	if args.action == 'get_video_options':
		mycam = cam_control(args.cam, user=user,password=pwd)
		print(mycam.get_video_options())

	if args.action == 'set_video_options':
		mycam = cam_control(args.cam, user=user, password=pwd)
		mycam.set_video_options(num,enc=encoder,fps=fps,bitrate=bitrate,gop=gop,width=width,height=height)

	if args.action == 'get_ntp':
		mycam = cam_control(args.cam, user=user, password=pwd)
		mycam.get_ntp()

	if args.action == 'set_ntp':
		mycam = cam_control(args.cam, user=user, password=pwd)
		mycam.set_ntp(args.server)

	if args.action == 'get_date':
		mycam = cam_control(args.cam, user=user, password=pwd)
		print(mycam.get_date())

	if args.action == 'set_date':
		mycam = cam_control(args.cam, user=user, password=pwd)
		mycam.set_date(set_tz=False)

	if args.action == 'get_snapshot_uri':
		mycam = cam_control(args.cam, user=user, password=pwd)
		print(mycam.get_snapshot_uri())

	if args.action == 'get_video_uri':
		mycam = cam_control(args.cam, user=user, password=pwd)
		print(mycam.get_video_uri())

	if args.action == 'get_video_codec_options':
		mycam = cam_control(args.cam, user=user, password=pwd)
		print(mycam.get_video_codec_options())

	if args.action == 'get_cam_details':
		mycam = cam_control(args.cam, user=user, password=pwd)
		print(json.dumps(mycam.get_cam_details()))
