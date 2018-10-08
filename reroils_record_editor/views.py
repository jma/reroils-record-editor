# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2017 RERO.
#
# Invenio is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# Invenio is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Invenio; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, RERO does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""reroils record editor."""


import uuid
from functools import partial
from json import dumps, loads

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, \
    render_template, request, url_for
from flask_babelex import gettext as _
from flask_login import current_user
from flask_menu import current_menu
from flask_principal import PermissionDenied
from invenio_db import db
from invenio_indexer.api import RecordIndexer
from invenio_pidstore import current_pidstore
from invenio_pidstore.errors import PIDDoesNotExistError
from invenio_records.api import Record
from invenio_records_rest.utils import obj_or_import_string
from pkg_resources import resource_string

from .babel_extractors import translate
from .permissions import can_edit, record_edit_permission
from .utils import clean_dict_keys, delete_record, get_schema, \
    get_schema_url, remove_pid, resolve, save_record

from flask_admin import BaseView, expose
from invenio_admin.permissions import admin_permission_factory as default_admin_permission_factory

class ResourceView(BaseView):

    def is_accessible(self):
        app_config = current_app.config
        cfg = app_config.get('REROILS_RECORD_EDITOR_OPTIONS')
        cfg = cfg.get(self.endpoint)
        if not cfg.get('api'):
            return False
        return (can_edit() or default_admin_permission_factory(self).can())

    @expose('/')
    def index_view(self):
        """
            List view
        """
        app_config = current_app.config
        cfg = app_config.get('REROILS_RECORD_EDITOR_OPTIONS')
        cfg = cfg.get(self.endpoint)
        if not cfg:
            abort(404)
        api = cfg.get('api', app_config.get('SEARCH_UI_SEARCH_API'))
        search_template = cfg.get(
            'results_template',
            app_config.get('SEARCH_UI_JSTEMPLATE_RESULTS')
        )
        search_index = app_config.get('RECORDS_REST_ENDPOINTS', {}) \
            .get(self.endpoint, {}) \
            .get('search_index', app_config.get('SEARCH_UI_SEARCH_INDEX'))
        return render_template(
            'rero_ils/search.html', search_api=api,
            search_results_template=search_template,
            search_index=search_index,
            record_type=self.endpoint
        )

    @expose('/new', methods=('GET', 'POST'))
    def create_view(self):
        """
            Create model view
        """
        app_config = current_app.config
        cfg = app_config.get('REROILS_RECORD_EDITOR_OPTIONS')
        cfg = cfg.get(self.endpoint)
        default_template = app_config[
            'REROILS_RECORD_EDITOR_EDITOR_TEMPLATE'
        ]
        template = cfg.get('editor_template', default_template)
        schema = cfg.get('schema')
        if not cfg or not schema:
            abort(404)

        form_options = cfg.get('form_options')
        schema_url = get_schema_url(schema)

        if form_options:
            options_in_bytes = resource_string(*form_options)
            form_options = loads(options_in_bytes.decode('utf8'))

            for key_to_remove in cfg.get('form_options_create_exclude', []):
                remove_pid(form_options, key_to_remove)
            keys = current_app.config['REROILS_RECORD_EDITOR_TRANSLATE_JSON_KEYS']
            form_options = translate(form_options, keys=keys)
        return render_template(
            template,
            form=form_options or ['*'],
            model={'$schema': schema_url},
            schema=get_schema(schema),
            api_save_url='/editor/save/%s' % self.endpoint,
            record_type=self.endpoint
        )

    @expose('/edit/<pid>', methods=('GET', 'POST'))
    def edit_view(self, pid):
        """
            Edit model view
        """
        app_config = current_app.config
        cfg = app_config.get('REROILS_RECORD_EDITOR_OPTIONS')
        cfg = cfg.get(self.endpoint)
        record_type = self.endpoint
        default_template = current_app.config[
            'REROILS_RECORD_EDITOR_EDITOR_TEMPLATE'
        ]
        template = cfg.get('editor_template', default_template)
        schema = cfg.get('schema')
        if not cfg or not schema:
            abort(404)

        form_options = cfg.get('form_options')
        schema_url = get_schema_url(schema)

        if form_options:
            options_in_bytes = resource_string(*form_options)
            form_options = loads(options_in_bytes.decode('utf8'))

            keys = current_app.config['REROILS_RECORD_EDITOR_TRANSLATE_JSON_KEYS']
            form_options = translate(form_options, keys=keys)

        try:
            pid, rec = resolve(record_type, pid)
        except PIDDoesNotExistError:
            flash(_('The record %s does not exists.' % pid), 'danger')
            abort(404)

        return render_template(
            template,
            form=form_options or ['*'],
            model=rec,
            schema=get_schema(schema),
            api_save_url='/editor/save/%s' % record_type,
            record_type=record_type
        )

    @expose('/delete/<pid>', methods=('POST', 'GET'))
    def delete_view(self, pid):
        """
            Delete model view. Only POST method is allowed.
        """
        app_config = current_app.config
        cfg = app_config.get('REROILS_RECORD_EDITOR_OPTIONS')
        cfg = cfg.get(self.endpoint)
        record_type = self.endpoint
        record_indexer = cfg.get('indexer_class') or RecordIndexer
        _delete_record = obj_or_import_string(cfg.get('delete_record')) \
            or delete_record
        try:
            _next, pid = _delete_record(record_type, pid, record_indexer)
        except PIDDoesNotExistError:
            flash(_('The record %s does not exists.' % pid.pid_value), 'danger')
            abort(404)
        except Exception as e:
            raise(e)
            flash(_('An error occured on the server.'), 'danger')
            abort(500)

        flash(_('The record %s has been deleted.' % pid.pid_value), 'success')

        return redirect(_next)

    @expose('/ajax/update/', methods=('POST',))
    def ajax_update(self):
        """
            Edits a single column of a record in list view.
        """
        pass




@record_edit_permission.require()
def delete(record_type, pid, endpoints):
    """Remove a record.

    TODO: remove items also
    """
    parent_pid = request.args.get('parent_pid')
    cfg = endpoints.get(record_type)
    record_indexer = cfg.get('indexer_class') or RecordIndexer
    _delete_record = obj_or_import_string(cfg.get('delete_record')) \
        or delete_record
    try:
        _next, pid = _delete_record(record_type, pid, record_indexer,
                                    parent_pid)
    except PIDDoesNotExistError:
        flash(_('The record %s does not exists.' % pid.pid_value), 'danger')
        abort(404)
    except Exception as e:
        raise(e)
        flash(_('An error occured on the server.'), 'danger')
        abort(500)

    flash(_('The record %s has been deleted.' % pid.pid_value), 'success')

    return redirect(_next)


@record_edit_permission.require()
def save(record_type, endpoints):
    """Save record in the db and reindex it."""
    parent_pid = request.args.get('parent_pid')
    config = current_app.config['RECORDS_REST_ENDPOINTS']
    config = config.get(record_type, {})
    cfg = endpoints.get(record_type)
    record_class = cfg.get('record_class') or Record
    record_indexer = config.get('indexer_class') or RecordIndexer
    pid_minter = config.get('pid_minter')
    minter = current_pidstore.minters[pid_minter]
    pid_fetcher = config.get('pid_fetcher')
    fetcher = current_pidstore.fetchers[pid_fetcher]
    _save_record = obj_or_import_string(cfg.get('save_record', save_record))
    try:
        _next, pid = _save_record(request.get_json(), record_type, fetcher,
                                  minter, record_indexer, record_class,
                                  parent_pid)
        message = {
            'pid': pid.pid_value,
            'next': _next
        }

        flash(
            _('The record has been saved (%s, pid: %s).'
              % (_(record_type), pid.pid_value)), 'success')
        return jsonify(message)
    except PIDDoesNotExistError:
        msg = _('Cannot retrieve the record (%s).' % record_type)
        response = {
            'content': msg
        }
        return jsonify(response), 404

    except Exception as e:
        raise(e)
        msg = _('An error occured on the server.')
        response = {
            'content': msg
        }
        return jsonify(response), 500


def jsondumps(data):
    """Override the default tojson filter to avoid escape simple quote."""
    return dumps(data, indent=4)


def permission_denied_page(error):
    """Show a personalized error message."""
    if not current_user.is_authenticated:
        return redirect(url_for(
                    current_app.config['ADMIN_LOGIN_ENDPOINT'],
                    next=request.url))
    return render_template(current_app.config['THEME_403_TEMPLATE']), 403


# def init_menu(endpoints):
#     """Initialize menu before first request."""
#     return
#     item = current_menu.submenu('main.manage')
#     item.register(
#         endpoint=None,
#         text=_('Manage'),
#         visible_when=can_edit,
#         order=0
#     )
#     for record_type in endpoints.keys():
#         if endpoints.get(record_type, {}).get('api'):
#             subitem = current_menu.submenu('main.manage.%s' % record_type)
#             icon = '<i class="fa fa-pencil-square-o fa-fw"></i> '
#             subitem.register(
#                 endpoint='reroils_record_editor.search_%s' % record_type,
#                 text=icon + _(record_type),
#                 visible_when=can_edit,
#             )


def create_blueprint(endpoints, app):
    """Create Invenio-Records-REST blueprint.

    :params endpoints: Dictionary representing the endpoints configuration.
    :returns: Configured blueprint.
    """
    endpoints = endpoints or {}

    blueprint = Blueprint(
        'reroils_record_editor',
        __name__,
        template_folder='templates',
        static_folder='static',
        url_prefix='/editor',
    )
    rec_types = endpoints.keys()
    blueprint.add_app_template_filter(can_edit)
    blueprint.add_app_template_filter(jsondumps)
    blueprint.register_error_handler(PermissionDenied,
                                     permission_denied_page)
    adm = app.extensions['invenio-admin']

    for rec_type in rec_types:
        adm.register_view(ResourceView, **dict(
            name=rec_type, category='Resources',
            menu_icon_type='fa', menu_icon_value='fa-pencil-square-o',
            endpoint=rec_type)
        )

        # delete view
        delete_func = partial(delete, record_type=rec_type,
                              endpoints=endpoints)
        delete_func.__module__ = delete.__module__
        delete_func.__name__ = delete.__name__
        blueprint.add_url_rule('/delete/%s/<int:pid>' % rec_type,
                               endpoint='delete_%s' % rec_type,
                               view_func=delete_func)
        # save api
        save_func = partial(save, record_type=rec_type, endpoints=endpoints)
        save_func.__module__ = save.__module__
        save_func.__name__ = save.__name__
        blueprint.add_url_rule('/save/%s' % rec_type,
                               endpoint='save_%s' % rec_type,
                               view_func=save_func, methods=['POST'])
    return blueprint
