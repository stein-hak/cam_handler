#!/usr/bin/env python

from execute import execute


def fping(hosts, retries=1, timeout=1000):
	alive = []
	dead = []
	cmd = ['fping', '-q', '-r', str(retries), '-t', str(timeout), '-a']
	for host in hosts:
		cmd.append(str(host))

	out, err, rc = execute(cmd)

	for line in out.splitlines():
		try:
			hosts.remove(line)
			alive.append(line)
		except:
			# print hosts
			# print line
			pass
			# multiple entries?

	dead = hosts


	return alive, dead


if __name__ == '__main__':
	print(fping(['192.168.20.106', '192.168.20.107']))
