from backend.delivery import deliver

def test_local_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERCODE_HOME", str(tmp_path))
    from backend import home as H
    H.HOME = tmp_path
    r = deliver("local", "hola", {"job": "j1"})
    assert r.get("ok")
