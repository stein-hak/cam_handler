Cam_handler. A library and cli utility to record video from CCTV cameras with onvif support and upload it to FTP server. Supports video distrubution using mpeg-ts over http or mjpeg over http (can be played with vlc player) and motion detection using motioncells gstreamer plugin.

Dependecies : See dockerfile for ubuntu. Requires addition redis server for interpricess communitations.

Cli usage:

usage: cam_handler.py [-h] [-r REDIS_HOST] [-f FTP_HOST] [--ftp_user FTP_USER]
                      [--ftp_passwd FTP_PASSWD] [-n NAME] [-c CAM_HOST]
                      [-u CAM_USER] [-p CAM_PASSWD]

Usage: cam_handler.py -r 192.168.1.1 -f 127.0.0.1 -n test_cam -c 192.168.1.100
-u admin -p admin

optional arguments:
  -h, --help            show this help message and exit
  -r REDIS_HOST, --redis_host REDIS_HOST
                        Hostname for redis
  -f FTP_HOST, --ftp_host FTP_HOST
                        Hostname for ftp archive
  --ftp_user FTP_USER   Username for ftp archive
  --ftp_passwd FTP_PASSWD
                        Password for ftp archive
  -n NAME, --name NAME  Name of the instance
  -c CAM_HOST, --cam_host CAM_HOST
                        Hostname for onvif camera
  -u CAM_USER, --cam_user CAM_USER
                        Onvif user for camera
  -p CAM_PASSWD, --cam_passwd CAM_PASSWD
                        Onvif password for camera


By default listens on 8888 tcp port for http connections, increments port by 1 if not available. To watch remote streams use http://ip_address:8888/live?res=n link where n is a number of sub stream from ip camera from 0 to the last one available.
