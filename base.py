                                                                                                                                                                                                              #!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
sys.path.append('/opt/lib')
from datetime import datetime, date, timedelta
import sys
from execute import execute
from multiprocessing import Value, Queue, Event, Array
import ctypes
import time
import os
from collections import OrderedDict
import ffprobe
import re
from copy import copy, deepcopy
from saver import load_class, save_class
import urlparse
from ping import fping
import camctl

# Base class describing cam
class job:
	def __init__(self, name, url=None, record=None, storage=None, calendar=None, uuid=None, cmdline='', motion_zones=[], enabled=False, primary_server=None,backup_server=None):
		self.name = name
		self.init_time = int(time.time())
		self.type = 'native' # guest. orhan, backup, test(to do)
		self.uuid = uuid
		self.state = Value('i', 0)  # {0:pre-init,1:recording, 2:running but not recording (out of timezone),-1:ping timeout, -2: stream error}
		self.last_state = Value('i', 0)
		self.state_time_changed = Value('i', self.init_time)
		self.last_event = Value('i', 0)
		self.last_time_update = Value('i', 0)
		self.last_error = Value('i',0)
		self.last_error_time = Value('i',0)
		self.watchdog_reset = Event()
		self.in_queue = Queue()
		self.out_queue = Queue()
		self.conf_queue = Queue()
		self.backup_timeout = 2000
		self.old_record = None
		self.primary_server = primary_server
		self.backup_server = backup_server



		self.alarmed = Event()
		self.events = []
		self.offset = Value('i',0)
		self.watchdog = Value('i',0)
		self.live = None
		self.credentials = None
		self.jpeg = None
		self.enabled = enabled
		self.server_id = None
		self.generalized = False
		self.onvif_support = Value('i',0)
		self.tcp_port = Value('i',0)
		self.http_code = Value('i',0)


		self.cam_streams = None
		self.tcp_ports = Array('i', [0,0,0,0,0,0,0,0])
		self.url = url
		self.record = record
		self.storage = storage
		self.calendar = calendar
		self.motion_zones = motion_zones

		self.fps = Value('i', 0)
		self.gop = Value('i', 0)
		self.bitrate = Value('i', 0)
		self.pid = Value('i',0)

		if not self.url or not self.record:
			self.type = 'orphan'
			self.storage = storage
			self.calendar = calendar
			self.host = None
			self.proto = None
			self.location = None
			self.username = None
			self.password = None

		else:
			self.host, self.proto, self.location, self.username, self.password = self.parse_addr(self.url)

		self.parse_cmdline(cmdline)

		self.ports = []
		self.delay = Value('i',0)


		self.uri = []
		self.snap_uri = None
		self.video_options = []


	# def handle_onvif(self):
	# 	if self.host and (self.proto == 'rtsp' or self.proto == 'onvif'):
	# 		onvif = self.setup_onvif(self.host,self.username,self.password)
	# 		if onvif:
	# 			state, video_uri, snap_uri, video_options = self.onvif_test(onvif)
	# 			if state == 0:
	# 				self.onvif_support.value = 1
	# 				self.uri = video_uri
	# 				self.snap_uri = snap_uri
	# 				self.video_options = video_options




	def parse_addr(self, url):
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

	# def setup_onvif(self, host, user=None, passwd=None):
	# 	onvif = None
	# 	if user and passwd:
	# 		try:
	# 			onvif = camctl.cam_control(host, user=user, password=passwd)
	# 		except:
	# 			pass
	# 	else:
	# 		try:
	# 			onvif = camctl.cam_control(host)
	# 		except:
	# 			pass
	#
	# 	return onvif
	#
	# def onvif_test(self, onv):
	# 	state = 1
	# 	snap_uri = None
	# 	video_uri = []
	# 	video_options = []
	# 	try:
	# 		snap_uri = onv.get_snapshot_uri()
	# 		video_uri = onv.get_video_uri()
	# 		video_options = onv.get_video_options()
	# 	except:
	# 		state = 1
	#
	# 	if video_uri and snap_uri:
	# 		state = 0
	#
	# 	return state, video_uri, snap_uri, video_options


	def parse_cmdline(self,cmdline=''):

		if not cmdline:
			self.cmdline = 'basic'
		else:
			if 'record.py' in cmdline:
				self.cmdline = 'basic'
			if 'vlc' in cmdline:
				self.cmdline = 'basic'

			else:
				self.cmdline = cmdline


		if self.type == 'backup':
			if 'important' in cmdline:
				if not self.old_record:
					self.old_record = deepcopy(self.record)
				self.record.type = 'event'
				self.record.event_seconds_pre = 60
				self.record.event_seconds_post = 60
				self.record.important = True

			else:
				self.record.important = False
				if self.old_record:
					self.record = deepcopy(self.old_record)
				self.old_record = None


		else:
			if 'hybrid' in cmdline:
				if not self.old_record:
					self.old_record = deepcopy(self.record)

				self.record.type = 'hybrid'
				if not self.record.event_seconds_pre:
					self.record.event_seconds_pre=30
				if not self.record.event_seconds_post:
					self.record.event_seconds_post=15

			else:
				if self.old_record:
					self.record = deepcopy(self.old_record)
				self.old_record = None




	def generalize(self):
		'Call to dump a standart object before save to file'
		if not self.generalized:
			self.generalized = True
			self.state = 0
			self.last_state = 0
			self.last_event = None
			self.last_time_changed = 0
			self.state_time_changed = 0
			self.offset = 0
			self.alarmed = None
			self.events= []
			self.streamers = {}
			self.tcp_port = 0
			self.http_code = 0
			self.last_time_update = 0
			self.onvif_support = 0
			self.last_error = 0
			self.last_error_time = 0
			self.delay = int(self.delay.value)
			self.fps = int(self.fps.value)
			self.gop = int(self.gop.value)
			self.bitrate = int(self.bitrate.value)
			self.tcp_ports = None
			self.in_queue = None
			self.out_queue = None
			self.err_queue = None
			self.conf_queue = None
			self.watchdog_reset = None
			self.cam_streams = None
			self.tcp_ports = None
			self.watchdog = None
			self.pid = None
		else:
			pass


	def update_state(self):
		self.last_state = self.state

	def set_state(self, state):
		if state != self.state:
			self.state = state
			self.state_time_changed = datetime.now()


	def get_state(self):
		return (self.state, self.last_state)

	#Call to dump job data in dict type for http api
	def get_storage_data(self):
		ret = OrderedDict()

		ret['enabled'] = self.enabled
		ret['url'] = self.url
		ret['uuid'] = self.uuid
		ret['cmdline'] = self.cmdline
		ret['record_type'] = self.record.type
		ret['record_time'] = self.record.time
		ret['record_week'] = self.record.week
		ret['record_hours'] = self.record.hours
		ret['record_event_seconds_pre'] = self.record.event_seconds_pre
		ret['record_event_seconds_post'] = self.record.event_seconds_post
		ret['storage_size'] = self.storage.size
		ret['storage_add_hours'] = self.storage.size_add_hours
		ret['storage_backup'] = self.storage.backup
		ret['storage_backup_size'] = self.storage.backup_size
		ret['calendar_name'] = self.calendar.name
		ret['calendar_days'] = self.calendar.days


		return ret
	# Do we need to record job now or after specific datetime.timedelta

	def is_active(self,advance=0):
		#print advance
		try:
			advance = int(advance)
		except:
			advance = 0

		active = self.is_active_time(advance=advance)

		if not self.alarmed.is_set():
			if active:
				if self.type != 'backup':
					return True
				else:
					if self.record.important:
						return True
					else:
						return False
			else:
				return False
		else:
			return True

	def is_active_time(self,advance=0):
		#print advance
		try:
			advance = int(advance)
		except:
			advance = 0
		#print advance
		if advance:
				now = datetime.now() + timedelta(minutes=advance)
		else:
			now = datetime.now()
		#print now
		#print self.name
		day = now.strftime('%m%d')

		weekday = int(now.strftime('%w')) - 1
		if weekday < 0:  # change us weekdays to russian
			weekday = 6
		hour = int(now.strftime('%H'))

		if self.calendar.get_working(day) and int(self.record.week[weekday]) and int(self.record.hours[hour]):
			return True
		else:
			return False

	# Was job active at specific day. Excepts string in %y%m%d
	def was_active_day(self, day):
		now = datetime.strptime(day, '%y%m%d')
		day_m = now.strftime('%m%d')
		weekday = now.weekday()

		if self.calendar.get_working(day_m) and int(self.record.week[weekday]):
			return True
		else:
			return False

	# Was job active at specific hour. Excepts string in %y%m%d%H

	def was_active_hour(self, day_hour):
		now = datetime.strptime(day_hour, '%y%m%d%H')
		day_m = now.strftime('%m%d')
		weekday = now.date().weekday()
		hour = int(now.strftime('%H'))

		if self.calendar.get_working(day_m) and int(self.record.week[weekday]) and int(self.record.hours[hour]):
			return True
		else:
			return False

	# Get int of days stored for job. if not day1- assume today
	def get_days_past(self, day, day1=None):
		past = datetime.strptime(day, '%y%m%d').date()
		counter = 0
		if day1:
			present = datetime.strptime(day1, '%y%m%d').date()
			delta = present - past
		else:
			today = (datetime.now() + timedelta(minutes=10)).date()
			delta = today - past

		for i in range(delta.days):
			cur_day = past + timedelta(days=i)
			if cur_day == date.today():
				pass
			else:
				if self.was_active_day(cur_day.strftime('%y%m%d')):
					counter += 1

		return counter

	# Get int of hours stored for job from day + hour till today.
	def get_hours_past(self, day, hour):
		past = datetime.strptime(day + hour, '%y%m%d%H')
		present = datetime.now() + timedelta(minutes=10)
		counter = 0
		delta = present - past
		for i in range(int(round((delta.total_seconds() // 3600)+ 0.6))):
			cur = past + timedelta(hours=i)
			day_hour = cur.strftime('%y%m%d%H')
			if day_hour == present.strftime('%y%m%d%H'):
				pass
			else:
				if self.was_active_hour(day_hour):
					counter += 1

		return counter

	# Return hash for job for updates
	def __hash__(self):
		try:
			h = hash((self.name, self.url, hash(self.record), hash(self.storage),
			             hash(self.calendar), self.uuid, self.cmdline))
		except:
			h = hash((self.name, self.url, hash(self.record), hash(self.storage),
			          hash(self.calendar), self.uuid))

		return h


# Class for data storage configuration and cleanup. See videoserver mysql base
class storage:
	def __init__(self, size=2, size_add_hours=0, backup=2, backup_size=2):
		self.size = size
		self.size_add_hours = size_add_hours
		self.backup = backup
		self.backup_size = backup_size

	def __hash__(self):
		return hash((self.size, self.size_add_hours, self.backup, self.backup_size))

# Record format for job. Base time, type and active days/ hours See videoserver mysql base
class record_format:
	def __init__(self, time=120, intersect=3, week='1111111', hours='111111111111111111111111', event_seconds_pre=0, event_seconds_post=0,important=False):
		self.time = time
		self.intersect = intersect
		self.week = week
		self.hours = hours
		self.event_seconds_pre = event_seconds_pre
		self.event_seconds_post = event_seconds_post
		self.important = False



		if not event_seconds_pre:
			if not event_seconds_post:
				self.type = 'persistent'

		else:
			if event_seconds_pre or event_seconds_post:
				self.type = 'event'

	def __hash__(self):
		return hash((self.time, self.type, self.week, self.hours, self.event_seconds_pre, self.event_seconds_post))

# Holyday calendar for job See videoserver mysql base

class calendar:
	def __init__(self, name='2003',days=OrderedDict()):
		self.name = str(name)
		self.days = days

	def add(self, day, value):
		self.days[day] = value

	def get_working(self, day):
		if day in self.days.keys():
			if self.days[day] == 1:
				return True
			else:
				return False
		else:
			return True

	def __hash__(self):
		return hash((self.name, str(sorted(self.days))))



# Base class for mailing events
class mail:
	def __init__(self, from_addr, to_addr, subject, content, encoding='utf-8', priority='3', image=None):
		self.time = datetime.now()
		self.from_addr = from_addr
		self.to_addr = to_addr
		self.subject = subject
		self.content = content
		self.encoding = encoding
		self.priority = priority
		self.image = image

# Base class for describing mail servers.  See videoserver mysql base
class smtpserver:
	def __init__(self, host, port, encoding,uuid=None,enabled=False):
		self.host = host
		self.port = port
		self.uuid=uuid
		self.enabled = int(enabled)
		self.encoding = encoding

	def __hash__(self):
		return hash((self.host, self.port, self.encoding))

# Get path of running app
def get_app_path():
	if getattr(sys, 'frozen', False):
		apath = \
			os.path.dirname(sys.executable)
	elif __file__:
		apath = os.path.dirname(__file__)
	return apath

# Print line skipping buffer. Better use logging instead
def sprint(line):
	print(line)
	sys.stdout.flush()

# Is pid running in system
def is_running(pid):
	out, err, rc = execute(['ps', '-p', str(pid), '-o', 'comm='])
	if rc == 0 and len(out) > 0:
		return True
	else:
		return False

# Class for day statistics of job
class day_file:
	def __init__(self, type):
		self.type = type
		self.duration = 0
		self.bitrate = 0
		self.gapes = []
		self.hours = {}
		self.cleared = False

	def add_hour(self, name, bitrate, duration, gapes=[]):
		self.hours[name] = (bitrate, duration)
		self.gapes.extend(gapes)
		self.bitrate = self.measure_bitrate()
		self.duration = self.measure_duration()

	def measure_bitrate(self):
		total = 0
		average = 0
		for h in self.hours.keys():
			total += self.hours[h][0]
		average = total / len(self.hours)

		return average

	def measure_duration(self):
		total = 0
		for h in self.hours.keys():
			total += self.hours[h][1]

		return total


# Class for hour statistics of job. Fixes gapes <= max delta if detected
class hour_file:
	def __init__(self, dir, type, max_delta=120):

		self.type = type
		self.duration = 0
		self.bitrate = 0
		self.files = {}
		self.snapshots = []
		self.gapes = []
		self.cleared = False
		self.finished = False
		self.max_delta = max_delta
		self.head = True
		self.directory = dir

		self.hour = os.path.basename(self.directory)
		self.day = os.path.basename(os.path.dirname(self.directory))

		self.time = datetime.strptime(self.day + self.hour, '%y%m%d%H')
		self.exported_files = []


	def add_file(self, name, start_time, duration, bitrate):
		if not name in self.files.keys():
			self.files[name] = (start_time, duration, bitrate)
			self.duration = self.measure_duration()
			self.bitrate = self.measure_bitrate()
			self.detect_gapes()



	def update(self):
		if self.time.strftime('%y%m%d%H') != datetime.now().strftime('%y%m%d%H'):
			self.finished = True

		if os.path.exists(self.directory):
			files = os.listdir(self.directory)
			for f in sorted(files):
				name = os.path.join(self.directory,f)
				if not name in self.files.keys():
					updated=True
					try:
						if f.split('.') > 1 and f.split('.')[1] == 'mp4':
							time = datetime.strptime(self.day + self.hour + str(f.split('.')[0].split('-')[0]), '%y%m%d%H%M%S')
							duration = int(f.split('.')[0].split('-')[1].split('#')[0].split('+')[0])

							try:
								bitrate = (os.path.getsize(os.path.join(dir, f)) / duration) * 1024

							except:
								bitrate = 0

							self.add_file(name, time, duration, bitrate)
					except:
						pass
			if updated:
				self.detect_gapes()



	def detect_gapes(self):
		if self.type == 'persistent' or self.type == 'hybrid':
			flist = sorted(self.files.keys())

			for f in flist:
				index = flist.index(f)
				file = self.files[f]

				try:
					next_file = self.files[flist[index+1]]


					if file[0] + timedelta(seconds=file[1]) >= next_file[0]:
						pass

					else:
						delta = (next_file[0] - (file[0] + timedelta(seconds=file[1]))).seconds
						if self.max_delta:
							if delta <= self.max_delta:
								self.fix_duration(f, delta)

							else:
								self.gapes.append((file[0]+ timedelta(seconds=file[1]),next_file[0]))
						else:
							self.gapes.append((file[0] + timedelta(seconds=file[1]), next_file[0]))

				except:

					if self.finished:
						next_hour = self.time + timedelta(hours=1)
						if file[0] + timedelta(seconds=file[1]) >= next_hour:
							pass

						else:
							delta = (next_hour - (file[0] + timedelta(seconds=file[1]))).seconds
							if self.max_delta:
								if delta <= self.max_delta and delta > file[1]:
									self.fix_duration(f, delta)
								else:
									self.gapes.append((file[0] + timedelta(seconds=file[1]), next_hour))
							else:
								self.gapes.append((file[0] + timedelta(seconds=file[1]), next_hour))

					else:
						pass


	def get_marked_files(self):
		self.update()
		ret = []
		for i in self.files.keys():
			if '+' in i:
				ret.append(i)
		return ret

	def get_motion_files(self):
		self.update()
		ret = []
		for i in self.files.keys():
			if '#' in i:
				ret.append(i)
		return ret

	def fix_duration(self, f, delta):

		if delta == 0:
			delta = 1

		name = f

		file = self.files[f]

		new_duration = file[1] + delta

		new_name = os.path.join(os.path.dirname(name), file[0].strftime('%M%S') + '-' + '%05d' % int(new_duration) + '.mp4')
		os.rename(name, new_name)
		self.files[new_name] = (file[0], new_duration, file[2])
		del self.files[name]

	def measure_bitrate(self):
		total = 0
		average = 0

		for f in self.files.values():
			total += f[2]
		average = total / len(self.files)

		return average

	def measure_duration(self):
		total_duration = 0
		for f in self.files.values():
			total_duration += f[1]

		return total_duration


class alarm:
	def __init__(self, type='start', time=datetime.now(), timeout=720, min_time_active=0):
		self.type = type
		self.time = time
		self.min_time_active = min_time_active
		self.timeout = timeout

# Class for oper server params. Used for backup server role
class oper_server:
	def __init__(self,enabled, host ,connect_timeout=2000, http_port=8080,id = None,user='root',passwd='mysql',uuid=None,role='master'):
		self.enabled = enabled
		self.state = 0
		self.last_state = self.state
		self.host = host
		self.connect_timeout = connect_timeout
		self.http_port = http_port
		self.id = id
		self.jobs = {}
		self.fail_count = 0
		self.last_fail_time = None
		self.last_event = 0
		self.user = user
		self.passwd = passwd
		self.last_update_time = None
		self.uuid = uuid
		self.role = role
		self.disabled_jobs = []
		self.enabled_jobs = []

	def get_active_jobs(self):
		ret = []
		for job in self.jobs.values():
			if job.enabled:
				if job in self.disabled_jobs:
					job.enabled = 0

				try:
					if self.uuid:
						if job.primary_server:
							if job.primary_server == self.uuid:
								ret.append(job)
						else:
							if self.role == 'master':
								ret.append(job)
				except:
					print('Failed to get server from job %s' % job.name)
					ret.append(job)
			else:
				if job in self.enabled_jobs:
					job.enabled = 1
				try:
					if self.uuid:
						if job.primary_server:
							if job.primary_server == self.uuid:
								ret.append(job)
						else:
							if self.role == 'master':
								ret.append(job)
				except:
					print('Failed to get server from job %s' % job.name)
					ret.append(job)

		if ret:
			ret =  sorted(ret, key=lambda kv: kv.name)

		return ret

	def enable_job(self,name):
		jobs = self.get_active_jobs()
		for job in jobs:
			if job.name == name:
				if job in self.disabled_jobs:
					#print(job)
					self.disabled_jobs.remove(job)
				if not job.enabled:
					#print(job)
					self.enabled_jobs.append(job)
				break

	def disable_job(self,name):
		jobs = self.get_active_jobs()
		for job in jobs:
			print(job)
			if job.name == name:
				if job not in  self.disabled_jobs:
					self.disabled_jobs.append(job)
				if job in self.enabled_jobs:
					self.enabled_jobs.remove(job)
				break

	def get_alive_jobs(self):
		alive_jobs = []
		dead_jobs = []
		hosts = OrderedDict()
		active = self.get_active_jobs()
		for job in active:
			hosts[job] = job.host

		alive_hosts, dead_hosts = fping(hosts.values())

		for host in alive_hosts:
			for job in hosts.keys():
				if hosts.get(job):
					if hosts.get(job) == host:
						alive_jobs.append(job)


		for host in dead_hosts:
			for job in hosts.keys():
				if hosts.get(job):
					if hosts.get(job) == host:
						dead_jobs.append(job)

		return alive_jobs, dead_jobs





	def update_jobs(self, jobs):
		added = []
		reconfigured = []
		deleted = []
		changed = False
		old_jobs = copy(self.jobs)

		for job in jobs:
			old_job = old_jobs.get(job.uuid)


			if not old_job:
				changed = True
				self.jobs[job.uuid] = job
				added.append(job)

			else:
				if not job.enabled and old_job.enabled:
					current_job = self.jobs.get(job.uuid)
					changed = True
					reconfigured.append(job)
					current_job.enabled = 0
					print('Disabling %s' % old_job.name)

				elif job.enabled and not old_job.enabled:
					current_job = self.jobs.get(job.uuid)
					jobs_changed = True
					reconfigured.append(job)
					current_job.enabled = 1
					print('Enabling %s' % old_job.name)

				elif job.enabled and old_job.enabled:

					if hash(job) != hash(old_job):
						current_job = self.jobs.get(job.uuid)


						if hash(job.record) != hash(old_job.record):
							#if not job.old_record:
							changed = True
							reconfigured.append(job)
							print('Reconfiguring job name %s' % old_job.name)
							print ('Reconfiguring record')
							current_job.record = job.record
							current_job.in_queue.put(job.record)

						if hash(job.storage) != hash(old_job.storage):
							changed = True
							reconfigured.append(job)
							print('Reconfiguring job name %s' % old_job.name)
							print ('Reconfiguring storage')
							current_job.storage = job.storage
							current_job.in_queue.put(job.storage)

						if hash(job.calendar) != hash(old_job.calendar):
							changed = True
							reconfigured.append(job)
							print('Reconfiguring job name %s' % old_job.name)
							print('Reconfiguring calendar')
							current_job.calendar = job.calendar
							current_job.in_queue.put(job.calendar)

						if job.url != old_job.url:
							changed = True
							reconfigured.append(job)
							print('Reconfiguring job name %s' % old_job.name)
							print('Reconfiguring URL')
							current_job.url = job.url
							current_job.in_queue.put(['url',job.url])

						if job.cmdline != old_job.cmdline:
							changed = True
							reconfigured.append(job)
							print('Reconfiguring job name %s' % old_job.name)
							print('Reconfiguring cmdline')
							current_job.cmdline = job.cmdline
							current_job.in_queue.put(['cmdline',job.cmdline])

				del old_jobs[old_job.uuid]
		# Old and unused jobs

		for old_job in old_jobs.values():
			changed= True
			deleted.append(old_job)
			print('Deleting unwanted job %s' % old_job.name)
			del self.jobs[old_job.uuid]

		return changed, added, reconfigured, deleted









# Class for motion detection and analytics. See motiondetector mysql base
class detector_zone:
	def __init__(self,uuid, name, zone_name='Весь кадр', cell_w=16, cell_h=16, cells='', detector_frequency=1000, alarm_frequency=2, hours = '111111111111111111111111', min_alarm_pixel_count=20, max_alarm_cell_count_percent=50, sensitivity=20, min_alarm_cell_count=10 , enabled=1,min_zone_alarm_count=0):
		self.uuid = uuid
		self.enabled = enabled
		self.name = name
		self.zone_name = zone_name
		self.cell_w = cell_w
		self.cell_h = cell_h
		self.cells= []

		try:
			for c in  cells.split(';'):
				self.cells.append((int(c.split(',')[0]), int(c.split(',')[1])))
		except:
			pass

		self.detector_frequency = (float(detector_frequency) / 1000.0)
		self.alarm_frequency = alarm_frequency
		self.hours = hours
		self.min_alarm_pixel_count = min_alarm_pixel_count
		self.max_alarm_cell_count_percent = max_alarm_cell_count_percent
		self.sensitivity = sensitivity
		self.min_alarm_cell_count = min_alarm_cell_count

		self.scale = self.cell_w * self.cell_h

		self.thresh_min = int((self.scale / 100) * self.min_alarm_pixel_count)
		self.active_zones = {}
		self.last_event_time = None
		self.min_zone_alarm_count = min_zone_alarm_count



	def is_active(self):
		ret = False
		now = datetime.now()
		hour = int(now.strftime('%H'))
		if self.enabled and int(self.hours[hour]):
			ret = True
		else:
			ret = False

		return ret


	def was_active_hour(self, day_hour):
		ret = False
		now = datetime.strptime(day_hour, '%y%m%d%H')
		hour = int(now.strftime('%H'))

		if self.enabled and int(self.hours[hour]):
			ret = True
		else:
			ret = False

		return ret

# Class for maping all video files in archive. used for cleanup. updated with storage manager and inotify in future.

class total_map():

	def __init__(self, archive_path):
		self.archive_path = archive_path
		self.job_maps = OrderedDict()
		self.bitrate = None


	def get_job(self,name):
		if name in self.job_maps.keys():
			return self.job_maps[name].job
		else:
			return None



	def update_maps_from_disk(self):
		for cam in os.listdir(self.archive_path):
			job_file = os.path.join(self.archive_path,cam,'.cam')
			job = load_class(job_file)
			if job:
				if cam in self.job_maps.keys():
					map = self.job_maps[cam]
					if hash(job) != hash(map.job):
						map.job = job

				else:
					new_map = job_map(self.archive_path,job)
					self.add_map(new_map)

		for empty in self.get_empty_cams():
			self.del_map(empty.job.name)




	def add_map(self,map):
		if map:
			self.job_maps[map.job.name] = map



	def del_map(self,map):
		if map:
			if map.job.name in self.job_maps.keys():
				del self.job_maps[map.job.name]


	def add_day(self,path,cam,day):
		if self.archive_path == path:
			if self.job_maps.get(cam):
				map =self.job_maps[cam]
				map.add_day(day)
			else:
				self.update_maps_from_disk()

				if self.job_maps.get(cam):
					map = self.job_maps[cam]
					map.add_day(day)


	def add_hour(self,path,cam,day,hour):
		if self.archive_path == path:
			if self.job_maps.get(cam):
				map =self.job_maps[cam]
				map.add_hour(day,hour)
			else:
				self.update_maps_from_disk()
				if self.job_maps.get(cam):
					map = self.job_maps[cam]
					map.add_hour(day, hour)



	def add_file(self,path,cam,day,hour,file):
		if self.archive_path == path:
			if self.job_maps.get(cam):
				map =self.job_maps[cam]
				map.add_file(day,hour,file)
			else:
				self.update_maps_from_disk()
				if self.job_maps.get(cam):
					map = self.job_maps[cam]
					map.add_hour(day, hour)


	def get_days(self,cam):
		ret = []
		if self.job_maps.get(cam):
			map = self.job_maps[cam]
			days = map.get_days()
			for day in sorted(days):
				value = (self.archive_path,cam,day)
				ret.append(value)

		return ret



	def get_hours(self,cam,day):
		ret = []
		if self.job_maps.get(cam):
			map = self.job_maps[cam]
			if day in map.get_days():
				hours = map.get_hours(day)
				for hour in sorted(hours):
					value = (self.archive_path,cam,day,hour)
					ret.append(value)

		return ret

	def get_files(self,cam,day,hour):
		ret = []
		if self.job_maps.get(cam):
			map = self.job_maps[cam]
			if day in map.get_days():
				if hour in map.get_hours(day):
					files = map.get_files(day,hour)
					for file in sorted(files):
						value = (self.archive_path, cam, day, hour,file)
						ret.append(value)
		return ret


	def get_empty_cams(self):
		empty = []
		for map in self.job_maps.values():
			if map.is_empty():
				empty.append(map.job.name)

		return empty


	def get_empty_days(self):
		empty = {}
		for map in self.job_maps.values():
			empty_days = map.get_empty_days()
			if empty_days:
				empty[map.job.name] = []
			for day in sorted(empty_days):
				value = (self.archive_path,map.job.name,day)
				empty[map.job.name].append(value)

		return empty

	def get_empty_hours(self):
		empty = {}
		for map in self.job_maps.values():
			empty_hours = map.get_empty_hours()
			if empty_hours:
				empty[map.job.name] = []
			for day_hour in empty_hours:
				value = (self.archive_path, map.job.name, day_hour[0],day_hour[1])
				empty[map.job.name].append(value)

		return empty


	def get_marked_files(self):
		marked = {}
		for map in self.job_maps.values():
			job = map.job
			marked_files = map.get_marked_files()
			if marked_files:
				marked[job] = marked_files

		return marked

	def get_motion_files(self):
		marked = {}
		for map in self.job_maps.values():
			job = map.job
			marked_files = map.get_motion_files()
			if marked_files:
				marked[job] = marked_files

		return marked


	def get_stale_files(self):
		marked = {}
		for map in self.job_maps.values():
			job = map.job
			marked_files = map.get_stale_files()
			if marked_files:
				marked[job] = marked_files

		return marked

	def get_file_position(self,cam,time):
		value = None
		if self.job_maps.get(cam):
			map = self.job_maps[cam]
			value = map.get_file_position(time)

		return value


	def get_export_period(self,cam,time1,time2=None):
		value = []
		if self.job_maps.get(cam):
			map = self.job_maps[cam]
			value = map.get_export_period(time1,time2)

		return value


	def map_expired_cams(self,arch_type):
		ret = {}
		for map in self.job_maps.values():
			job = map.job

			if map.is_empty():
				ret[job] = (self.archive_path,job.name)

			else:
				days = sorted(map.get_days())

				if not days:
					ret[job] = (self.archive_path, job.name)

				else:
					oldest_day = days[0]
					hours = sorted(map.get_hours(oldest_day))

					if not hours:
						ret[job] = (self.archive_path, job.name, oldest_day)
					else:
						oldest_hour = hours[0]

						hours_in_day = 0

						for i in job.record.hours:
							if int(i) == 1:
								hours_in_day += 1

						if arch_type == 'oper':
							desired_days = job.storage.size
							desired_hours = job.storage.size * hours_in_day + job.storage.size_add_hours

						elif arch_type == 'backup':
							desired_days = job.storage.size + job.storage.backup_size
							desired_hours = desired_days * hours_in_day + job.storage.size_add_hours


						days_old = job.get_days_past(oldest_day)
						hours_old = job.get_hours_past(oldest_day, oldest_hour)


						if days_old < desired_days:
							pass

						elif days_old > desired_days:

							if len(days) > 1:

								ret[job] = (self.archive_path,job.name,oldest_day)

							else:

								ret[job] = (self.archive_path,job.name)

						elif days_old == desired_days:
							if hours_old > desired_hours:
								if len(hours) > 1:
									ret[job] = (self.archive_path,job.name,oldest_day,oldest_hour)
								else:
									ret[job] = (self.archive_path,job.name,oldest_day)

		return ret


	def map_oldest_cams(self):
		ret = {}
		last_hours = {}
		pre_ret = {}
		for map in self.job_maps.values():
			job = map.job

			if job.type == 'orphan':
				days = sorted(map.get_days())
				oldest_day = days[0]
				hours = sorted(map.get_hours(oldest_day))

				if not hours or len(hours) == 1:
					value = (self.archive_path, job.name, oldest_day)
					ret[job] = value

				else:
					oldest_hour = hours[0]
					value = (self.archive_path,job.name,oldest_day,oldest_hour)
					ret[job] = value

			else:

				days = sorted(map.get_days())
				oldest_day = days[0]
				hours = sorted(map.get_hours(oldest_day))

				if not hours:
					ret[job] = (self.archive_path, job.name, oldest_day)

				else:
					oldest_hour = hours[0]
					oldest_date = oldest_day + oldest_hour


					if len(hours) == 1:
						value = (self.archive_path, job.name, oldest_day)
						last_hours[job] = value

					else:

						value = (self.archive_path,job.name,oldest_day,oldest_hour)
						last_hours[job] = value


					if not pre_ret.get(oldest_date):
						pre_ret[oldest_date] = [job,]
					else:
						pre_ret[oldest_date].append(job)



		oldest_oldest_date = min(pre_ret.keys())

		oldest_cams = pre_ret[oldest_oldest_date]

		for cam in oldest_cams:
			ret[cam] = last_hours[cam]

		return ret













class job_map():

	def __init__(self,archive_path,job):
		self.archive_path = archive_path
		self.job = job
		self.days=OrderedDict()
		self.bitrate = None

	def add_day(self,day):
		if day not in self.days.keys():
			self.days[day] = OrderedDict()

	def add_hour(self,day,hour):
		self.add_day(day)
		if hour not in self.days[day].keys():
			self.days[day][hour] = []

	def add_file(self,day,hour,file):
		self.add_day(day)
		self.add_hour(day,hour)
		if file not in self.days[day][hour]:
			self.days[day][hour].append(file)


	def del_day(self,day):
		if day in self.days.keys():
			del self.days[day]

	def del_hour(self,day,hour):
		if day in self.days.keys():
			if hour in self.days[day].keys():
				del self.days[day][hour]

	def del_file(self,day,hour,file):
		if day in self.days.keys():
			if hour in self.days[day].keys():
				if file in self.days[day][hour]:
					del self.days[day][hour][file]


	def get_days(self):
		return sorted(self.days.keys())

	def get_hours(self,day):
		if day in self.days.keys():
			return sorted(self.days[day].keys())
		else:
			return []

	def get_files(self,day,hour):
		if day in self.days.keys():
			if hour in self.days[day].keys():
				return sorted(self.days[day][hour])
			else:
				return []
		else:
			return []

	def get_files_full(self,day,hour):
		ret = []
		if day in self.days.keys():
			if hour in self.days[day].keys():
				for file in sorted(self.days[day][hour]):
					value = (self.archive_path,self.job.name,day,hour,file)
					ret.append(value)

		return ret



	def is_empty(self):
		if not self.get_days():
			return True
		else:
			return False

	def get_empty_days(self):
		empty = []
		for day in self.get_days():
			if not self.get_hours(day):
				empty.append(day)
		return empty

	def get_empty_hours(self):
		empty = []
		for day in self.get_days():
			for hour in self.get_hours(day):
				if not self.get_files(day,hour):
					empty.append((day,hour))

		return empty

	def get_marked_files(self):
		marked = []
		for day in self.get_days():
			for hour in self.get_hours(day):
				for file in self.get_files(day,hour):
					if '+' in file:
						value = (self.archive_path,self.job.name, day,hour,file)
						marked.append(value)

		return marked

	def get_motion_files(self):
		marked = []
		for day in self.get_days():
			for hour in self.get_hours(day):
				for file in self.get_files(day,hour):
					if '#' in file:
						value = (self.archive_path,self.job.name,day,hour,file)
						marked.append(value)

		return marked


	def get_stale_files(self):
		marked = []
		for day in self.get_days():
			for hour in self.get_hours(day):
				for file in self.get_files(day,hour):
					if '#' not in file:
						value = (self.archive_path,self.job.name,day,hour,file)
						marked.append(value)

		return marked

	def get_times_file(self,day,hour,file):
		begin_t = datetime.strptime(day + hour + str(file.split('.')[0].split('-')[0]),
									'%y%m%d%H%M%S')
		duration = int(file.split('.')[0].split('-')[1].split('#')[0].split('+')[0])
		end_t = begin_t + timedelta(seconds=duration)

		return (begin_t,end_t)


	def get_gapes(self):
		gapes = []
		for day in self.get_days():
			last_time = None
			for hour in self.get_hours(day):
				for file in self.get_files(day,hour):
					begin_t, end_t = self.get_times_file(day,hour,file)
					if last_time:
						if begin_t <= last_time:
							pass
						else:
							value = (last_time,begin_t, (begin_t - last_time).seconds)
							gapes.append(value)

					last_time = end_t

		return gapes



	def get_file_position(self,time):
		ret = None
		day_begin = time.strftime('%y%m%d')
		hour_begin = time.strftime('%H')
		if self.days.get(day_begin):
			if self.days.get(day_begin).get(hour_begin):
				for file in self.days.get(day_begin).get(hour_begin):
					begin_t, end_t = self.get_times_file(day_begin, hour_begin, file)

					if end_t < time:
						pass

					elif begin_t < time and end_t > time:
						offset = (time - begin_t).seconds
						full_name = os.path.join(self.archive_path, self.job.name, day_begin, hour_begin, file)
						ret = (full_name, offset, 0)
						break

		return ret




	def get_export_period(self,time1,time2=None):
		export_list = []
		start_day_index = None
		start_hour_index = None
		day_begin = time1.strftime('%y%m%d')
		hour_begin = time1.strftime('%H')

		if self.days.get(day_begin):
			start_day_index = self.days.keys().index(day_begin)

			if self.days.get(day_begin).get(hour_begin):
				start_hour_index = self.days.get(day_begin).keys().index(hour_begin)


				for day in self.days.keys()[start_day_index:]:

					if day != day_begin:
						start_hour_index = 0

					for hour in self.days.get(day).keys()[start_hour_index:]:

						for file in self.get_files(day,hour):
							begin_t, end_t = self.get_times_file(day,hour,file)
							if end_t < time1:
								pass

							elif begin_t < time1 and end_t > time1:
								offset = (time1 - begin_t).seconds
								full_name = (self.archive_path, self.job.name, day, hour, file)

								if time2:
									if end_t > time2:

										offset_back = (end_t - time2).seconds
										f_duration = (end_t - begin_t).seconds

										duration = f_duration - offset - offset_back

										entry = (full_name,offset,duration)
										export_list.append(entry)
										break


								entry = (full_name,offset,0)
								export_list.append(entry)


							elif begin_t > time1 and end_t > time1:
								full_name = (self.archive_path, self.job.name, day, hour, file)

								if time2:
									if end_t > time2:
										f_duration = (end_t - begin_t).seconds
										offset = (end_t - time2).seconds
										if offset > f_duration:
											break
										else:
											duration = f_duration - offset
											entry = (full_name,0,duration)
											export_list.append(entry)
											break

								entry = (full_name, 0,0)
								export_list.append(entry)






		return export_list


class recover_event():
	def __init__(self,cam,day,new,modif,deleted,renamed):
		self.cam = cam
		self.day = day
		self.time = datetime.now()
		self.new=new
		self.modif = modif
		self.deleted = deleted
		self.renamed = renamed




























