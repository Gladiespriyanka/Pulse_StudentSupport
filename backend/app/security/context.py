"""Per-request context (client ip, request id) available to audit logging without threading it through every call."""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass
class Ctx:
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None


_ctx: contextvars.ContextVar[Ctx] = contextvars.ContextVar("pulse_ctx", default=Ctx())


def set_ctx(ctx: Ctx):
    return _ctx.set(ctx)


def get_ctx() -> Ctx:
    return _ctx.get()
