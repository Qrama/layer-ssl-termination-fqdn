#!/usr/bin/env python3
# Copyright (C) 2017  Qrama, developed by Tengu-team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import yaml
from charmhelpers.core.templating import render
from charmhelpers.core.hookenv import status_set, config, application_name
from charms.reactive import when, when_not, set_flag, clear_flag, when_any
from charms.reactive.relations import endpoint_from_flag


config = config()


########################################################################
# Install
########################################################################

@when('endpoint.ssl-termination.available')
@when_not('endpoint.kubernetes-deployer.available')
def missing_http_relation():
    clear_flag('client.cert-created')
    status_set('blocked', 'Waiting for kubernetes-deployer relation')


@when('endpoint.kubernetes-deployer.available')
@when_not('endpoint.ssl-termination.available')
def missing_ssl_termination_relation():
    status_set('blocked', 'Waiting for ssl-termination-proxy relation')


@when_any('config.changed.fqdns',
          'config.changed.credentials',
          'config.changed.nodeport',
          'endpoint.kubernetes-deployer.changed')
def fqdns_changed():
    clear_flag('client.cert-requested')
    clear_flag('client.cert-created')


########################################################################
# Configure certificateS
########################################################################

@when('endpoint.kubernetes-deployer.available',
      'endpoint.ssl-termination.available')
@when_not('client.cert-requested', 'client.k8s-requested')
def send_k8s_request():
    if not config.get('fqdns'):
        status_set('blocked', 'Waiting for fqdns config')
        return
    if not config.get('nodeport'):
        status_set('blocked', 'Waiting for nodeport config')
        return
    endpoint = endpoint_from_flag('endpoint.kubernetes-deployer.available')
    context = {'name': application_name(), 'fqdns': config.get('fqdns'), 'nodeport': config.get('nodeport')}
    resource = render('resource.yaml', None, context)
    endpoint.send_create_request([yaml.load(resource)])
    set_flag('client.k8s-requested')


@when('endpoint.kubernetes-deployer.new-status',
      'endpoint.ssl-termination.available',
      'client.k8s-requested')
@when_not('client.cert-requested')
def create_cert_request():
    endpoint = endpoint_from_flag('endpoint.kubernetes-deployer.new-status')
    ssl_termination = endpoint_from_flag('endpoint.ssl-termination.available')
    workers = endpoint.get_worker_ips()
    if not workers:
        return

    upstreams = []
    for worker in workers:
        host = [{'hostname': worker,
                'private-address': worker,
                'port': config.get('nodeport')}]
        upstreams.extend(host)

    ssl_termination.send_cert_info({
        'fqdn': config.get('fqdns').rstrip().split(),
        'contact-email': config.get('contact-email', ''),
        'credentials': config.get('credentials', ''),
        'upstreams': upstreams,
    })
    status_set('waiting', 'Waiting for proxy to register certificate')
    set_flag('client.cert-requested')


@when('endpoint.kubernetes-deployer.available',
      'endpoint.ssl-termination.update',
      'client.cert-requested')
@when_not('client.cert-created')
def check_cert_created():
    ssl_termination = endpoint_from_flag('endpoint.ssl-termination.update')
    status = ssl_termination.get_status()

    # Only one fqdn will be returned for shared certs.
    # If any fqdn match, the cert has been created.
    match_fqdn = config.get('fqdns').rstrip().split()
    for unit_status in status:
        for fqdn in unit_status['status']:
            if fqdn in match_fqdn:
                status_set('active', 'Ready')
                set_flag('client.cert-created')
                clear_flag('endpoint.ssl-termination.update')


########################################################################
# Unconfigure certificate
########################################################################

@when('endpoint.ssl-termination.available',
      'client.cert-requested')
@when_not('endpoint.kubernetes-deployer.available')
def website_removed():
    endpoint = endpoint_from_flag('endpoint.ssl-termination.available')
    endpoint.send_cert_info({})
    clear_flag('client.cert-requested')
