"""Actualizador: check/apply sobre el checkout (git mockeado)."""
import subprocess
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main
from app import update

LOCAL = "abc1234" + "0" * 33
REMOTE = "fed9876" + "0" * 33


def client(tmp_path):
    main.CON = main.db.connect(tmp_path / "t.db")
    return TestClient(main.app)


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _fake_git(outs, calls=None):
    """_git falso. Busca primero por los dos primeros argumentos (así
    `rev-parse --is-shallow-repository` y `rev-parse HEAD` pueden responder
    distinto) y si no, por el subcomando."""
    def run(*a, **k):
        if calls is not None:
            calls.append(a)
        return outs[a[:2]] if a[:2] in outs else outs[a[0]]
    return run


def test_check_reports_behind(tmp_path):
    c = client(tmp_path)
    outs = {"rev-parse": _cp(LOCAL + "\n"),
            "ls-remote": _cp(f"{REMOTE}\trefs/heads/main\n")}
    with patch.object(update, "_git", side_effect=_fake_git(outs)):
        r = c.get("/api/update/check").json()
    assert r == {"git": True, "current": "abc1234", "behind": 1, "latest": "fed9876"}


def test_check_up_to_date(tmp_path):
    c = client(tmp_path)
    outs = {"rev-parse": _cp(LOCAL + "\n"),
            "ls-remote": _cp(f"{LOCAL}\trefs/heads/main\n")}
    with patch.object(update, "_git", side_effect=_fake_git(outs)):
        r = c.get("/api/update/check").json()
    assert r == {"git": True, "current": "abc1234", "behind": 0, "latest": "abc1234"}


def test_check_failure_is_an_error_not_up_to_date(tmp_path):
    # La regresión de v1.33.2: si git falla, la respuesta no puede ser
    # «estás al día». Así quedaron dos usuarios una semana atrapados en 1.32.0.
    c = client(tmp_path)
    outs = {"rev-parse": _cp(LOCAL + "\n"),
            "ls-remote": _cp("", returncode=128, stderr="fatal: unable to access")}
    with patch.object(update, "_git", side_effect=_fake_git(outs)):
        r = c.get("/api/update/check").json()
    assert "unable to access" in r.get("error", "")
    assert "behind" not in r


def test_check_without_git_checkout(tmp_path):
    c = client(tmp_path)
    with patch.object(update, "is_git_checkout", return_value=False):
        assert c.get("/api/update/check").json() == {"git": False}


def test_apply_pull_and_deps_hint(tmp_path):
    c = client(tmp_path)
    outs = {("rev-parse", "--is-shallow-repository"): _cp("false\n"),
            "diff": _cp("pyproject.toml\napp/main.py\n"), "pull": _cp("ok"),
            "rev-parse": _cp("def5678\n")}
    with patch.object(update, "_git", side_effect=_fake_git(outs)):
        r = c.post("/api/update/apply").json()
    assert r["ok"] and r["deps_changed"] and r["current"] == "def5678"
    assert r["installer"] in ("./install.sh", "install.ps1")


def test_apply_unshallows_a_shallow_clone_first(tmp_path):
    # El bootstrap clona con --depth 1: sin completar la historia, el
    # fast-forward no tiene contra qué avanzar.
    c = client(tmp_path)
    calls = []
    outs = {("rev-parse", "--is-shallow-repository"): _cp("true\n"),
            "fetch": _cp(), "pull": _cp("ok"), "diff": _cp(""),
            "rev-parse": _cp("def5678\n")}
    with patch.object(update, "_git", side_effect=_fake_git(outs, calls)):
        r = c.post("/api/update/apply").json()
    assert r["ok"]
    assert ("fetch", "--unshallow", "origin", "main") in calls
    subcmds = [a[0] for a in calls]
    assert subcmds.index("fetch") < subcmds.index("pull")


def test_apply_surfaces_pull_error(tmp_path):
    c = client(tmp_path)
    outs = {("rev-parse", "--is-shallow-repository"): _cp("false\n"),
            "diff": _cp(""),
            "pull": _cp("", returncode=1, stderr="error: local changes")}
    with patch.object(update, "_git", side_effect=_fake_git(outs)):
        r = c.post("/api/update/apply").json()
    assert "error" in r and "local changes" in r["error"]
