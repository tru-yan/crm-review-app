"""Approve or reject one proposal and record the outcome. apply.apply_proposal does the CRM writing."""
import logging
from dataclasses import dataclass

from .. import store
from ..apply import AlreadyHandled, StaleProposal, apply_proposal

log = logging.getLogger(__name__)

APPLIED = "applied"                         # written; the CRM's response is stored on the proposal
ALREADY_APPLIED = "already_applied"         # the CRM already had the change (an earlier response was lost)
STALE = "stale"                             # the record changed since the proposal was made: nothing written
FAILED = "failed"                           # the CRM refused or the call failed: the error is stored
ALREADY_HANDLED = "already_handled"         # decided already, or another click is applying it: nothing done
REJECTED = "rejected"
NEEDS_CONFIRMATION = "needs_confirmation"   # not confirmed yet: nothing written
UNRECORDED = "unrecorded"                   # written, but saving the decision failed
ERROR = "error"                             # the database failed before or while saving: see the server output


@dataclass(frozen=True)
class Outcome:
    kind: str           # one of the constants above
    key: str
    message: str = ""
    result: dict | None = None


def _record_failure(conn, key: str, comment: str | None, result: dict) -> bool:
    """Store a failed attempt on the proposal. False if even that failed: the status then stays 'applying'."""
    try:
        store.set_status(conn, key, "failed", comment, result)
        return True
    except Exception:
        log.exception("could not record the failed attempt on proposal %s", key)
        return False


def approve(conn, client, key: str, comment: str | None = None, *, confirmed: bool = False) -> Outcome:
    """Write one proposal to the graded sandbox, once the reviewer has confirmed (confirmed=True, keyword-only).
    Never raises: every failure becomes an outcome, and is stored on the proposal when the database allows."""
    comment = comment or None
    try:
        row = store.get(conn, key)
    except Exception as e:
        log.exception("could not read proposal %s", key)
        return Outcome(ERROR, key, f"{type(e).__name__}: {e}")
    if row is None:
        return Outcome(ALREADY_HANDLED, key, "There is no such proposal.")
    if row["status"] not in store.UNDECIDED:
        return Outcome(ALREADY_HANDLED, key, f"'{row['title']}' is {row['status']}, not pending or failed.")
    if not confirmed:
        return Outcome(NEEDS_CONFIRMATION, key)
    try:
        result = apply_proposal(conn, client, row)  # it claims the proposal first, so a second click can't write twice
    except AlreadyHandled as e:
        return Outcome(ALREADY_HANDLED, key, str(e))
    except StaleProposal as e:
        recorded = _record_failure(conn, key, comment, {"stale": str(e)})
        return Outcome(STALE if recorded else ERROR, key, str(e))
    except Exception as e:  # any API or unexpected error: keep it on the proposal for the reviewer
        message = f"{type(e).__name__}: {e}"
        log.warning("approving proposal %s failed: %s", key, message)
        recorded = _record_failure(conn, key, comment, {"error": message})
        return Outcome(FAILED if recorded else ERROR, key, message)
    try:
        store.set_status(conn, key, "applied", comment, result)
    except Exception as e:  # the CRM write succeeded; only recording it failed
        log.exception("proposal %s was written to the CRM but not recorded as applied; result: %s", key, result)
        return Outcome(UNRECORDED, key, f"{type(e).__name__}: {e}", result)
    return Outcome(ALREADY_APPLIED if result.get("already_applied") else APPLIED, key, "", result)


def reject(conn, key: str, comment: str | None = None) -> Outcome:
    """Reject a pending or failed proposal. It is never proposed again. Never raises."""
    try:
        if store.reject(conn, key, comment or None):
            return Outcome(REJECTED, key)
        row = store.get(conn, key)
    except Exception as e:
        log.exception("could not reject proposal %s", key)
        return Outcome(ERROR, key, f"{type(e).__name__}: {e}")
    status = row["status"] if row else "missing"
    return Outcome(ALREADY_HANDLED, key, f"Not rejected: it is {status} now.")
