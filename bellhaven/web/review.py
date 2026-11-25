"""Approve or reject one proposal and record the outcome. apply.apply_proposal does the CRM writing."""
from dataclasses import dataclass

from .. import store
from ..apply import AlreadyHandled, StaleProposal, apply_proposal

APPLIED = "applied"                         # written; the CRM's response is stored on the proposal
ALREADY_APPLIED = "already_applied"         # the CRM already had the change (an earlier response was lost)
STALE = "stale"                             # the record changed since the proposal was made: nothing written
FAILED = "failed"                           # the CRM refused or the call failed: the error is stored
ALREADY_HANDLED = "already_handled"         # decided already, or another click is applying it: nothing done
REJECTED = "rejected"
NEEDS_CONFIRMATION = "needs_confirmation"   # live sandbox, not confirmed yet: nothing written
UNRECORDED = "unrecorded"                   # written, but saving the decision failed


@dataclass(frozen=True)
class Outcome:
    kind: str           # one of the constants above
    key: str
    message: str = ""
    result: dict | None = None


def _record_failure(conn, key: str, comment: str | None, result: dict) -> None:
    try:
        store.set_status(conn, key, "failed", comment, result)
    except Exception:  # the outcome still reaches the reviewer; the API log has any write attempt
        pass


def approve(conn, client, key: str, comment: str | None = None, confirmed: bool = False,
            live: bool = False) -> Outcome:
    """Write one proposal to the CRM. Never raises: every failure becomes an outcome, and is stored on the proposal.

    On the live sandbox nothing is written until the reviewer has confirmed (confirmed=True).
    """
    comment = comment or None
    row = store.get(conn, key)
    if row is None:
        return Outcome(ALREADY_HANDLED, key, "There is no such proposal.")
    if row["status"] not in store.UNDECIDED:
        return Outcome(ALREADY_HANDLED, key, f"'{row['title']}' is {row['status']}, not pending or failed.")
    if live and not confirmed:
        return Outcome(NEEDS_CONFIRMATION, key)
    try:
        result = apply_proposal(conn, client, row)  # it claims the proposal first, so a second click can't write twice
    except AlreadyHandled as e:
        return Outcome(ALREADY_HANDLED, key, str(e))
    except StaleProposal as e:
        _record_failure(conn, key, comment, {"stale": str(e)})
        return Outcome(STALE, key, str(e))
    except Exception as e:  # any API or unexpected error: keep it on the proposal for the reviewer
        message = f"{type(e).__name__}: {e}"
        _record_failure(conn, key, comment, {"error": message})
        return Outcome(FAILED, key, message)
    try:
        store.set_status(conn, key, "applied", comment, result)
    except Exception as e:  # the CRM write succeeded; only recording it failed
        return Outcome(UNRECORDED, key, f"{type(e).__name__}: {e}", result)
    return Outcome(ALREADY_APPLIED if result.get("already_applied") else APPLIED, key, "", result)


def reject(conn, key: str, comment: str | None = None) -> Outcome:
    """Reject a pending or failed proposal. It is never proposed again."""
    if store.reject(conn, key, comment or None):
        return Outcome(REJECTED, key)
    row = store.get(conn, key)
    status = row["status"] if row else "missing"
    return Outcome(ALREADY_HANDLED, key, f"Not rejected: it is {status} now.")
