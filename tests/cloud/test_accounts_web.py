"""Account web/desktop round trips use synthetic IDs and a temporary config directory."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.cloud.management_web.accounts import (
    CLOUD_ACCOUNT_SWITCH_UNAVAILABLE, public_task_info, register_accounts,
)
from src.task.account.MultiAccountConfigStore import MultiAccountConfigStore


def task_instance(name, visible=True):
    task = type(name, (), {'__module__': 'src.task.' + name})()
    task.name = name
    task.visible = visible
    task.enabled = False
    task.default_config = {'Mode': 'easy', 'Count': 1, 'Password': 'dummy-secret'}
    task.config = {'Count': 3}
    task.config_description = {}
    task.config_type = {
        'Mode': {'options': ['easy', 'hard'], 'sub_configs': {'hard': ['Count']}},
        'Count': {'min': 1, 'max': 10},
    }
    return task


@pytest.fixture
def editor(tmp_path):
    task = task_instance('ExampleTask')
    runner = task_instance('MultiAccountConfigTask', False)
    daily = task_instance('MultiAccountDailyTask')
    executor = SimpleNamespace(config_folder=tmp_path, onetime_tasks=[daily, runner, task])
    calls = []
    def start(name):
        calls.append(('start', name))
        runner.enabled = True

    def action(name, operation):
        calls.append((operation, name))
        runner.enabled = False

    app = FastAPI()
    register_accounts(app, SimpleNamespace(executor=executor, start_task=start, task_action=action))
    store = MultiAccountConfigStore(tmp_path)
    with TestClient(app) as client:
        yield client, store, runner, calls


def test_private_read_real_store_roundtrip_and_stale_revisions(editor):
    client, store, _, _ = editor
    reference = {'class': 'ExampleTask', 'module': 'src.task.ExampleTask'}
    first = {'account_key': 'fixture-account-a1', 'enabled': True, 'task': reference,
             'config': {'Count': 7, 'Password': 'dummy-saved-secret', '_internal': 'private-sentinel'}}
    second = {'account_key': 'fixture-account-b2', 'task': None, 'config': {}}
    store.save([first, second])
    store.record_result(first, 'failed', 'private-sentinel')
    response = client.get('/api/accounts')
    assert response.headers['Cache-Control'] == 'no-store'
    for private in ('fixture-account', 'dummy-secret', 'dummy-saved-secret', 'private-sentinel', 'Password'):
        assert private not in response.text
    data = response.json()
    assert data['accounts'][0]['label'].endswith('a1')
    assert data['results'][0]['status'] == 'failed'
    assert data['tasks'][0]['task'] == reference
    assert len(data['tasks']) == 1
    assert data['tasks'][0]['fields'][0]['children'] == [{'choice': 'hard', 'keys': ['Count']}]
    assert data['tasks'][0]['fields'][1]['value'] == 3
    rows = list(reversed(data['accounts']))
    rows[0].update(account_key='fixture-renamed-c3', enabled=False)
    rows[1]['config'] = {'Mode': 'hard', 'Count': 9}
    payload = {'accounts': rows, 'version': data['version']}
    assert client.put('/api/accounts', json=payload).status_code == 200
    saved = store.load()
    assert saved[0] == {'account_key': 'fixture-renamed-c3', 'enabled': False, 'task': None, 'config': {}}
    assert saved[1]['account_key'] == first['account_key']
    assert saved[1]['config'] == {**first['config'], 'Mode': 'hard', 'Count': 9}
    assert client.put('/api/accounts', json=payload).status_code == 409
    assert store.load_results()[first['account_key']]['message'] == 'private-sentinel'


def test_prevent_duplicate_ids_and_unsafe_edits_and_disable_unsupported_cloud_switch(editor):
    client, store, runner, calls = editor
    snapshot = client.get('/api/accounts').json()
    ref = snapshot['tasks'][0]['task']
    row = {'account_key': 'fixture-id', 'enabled': True, 'task': ref, 'config': {}}
    def send(rows):
        return client.put('/api/accounts', json={'version': snapshot['version'], 'accounts': rows})

    for invalid in (
        [{**row, 'task': {'class': 'MultiAccountDailyTask'}}],
        [{**row, 'config': {'Count': 60}}], [{**row, 'config': {'Password': 'synthetic'}}],
        [{**row, 'account_key': 'test***'}], [row, row], [{**row, 'index': False}],
    ):
        response = send(invalid)
        assert response.status_code == 400
        assert 'fixture-id' not in response.text
        assert not calls and not store.path.exists()
    assert snapshot['run_unavailable'] == CLOUD_ACCOUNT_SWITCH_UNAVAILABLE
    blocked = client.post('/api/accounts/start', json={'version': snapshot['version'], 'accounts': [row]})
    assert blocked.status_code == 409 and blocked.json()['detail'] == CLOUD_ACCOUNT_SWITCH_UNAVAILABLE
    assert not calls and not store.path.exists()
    result = send([row])
    assert result.status_code == 200 and not result.json()['busy']
    assert 'Password' not in store.load()[0]['config']
    runner.enabled = True
    runner.info = {'当前账号': 'fixture-id', '已完成': [], 'Error': 'sensitive-sentinel'}
    status = client.get('/api/accounts/status')
    assert status.json()['current_row'] == 0
    assert 'fixture-id' not in status.text and 'sensitive-sentinel' not in status.text
    assert client.put('/api/accounts', json={'version': result.json()['version'], 'accounts': []}).status_code == 409
    assert client.post('/api/accounts/stop').json()['busy'] is False
    assert calls[-1] == ('stop', 'MultiAccountConfigTask')
    assert client.post('/api/accounts/stop').status_code == 409


def test_public_multi_account_task_info_keeps_only_counts_and_known_phases():
    daily = task_instance('MultiAccountDailyTask')
    configured = task_instance('MultiAccountConfigTask')
    nested = task_instance('ExampleTask')
    info = {'全部配置账号': ['fixture-a', 'fixture-b'], '当前账号': 'fixture-b', '执行任务': 'fixture',
            '已完成': ['fixture-a'], '状态': '执行中', 'Error': 'private-sentinel'}
    assert public_task_info(configured, info) == {'All Accounts': 2, 'Completed': 1, 'Status': 'Running'}
    assert public_task_info(nested, info) == public_task_info(configured, info)
    assert public_task_info(daily, {'All Accounts': {'fixture-a'}, 'Completed': {'fixture-a'}}) == {
        'All Accounts': 1, 'Completed': 1,
    }
    assert public_task_info(daily, {'Error': 'private-sentinel', '状态': 'private-sentinel'}) == {}
    assert public_task_info(nested, {'Count': 42}) == {'Count': 42}


def test_disabled_missing_tasks_survive_and_invalid_storage_is_not_overwritten(editor):
    client, store, _, _ = editor
    row = {'account_key': 'fixture-gone', 'enabled': False,
           'task': {'class': 'Gone', 'module': 'src.task.Gone'}, 'config': {'stale': 'sensitive-sentinel'}}
    store.save([row])
    response = client.get('/api/accounts')
    assert 'sensitive-sentinel' not in response.text
    data = response.json()
    assert client.put('/api/accounts', json=data).status_code == 200 and store.load() == [row]
    data['accounts'][0]['enabled'] = True
    assert client.put('/api/accounts', json=data).status_code == 400
    store.path.write_text('invalid', encoding='utf-8')
    assert client.get('/api/accounts').status_code == 409
    assert client.put('/api/accounts', json=data).status_code == 409
    assert store.path.read_text(encoding='utf-8') == 'invalid'
