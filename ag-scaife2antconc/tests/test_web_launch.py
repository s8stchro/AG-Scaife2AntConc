"""Launcher lifecycle regressions, independent of linguistic providers."""
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest

from ag_scaife2antconc import APP_NAME, web
from ag_scaife2antconc.pipeline import SetupError
from ag_scaife2antconc.web_api import create_app
from ag_scaife2antconc.web_launch import local_url, owns_record
from ag_scaife2antconc.web_service import Workbench
from ag_scaife2antconc.web_storage import atomic_json

SECRET='s'*48
URL='http://127.0.0.1:8766'


def record(root, **changes):
    return dict(application=APP_NAME,pid=123,process_identity=456,started=0,
                state_directory=str(root),executable=sys.executable,process_image=sys.executable,module=web.__file__,url=URL,**changes)


@pytest.mark.parametrize('value',[None,'https://127.0.0.1:8766','http://example.org:8766',
    'http://127.0.0.1:8766@evil.example','http://127.0.0.1:8766/?x=1','http://127.0.0.1:8766/#secret',
    'http://127.0.0.1:22','http://127.0.0.1:99999'])
def test_record_cannot_redirect_launcher_to_foreign_or_ambiguous_url(value):
    assert local_url(value) is None


def test_valid_record_url_and_ownership_are_strict(tmp_path):
    assert local_url(URL+'/')==URL
    data=record(tmp_path)
    assert owns_record(data,tmp_path)
    for key,value in [('process_identity',None),('pid','123'),('application','other'),
                      ('executable','C:/unrelated/python.exe'),('state_directory',str(tmp_path/'other'))]:
        assert not owns_record(dict(data,**{key:value}),tmp_path)


@pytest.mark.parametrize('ready,expected',[(True,True),(False,False)])
def test_probe_does_not_reuse_closing_server_or_send_credentials(monkeypatch,ready,expected):
    def open_request(request,timeout):
        assert request.get_header('Authorization') is None
        return BytesIO(json.dumps({'application':APP_NAME,'instance':hashlib.sha256(SECRET.encode()).hexdigest(),
                                  'ready':ready}).encode())
    monkeypatch.setattr(web.urllib.request,'build_opener',lambda *a:type('Opener',(),{'open':staticmethod(open_request)})())
    assert web.existing(URL,SECRET)==expected


def test_healthy_instance_is_reused_without_binding_or_termination(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'existing',lambda *a,**k:True)
    monkeypatch.setattr(web,'listening_socket',lambda *a:pytest.fail('healthy server was duplicated'))
    monkeypatch.setattr(web,'terminate_owned_server',lambda *a:pytest.fail('healthy server was killed'))
    assert web.connect_or_listen(URL,SECRET,tmp_path) is None


def test_closing_listener_is_allowed_to_release_its_port(tmp_path,monkeypatch):
    calls=[]; expected=object()
    def listen(port):
        calls.append(port)
        if len(calls)==1: raise OSError('still closing')
        return expected
    monkeypatch.setattr(web,'existing',lambda *a,**k:False)
    monkeypatch.setattr(web,'listening_socket',listen)
    monkeypatch.setattr(web.time,'sleep',lambda *a:None)
    monkeypatch.setattr(web,'terminate_owned_server',lambda *a:pytest.fail('graceful close was forced'))
    assert web.connect_or_listen(URL,SECRET,tmp_path) is expected


def test_verified_unresponsive_instance_can_be_recovered(tmp_path,monkeypatch):
    atomic_json(tmp_path/'server.json',record(tmp_path))
    stopped=[]; bound=object()
    monkeypatch.setattr(web,'existing',lambda *a,**k:False)
    monkeypatch.setattr(web,'RECOVERY_WAIT',0)
    def listen(port):
        if not stopped: raise OSError('occupied')
        return bound
    monkeypatch.setattr(web,'listening_socket',listen)
    monkeypatch.setattr(web,'terminate_owned_server',lambda data,root:stopped.append(data['pid']) or True)
    assert web.connect_or_listen(URL,SECRET,tmp_path) is bound
    assert stopped==[123]


def test_foreign_port_does_not_terminate_our_server_at_another_address(tmp_path,monkeypatch):
    atomic_json(tmp_path/'server.json',dict(record(tmp_path),url='http://127.0.0.1:8777'))
    monkeypatch.setattr(web,'existing',lambda *a,**k:False)
    monkeypatch.setattr(web,'RECOVERY_WAIT',0)
    monkeypatch.setattr(web,'listening_socket',lambda *a:(_ for _ in ()).throw(OSError('foreign listener')))
    monkeypatch.setattr(web,'terminate_owned_server',lambda *a:pytest.fail('wrong listener targeted'))
    with pytest.raises(SetupError): web.connect_or_listen(URL,SECRET,tmp_path)


def test_health_marks_a_closing_service_as_unavailable(tmp_path):
    from fastapi.testclient import TestClient
    service=Workbench(tmp_path/'state')
    app=create_app(service,origin='http://testserver',secret=SECRET)
    with TestClient(app) as client:
        assert client.get('/api/health').json()['ready'] is True
        service.closed=True
        assert client.get('/api/health').json()['ready'] is False


def test_quitting_each_window_preserves_the_other_then_exits(tmp_path):
    from fastapi.testclient import TestClient
    service = Workbench(tmp_path)
    first, second = uuid4(), uuid4()
    service.windows.update({first: object(), second: object()})
    shutdown = []
    app = create_app(service, origin='http://testserver', secret=SECRET, shutdown=lambda: shutdown.append(True))
    with TestClient(app, headers={'Authorization': 'Bearer ' + SECRET, 'X-AG-CSRF': app.state.csrf}) as client:
        assert client.post(f'/api/windows/{first}/quit').json()['status'] == 'other_windows'
        service.last_activity = time.monotonic() - 600
        assert not web.should_exit_workbench(service, 60)
        assert not service.closed and not shutdown
        assert client.post(f'/api/windows/{second}/quit').json()['status'] == 'closed'
        assert service.closed and shutdown == [True]


def test_quit_waits_for_worker_even_with_idle_timeout_disabled(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    service = Workbench(tmp_path)
    job = {'state': 'running'}
    monkeypatch.setattr(service, 'active_job', lambda: job)
    app = create_app(service, origin='http://testserver', secret=SECRET)
    with TestClient(app, headers={'Authorization': 'Bearer ' + SECRET, 'X-AG-CSRF': app.state.csrf}) as client:
        assert client.post(f'/api/windows/{uuid4()}/quit').json()['status'] == 'finishing_job'
        for phase in ('running', 'publishing'):
            job['state'] = phase
            assert not web.should_exit_workbench(service, 0)
        job.clear()
        assert web.should_exit_workbench(service, 0)
        assert service.closed


def test_relaunch_reopens_existing_workspace_and_preserves_authentication(tmp_path,monkeypatch):
    root=tmp_path/'state'; root.mkdir()
    atomic_json(root/'authentication.json',{'secret':SECRET})
    atomic_json(root/'server.json',{'url':URL,'pid':123})
    before=(root/'authentication.json').read_bytes(); opened=[]
    monkeypatch.setattr(web,'connect_or_listen',lambda *a:None)
    monkeypatch.setattr(web,'open_browser',lambda url,secret:opened.append((url,secret)))
    web.main(['--state',str(root)])
    assert opened==[(URL,SECRET)]
    assert (root/'authentication.json').read_bytes()==before


def test_open_browser_prefers_app_window_and_falls_back_to_tab(monkeypatch):
    spawned, tabs = [], []
    monkeypatch.delenv('AG_BROWSER_TAB', raising=False)
    monkeypatch.setattr(web, 'find_app_browser', lambda: r'C:\Edge\msedge.exe')
    monkeypatch.setattr(web.subprocess, 'Popen', lambda cmd, **kw: spawned.append(cmd))
    monkeypatch.setattr(web.webbrowser, 'open_new_tab', lambda url: tabs.append(url))

    web.open_browser(URL, SECRET)
    assert spawned == [[r'C:\Edge\msedge.exe', f'--app={URL}/#launch={SECRET}']]
    assert tabs == []

    monkeypatch.setenv('AG_BROWSER_TAB', '1')
    web.open_browser(URL, SECRET)
    assert tabs == [f'{URL}/#launch={SECRET}']

    monkeypatch.delenv('AG_BROWSER_TAB', raising=False)
    monkeypatch.setattr(web, 'find_app_browser', lambda: None)
    web.open_browser(URL, SECRET)
    assert len(tabs) == 2
