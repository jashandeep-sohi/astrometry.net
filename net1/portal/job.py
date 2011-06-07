import datetime
import logging
import os.path
import os
import random
import hashlib
import shutil

from django.db import models
from django.contrib.auth.models import User

import astrometry.net1.settings as settings
from astrometry.net1.upload.models import UploadedFile
from astrometry.net1.portal.log import log
from astrometry.net1.portal.wcs import *
from astrometry.net1.portal.convert import get_objs_in_field
from astrometry.net1.portal.models import UserProfile
from astrometry.util import healpix


# Represents one file on disk
class DiskFile(models.Model):
	# sha-1 hash of the file contents.
	filehash = models.CharField(max_length=40, unique=True, primary_key=True)

	### Everything below here can be derived from the above; they're
	### just cached for performance reasons.

	# type of the file
	# ("jpg", "png", "gif", "fits", etc)
	filetype = models.CharField(max_length=16, null=True)

	# size of the image
	imagew = models.PositiveIntegerField(null=True)
	imageh = models.PositiveIntegerField(null=True)

	def __str__(self):
		return ('<DiskFile %s, type %s, size %ix%i>' %
				(self.filehash, self.filetype or '(none)',
				 self.imagew or 0, self.imageh or 0))

	def content_type(self):
		typemap = {
			'jpg' : 'image/jpeg',
			'gif' : 'image/gif',
			'png' : 'image/png',
			'fits' : 'image/fits',
			'text' : 'text/plain',
			'xyls' : 'image/fits',
			}
		if not self.filetype in typemap:
			return None
		return typemap[self.filetype]

	def show(self):
		return (self.jobs.all().filter(exposejob=True).count() > 0)

	def submitted_by_user(self, u):
		jobs = self.jobs.all()
		for job in jobs:
			if job.user == u:
				return True
		return False
	
	# Moves the given file into the place where it belongs.
	@staticmethod
	def for_file(path):
		log('DiskFile.for_file(' + path + ')')
		hsh = DiskFile.get_hash(path)
		log('Hash=' + hsh)
		existing = DiskFile.objects.all().filter(filehash=hsh)
		log('Existing DiskFiles with this hash: ' + '\n'.join([str(x) for x in existing]))
		if len(existing) == 1:
			if existing[0].file_exists():
				return existing[0]
			existing = []
		assert(len(existing) == 0)
		df = DiskFile(filehash=hsh)
		dest = df.get_path()
		df.create_dir()
		shutil.move(path, dest)
		# FIXME - set filetype, imagew, imageh?
		return df

	def get_path(self):
		hsh = str(self.filehash)
		return os.path.join(settings.FIELD_DIR, hsh[:2], hsh[2:])

	# ensure that this file's directory exists.
	def create_dir(self):
		path = self.get_path()
		d = os.path.dirname(path)
		if os.path.exists(d):
			return
		os.makedirs(d)

	def file_exists(self):
		return os.path.exists(self.get_path())

	def delete_file(self):
		os.unlink(self.get_path())

	def needs_medium_size(self):
		if self.imagew is None or self.imageh is None:
			return None
		(scale, mw, mh) = self.get_medium_scale()
		return (scale != 1.0)

	@staticmethod
	def get_hash(fn):
		h = hashlib.sha1()
		f = open(fn, 'rb')
		while True:
			d = f.read(4096)
			if len(d) == 0:
				break
			h.update(d)
		return h.hexdigest()

	def get_medium_scale(self):
		w = self.imagew
		h = self.imageh
		scale = max(1.0,
					math.pow(2.0, math.ceil(
			math.log(max(w, h) / 800.) / math.log(2.0))))
		displayw = int(round(w / scale))
		displayh = int(round(h / scale))
		return (scale, displayw, displayh)

	def get_small_scale(self):
		w = self.imagew
		h = self.imageh
		scale = float(max(1.0, max(w, h) / 300.))
		displayw = int(round(w / scale))
		displayh = int(round(h / scale))
		return (scale, displayw, displayh)

	def get_thumbnail_scale(self):
		w = self.imagew
		h = self.imageh
		scale = float(max(1.0, max(w, h) / 150.))
		displayw = int(round(w / scale))
		displayh = int(round(h / scale))
		return (scale, displayw, displayh)


class License(models.Model):
	pass

class Tag(models.Model):
	# To which job has this Tag been applied?
	job = models.ForeignKey('Job', related_name='tags')

	# Who added this tag?
	user = models.ForeignKey(User, null=True)

	# Machine tag or human-readable?
	machineTag = models.BooleanField(default=False)

	# The tag.
	text = models.CharField(max_length=4096)

	# When was this tag added?
	addedtime = models.DateTimeField()

	def __str__(self):
		return self.text

	def username(self):
		if self.user:
			return self.user.username
		return 'anonymous'

	def can_remove_tag(self, user):
		return user in [self.user, self.job.get_user()]

	# check if this user has already tagged this image with this text...
	def is_duplicate(self):
		tags = Tag.objects.all().filter(job=self.job,
										user=self.user,
										machineTag=self.machineTag,
										text=self.text)
		return (tags.count() > 0)
		

class Calibration(models.Model):
	# TAN WCS, straight from the quad match
	raw_tan = models.ForeignKey(TanWCS, related_name='calibrations_raw', null=True)
	# TAN WCS, after tweaking
	tweaked_tan = models.ForeignKey(TanWCS, related_name='calibrations_tweaked', null=True)
	# SIP
	sip = models.ForeignKey(SipWCS, null=True)

	# RA,Dec bounding box.
	ramin  = models.FloatField()
	ramax  = models.FloatField()
	decmin = models.FloatField()
	decmax = models.FloatField()

	# in the future...
	#blind_date = models.DateField()
	# or
	#blind_date = models.ForeignKey(BlindDateSolution, null=True)

	# bandpass
	# zeropoint
	# psf

	def save(self):
		if self.ramin is None:
			(self.ramin, self.ramax, self.decmin, self.decmax) = self.raw_tan.radec_bounds()
		super(Calibration,self).save()

class BatchSubmission(models.Model):
	pass

class WebSubmission(models.Model):
	# All sorts of goodies like IP, HTTP headers, etc.
	pass


class Submission(models.Model):
	scaleunits_CHOICES = (
		('arcsecperpix', 'arcseconds per pixel'),
		('arcminwidth' , 'width of the field (in arcminutes)'), 
		('degwidth' , 'width of the field (in degrees)'),
		('focalmm'	   , 'focal length of the lens (for 35mm film equivalent sensor)'),
		)
	scaleunits_default = 'degwidth'

	scaletype_CHOICES = (
		('ul', 'lower and upper bounds'),
		('ev', 'estimate and error bound'),
		)

	parity_CHOICES = (
		(2, 'Try both simultaneously'),
		(0, 'Positive'),
		(1, 'Negative'),
		)

	datasrc_CHOICES = (
		('url', 'URL'),
		('file', 'File'),
		)

	filetype_CHOICES = (
		('image', 'Image (jpeg, png, gif, tiff, raw, or FITS)'),
		('fits', 'FITS table of source locations'),
		('text', 'Text list of source locations'),
		)
	###

	subid = models.CharField(max_length=32, unique=True, primary_key=True)

	# User, or None for anonymous.
	user = models.ForeignKey(User, related_name='submissions', null=True)

	# Only one of these should be set...
	batch = models.ForeignKey(BatchSubmission, null=True)
	web = models.ForeignKey(WebSubmission, null=True)

	description = models.CharField(max_length=1024, null=True)

	# url / file / etc.
	datasrc = models.CharField(max_length=10, choices=datasrc_CHOICES)

	# image / fits / text
	filetype = models.CharField(max_length=10, choices=filetype_CHOICES)

	status = models.CharField(max_length=16)
	failurereason = models.CharField(max_length=256)

	# have all jobs belonging to this submission been added
	# to the database?
	alljobsadded = models.BooleanField(default=False)

	# does this submission have multiple jobs?
	multijob = models.BooleanField(default=False)

	url = models.URLField(blank=True, null=True)

	# The file that was submitted.
	fileorigname = models.CharField(max_length=64, null=True)
	diskfile = models.ForeignKey(DiskFile, null=True)

	uploaded = models.ForeignKey(UploadedFile, null=True, blank=True)

	parity = models.PositiveSmallIntegerField(choices=parity_CHOICES,
											  default=2)

	# for FITS tables, the names of the X and Y columns.
	xcol = models.CharField(max_length=16, blank=True)
	ycol = models.CharField(max_length=16, blank=True)

	# image scale.
	scaleunits = models.CharField(max_length=16, choices=scaleunits_CHOICES,
								  default=scaleunits_default)
	scaletype  = models.CharField(max_length=3, choices=scaletype_CHOICES,
								  default='ul')
	scalelower = models.FloatField(default=0.1, blank=True, null=True)
	scaleupper = models.FloatField(default=180, blank=True, null=True)
	scaleest   = models.FloatField(blank=True, null=True)
	scaleerr   = models.FloatField(blank=True, null=True)

	# tweak.
	tweak = models.BooleanField(default=True)
	tweakorder = models.PositiveSmallIntegerField(default=2)

	submittime = models.DateTimeField(null=True)

	def __init__(self, *args, **kwargs):
		for k,v in kwargs.items():
			if v is None:
				del kwargs[k]
		if not 'subid' in kwargs:
			kwargs['subid'] = Job.generate_jobid()
		if 'user' in kwargs:
			if not kwargs['user'].is_authenticated():
				# set anonymous users to None.
				kwargs['user'] = None
		super(Submission, self).__init__(*args, **kwargs)

	def __str__(self):
		s = '<Submission %s, status %s' % (self.get_id(), self.status)
		if self.user:
			s += ', user %s' % self.user.username
		else:
			s += ', user anonymous'

		if self.datasrc == 'url':
			s += ', url ' + str(self.url)
		elif self.datasrc == 'file':
			s += ', file "%s" (upload id %s)' % (self.uploaded.userfilename, str(self.uploaded))
		s += ', ' + self.filetype
		pstrs = [ 'pos', 'neg', 'both' ]
		s += ', parity ' + pstrs[int(self.parity)]
		if self.scaletype == 'ul':
			s += ', scale [%g, %g] %s' % (self.scalelower, self.scaleupper, self.scaleunits)
		elif self.scaletype == 'ev':
			s += ', scale [%g +- %g %%] %s' % (self.scaleest, self.scaleerr, self.scaleunits)
		if self.tweak:
			s += ', tweak order ' + str(self.tweakorder)
		else:
			s += ', no tweak'
		s += '>'
		return s

	def typestr(self):
		return 'Submission'

	def jobs_queued(self):
		return self.jobs.filter(status='Queued')
	def jobs_running(self):
		return self.jobs.filter(status='Running')
	def jobs_solved(self):
		return self.jobs.filter(status='Solved')
	def jobs_unsolved(self):
		return self.jobs.filter(status='Unsolved')
	def jobs_error(self):
		return self.jobs.filter(status='Error')

	def njobs_queued(self):
		return self.jobs_queued().count()
	def njobs_running(self):
		return self.jobs_running().count()
	def njobs_solved(self):
		return self.jobs_solved().count()
	def njobs_unsolved(self):
		return self.jobs_unsolved().count()
	def njobs_error(self):
		return self.jobs_error().count()

	def check_if_finished(self):
		#alljobs = self.jobs.all()
		#for job in alljobs:
		#	 if not job.is_finished():
		#		 return
		errors = self.jobs.all().filter(status='Error').count()
		if errors > 0:
			self.set_status('Error')
			self.save()
			return
		for job in self.jobs.all():
			if not job.finished_without_error():
				return
		self.set_status('Finished')
		self.save()

	def finished_without_error(self):
		return self.is_finished()

	def is_finished(self):
		return self.status in [ 'Finished' ]

	def set_status(self, stat, reason=None):
		if stat in ['Queued', 'Running', 'Error', 'Finished']:
			self.status = stat
		else:
			raise ValueError('Invalid status "%s"' % stat)
		if stat == 'Error' and reason is not None:
			self.failurereason = reason[:256]
		if stat != 'Error':
			self.failurereason = ''

	def format_status(self):
		s = self.status
		r = self.failurereason
		if r:
			s = s + ': ' + r
		return s

	def get_id(self):
		return self.subid

	def get_job_dir(self):
		return Job.s_get_job_dir(self.get_id())

	def get_relative_job_dir(self):
		return Job.s_get_relative_job_dir(self.get_id())

	def create_job_dir(self):
		Job.create_dir_for_jobid(self.get_id())

	def get_filename(self, fn):
		return Job.get_job_filename(self.get_id(), fn)

	def get_url(self):
		if self.datasrc == 'url':
			return self.url
		return None

	def get_userfilename(self):
		if self.datasrc == 'file':
			return self.uploaded.userfilename
		return None

	def set_submittime_now(self):
		self.submittime = Job.timenow()
	def format_submittime(self):
		return Job.format_time(self.submittime)
	def format_submittime_brief(self):
		return Job.format_time_brief(self.submittime)


class Job(models.Model):

	# eg 'test-200802-12345678'
	jobid = models.CharField(max_length=32, unique=True, primary_key=True)

	submission = models.ForeignKey(Submission, related_name='jobs', null=True)

	description = models.CharField(max_length=1024, null=True)

	# The file that goes with this job
	diskfile = models.ForeignKey(DiskFile, related_name='jobs', null=True)

	# The license associated with the file.
	filelicense = models.ForeignKey(License, null=True)

	# The original filename on the user's machine, or basename of URL, etc.
	# Needed for, eg, tarball submissions.	Purely for informative purposes,
	# to show the user a filename that makes sense.
	fileorigname = models.CharField(max_length=64, null=True)

	calibration = models.ForeignKey(Calibration, null=True)

	# Has the user granted us permission to show this job to everyone?
	exposejob = models.NullBooleanField(null=True, blank=True, default=None)

	status = models.CharField(max_length=16)
	failurereason = models.CharField(max_length=256)

	# How did we find the calibration for this job?
	howsolved = models.CharField(max_length=256)

	# times
	enqueuetime = models.DateTimeField(default='2000-01-01')
	starttime  = models.DateTimeField(default='2000-01-01')
	finishtime = models.DateTimeField(default='2000-01-01')

	# Is there an identical DiskFile that solved?
	duplicate = models.NullBooleanField(blank=True, null=True)
	
	def __init__(self, *args, **kwargs):
		super(Job, self).__init__(*args, **kwargs)
		if self.exposejob is None:
			u = self.get_user()
			if u:
				prefs = UserProfile.for_user(u)
				if prefs:
					self.exposejob = prefs.expose_jobs()
				else:
					log('No preferences found for user %s' % str(u))
			else:
				# Anonymous users have their jobs exposed.
				self.exposejob = True

	def __str__(self):
		s = '<Job %s, ' % self.get_id()
		s += str(self.submission)
		if self.fileorigname:
			s += ', origfile %s' % self.fileorigname
		if self.status:
			s += ', %s' % self.status
		s += ' ' + str(self.diskfile)
		s += '>'
		return s

	def typestr(self):
		return 'Job'

	# (ra,dec) in degrees or None
	def get_radec_center(self):
		if not self.calibration:
			return None
		tan = self.calibration.raw_tan
		if not tan:
			return None
		return tan.get_field_center()

	# returns bounding radius in degrees.
	def get_field_radius(self):
		if not self.calibration:
			return None
		tan = self.calibration.raw_tan
		if not tan:
			return None
		return tan.get_field_bounding_radius()

	def write_wcs_to_file(self, filename):
		if not self.calibration:
			return False
		tan = None
		sip = None
		if self.calibration.sip:
			sip = self.calibration.sip
		elif self.calibration.tweaked_tan:
			tan = self.calibration.tweaked_tan
		elif self.calibration.raw_tan:
			tan = self.calibration.raw_tan
		else:
			return False

		# convert to astrometry.util.sip.Tan
		if tan:
			tan.to_tanwcs().write_to_file(filename)
		if sip:
			sip.to_sipwcs().write_to_file(filename)
		return True

	def get_user_tags(self):
		return self.tags.filter(machineTag=False)

	def get_machine_tags(self):
		return self.tags.filter(machineTag=True)

	def can_be_viewed_by(self, user):
		return self.is_exposed() or (self.get_user() == user)

	def set_is_duplicate(self):
		others = Job.objects.all().filter(diskfile=self.diskfile, status='Solved').order_by('enqueuetime')
		self.duplicate = (others.count() > 0) and (others[0] != self)
 
	def add_machine_tags(self):
		from astrometry.net1.portal import nearby

		# Find the list of objects in the field and add them as
		# machine tags to the Job.
		if self.solved():
			objs = get_objs_in_field(self)
			for obj in objs:
				tag = Tag(job=self,
						  user=self.get_user(),
						  machineTag=True,
						  text=obj,
						  addedtime=Job.timenow())
				tag.save()

			# Add healpix machine tag.
			nearby.add_tags_to_job(self)

	def remove_all_machine_tags(self):
		Tag.objects.all().filter(job=self, machineTag=True,
								 user=self.get_user()).delete()

	def get_tan_wcs(self):
		if not self.calibration:
			return None
		return self.calibration.raw_tan

	def get_description(self):
		if self.description:
			return self.description
		return self.submission.description

	def short_description(self):
		s = self.get_description()
		if len(s) > 20:
			s = s[:20] + '...'
		return s

	def short_userfilename(self):
		s = self.submission.uploaded.userfilename
		if len(s) > 20:
			s = s[:20] + '...'
		return s

	def is_finished(self):
		return self.status in ['Solved', 'Unsolved', 'Error']

	def finished_without_error(self):
		return self.status in ['Solved', 'Unsolved' ]

	def set_error_status(self, reason):
		return self.set_status('Error', reason)

	def set_status(self, stat, reason=None):
		if stat in ['Queued', 'Running', 'Solved', 'Unsolved', 'Error']:
			self.status = stat
		else:
			raise ValueError('Invalid status "%s"' % stat)
		if stat == 'Error' and reason is not None:
			self.failurereason = reason[:256]
		if stat != 'Error':
			self.failurereason = ''
		self.save()
		if self.is_finished():
			self.submission.check_if_finished()
		if stat == 'Running':
			self.submission.set_status('Running')

	def get_id(self):
		return self.jobid

	def get_fileid(self):
		return self.diskfile.filehash

	def set_exposed(self, exposed):
		self.exposejob = exposed and True or False
		log('job.is_exposed() is now: %s' % self.is_exposed())

	def is_exposed(self):
		return self.exposejob

	def can_add_tag(self, user):
		return self.is_exposed() or (self.get_user() == user)

	def solved(self):
		calib = self.calibration
		#log('calib is %s' % str(calib))
		if calib is None:
			return False
		return True

	def is_input_fits(self):
		return self.submission.filetype == 'fits'

	def is_input_text(self):
		return self.submission.filetype == 'text'

	def get_xy_cols(self):
		return (self.submission.xcol, self.submission.ycol)

	def friendly_parity(self):
		pstrs = [ 'Positive', 'Negative', 'Try both' ]
		return pstrs[int(self.get_parity())]

	def friendly_scale(self):
		val = None
		stype = self.get_scaletype()
		if stype == 'ul':
			val = '%.2f to %.2f' % (self.get_scalelower(), self.get_scaleupper())
		elif stype == 'ev':
			val = '%.2f plus or minus %.2f%%' % (self.get_scaleest(), self.get_scaleerr())

		txt = None
		units = self.get_scaleunits()
		if units == 'arcsecperpix':
			txt = val + ' arcseconds per pixel'
		elif units == 'arcminwidth':
			txt = val + ' arcminutes wide'
		elif units == 'degwidth':
			txt = val + ' degrees wide'
		elif units == 'focalmm':
			txt = 'focal length of ' + val + ' mm'
		return txt

	def short_failurereason(self):
		s = self.failurereason
		if len(s) > 25:
			s = s[:25] + '...'
		return s

	def get_scale_bounds(self):
		stype = self.get_scaletype()
		if stype == 'ul':
			return (self.get_scalelower(), self.get_scaleupper())
		elif stype == 'ev':
			est = self.get_scaleest()
			err = self.get_scaleerr()
			return (est * (1.0 - err / 100.0),
					est * (1.0 + err / 100.0))
		else:
			return None

	def get_parity(self):
		return self.submission.parity

	def get_scalelower(self):
		return self.submission.scalelower

	def get_scaleupper(self):
		return self.submission.scaleupper

	def get_scaleest(self):
		return self.submission.scaleest

	def get_scaleerr(self):
		return self.submission.scaleerr

	def get_scaletype(self):
		return self.submission.scaletype

	def get_scaleunits(self):
		return self.submission.scaleunits

	def get_tweak(self):
		return (self.submission.tweak, self.submission.tweakorder)

	def get_job_dir(self):
		return Job.s_get_job_dir(self.get_id())

	def get_relative_job_dir(self):
		return Job.s_get_relative_job_dir(self.get_id())

	def get_username(self):
		if self.get_user() is None:
			return 'anonymous'
		return self.get_user().username

	def get_user(self):
		return self.submission.user

	def get_fieldid(self):
		return self.diskfile.filehash

	def create_job_dir(self):
		Job.create_dir_for_jobid(self.get_id())

	def allowanonymous(self, prefs=None):
		return self.exposejob

	def set_enqueuetime_now(self):
		self.enqueuetime = Job.timenow()
	def set_starttime_now(self):
		self.starttime = Job.timenow()
	def set_finishtime_now(self):
		self.finishtime = Job.timenow()

	def set_enqueuetime(self, t):
		self.enqueuetime = t
	def set_starttime(self, t):
		self.starttime = t

	def format_status(self):
		s = self.status
		r = self.short_failurereason()
		if r:
			s = s + ': ' + r
		return s

	def format_status_full(self):
		s = self.status
		r = self.failurereason
		if r:
			s = s + ': ' + r
		return s

	def format_enqueuetime(self):
		return Job.format_time(self.enqueuetime)
	def format_starttime(self):
		return Job.format_time(self.starttime)
	def format_finishtime(self):
		return Job.format_time(self.finishtime)

	def format_enqueuetime_brief(self):
		return Job.format_time_brief(self.enqueuetime)
	def format_starttime_brief(self):
		return Job.format_time_brief(self.starttime)
	def format_finishtime_brief(self):
		return Job.format_time_brief(self.finishtime)

	def get_orig_file(self):
		return self.fileorigname

	def get_axy_filename(self):
		return self.get_filename('job.axy')

	def get_filename(self, fn):
		return Job.get_job_filename(self.get_id(), fn)

	def get_relative_filename(self, fn):
		return os.path.join(self.get_relative_job_dir(), fn)

	@staticmethod
	def timenow():
		return datetime.datetime.utcnow()

	@staticmethod
	def is_default_time(t):
		return t.year == 2000 and t.month == 1 and t.day == 1

	@staticmethod
	def format_time(t):
		if not t:
			return None
		if Job.is_default_time(t):
			return None
		return t.strftime('%Y-%m-%d %H:%M:%S+Z')
	
	@staticmethod
	def format_time_brief(t):
		if not t:
			return None
		if Job.is_default_time(t):
			return None
		return t.strftime('%Y-%m-%d %H:%M')

	@staticmethod
	def create_dir_for_jobid(jobid):
		d = Job.s_get_job_dir(jobid)
		# HACK - more careful here...
		if os.path.exists(d):
			return
		mode = 0770
		os.makedirs(d, mode)

	@staticmethod
	def s_get_job_dir(jobid):
		return os.path.join(settings.JOB_DIR, Job.s_get_relative_job_dir(jobid))
	
	@staticmethod
	def get_job_filename(jobid, fn):
		return os.path.join(Job.s_get_job_dir(jobid), fn)

	@staticmethod
	def s_get_relative_job_dir(jobid):
		return os.path.join(*jobid.split('-'))

	@staticmethod
	def generate_jobid():
		today = datetime.date.today()
		#log('Choosing job id: site id is', settings.SITE_NAME)
		# HACK - we don't check that it's unique!!
		jobid = '%s-%i%02i-%08i' % (settings.SITE_NAME, today.year,
									today.month, random.randint(0, 99999999))
		#log('Chose jobid', jobid)
		return jobid

	@staticmethod
	def submit_job_or_submission(j):
		os.umask(07)
		j.create_job_dir()

		from astrometry.net1.portal.queue import QueuedJob
		QueuedJob.submit_job_or_submission(j)

		# watcher-based
		if False:
			# enqueue by creating a symlink in the job queue directory.
			jobdir = j.get_job_dir()
			link = settings.JOB_QUEUE_DIR + j.get_id()
			if os.path.exists(link):
				os.unlink(link)
			os.symlink(jobdir, link)