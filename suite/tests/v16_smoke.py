"""Focused regressions for the v16 ports. External AI/AWS calls are simulated."""
import importlib
import io
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.base_document import get_controller


def run(app):
    assert app in frappe.get_installed_apps()
    modules = frappe.get_all('Module Def', filters={'app_name': app}, pluck='name')
    doctypes = frappe.get_all('DocType', filters={'module': ['in', modules]}, pluck='name')
    for doctype in doctypes:
        frappe.get_meta(doctype)
        get_controller(doctype)
    globals()['check_' + app]()
    print({'app': app, 'controllers': len(doctypes), 'regressions': 'passed'})


def run_unittests(module):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(module)))
    assert result.testsRun > 0 and result.wasSuccessful(), module


def check_suite():
    from suite.writer.api import docs
    from suite.suite_core.boot import before_install
    payload = b'---\ntitle: Demo\n---\n# Hello\n\n**World**\n\n[[Example]]'
    result = {}
    with patch.object(docs, 'FileManager') as manager:
        manager.return_value.get_file.return_value = io.BytesIO(payload)
        docs.get_markdown_file(frappe._dict(name='test'), result)
    assert '<h1>Hello</h1>' in result['file_content'], result
    assert '<strong>World</strong>' in result['file_content']
    assert result['properties']['title'] == ['Demo']
    assert 'get_wiki_link?title=Example' in result['file_content']
    with patch('frappe.get_installed_apps', return_value=['frappe', 'drive']):
        try: before_install()
        except frappe.ValidationError: pass
        else: raise AssertionError('Suite must reject standalone Drive on the same site')
    from frappe.modules.utils import get_module_app
    assert get_module_app('Suite Drive') == 'suite'
    assert get_module_app('Suite Meet') == 'suite'
