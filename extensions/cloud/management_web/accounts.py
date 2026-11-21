"""Edit the desktop account plan through the cloud runtime."""

import hashlib
import hmac
import json
import math
import re
import secrets
from copy import deepcopy
from datetime import datetime
from threading import Lock
from time import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.task.account.MultiAccountConfigStore import (
    MULTI_ACCOUNT_TASKS, MultiAccountConfigStore, collect_available_tasks, task_reference,
)
from src.task.account.RuntimeTaskConfig import effective_config
from .app import _config_fields


_PRIVATE_FIELD = re.compile(r'password|passwd|secret|token|cookie|credential|account|api.?key|密码|令牌|凭据|账号', re.I)
CLOUD_ACCOUNT_SWITCH_UNAVAILABLE = (
    'The cloud adapter uses one signed-in browser profile and does not support switching accounts.'
)
_PHASES = {'执行中': 'Running', '全部完成': 'Completed', '已停止': 'Stopped',
           '执行失败': 'Failed', '没有启用的账号': 'No enabled accounts'}


def is_multi_account_task(task):
    return (type(task).__module__, type(task).__name__) in MULTI_ACCOUNT_TASKS


def public_task_info(task, info):
    """Publish counts and known phases. Nested desktop tasks may share runner.info."""
    if not is_multi_account_task(task) and not any(key in info for key in ('当前账号', '全部配置账号', 'All Accounts')):
        return info
    public = {}
    for key, label in (('全部配置账号', 'All Accounts'), ('All Accounts', 'All Accounts'),
                       ('已完成', 'Completed'), ('Completed', 'Completed')):
        value = info.get(key)
        if isinstance(value, (list, tuple, set, frozenset)):
            public[label] = len(value)
    phase = info.get('状态')
    if isinstance(phase, str) and phase in _PHASES:
        public['Status'] = _PHASES[phase]
    return public


def _task_fields(task):
    # Only declared settings travel to the browser. Internal and retired keys
    # stay in the stored plan; a regular save must preserve them.
    values = effective_config(task, {})
    public = {key: values[key] for key in task.default_config if not _PRIVATE_FIELD.search(key)}
    types = {
        key: {k: v for k, v in spec.items() if k != 'sub_configs'}
        for key, spec in task.config_type.items() if isinstance(spec, dict)
    }
    fields = _config_fields(public, task.config_description, types, task.default_config)
    for field in fields:
        rules = task.config_type.get(field['key'], {})
        if isinstance(rules, dict) and isinstance(rules.get('sub_configs'), dict):
            field['children'] = [
                {'choice': choice, 'keys': [keys] if isinstance(keys, str) else list(keys)}
                for choice, keys in rules['sub_configs'].items()
            ]
    return fields


def _valid_value(field, value):
    kind = field['kind']
    options = field['options']
    if kind == 'boolean':
        return type(value) is bool
    if kind in {'integer', 'number'}:
        if type(value) not in ((int,) if kind == 'integer' else (int, float)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        return ((field['minimum'] is None or value >= field['minimum'])
                and (field['maximum'] is None or value <= field['maximum']))
    if kind in {'multi_selection', 'list'}:
        if not isinstance(value, list) or not all(type(item) in (str, int, bool, float) for item in value):
            return False
        if options is not None:
            return (all(item in options for item in value)
                    and (field['allow_duplication'] or len(set(value)) == len(value)))
        return True
    if kind == 'select':
        return value in (options or [])
    return isinstance(value, str)


class AccountEditor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = Lock()
        self.version_key = secrets.token_bytes(32)

    @property
    def store(self):
        return MultiAccountConfigStore(self.runtime.executor.config_folder)

    def version(self, accounts):
        # A revision also scopes the opaque row indexes to this exact plan.
        document = json.dumps(accounts, sort_keys=True, ensure_ascii=False).encode()
        return hmac.new(self.version_key, document, hashlib.sha256).hexdigest()

    def catalog(self):
        return {type(task).__name__: task for task in collect_available_tasks(self.runtime.executor)
                if not is_multi_account_task(task)}

    def runners(self):
        return [task for task in self.runtime.executor.onetime_tasks
                if is_multi_account_task(task)]

    def state(self, accounts):
        runners = self.runners()
        active = next((task for task in runners if task.enabled), None)
        results = self.store.load_results()
        history = []
        for account in accounts:
            result = results.get(account['account_key'])
            history.append({'status': result['status'], 'finished_at': result['finished_at']} if result else None)
        owner = active or max(runners, key=lambda task: getattr(task, 'start_time', 0) or 0, default=None)
        info = getattr(owner, 'info', {}) or {}
        current = info.get('当前账号')
        completed = info.get('已完成', [])
        start_time = getattr(owner, 'start_time', 0) or 0
        elapsed = None
        if start_time:
            if active:
                elapsed = max(0, int(time() - start_time))
            else:
                for result in history:
                    if result:
                        try:
                            finish = datetime.fromisoformat(result['finished_at']).timestamp()
                        except (ValueError, OSError, OverflowError):
                            continue
                        if finish >= start_time:
                            elapsed = max(elapsed or 0, int(finish - start_time))
        public = public_task_info(owner, info) if owner else {}
        return {
            'version': self.version(accounts), 'results': history,
            'run_unavailable': CLOUD_ACCOUNT_SWITCH_UNAVAILABLE,
            'busy': active is not None, 'can_stop': active is not None,
            'current_row': next((i for i, row in enumerate(accounts) if row['account_key'] == current), None),
            'completed': len(completed) if isinstance(completed, list) else 0,
            'start_time': start_time, 'elapsed': elapsed, 'phase': public.get('Status'),
        }

    def read(self, accounts):
        tasks = self.catalog()
        catalog = [{'task': task_reference(task), 'name': task.name, 'fields': _task_fields(task)}
                   for task in tasks.values()]
        field_keys = {item['task']['class']: {field['key'] for field in item['fields']} for item in catalog}
        rows = []
        for index, row in enumerate(accounts):
            reference = row['task']
            task = tasks.get(reference['class']) if reference else None
            keys = field_keys[reference['class']] if task and task_reference(task) == reference else set()
            key = row['account_key']
            rows.append({
                'index': index, 'label': '••••' + (key[-2:] if len(key) >= 5 else ''),
                'enabled': row.get('enabled', True), 'task': reference,
                'config': {key: value for key, value in row['config'].items() if key in keys},
            })
        return {**self.state(accounts), 'accounts': rows, 'tasks': catalog}

    def save(self, body):
        existing = self.store.load()
        if body.get('version') != self.version(existing):
            raise HTTPException(409, 'Account configuration changed. Reload before saving.')
        runners = self.runners()
        if any(task.enabled for task in runners):
            raise HTTPException(409, 'Account configuration is in use.')
        rows = body.get('accounts')
        if not isinstance(rows, list):
            raise HTTPException(400, 'Invalid account configuration.')
        tasks = self.catalog()
        plan = []
        used_indexes = set()
        for row_number, row in enumerate(rows, 1):
            invalid = HTTPException(400, f'Invalid configuration in account row {row_number}.')
            if not isinstance(row, dict):
                raise invalid
            index = row.get('index')
            if index is not None:
                if type(index) is not int or not 0 <= index < len(existing) or index in used_indexes:
                    raise invalid
                used_indexes.add(index)
            old = existing[index] if index is not None else {}
            key = row.get('account_key', old.get('account_key'))
            reference = row.get('task')
            enabled = row.get('enabled', True)
            changes = row.get('config', {})
            if not isinstance(changes, dict) or ('task' not in row):
                raise invalid
            saved = old.get('config', {}) if reference == old.get('task') else {}
            if reference is None:
                if changes:
                    raise invalid
                config = {}
            else:
                if not isinstance(reference, dict) or not isinstance(reference.get('class'), str):
                    raise invalid
                task = tasks.get(reference['class'])
                if task is None or task_reference(task) != reference:
                    if reference != old.get('task') or enabled or changes:
                        raise invalid
                    config = deepcopy(saved)
                else:
                    fields = {field['key']: field for field in _task_fields(task)}
                    config = {key: value for key, value in effective_config(task, saved).items()
                              if key in saved or not _PRIVATE_FIELD.search(key)}
                    for field_key, value in changes.items():
                        if field_key not in fields:
                            raise invalid
                        unchanged = (field_key in saved and type(value) is type(saved[field_key])
                                     and value == saved[field_key])
                        if not unchanged and not _valid_value(fields[field_key], value):
                            raise invalid
                        config[field_key] = value
            plan.append({'account_key': key, 'enabled': enabled, 'task': reference, 'config': config})
        try:
            self.store.save(plan, self.runtime.executor)
        except (ValueError, TypeError):
            # Desktop validation includes the actual account ID in some errors.
            raise HTTPException(400, 'Check account IDs, duplicate rows and task settings.') from None
        return plan


def register_accounts(app, runtime):
    editor = AccountEditor(runtime)

    def response(data):
        return JSONResponse(data, headers={'Cache-Control': 'no-store'})

    def with_store(action):
        try:
            with editor.lock:
                return response(action())
        except (ValueError, OSError, TypeError):
            raise HTTPException(409, 'Could not read or write account configuration or history.') from None

    @app.get('/api/accounts')
    def accounts():
        return with_store(lambda: editor.read(editor.store.load()))

    @app.get('/api/accounts/status')
    def status():
        return with_store(lambda: editor.state(editor.store.load()))

    @app.put('/api/accounts')
    def save(body: dict):
        return with_store(lambda: editor.read(editor.save(body)))

    @app.post('/api/accounts/start')
    def start():
        raise HTTPException(409, CLOUD_ACCOUNT_SWITCH_UNAVAILABLE)

    @app.post('/api/accounts/stop')
    def stop():
        with editor.lock:
            active = next((task for task in editor.runners() if task.enabled), None)
            if active is None:
                raise HTTPException(409, 'No account task is running.')
            try:
                runtime.task_action(active.name, 'stop')
            except (ValueError, RuntimeError):
                raise HTTPException(409, 'Could not stop account task.') from None
        return status()
