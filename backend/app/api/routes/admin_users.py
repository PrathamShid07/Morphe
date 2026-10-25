"""Administrator view of accounts and their activity.

What an administrator can see: account metadata, counts, and the audit trail (including the text of questions, as disclosed in the privacy
notice). What they cannot see by default: the content of anyone's personal memories or the text of retrieved results. Reading a user's memory
content is a separate, explicit action (`/admin/users/{id}/memories`) that needs a stated reason and is itself written to the audit log.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ...accounts.users import DISABLED, UserStore, public_user
from ...auth.history import QueryHistory
from ...auth.service import Identity
from ...common import ConflictError, NotFoundError, ValidationFailure
from ...container import AppContext
from ..deps import get_auth, get_ctx, require_admin

router = APIRouter(prefix="/admin", tags=["admin (users & activity)"], dependencies=[Depends(require_admin)])


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"]
    reason: str | None = Field(None, max_length=200)


def users_dep(request: Request) -> UserStore:
    return get_auth(request).users


def _user(users: UserStore, user_id: str) -> dict[str, Any]:
    return users.get(user_id)


def _decorate(ctx: AppContext, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = ctx.audit.user_summaries([d["user_id"] for d in docs])
    out = []
    for d in docs:
        u = public_user(d)
        mem = ctx.user_data.stats(d["user_id"])
        s = summaries[d["user_id"]]
        out.append({**u, "stats": {"private_memories": mem["memories"], "personal_modules": mem["modules"], "sources": mem["sources"],
                                   "queries_submitted": s["queries_submitted"], "queries_succeeded": s["queries_succeeded"], "queries_failed": s["queries_failed"],
                                   "memory_saves": s["memory_saves"], "memory_save_failures": s["memory_save_failures"], "memory_deletions": s["memory_deletions"],
                                   "failed_operations": s["failed_operations"], "failed_logins": s["failed_logins"]},
                    **({"disabled_at": d.get("disabled_at"), "disabled_reason": d.get("disabled_reason")} if d.get("status") == DISABLED else {})})
    return out


@router.get("/users")
def list_users(search: str | None = Query(None, max_length=100), status: Literal["active", "disabled"] | None = None,
               active_after: str | None = Query(None, description="ISO timestamp: last activity at or after"), active_before: str | None = None,
               limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), ctx: AppContext = Depends(get_ctx),
               users: UserStore = Depends(users_dep)):
    flt: dict[str, Any] = {}
    if search:
        rx = {"$regex": re.escape(search.strip()), "$options": "i"}
        flt["$or"] = [{"full_name": rx}, {"email": rx}, {"user_id": rx}]
    if status:
        flt["status"] = status
    if active_after or active_before:
        flt["last_activity_at"] = {**({"$gte": active_after} if active_after else {}), **({"$lte": active_before} if active_before else {})}
    docs, total = users.page(flt, limit, offset)
    return {"items": _decorate(ctx, docs), "total": total, "limit": limit, "offset": offset}


@router.get("/users/{user_id}")
def user_detail(user_id: str, ctx: AppContext = Depends(get_ctx), users: UserStore = Depends(users_dep)):
    doc = _user(users, user_id)
    out = _decorate(ctx, [doc])[0]
    out["recent_activity"] = ctx.audit.list(user_id=user_id, limit=10)["items"]
    return out


@router.get("/users/{user_id}/activity")
def user_activity(user_id: str, action: str | None = None, status: Literal["success", "failure", "denied"] | None = None,
                  since: str | None = None, until: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  ctx: AppContext = Depends(get_ctx), users: UserStore = Depends(users_dep)):
    _user(users, user_id)
    return ctx.audit.list(user_id=user_id, action=action, status=status, since=since, until=until, limit=limit, offset=offset)


@router.get("/users/{user_id}/queries")
def user_queries(user_id: str, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), ctx: AppContext = Depends(get_ctx),
                 users: UserStore = Depends(users_dep)):
    """The questions a user asked (retained under the disclosed audit policy), without the text of what was retrieved."""
    _user(users, user_id)
    items, total = QueryHistory(ctx.mongo, ctx.settings.user_history_limit).admin_list(user_id, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/users/{user_id}/queries/{query_id}")
def user_query(user_id: str, query_id: str, ctx: AppContext = Depends(get_ctx), users: UserStore = Depends(users_dep)):
    _user(users, user_id)
    return QueryHistory(ctx.mongo, ctx.settings.user_history_limit).admin_get(user_id, query_id)


@router.post("/users/{user_id}/status")
def set_status(user_id: str, body: StatusRequest, request: Request, ident: Identity = Depends(require_admin), ctx: AppContext = Depends(get_ctx),
               users: UserStore = Depends(users_dep)):
    """Disable or reactivate an ordinary account. Disabling revokes every active session at once; the account can neither sign in nor
    use a session it already holds (the very next request is refused with 403 `account_disabled`)."""
    doc = users.get(user_id)
    if doc.get("status") == body.status:
        raise ConflictError(f"the account is already {body.status}", code="no_change", status_code=409)
    users.set_status(user_id, body.status, ident.username or "admin", body.reason)
    revoked = get_auth(request).revoke_user_sessions(user_id) if body.status == DISABLED else 0
    ctx.audit.record("user.disabled" if body.status == DISABLED else "user.reactivated", actor_role="admin", actor_label=ident.username,
                     subject_user_id=user_id, target_type="account", target_id=user_id, metadata={"reason": body.reason, "sessions_revoked": revoked})
    return _decorate(ctx, [users.get(user_id)])[0]


@router.delete("/users/{user_id}")
def delete_user(user_id: str, request: Request, confirm: str = Query(..., description='Must be "DELETE"'), ident: Identity = Depends(require_admin),
                ctx: AppContext = Depends(get_ctx), users: UserStore = Depends(users_dep)):
    """Permanently delete an account and everything personal it owns (memories, modules, graph, question history, sessions). The audit trail
    is kept, including this deletion. Irreversible, hence the explicit confirmation."""
    if confirm != "DELETE":
        raise ValidationFailure('confirm must equal "DELETE"', code="confirmation_required")
    doc = users.get(user_id)
    revoked = get_auth(request).revoke_user_sessions(user_id)
    ctx.user_vectors.delete_owner(user_id)
    ctx.user_data.delete_owner(user_id)
    history = QueryHistory(ctx.mongo, ctx.settings.user_history_limit).delete_owner(user_id)
    ctx.mongo.col("user_sessions").delete_many({"user_id": user_id})
    users.col.delete_one({"user_id": user_id})
    ctx.audit.record("user.deleted", actor_role="admin", actor_label=ident.username, subject_user_id=user_id, target_type="account", target_id=user_id,
                     metadata={"email": doc.get("email"), "sessions_revoked": revoked, "history_rows_removed": history})
    return {"user_id": user_id, "status": "deleted"}


@router.get("/users/{user_id}/memories")
def user_memories_support_access(user_id: str, reason: str = Query(..., min_length=8, max_length=200, description="Why the content is being opened (recorded)"),
                                 limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0), ident: Identity = Depends(require_admin),
                                 ctx: AppContext = Depends(get_ctx), users: UserStore = Depends(users_dep)):
    """Explicit support/moderation access to a user's memory content. Requires a reason and is always written to the audit log."""
    _user(users, user_id)
    out = ctx.personal.list_memories(user_id, limit=limit, offset=offset)
    ctx.audit.record("admin.user_memories_accessed", actor_role="admin", actor_label=ident.username, subject_user_id=user_id, target_type="account",
                     target_id=user_id, metadata={"reason": reason, "records_returned": len(out["items"]), "total": out["total"]})
    return out


# ------------------------------------------------------------------------------------------------------------------ activity
@router.get("/activity")
def activity(user_id: str | None = None, action: str | None = None, status: Literal["success", "failure", "denied"] | None = None,
             target_type: str | None = None, role: Literal["user", "admin"] | None = None, since: str | None = None, until: str | None = None,
             search: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             ctx: AppContext = Depends(get_ctx)):
    out = ctx.audit.list(user_id=user_id, action=action, status=status, target_type=target_type, role=role, since=since, until=until, search=search,
                         limit=limit, offset=offset)
    ids = sorted({e["subject_user_id"] for e in out["items"] if e.get("subject_user_id")})
    names = {d["user_id"]: {"full_name": d.get("full_name"), "email": d.get("email")} for d in ctx.mongo.col("users").find({"user_id": {"$in": ids}})}
    for e in out["items"]:
        e["user"] = names.get(e.get("subject_user_id"))
    return out


@router.get("/activity/actions")
def activity_actions(ctx: AppContext = Depends(get_ctx)):
    """The distinct actions present in the log (for the filter control)."""
    return {"items": sorted(ctx.audit.col.distinct("action"))}


@router.get("/activity/{event_id}")
def activity_event(event_id: str, ctx: AppContext = Depends(get_ctx)):
    e = ctx.audit.get(event_id)
    if not e:
        raise NotFoundError("event not found", code="event_not_found")
    return e
