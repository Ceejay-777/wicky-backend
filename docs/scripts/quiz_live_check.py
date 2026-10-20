"""Live end-to-end check of async quiz generation: real web server, Celery worker,
Redis and SSE, with only the AI call mocked. See docs/reference/live-testing.md.

    python docs/scripts/quiz_live_check.py worker   # Celery worker with a mocked AI call
    python docs/scripts/quiz_live_check.py check    # run the scenarios (web server must be up)

Run from the repo root with DATABASE_URL, CELERY_BROKER_URL and DJANGO_SETTINGS_MODULE set.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

sys.path.insert(0, os.getcwd())

import django

django.setup()

import httpx
import redis
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.ai.exceptions import ProviderError

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8011")
PASSWORD = "pw-live-12345"
r = redis.Redis.from_url(os.environ["CELERY_BROKER_URL"])


def fake_generate_content(prompt, system_instruction=None, output_schema=None, *, action="topic"):
    """AI stand-in. Redis keys: live:ai_calls (counter), live:ai_delay (s), live:ai_fail (set to fail)."""
    r.incr("live:ai_calls")
    time.sleep(float(r.get("live:ai_delay") or 2))
    if r.get("live:ai_fail"):
        raise ProviderError("mock provider down", provider="groq")
    count, total = (5, 100) if action == "quiz_normal" else (10, 200)
    return {
        "questions": [
            {
                "question": f"Mock question {i}?",
                "options": [f"opt {i}a", f"opt {i}b", f"opt {i}c", f"opt {i}d"],
                "correct_index": 0,
                "explanation": f"Because {i}.",
            }
            for i in range(count)
        ],
        "total_points": total,
    }


def run_worker():
    from unittest.mock import patch

    from config.celery import app

    pool = os.environ.get("WORKER_POOL", "threads")  # prefork does not run on Windows
    with patch("apps.learning.services.generate_content", fake_generate_content):
        app.worker_main(["worker", "-l", "info", "-P", pool, "-c", "4", "-n", "live@%h"])


class SSEListener:
    def __init__(self, cookies):
        self.events = []
        self.status = None
        self._ready = threading.Event()
        self._cookies = cookies
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(10)

    def _run(self):
        with httpx.stream("GET", f"{BASE}/api/events", cookies=self._cookies, timeout=None) as resp:
            self.status = resp.status_code
            self._ready.set()
            event = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event:
                    payload = line.split(":", 1)[1].strip()
                    if payload:  # control events such as stream-open carry no data
                        self.events.append((event, json.loads(payload)))
                    event = None

    def wait_for(self, event, pred=lambda data: True, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name, data in self.events:
                if name == event and pred(data):
                    return data
            time.sleep(0.1)
        return None


def sign_in(email):
    client = httpx.Client(base_url=BASE, timeout=30)
    resp = client.post("/api/auth/signin", json={"email": email, "password": PASSWORD})
    resp.raise_for_status()
    return client


def run_checks():
    from apps.learning.models import QuizAttempt, Subject, Topic, UserSubjectProgress
    from apps.learning.tasks import cleanup_stuck_generations

    outcomes = []

    def check(name, ok, detail=""):
        outcomes.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))

    r.delete("live:ai_calls", "live:ai_fail")
    r.set("live:ai_delay", 2)
    stamp = int(time.time())
    User = get_user_model()
    users = [User.objects.create_user(email=f"live{stamp}-{n}@example.com", password=PASSWORD) for n in (1, 2)]
    subject = Subject.objects.create(name=f"Live Subject {stamp}", roadmap_status="READY")
    topics = [
        Topic.objects.create(
            subject=subject, title=f"Topic {n}", level=1, order=n, summary="Summary.",
            content_status="READY", review_status="PASSED",
        )
        for n in range(1, 5)
    ]
    for user in users:
        UserSubjectProgress.objects.create(user=user, subject=subject)

    owner, other = sign_in(users[0].email), sign_in(users[1].email)
    owner_sse, other_sse = SSEListener(dict(owner.cookies)), SSEListener(dict(other.cookies))
    time.sleep(1)
    check("SSE opens for both users", owner_sse.status == 200 and other_sse.status == 200)
    check("SSE without a cookie returns 401", httpx.get(f"{BASE}/api/events", timeout=10).status_code == 401)

    def generate(topic, client=owner):
        return client.post(f"/api/learning/topics/{topic.id}/quiz/generate", json={})

    def ai_calls():
        return int(r.get("live:ai_calls") or 0)

    # async generate + event
    started = time.time()
    resp = generate(topics[0])
    took = time.time() - started
    data = resp.json()["data"]
    check("generate returns 202 GENERATING in under 1s", resp.status_code == 202 and data["status"] == "GENERATING" and took < 1, f"{resp.status_code} {took:.2f}s")
    check("no questions yet, resumed=false", data["questions"] == [] and data["resumed"] is False)
    attempt_id = data["id"]
    event = owner_sse.wait_for("quiz.status", lambda p: p["attempt_id"] == attempt_id)
    check("owner gets quiz.status READY", event is not None and event["status"] == "READY", event)
    check("event payload shape", event is not None and set(event) == {"subject_id", "topic_id", "attempt_id", "quiz_type", "status"})
    time.sleep(1)
    check("other user gets no quiz.status", not any(name == "quiz.status" for name, _ in other_sse.events))
    got = owner.get(f"/api/learning/topics/{topics[0].id}/quiz/attempts/{attempt_id}").json()["data"]
    check("attempt is READY, 5 questions, answers hidden", got["status"] == "READY" and len(got["questions"]) == 5 and all(set(q) == {"question", "options"} for q in got["questions"]))
    check("AI called once", ai_calls() == 1, ai_calls())
    again = generate(topics[0]).json()["data"]
    check("generate after READY returns the same quiz, resumed=true, no AI call", again["id"] == attempt_id and again["resumed"] is True and ai_calls() == 1)
    submitted = owner.post(f"/api/learning/topics/{topics[0].id}/quiz/submit", json={"attempt_id": attempt_id, "answers": [0, 0, 0, 1, 1]}).json()["data"]
    check("submit scores 3/5 with results", submitted["correct_count"] == 3 and submitted["results"][0]["correct_index"] == 0)

    # concurrent generate -> one attempt, one AI call
    r.set("live:ai_delay", 3)
    before = ai_calls()
    barrier = threading.Barrier(8)

    def fire(_):
        client = httpx.Client(base_url=BASE, cookies=dict(owner.cookies), timeout=60)
        barrier.wait()
        body = generate(topics[1], client).json()["data"]
        return body["id"], body["resumed"]

    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(fire, range(8)))
    check("8 simultaneous generates share one attempt", len({o[0] for o in out}) == 1, out)
    check("exactly one request started the generation", sum(1 for o in out if o[1] is False) == 1, out)
    owner_sse.wait_for("quiz.status", lambda p: p["attempt_id"] == out[0][0])
    time.sleep(1)
    check("AI called once for the 8 requests", ai_calls() - before == 1, ai_calls() - before)
    check("one DB row", QuizAttempt.objects.filter(user=users[0], topic=topics[1]).count() == 1)

    # failure -> FAILED -> retry
    r.set("live:ai_delay", 0.5)
    r.set("live:ai_fail", 1)
    before = ai_calls()
    failed_id = generate(topics[2]).json()["data"]["id"]
    event = owner_sse.wait_for("quiz.status", lambda p: p["attempt_id"] == failed_id and p["status"] == "FAILED", timeout=90)
    check("provider failure ends as a FAILED event", event is not None, event)
    check("AI tried 3 times (1 + 2 retries)", ai_calls() - before == 3, ai_calls() - before)
    r.delete("live:ai_fail")
    retry = generate(topics[2]).json()["data"]
    check("retry reuses the row, resumed=false", retry["id"] == failed_id and retry["resumed"] is False and retry["status"] == "GENERATING" and retry["attempt_number"] == 1, retry)
    event = owner_sse.wait_for("quiz.status", lambda p: p["attempt_id"] == failed_id and p["status"] == "READY", timeout=30)
    check("retry ends READY", event is not None, event)

    # stuck attempt -> cleanup task
    stuck = QuizAttempt.objects.create(
        user=users[0], topic=topics[3], quiz_type="NORMAL", attempt_number=1, questions=[],
        status="GENERATING", generation_started_at=timezone.now() - timedelta(minutes=20),
    )
    cleanup_stuck_generations.delay()
    event = owner_sse.wait_for("quiz.status", lambda p: p["attempt_id"] == stuck.id)
    check("cleanup fails the stuck quiz and notifies", event is not None and event["status"] == "FAILED", event)
    retried = generate(topics[3]).json()["data"]
    check("stuck attempt can be retried", retried["id"] == stuck.id and retried["resumed"] is False)

    print(f"\n{sum(outcomes)}/{len(outcomes)} checks passed")
    sys.exit(0 if all(outcomes) else 1)


if __name__ == "__main__":
    modes = {"worker": run_worker, "check": run_checks}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        sys.exit(__doc__)
    modes[sys.argv[1]]()
