#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import cherrypy
import jinja2
import logging
import os
import random
import string
import subprocess

from . import pwdtools
from . import setupenv
from . import wgcfg


class WebApp():
    PREFIX = '/redirectlocal/wireguard'  # set to '' to disable prefix
    ALLOWED_IPS = ['127.0.0.1']  # currently, only allow local access made by flask server redirection
    def __init__(self, cfg):
        """Instance initialization"""
        self.cfg = cfg
        self.jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')))
        self.wg = wgcfg.WGCfg(self.cfg.wg_configfile, self.cfg.libdir, self.on_change_func)

    @cherrypy.expose
    def index(self, action=None, id=None, description=None):
        if (action == 'delete') and id:
            peer, peerdata = self.wg.get_peer_byid(id)
            self.wg.delete_peer(peer)
        peers = self.wg.get_peers()
        tmpl = self.jinja_env.get_template('index.html')
        return tmpl.render(sessiondata=cherrypy.session, peers=peers, prefix=self.PREFIX)

    @cherrypy.expose
    def config(self, action=None, id=None, description=None):
        peer = None
        config = None
        peerdata = None
        if (action == 'save') and id:
            peer, peerdata = self.wg.get_peer_byid(id)
            peerdata = self.wg.update_peer(peer, description)
        if (action == 'save') and not id:
            peer = self.wg.create_peer(description)
            peerdata = self.wg.get_peer(peer)
        if not peerdata:
            peer, peerdata = self.wg.get_peer_byid(id)
        if peer:
            config, _ = self.wg.get_peerconfig(peer)
        tmpl = self.jinja_env.get_template('config.html')
        return tmpl.render(sessiondata=cherrypy.session, peerdata=peerdata, config=config, prefix=self.PREFIX)

    @cherrypy.expose
    def edit(self, action='edit', id=None, description=None):
        if id: # existing client
            peer, peerdata = self.wg.get_peer_byid(id)
            if description:
                peerdata = self.wg.update_peer(peer, description)
        else:
            if not description:
                description = 'My new client'
            if action == 'new': # default values for new client
                peerdata = { 'Description': description, 'Id': '' }
            else: # save changes
                raise ValueError()
        tmpl = self.jinja_env.get_template('edit.html')
        return tmpl.render(sessiondata=cherrypy.session, peerdata=peerdata, prefix=self.PREFIX)

    @cherrypy.expose
    def download(self, id):
        """Provide the WireGuard config for the client with the given identifier for download"""
        peer, peerdata = self.wg.get_peer_byid(id)
        config, peerdata = self.wg.get_peerconfig(peer)
        cherrypy.response.headers['Content-Disposition'] = f'attachment; filename=wg_{id}.conf'
        cherrypy.response.headers['Content-Type'] = 'text/plain' # 'application/x-download' 'application/octet-stream'
        return config.encode('utf-8')

    def check_username_and_password(self, username, password):
        """Check whether provided username and password are valid when authenticating"""
        if (username in self.cfg.users) and (pwdtools.verify_password(self.cfg.users[username], password)):
            cherrypy.log('Login of user: ' + username, context='WEBAPP', severity=logging.INFO, traceback=False)
            return
        cherrypy.log('Login failed for user: ' + username, context='WEBAPP', severity=logging.WARNING, traceback=False)
        return 'invalid username/password'

    def login_screen(self, from_page='..', username='', error_msg='', **kwargs):
        """Shows a login form"""
        print(f'{cherrypy.request.remote.ip=}')
        if cherrypy.request.remote.ip not in self.ALLOWED_IPS:
            raise cherrypy.HTTPError(403, 'Access denied')
        tmpl = self.jinja_env.get_template('login.html')
        return tmpl.render(from_page=from_page, username=username, error_msg=error_msg, prefix=self.PREFIX).encode('utf-8')

    @cherrypy.expose
    def logout(self):
        username = cherrypy.session['username']
        cherrypy.session.clear()
        cherrypy.response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        cherrypy.response.headers['Pragma'] = 'no-cache'
        cherrypy.response.headers['Expires'] = '0'
        raise cherrypy.HTTPRedirect(self.PREFIX + '/', 302)
        return '"{0}" has been logged out'.format(username)

    def on_change_func(self):
        """React on config changes"""
        on_change_command = self.cfg.on_change_command
        if (on_change_command is not None) and (len(on_change_command) > 0):
            returncode = subprocess.call(on_change_command, shell=True)
            if returncode != 0:
                cherrypy.log('Error calling on_change_command', context='WEBAPP', severity=logging.ERROR, traceback=False)


def run_webapp(cfg):
    """Runs the CherryPy web application with the provided configuration data"""
    script_path = os.path.dirname(os.path.abspath(__file__))
    app = WebApp(cfg)
    # Use SSL if certificate files exist
    ssl = os.path.exists(cfg.sslcertfile) and os.path.exists(cfg.sslkeyfile)
    if ssl:
        # Use ssl/tls if certificate files are present
        cherrypy.server.ssl_module = 'builtin'
        cherrypy.server.ssl_certificate = cfg.sslcertfile
        cherrypy.server.ssl_private_key = cfg.sslkeyfile
    # Define socket parameters
    cherrypy.config.update({'server.socket_host': cfg.socket_host,
                            'server.socket_port': cfg.socket_port,
                            'request.show_tracebacks': False,
                           })
    # Select environment
    cherrypy.config.update({'staging':
                             {
                               'environment' : 'production'
                             }
                           })
    # Configure the web application
    app_conf = {
      'global': {
         'environment' : 'production'
       },
       '/': {
            'tools.sessions.on': True,
            'tools.sessions.secure': ssl,
            'tools.sessions.httponly': True,
            'tools.staticdir.root': os.path.join(script_path, 'webroot'),
            'tools.session_auth.on': True,
            'tools.session_auth.login_screen': app.login_screen,
            'tools.session_auth.check_username_and_password': app.check_username_and_password,
            },
        '/configs': {
            'tools.staticdir.on': True,
            'tools.staticdir.root': None,
            'tools.staticdir.dir': cfg.libdir
        },
        '/static': {
            'tools.session_auth.on': False,
            'tools.staticdir.on': True,
            'tools.staticdir.dir': 'static'
        },
        '/favicon.ico':
        {
            'tools.session_auth.on': False,
            'tools.staticfile.on': True,
            'tools.staticfile.filename': os.path.join(script_path, 'webroot', 'static', 'favicon.ico')
        }
    }
    # Start CherryPy
    cherrypy.tree.mount(app, config=app_conf)
    cherrypy.tree.mount(app, WebApp.PREFIX, config=app_conf)  # mount on prefix so that redirecting target with prefix can be normally accessed
    if setupenv.is_root():
        # Drop privileges
        uid, gid = setupenv.get_uid_gid(cfg.user, cfg.user)
        cherrypy.process.plugins.DropPrivileges(cherrypy.engine, umask=0o022, uid=uid, gid=gid).subscribe()
    cherrypy.engine.start()
    cherrypy.engine.signals.subscribe()
    cherrypy.engine.block()


if __name__ == '__main__':
    pass
