"""agent_testlock: in team mode, timing tests run alone on the VM; other test runs still overlap each other.

The checkouts of a team project share one VM, so a timing test (a 0.5 s page budget) measured while another
checkout runs its suite fails for reasons that are not in the code (frontpage task 230, request 003). agent-team-lib
loads this plugin into every pytest run of its agents and their driver (PYTEST_PLUGINS + PYTHONPATH) and points
AGENT_TEST_LOCK at one lock file per team. After collection a run that includes a timing test (a test file whose
name contains AGENT_TEST_LOCK_EXCLUSIVE, default "perf") takes the lock exclusively; any other run takes it shared.
The lock is held until pytest exits. Waiting is announced on the terminal and gives up after AGENT_TEST_LOCK_WAIT
seconds (default 600; the driver's suite timeout is 900), running anyway with a warning. Without AGENT_TEST_LOCK
it does nothing; a pytest started from inside a locked run (AGENT_TEST_LOCK_HELD) does not lock again.

Timing tests are not part of the ordinary test run (2026-10-08: the six of frontpage took 75% of its suite time, and
every run that included them locked the other checkouts out). A run leaves them out unless it names a timing test file on its command line or sets
AGENT_TIMING=1; it says so on the terminal. The driver runs them on main after every merge, in the background
(timing-watch), and opens task 998 when they fail.
"""
import fcntl
import os
import time

_fd = None


def _say(session, msg):
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line("agent_testlock: " + msg)


def _timing(nodeid, word):
    return word in os.path.basename(nodeid.split("::")[0])


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("AGENT_TEST_LOCK") or os.environ.get("AGENT_TIMING") == "1":
        return
    word = os.environ.get("AGENT_TEST_LOCK_EXCLUSIVE", "perf")
    if any(_timing(str(a), word) for a in config.args):   # named on the command line: wanted
        return
    out = [i for i in items if _timing(i.nodeid, word)]
    if not out:
        return
    items[:] = [i for i in items if not _timing(i.nodeid, word)]
    config.hook.pytest_deselected(items=out)
    tr = config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(f"agent_testlock: {len(out)} timing test(s) (test files named *{word}*) left out: the driver runs "
                      "them on main after every merge and opens task 998 if they fail. Name the file to run them.")


def pytest_collection_finish(session):
    global _fd
    path = os.environ.get("AGENT_TEST_LOCK")
    if not path or os.environ.get("AGENT_TEST_LOCK_HELD") or os.environ.get("PYTEST_XDIST_WORKER") or _fd is not None:
        return
    word = os.environ.get("AGENT_TEST_LOCK_EXCLUSIVE", "perf")
    alone = any(word in os.path.basename(item.nodeid.split("::")[0]) for item in session.items)
    mode = fcntl.LOCK_EX if alone else fcntl.LOCK_SH
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    start = time.monotonic()
    announced = False
    while True:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            waited = time.monotonic() - start
            if not announced:
                _say(session, "timing tests run alone on this VM: waiting for another checkout's test run"
                     if alone else "waiting for another checkout's timing tests to finish")
                announced = True
            if waited > int(os.environ.get("AGENT_TEST_LOCK_WAIT", "600")):
                _say(session, f"gave up after {waited:.0f} s, running anyway: timing results may be off")
                os.close(fd)
                return
            time.sleep(1)
    if announced:
        _say(session, f"lock taken after {time.monotonic() - start:.0f} s")
    os.environ["AGENT_TEST_LOCK_HELD"] = "1"
    _fd = fd


def pytest_unconfigure(config):
    global _fd
    if _fd is not None:
        os.close(_fd)
        _fd = None
