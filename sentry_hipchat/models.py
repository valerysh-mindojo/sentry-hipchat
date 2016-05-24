"""
sentry_hipchat.models
~~~~~~~~~~~~~~~~~~~~~

:copyright: (c) 2011 by Linovia, see AUTHORS for more details.
:license: BSD, see LICENSE for more details.
"""
from datetime import datetime

from django import forms
from django.conf import settings
from django.utils.html import escape

from sentry.plugins.bases.notify import NotifyPlugin
from sentry.cache.redis import RedisCache

import sentry_hipchat

import urllib
import urllib2
import json
import logging


COLORS = {
    'ALERT': 'red',
    'ERROR': 'red',
    'WARNING': 'yellow',
    'INFO': 'green',
    'DEBUG': 'purple',
}

DEFAULT_ENDPOINT = "https://api.hipchat.com/v1/rooms/message"
DEFAULT_DELAY = 3600.0  # seconds


class HipchatOptionsForm(forms.Form):
    token = forms.CharField(help_text="Your hipchat API v1 token.")
    room = forms.CharField(help_text="Room name or ID.")
    notify = forms.BooleanField(help_text='Notify message in chat window.', required=False)
    include_project_name = forms.BooleanField(help_text='Include project name in message.', required=False)
    endpoint = forms.CharField(help_text="Custom API endpoint to send notifications to.", required=False,
                               widget=forms.TextInput(attrs={'placeholder': DEFAULT_ENDPOINT}))
    delay = forms.FloatField(help_text="Delay between showing the same alert (seconds)", required=False,
                             min_value=60.0, initial=3600.0)


class HipchatMessage(NotifyPlugin):
    author = 'Xavier Ordoquy'
    author_url = 'https://github.com/linovia/sentry-hipchat'
    version = sentry_hipchat.VERSION
    description = "Event notification to Hipchat."
    resource_links = [
        ('Bug Tracker', 'https://github.com/linovia/sentry-hipchat/issues'),
        ('Source', 'https://github.com/linovia/sentry-hipchat'),
    ]
    slug = 'hipchat'
    title = 'Hipchat'
    conf_title = title
    conf_key = 'hipchat'
    project_conf_form = HipchatOptionsForm
    timeout = getattr(settings, 'SENTRY_HIPCHAT_TIMEOUT', 3)

    def __init__(self):
        super(HipchatMessage, self).__init__()
        self.delay_cache = RedisCache()

    def is_configured(self, project):
        return all((self.get_option(k, project) for k in ('room', 'token')))

    def on_alert(self, alert, **kwargs):
        project = alert.project
        token = self.get_option('token', project)
        room = self.get_option('room', project)
        if not (token and room):
            return
        notify = self.get_option('notify', project) or False
        include_project_name = self.get_option('include_project_name', project) or False
        endpoint = self.get_option('endpoint', project) or DEFAULT_ENDPOINT

        self.send_payload(
            endpoint=endpoint,
            token=token,
            room=room,
            message='[ALERT]%(project_name)s %(message)s %(link)s' % {
                'project_name': (' <strong>%s</strong>' % escape(project.name)) if include_project_name else '',
                'message': escape(alert.message),
                'link': alert.get_absolute_url(),
            },
            notify=notify,
            color=COLORS['ALERT'],
        )

    def notify_users(self, group, event, fail_silently=False):
        project = event.project
        token = self.get_option('token', project)
        room = self.get_option('room', project)
        if not (token and room):
            return
        delay_cache_key = "delay_{}".format(group.id)
        if self.delay_cache.get(delay_cache_key):
            return  # the cache hasn't expired, so not sending again
        notify = self.get_option('notify', project) or False
        include_project_name = self.get_option('include_project_name', project) or False
        level = group.get_level_display().upper()
        link = group.get_absolute_url()
        endpoint = self.get_option('endpoint', project) or DEFAULT_ENDPOINT
        delay = self.get_option('delay', project) or DEFAULT_DELAY

        self.send_payload(
            endpoint=endpoint,
            token=token,
            room=room,
            message='[%(level)s]%(project_name)s %(message)s [<a href="%(link)s">view</a>]' % {
                'level': escape(level),
                'project_name': ((' <strong>%s</strong>' % escape(project.name)).encode('utf-8')
                                if include_project_name else ''),
                'message': escape(event.error()),
                'link': escape(link),
            },
            notify=notify,
            color=COLORS.get(level, 'purple'),
        )

        # put a marker no not send the same message within `delay` period
        self.delay_cache.set(delay_cache_key, True, delay)

    def send_payload(self, endpoint, token, room, message, notify, color='red'):
        values = {
            'auth_token': token,
            'room_id': room.encode('utf-8'),
            'from': 'Sentry',
            'message': message.encode('utf-8'),
            'notify': int(notify),
            'color': color,
        }
        data = urllib.urlencode(values)
        request = urllib2.Request(endpoint, data)
        response = urllib2.urlopen(request, timeout=self.timeout)
        raw_response_data = response.read()
        response_data = json.loads(raw_response_data)
        if 'status' not in response_data:
            logger = logging.getLogger('sentry.plugins.hipchat')
            logger.error('Unexpected response')
        if response_data['status'] != 'sent':
            logger = logging.getLogger('sentry.plugins.hipchat')
            logger.error('Event was not sent to hipchat')
