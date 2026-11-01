"""The host notices the `orchestra run` that started it is gone (parent.py)."""

import threading

from orchestra_agents.parent import ParentWatch, watch


def test_the_parent_is_gone_when_the_host_is_reparented():
    ppid = [4242]
    pw = ParentWatch(getppid=lambda: ppid[0], is_alive=lambda pid: True)
    assert pw.watching() and pw.parent == 4242 and not pw.gone()
    ppid[0] = 1                                     # the launcher died: init took the host over
    assert pw.gone()


def test_the_parent_is_gone_when_its_pid_is_no_longer_alive():
    living = {4242}
    pw = ParentWatch(getppid=lambda: 4242, is_alive=lambda pid: pid in living)
    assert not pw.gone()
    living.clear()
    assert pw.gone()


def test_a_host_started_under_init_watches_nothing():
    pw = ParentWatch(getppid=lambda: 1, is_alive=lambda pid: False)
    assert not pw.watching() and not pw.gone()
    assert watch(lambda pid: None, threading.Event(), parent=pw) is None


def test_watch_calls_back_once_when_the_parent_goes():
    ppid, called, stop = [4242], [], threading.Event()
    pw = ParentWatch(getppid=lambda: ppid[0], is_alive=lambda pid: True)
    th = watch(called.append, stop, every_s=0.01, parent=pw)
    ppid[0] = 1
    th.join(2)
    assert not th.is_alive() and called == [4242]


def test_watch_ends_when_the_host_stops():
    stop = threading.Event()
    th = watch(lambda pid: None, stop, every_s=0.01, parent=ParentWatch(getppid=lambda: 4242, is_alive=lambda pid: True))
    stop.set()
    th.join(2)
    assert not th.is_alive()
