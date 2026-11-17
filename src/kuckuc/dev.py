"""Local preview with nothing real behind it.

    python -m kuckuc.dev          then open http://localhost:8080

The phone is pretend: add ``?fake=ok``, ``?fake=silent`` or ``?fake=unwell`` to the address to
choose how it answers. The verification code is always 123456, sign-in accepts any email, and
the invite code comes from KUCKUC_INVITE_CODES. The clock runs faster than real time so the
waits between calls are short. Nothing is dialled and nothing leaves this computer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from kuckuc.local import build_local
from kuckuc.models import Answer, Question

SPEED = 5  # simulated seconds per real second
PICK_UP_AFTER_S = 3  # real seconds a pretend phone rings before it is answered
ANSWERS = {
    "ok": [Answer.OK],
    "silent": [Answer.YES, Answer.OK],  # the person never picks up; the neighbour goes and finds them well
    "unwell": [Answer.NEEDS_HELP, Answer.YES, Answer.NEEDS_HELP],
}
# What a pretend person says when nothing was prepared for this call (the family page's calls).
SPOKEN = {
    "ok": {Question.WELLBEING: Answer.OK, Question.YES_NO: Answer.YES, Question.NEIGHBOR_REPORT: Answer.OK},
    "unwell": {
        Question.WELLBEING: Answer.NEEDS_HELP,
        Question.YES_NO: Answer.YES,
        Question.NEIGHBOR_REPORT: Answer.NEEDS_HELP,
    },
}


def create_dev_app(monotonic: Callable[[], float] = time.monotonic, now: datetime | None = None) -> FastAPI:
    """``now`` is where the pretend clock starts; by default, the real time."""
    system = build_local(now)
    app = system.web
    lock = threading.Lock()
    state = {"mode": "ok", "last": monotonic(), "ringing_since": None}

    def start_run() -> None:
        with lock:
            system.deps.interpreter.answers = list(ANSWERS[state["mode"]])
            system.deps.telephony.script_status = ["no-answer"] * 3 if state["mode"] == "silent" else []
            state["last"] = monotonic()

    def pick_up() -> None:
        call = system.deps.store.get_call(system.ringing_call_id())
        if not system.deps.interpreter.answers and call is not None:
            spoken = SPOKEN["unwell" if state["mode"] == "unwell" else "ok"]
            system.deps.interpreter.answers = [spoken[call.question]]
        system.answer_next_call()

    def tick() -> None:
        """Let time pass, run whatever became due, and answer a phone that has rung long enough."""
        with lock:
            now = monotonic()
            system.run_tasks(advance_s=int((now - state["last"]) * SPEED))
            state["last"] = now
            if system.ringing_call_id() is None:
                state["ringing_since"] = None
            elif state["ringing_since"] is None:
                state["ringing_since"] = now
            elif now - state["ringing_since"] >= PICK_UP_AFTER_S:
                pick_up()
                state["ringing_since"] = None

    @app.middleware("http")
    async def pretend_world(request: Request, call_next):
        path = request.url.path
        if path in ("/", "/family") and request.query_params.get("fake") in ANSWERS:
            state["mode"] = request.query_params["fake"]
        elif path == "/api/demo/run":
            await run_in_threadpool(start_run)
        elif path.startswith(("/api/demo/timeline/", "/api/family/")):
            # Both pages keep asking while something is under way; each question moves the pretend world on.
            await run_in_threadpool(tick)
        return await call_next(request)

    return app


def main() -> None:
    import uvicorn

    print(__doc__)
    uvicorn.run(create_dev_app(), host="127.0.0.1", port=8080, log_level="warning")


if __name__ == "__main__":
    main()
