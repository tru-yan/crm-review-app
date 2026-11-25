"""Review queue for Bellhaven CRM proposals.  Run:  streamlit run review_app.py"""
import json

import streamlit as st

from bellhaven import store
from bellhaven.apply import AlreadyHandled, StaleProposal, apply_proposal
from bellhaven.config import API_BASE, DB_PATH, USING_MOCK
from bellhaven.crm import CrmClient

st.set_page_config(page_title="Bellhaven CRM review", layout="wide")

KIND_LABELS = {
    "update": "Update fields", "chow": "Change of ownership (new account)", "duplicate": "Duplicate",
    "create": "New account", "not_on_website": "Not on website",
}
STATUSES = ["pending", "applied", "rejected", "failed", "applying", "superseded"]


@st.cache_resource
def resources():
    return store.connect(DB_PATH, crm_base=API_BASE), CrmClient()


def flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def decide(conn, client, row, approve: bool, comment: str) -> None:
    if not approve:
        if store.reject(conn, row["key"], comment):
            st.session_state.flash = ("info", f"Rejected: {row['title']}")
        else:
            now = store.get(conn, row["key"])["status"]
            st.session_state.flash = ("warning", f"Not rejected: '{row['title']}' is {now} now.")
        return
    try:
        result = apply_proposal(conn, client, row)  # it claims the proposal first, so a second click can't write twice
    except AlreadyHandled as e:
        st.session_state.flash = ("warning", str(e))
        return
    except StaleProposal as e:
        store.set_status(conn, row["key"], "failed", comment, {"stale": str(e)})
        st.session_state.flash = ("error", f"Not written: the CRM record changed since this proposal was made. "
                                           f"Re-run the pipeline. {e}")
        return
    except Exception as e:  # show any API error to the reviewer and keep it on the proposal
        store.set_status(conn, row["key"], "failed", comment, {"error": str(e)})
        st.session_state.flash = ("error", f"Write failed: {e}")
        return
    try:
        store.set_status(conn, row["key"], "applied", comment, result)
    except Exception as e:  # the CRM write succeeded; only recording it as applied failed
        st.session_state.flash = ("error", f"Written to the CRM ({result}) but not recorded as applied: {e}. "
                                           "The API log and change log have the write.")
        return
    st.session_state.flash = ("success", f"Written to CRM: {row['title']} -> {result}")


def show(conn, client, row) -> None:
    ev, changes, before = json.loads(row["evidence"]), json.loads(row["changes"]), json.loads(row["before"])
    header = f"{KIND_LABELS.get(row['kind'], row['kind'])} · {row['title']}"
    if row["confidence"] == "low":
        header += " · LOW CONFIDENCE"
    with st.expander(header):
        for reason in ev.get("reasons", []):
            st.markdown(f"- {reason}")
        sop = ev.get("sop")
        if sop and (row["kind"] == "chow" or "parent_id" in changes):
            verdict = "keep old account, use CHOW" if sop["preserve_old_account"] else "re-parenting allowed"
            st.info(f"Billing SOP: lifetime revenue {sop['lifetime_revenue']}, "
                    f"outstanding AR {sop['outstanding_ar']} -> {verdict}")
        st.markdown("**Proposed change**")
        current = flat(before)
        st.table([{"field": f, "current": str(current.get(f, "")), "proposed": str(v)} for f, v in flat(changes).items()])
        left, right = st.columns(2)
        left.markdown("**Website**")
        left.json(ev.get("website") or {"-": "not on the website"}, expanded=False)
        right.markdown("**CRM now**")
        right.json(ev.get("crm") or {"-": "no CRM account"}, expanded=False)
        if "signals" in ev:
            st.caption(f"Match score {ev.get('score')}: " + ", ".join(f"{k} +{v}" for k, v in ev["signals"].items()))
        if row["status"] == "failed" and row["result"]:
            st.error(f"Last attempt failed at {row['decided_at']}: {row['result']}")
        if row["status"] == "applying":
            st.warning("Being applied right now, or an earlier apply was interrupted. If it stays here, check this "
                       "account's change log; a pipeline run started 10 or more minutes later puts it back in "
                       "the queue if the change didn't land.")
        if row["status"] in ("pending", "failed"):
            comment = st.text_input("Reviewer comment", key=f"comment-{row['key']}")
            approve_col, reject_col = st.columns(2)
            if approve_col.button("Approve and write to CRM", key=f"approve-{row['key']}", type="primary"):
                decide(conn, client, row, True, comment)
                st.rerun()
            if reject_col.button("Reject", key=f"reject-{row['key']}"):
                decide(conn, client, row, False, comment)
                st.rerun()
        else:
            st.caption(f"{row['status']} at {row['decided_at']}" + (f" · {row['comment']}" if row["comment"] else ""))
            if row["result"]:
                st.json(json.loads(row["result"]), expanded=False)
            changed = store.change_history(conn, proposal_key=row["key"])
            if changed:
                st.markdown("**Change log**")
                st.table([{k: h[k] for k in ("account_id", "action", "field", "old_value", "new_value", "changed_at")}
                          for h in changed])


conn, client = resources()
counts = store.counts(conn)
status = st.sidebar.radio("Status", STATUSES, format_func=lambda s: f"{s} ({counts.get(s, 0)})")
kinds = st.sidebar.multiselect("Type", list(KIND_LABELS), default=list(KIND_LABELS), format_func=KIND_LABELS.get)
st.title("Bellhaven CRM review queue")
if USING_MOCK:
    st.success(f"MOCK CRM ({API_BASE}): nothing here is graded. Database: {DB_PATH}")
else:
    st.error(f"LIVE SANDBOX: approving writes to the graded CRM. Database: {DB_PATH}")
if "flash" in st.session_state:
    level, message = st.session_state.pop("flash")
    getattr(st, level)(message)
rows = [r for r in store.list_proposals(conn, status) if r["kind"] in kinds]
st.write(f"{len(rows)} {status} proposals")
for row in rows:
    show(conn, client, row)
