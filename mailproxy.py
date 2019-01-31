import asyncio
import configparser
import logging
import os
import smtplib
import sys
import re
import publicsuffix
from publicsuffix import PublicSuffixList
from time import sleep

from aiosmtpd.controller import Controller


__version__ = '1.0.2'
regex=re.compile('X-PHP-Script: ((?:[a-z]+\.)+[a-z]+)', re.I)

class MailProxyHandler:
    def __init__(self, host, port=0, auth=None, use_ssl=False, starttls=False, prefix="noreply"):
        self._host = host
        self._port = port
        auth = auth or {}
        self._auth_user = auth.get('user')
        self._auth_password = auth.get('password')
        self._use_ssl = use_ssl
        self._starttls = starttls
        self.psl_file = publicsuffix.fetch()
        self.psl = PublicSuffixList(self.psl_file)
        self.prefix = prefix

    async def handle_DATA(self, server, session, envelope):
        global regex
        fromsender=regex.search(envelope.original_content.decode('utf-8')).group(1)
        domain=self.psl.get_public_suffix(fromsender)
        envelope.mail_from=self.prefix+"@"+domain
        try:
            refused = self._deliver(envelope)
        except smtplib.SMTPRecipientsRefused as e:
            logging.info('Got SMTPRecipientsRefused: %s', refused)
            return "553 Recipients refused {}".format(' '.join(refused.keys()))
        except smtplib.SMTPResponseException as e:
            return "{} {}".format(e.smtp_code, e.smtp_error)
        else:
            if refused:
                logging.info('Recipients refused: %s', refused)
            return '250 OK'

    # adapted from https://github.com/aio-libs/aiosmtpd/blob/master/aiosmtpd/handlers.py
    def _deliver(self, envelope):
        refused = {}
        try:
            if self._use_ssl:
                s = smtplib.SMTP_SSL()
            else:
                s = smtplib.SMTP()
            s.connect(self._host, self._port)
            if self._starttls:
                s.starttls()
                s.ehlo()
            if self._auth_user and self._auth_password:
                s.login(self._auth_user, self._auth_password)
            try:
                refused = s.sendmail(
                    envelope.mail_from,
                    envelope.rcpt_tos,
                    envelope.original_content
                )
            finally:
                s.quit()
        except (OSError, smtplib.SMTPException) as e:
            logging.exception('got %s', e.__class__)
            # All recipients were refused. If the exception had an associated
            # error code, use it.  Otherwise, fake it with a SMTP 554 status code. 
            errcode = getattr(e, 'smtp_code', 554)
            errmsg = getattr(e, 'smtp_error', e.__class__)
            raise smtplib.SMTPResponseException(errcode, errmsg.decode())


if __name__ == '__main__':
    if len(sys.argv) == 2:
        config_path = sys.argv[1]
    else:
        config_path = os.path.join(
            sys.path[0],
            'config.ini'
        )
    if not os.path.exists(config_path):
        raise Exception("Config file not found: {}".format(config_path))

    config = configparser.ConfigParser()
    config.read(config_path)
    
    use_auth = config.getboolean('remote', 'smtp_auth', fallback=False)
    if use_auth:
        auth = {
            'user': config.get('remote', 'smtp_auth_user'),
            'password': config.get('remote', 'smtp_auth_password')
        }
    else:
        auth = None
    
    controller = Controller(
        MailProxyHandler(
            host=config.get('remote', 'host'),
            port=config.getint('remote', 'port', fallback=25),
            auth=auth,
            use_ssl=config.getboolean('remote', 'use_ssl',fallback=False),
            starttls=config.getboolean('remote', 'starttls',fallback=False),
            prefix=config.get('global', 'prefix'),
        ),
        hostname=config.get('local', 'host', fallback='127.0.0.1'),
        port=config.getint('local', 'port', fallback=25)
    )
    controller.start()
    while controller.loop.is_running():
        sleep(0.2)
