"""Tests for the lab's telecloak pieces, with a fake Telegram (no network, no real keys):
server/doorbell/doorbell (AI box relay), harness/tools/telecloak-pull and ring-doorbell (harness VM).
Run: <python with cryptography + pytest> -m pytest -q analysis/tests/test_telecloak_lab.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "server" / "doorbell"))
from telecloak_core import Channel, Reassembler, load_psk, new_psk  # noqa: E402

BOT, HUMAN, STRANGER = 7000001, 5000002, 9000003
TOKEN = f"{BOT}:fake-token"


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeTelegram:
    def __init__(self):
        self.sent, self.updates, self.next_id = [], [], 100

    def __call__(self, token, method, params, timeout=30):
        assert token == TOKEN
        if method == "sendMessage":
            assert len(params["text"]) <= 4096
            self.sent.append(params["text"])
            return {"message_id": len(self.sent)}
        if method == "getUpdates":
            return [u for u in self.updates if u["update_id"] >= params["offset"]]
        raise AssertionError(method)

    def from_user(self, text, uid=HUMAN, chat_type="private", reply_to=None, date=None):
        self.next_id += 1
        m = {"message_id": 1000 + self.next_id, "date": int(date if date is not None else time.time()),
             "chat": {"id": uid, "type": chat_type}, "from": {"id": uid}, "text": text}
        if reply_to is not None:
            m["reply_to_message"] = {"message_id": reply_to}
        self.updates.append({"update_id": self.next_id, "message": m})


@pytest.fixture
def relay(tmp_path, monkeypatch, capsys):
    db = load(REPO / "server" / "doorbell" / "doorbell", "doorbell_under_test")
    (tmp_path / "state").mkdir()
    (tmp_path / "telegram.env").write_text(f"BOT_TOKEN={TOKEN}\nCHAT_ID={HUMAN}\n")
    monkeypatch.setattr(db, "STATE", str(tmp_path / "state"))
    monkeypatch.setattr(db, "CONF", str(tmp_path / "telegram.env"))
    monkeypatch.setattr(db, "KEY", str(tmp_path / "telecloak.key"))
    monkeypatch.setattr(db, "log", lambda m: None)
    tg = FakeTelegram()
    monkeypatch.setattr(db, "telegram", tg)
    psk = new_psk()

    class R:
        mod, fake, dir = db, tg, tmp_path
        human = Channel(load_psk(psk), HUMAN, BOT)       # the human's telecloak app

        @staticmethod
        def set_key():
            (tmp_path / "telecloak.key").write_text(psk + "\n")

        @staticmethod
        def run(cmd, stdin=b""):
            monkeypatch.setenv("SSH_ORIGINAL_COMMAND", cmd)
            monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": __import__("io").BytesIO(stdin)})())
            rc = db.main()
            return rc, capsys.readouterr().out

        @classmethod
        def read_sent(cls, i=-1):
            re = Reassembler()
            msg = None
            for w in ([cls.fake.sent[i]] if i != "all" else cls.fake.sent):
                msg = re.add(cls.human.open(w)) or msg
            return msg.body
    return R


def send(r, body):
    return r.run("send", json.dumps(body).encode())


def test_no_key_only_fixed_plain_ping_leaves(relay):
    rc, out = send(relay, {"kind": "ask", "project": "p1", "ask": "001", "text": "secret request text"})
    assert rc == 0 and "rang" in out
    assert relay.fake.sent == ["help i need your attention"]
    # fetch runs without a key (plain replies need none), but an encrypted message cannot be opened
    rc, out = relay.run("fetch")
    assert rc == 0 and out.strip() == ""
    relay.fake.from_user(relay.human.seal({"kind": "command", "command": "status"}).parts[0])
    assert relay.run("fetch")[1].count("{") == 0


def test_ask_is_encrypted_and_opens_for_the_human(relay):
    relay.set_key()
    rc, out = send(relay, {"kind": "ask", "project": "p1", "ask": "001", "text": "plug in the drive\x1b[31m", "x": 1})
    assert rc == 0 and "sent" in out
    wire = relay.fake.sent[-1]
    assert wire.startswith("tc1.") and "plug" not in wire
    assert relay.read_sent() == {"kind": "ask", "project": "p1", "ask": "001", "text": "plug in the drive[31m"}


def test_long_ask_is_split_and_reassembles(relay):
    relay.set_key()
    text = "x" * 7990
    send(relay, {"kind": "ask", "project": "p1", "ask": "002", "text": text})
    assert len(relay.fake.sent) == 3
    assert relay.read_sent("all")["text"] == text


def test_ring_gap_and_rate_limit(relay):
    relay.set_key()
    assert "rang" in relay.run("")[1]
    assert "not again yet" in relay.run("ring")[1]
    assert len(relay.fake.sent) == 1
    for i in range(19):
        assert send(relay, {"kind": "info", "text": f"m{i}"})[0] == 0
    rc, out = send(relay, {"kind": "info", "text": "one too many"})
    assert rc == 3 and "rate limit" in out and len(relay.fake.sent) == 20


def test_unknown_command_rings_like_the_old_doorbell(relay):
    relay.set_key()
    assert "rang" in relay.run("rm -rf /")[1]
    assert relay.read_sent()["kind"] == "ring"


def test_answer_fetched_until_acked(relay):
    relay.set_key()
    sealed = relay.human.seal({"kind": "answer", "project": "p1", "ask": "001", "text": "done, drive attached"})
    relay.fake.from_user(sealed.parts[0])
    rc, out = relay.run("fetch")
    lines = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert rc == 0 and len(lines) == 1
    m = lines[0]
    assert (m["kind"], m["project"], m["ask"], m["text"], m["id"]) == ("answer", "p1", "001", "done, drive attached",
                                                                       sealed.msg_id)
    assert json.loads(relay.run("fetch")[1].splitlines()[0])["id"] == sealed.msg_id   # still queued
    assert "acked 1" in relay.run(f"ack {sealed.msg_id} ../../etc/passwd")[1]
    assert relay.run("fetch")[1].strip() == ""


def test_replay_reflection_stranger_plaintext_and_stale_rejected(relay):
    relay.set_key()
    good = relay.human.seal({"kind": "command", "command": "status"})
    relay.fake.from_user(good.parts[0])
    relay.run("fetch")
    n_sent = len(relay.fake.sent)
    # replay: Telegram delivers the same ciphertext again
    relay.fake.from_user(good.parts[0])
    out = relay.run("fetch")[1]
    assert out.count("{") == 1                              # only the original, still unacked
    assert "replay" in relay.read_sent()["text"]            # the human is told (encrypted)
    relay.run(f"ack {good.msg_id}")
    # reflection: the bot's own message bounced back as if from the human
    send(relay, {"kind": "info", "text": "hello"})
    relay.fake.from_user(relay.fake.sent[-1])
    assert relay.run("fetch")[1].count("{") == 0
    # a stranger with a valid-looking message: ignored, and no notice goes to them or the human
    before = len(relay.fake.sent)
    relay.fake.from_user(relay.human.seal({"kind": "command", "command": "status"}).parts[0], uid=STRANGER)
    assert relay.run("fetch")[1].count("{") == 0 and len(relay.fake.sent) == before
    # unencrypted text from the human, no plain request waiting: not acted on, human told in plain text (phone)
    relay.fake.from_user("/stop hollowdeep")
    assert relay.run("fetch")[1].count("{") == 0
    assert relay.fake.sent[-1].startswith("The lab ignored a message from you: not encrypted, and no plain requests")
    # a day-old message (e.g. held back and delivered late)
    relay.fake.from_user(relay.human.seal({"kind": "command", "command": "status"}, now=time.time() - 90000).parts[0])
    assert relay.run("fetch")[1].count("{") == 0
    assert n_sent < len(relay.fake.sent)


@pytest.mark.parametrize("body", [
    {"kind": "command", "command": "rm"},
    {"kind": "command", "command": "start"},                      # start needs a project
    {"kind": "answer", "project": "p1", "ask": "1; rm", "text": "x"},
    {"kind": "message", "project": "../etc", "text": "x"},
    {"kind": "message", "project": "p1", "text": "   "},
    {"kind": "exec", "text": "x"},
])
def test_malformed_inbound_rejected(relay, body):
    relay.set_key()
    relay.fake.from_user(relay.human.seal(body).parts[0])
    assert relay.run("fetch")[1].count("{") == 0


def test_split_inbound_across_fetches(relay):
    relay.set_key()
    sealed = relay.human.seal({"kind": "message", "project": "p1", "text": "y" * 6000})
    relay.fake.from_user(sealed.parts[0])
    assert relay.run("fetch")[1].count("{") == 0
    for p in sealed.parts[1:]:
        relay.fake.from_user(p)
    out = relay.run("fetch")[1]
    assert json.loads(out.splitlines()[0])["text"] == "y" * 6000


# ---- harness VM side --------------------------------------------------------------------------------------------------

@pytest.fixture
def vm(tmp_path, monkeypatch):
    pull = load(REPO / "harness" / "tools" / "telecloak-pull", "pull_under_test")
    projects = tmp_path / "projects"
    p = projects / "p1"
    (p / ".agent" / "asks").mkdir(parents=True)
    (p / "tasks").mkdir()
    (p / "tasks" / "001-drive.md").write_text("Status: in-progress\n# 001: Use the drive\n")
    (p / ".agent" / "asks" / "001.md").write_text(
        "# Request 001\nstatus: open\nblocking: yes\ntask: tasks/001-drive.md\n\n## Request\nplug it in\n")
    (p / ".agent" / "asks" / "002.md").write_text("# Request 002\nstatus: closed\n\n## Request\nold\n")
    monkeypatch.setattr(pull, "PROJECTS", projects)
    replies = []
    monkeypatch.setattr(pull, "reply", lambda project, text, **kw: replies.append((project, text, kw)))
    monkeypatch.setattr(pull, "log", lambda m: None)
    return pull, p, replies


def test_pull_answer_marks_request_answered(vm):
    pull, p, replies = vm
    pull.handle({"kind": "answer", "project": "p1", "ask": "001", "text": "attached as /dev/sdb"})
    a = (p / ".agent" / "asks" / "001.md").read_text()
    assert "status: answered" in a and "status: open" not in a
    assert a.rstrip().endswith("attached as /dev/sdb") and ", telegram)" in a
    assert replies[-1][2] == {"answered": ["001"]}


def test_pull_answer_to_closed_request_becomes_a_message(vm):
    pull, p, replies = vm
    pull.handle({"kind": "answer", "project": "p1", "ask": "002", "text": "late answer"})
    inbox = list((p / ".agent" / "inbox").glob("*.md"))
    assert len(inbox) == 1 and "late answer" in inbox[0].read_text()
    assert "already closed" in replies[-1][1] and replies[-1][2] == {"answered": []}


def test_pull_message_goes_to_inbox_in_the_extension_format(vm):
    import re
    pull, p, replies = vm
    pull.handle({"kind": "message", "project": "p1", "text": "use sqlite, not postgres"})
    (f,) = (p / ".agent" / "inbox").iterdir()
    assert re.fullmatch(r"\d+\.md", f.name) and f.read_text() == "use sqlite, not postgres\n"
    assert "not running" in replies[-1][1]


def test_pull_unknown_project_and_traversal(vm):
    pull, p, replies = vm
    for name in ["nope", "..", "../p1"]:
        pull.handle({"kind": "message", "project": name, "text": "x"})
        assert "No project" in replies[-1][1]


def test_pull_status_and_commands(vm, tmp_path, monkeypatch):
    pull, p, replies = vm
    pull.handle({"kind": "command", "command": "status", "project": "p1"})
    assert replies[-1][1].startswith("p1: stopped; task 001: Use the drive; waiting on request(s) 001")
    pull.handle({"kind": "command", "command": "status"})
    assert replies[-1][1] == "p1: stopped; task 001: Use the drive; waiting on request(s) 001"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "agent-stop").write_text('#!/bin/sh\necho "stop called with: $*"\n')
    (bin_ / "agent-stop").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    pull.handle({"kind": "command", "command": "stop-now", "project": "p1"})
    assert replies[-1][1] == "p1: stop called with: p1 --now"


def test_ring_doorbell_sends_open_asks_and_ask_text(tmp_path):
    p = tmp_path / "proj"
    (p / ".agent" / "asks").mkdir(parents=True)
    (p / ".agent" / "asks" / "003.md").write_text("# Request 003\nstatus: open\n")
    (p / ".agent" / "asks" / "001.md").write_text("# Request 001\nstatus: closed\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text(f'#!/bin/sh\necho "$*" > {tmp_path}/args\ncat > {tmp_path}/stdin\necho "doorbell: rang"\n')
    (fake / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    tool = str(REPO / "harness" / "tools" / "ring-doorbell")
    r = subprocess.run([tool], cwd=p, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "doorbell: rang"
    assert (tmp_path / "args").read_text().split()[-2:] == ["doorbell", "send"]
    assert json.loads((tmp_path / "stdin").read_text()) == {
        "kind": "ring", "project": "proj", "asks": ["003"], "text": "waiting for your answer", "plain": True}
    r = subprocess.run([tool, "--ask", "003"], cwd=p, env=env, input="need a key", capture_output=True, text=True)
    assert json.loads((tmp_path / "stdin").read_text())["text"] == "need a key"
    assert subprocess.run([tool, "--ask", "x; id"], cwd=p, env=env, capture_output=True).returncode == 2


# ---- plain projects (not marked confidential) ---------------------------------------------------------------------

def fetched(relay):
    rc, out = relay.run("fetch")
    return [json.loads(l) for l in out.splitlines() if l.startswith("{")]


@pytest.mark.parametrize("key", [False, True])
def test_plain_ask_is_readable_and_a_reply_answers_it(relay, key):
    if key:
        relay.set_key()
    rc, out = send(relay, {"kind": "ask", "project": "p1", "ask": "001", "text": "plug in the drive\x1b[31m",
                           "plain": True})
    assert rc == 0 and "sent" in out
    msg = relay.fake.sent[-1]
    assert not msg.startswith("tc1.") and msg.startswith("p1, request 001:\nplug in the drive[31m")
    relay.fake.from_user("attached as /dev/sdb", reply_to=len(relay.fake.sent))
    (m,) = fetched(relay)
    assert (m["kind"], m["project"], m["ask"], m["text"]) == ("answer", "p1", "001", "attached as /dev/sdb")
    assert len(fetched(relay)) == 1                        # queued until acked, like any message
    assert "acked 1" in relay.run(f"ack {m['id']}")[1] and fetched(relay) == []


def test_plain_reply_to_a_notice_is_a_message_never_a_command(relay):
    send(relay, {"kind": "info", "project": "p1", "text": "decided request 002 myself", "plain": True})
    assert relay.fake.sent[-1] == "p1: decided request 002 myself"
    relay.fake.from_user("/stop p1", reply_to=len(relay.fake.sent))
    (m,) = fetched(relay)
    assert (m["kind"], m["project"], m["text"]) == ("message", "p1", "/stop p1") and "command" not in m


def test_plain_reply_rejected_unless_to_a_plain_message_from_the_human_and_fresh(relay):
    relay.set_key()
    send(relay, {"kind": "ask", "project": "p2", "ask": "003", "text": "sealed request"})      # confidential: sealed
    sealed_id = len(relay.fake.sent)
    send(relay, {"kind": "ask", "project": "p1", "ask": "004", "text": "plain request", "plain": True})
    plain_id = len(relay.fake.sent)
    relay.fake.from_user("answer to a sealed one", reply_to=sealed_id)          # a confidential project's message
    relay.fake.from_user("from a stranger", uid=STRANGER, reply_to=plain_id)    # not the human
    relay.fake.from_user("in a group", chat_type="group", reply_to=plain_id)
    relay.fake.from_user("held back", reply_to=plain_id, date=time.time() - 90000)
    relay.fake.from_user("held back, no reply", date=time.time() - 90000)
    assert fetched(relay) == []
    assert relay.fake.sent[-1].startswith("The lab ignored a message from you: that is a reply to a message the lab")


def test_plain_reply_replayed_is_taken_once(relay):
    send(relay, {"kind": "ask", "project": "p1", "ask": "005", "text": "q", "plain": True})
    relay.fake.from_user("yes", reply_to=len(relay.fake.sent))
    (m,) = fetched(relay)
    relay.run(f"ack {m['id']}")
    relay.fake.updates.append(relay.fake.updates[-1] | {"update_id": relay.fake.next_id + 1})   # delivered again
    relay.fake.next_id += 1
    assert fetched(relay) == []


def test_plain_needs_a_project_and_long_text_is_split(relay):
    send(relay, {"kind": "info", "text": "no project", "plain": True})
    assert relay.fake.sent[-1] == "help i need your attention"                  # no key, no project: fixed ping
    send(relay, {"kind": "ask", "project": "p1", "ask": "006", "text": "z" * 7990, "plain": True})
    assert len(relay.fake.sent) == 4 and all(len(t) <= 4000 for t in relay.fake.sent[1:])
    relay.fake.from_user("ok", reply_to=3)                                       # a reply to any part counts
    assert fetched(relay)[0]["ask"] == "006"


def test_ring_doorbell_plain_unless_confidential(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text(f'#!/bin/sh\ncat > {tmp_path}/stdin\necho "doorbell: sent"\n')
    (fake / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    tool = str(REPO / "harness" / "tools" / "ring-doorbell")
    main, wt = tmp_path / "proj", tmp_path / "proj.b"
    for d in (main, wt):
        (d / ".agent").mkdir(parents=True)
    (wt / ".agent" / "team.env").write_text(f"AGENT_ID=b\nTEAM_DIR={main}\n")

    def sent_from(d):
        subprocess.run([tool, "--info"], cwd=d, env=env, input="hi", capture_output=True, text=True, check=True)
        return json.loads((tmp_path / "stdin").read_text())
    assert sent_from(main).get("plain") is True and sent_from(wt).get("plain") is True
    (main / ".agent" / "confidential").touch()
    assert "plain" not in sent_from(main) and "plain" not in sent_from(wt)   # a team worktree follows its main checkout


def test_pull_replies_plain_for_plain_projects(vm, tmp_path, monkeypatch):
    pull, p, _ = vm
    pull = load(REPO / "harness" / "tools" / "telecloak-pull", "pull_reply_under_test")
    monkeypatch.setattr(pull, "PROJECTS", p.parent)
    bodies = []
    monkeypatch.setattr(pull, "ssh", lambda args, stdin=None, timeout=120: bodies.append(json.loads(stdin))
                        or subprocess.CompletedProcess(args, 0, "", ""))
    pull.reply("p1", "Answer delivered")
    pull.reply(None, "status of everything")
    (p / ".agent" / "confidential").touch()
    pull.reply("p1", "Answer delivered")
    assert [b.get("plain") for b in bodies] == [True, None, None]


def test_plain_new_message_answers_the_only_waiting_request(relay):
    send(relay, {"kind": "ask", "project": "p1", "ask": "004", "text": "which db?", "plain": True})
    assert "just send your answer" in relay.fake.sent[-1]
    relay.fake.from_user("sqlite")                                           # typed into the chat, not a reply
    (m,) = fetched(relay)
    assert (m["kind"], m["project"], m["ask"], m["text"]) == ("answer", "p1", "004", "sqlite")
    relay.run(f"ack {m['id']}")
    relay.fake.from_user("and another thing")                                # 004 is answered: nothing waits
    assert fetched(relay) == []
    assert relay.fake.sent[-1].startswith("The lab ignored a message from you: not encrypted, and no plain requests")


def test_plain_new_message_with_two_waiting_is_refused_and_names_them(relay):
    send(relay, {"kind": "ask", "project": "p1", "ask": "001", "text": "a?", "plain": True})
    send(relay, {"kind": "ask", "project": "p2", "ask": "002", "text": "b?", "plain": True})
    p2_msg = len(relay.fake.sent)
    relay.fake.from_user("yes")
    assert fetched(relay) == []
    assert "2 plain requests are waiting (p1 request 001, p2 request 002): reply to the one you mean" in relay.fake.sent[-1]
    relay.fake.from_user("yes", reply_to=p2_msg)
    assert [(m["project"], m["ask"]) for m in fetched(relay)] == [("p2", "002")]


def test_answer_confirmed_by_the_lab_stops_the_request_waiting(relay):
    send(relay, {"kind": "ask", "project": "p1", "ask": "001", "text": "a?", "plain": True})
    send(relay, {"kind": "ask", "project": "p1", "ask": "002", "text": "b?", "plain": True})
    send(relay, {"kind": "reply", "project": "p1", "text": "Answer delivered", "answered": ["001"], "plain": True})
    relay.fake.from_user("go with b")                                        # only 002 still waits
    assert [(m["project"], m["ask"]) for m in fetched(relay)] == [("p1", "002")]
