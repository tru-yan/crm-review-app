from bellhaven.web import present
from tests.factories import BH, CEDAR
from tests.review_fixtures import by_kind, make_queue


def queue():
    _, conn = make_queue()
    ps = by_kind(conn)
    return ps, present.account_names(list(ps.values()))


def update(changes, before):
    """A parsed update proposal, for the summary rules."""
    return {"key": "k", "kind": "update", "title": "Update X", "account_id": "A1", "status": "pending",
            "changes": changes, "before": before, "evidence": {"reasons": []}, "confidence": "high", "result": None}


def test_summaries_for_every_kind():
    ps, names = queue()
    assert present.summary(ps["update"], names) == "Name → Bellhaven of Chagrin Falls"
    assert present.summary(ps["chow"], names) == "New Bellhaven account; old one kept for billing"
    assert present.summary(ps["create"], names) == "Create under Bellhaven"
    assert present.summary(ps["duplicate"], names) == "Duplicate of Bellhaven of Owosso"
    assert present.summary(ps["not_on_website"], names) == "Status → Needs Review"


def test_a_duplicate_with_the_same_name_as_its_survivor_shows_the_survivor_id():
    ps, names = queue()
    twin = ps["duplicate"] | {"evidence": ps["duplicate"]["evidence"] | {"crm": {"name": "Bellhaven of Owosso"}}}
    assert present.summary(twin, names) == "Duplicate of Bellhaven of Owosso (A3)"


def test_update_summary_shows_short_old_values_the_parent_by_name_and_a_count():
    names = {BH: "Bellhaven Senior Living (Parent Account)"}
    assert present.summary(update({"billing_zip": "45662", "note": "n"}, {"billing_zip": "45626", "note": ""}),
                           names) == "Zip 45626 → 45662"
    many = update({"name": "Bellhaven of Lima", "parent_id": BH, "billing_street": "1 Elm St", "billing_city": "Lima",
                   "note": "n"},
                  {"name": "Lima Care", "parent_id": CEDAR, "billing_street": "PO Box 4", "billing_city": "Lyma",
                   "note": ""})
    assert present.summary(many, names) == "Name → Bellhaven of Lima, Parent → Bellhaven Senior Living +2 more"


def test_change_rows_show_only_what_changes():
    ps, names = queue()
    rows = {r["field"]: r for r in present.change_rows(ps["update"], names)}
    assert set(rows) == {"name", "note"}
    assert (rows["name"]["now"], rows["name"]["after"]) == ("Riverbend Manor", "Bellhaven of Chagrin Falls")
    assert rows["note"]["now"] is None  # empty: the page shows "empty"
    dup = {r["field"]: r for r in present.change_rows(ps["duplicate"], names)}
    assert dup["duplicate_of_account"]["after"] == "Bellhaven of Owosso (A3)"
    assert (dup["status"]["now"], dup["status"]["after"]) == ("Active", "Inactive")
    gone = {r["field"]: r["after"] for r in present.change_rows(ps["not_on_website"], names)}
    assert gone["status"] == "Needs Review"


def test_create_and_chow_rows_list_the_new_account():
    ps, names = queue()
    create = present.change_rows(ps["create"], names)
    assert all(r["new"] for r in create)
    assert {r["field"]: r["after"] for r in create}["parent_id"] == "Bellhaven Senior Living (Parent Account)"
    chow = present.change_rows(ps["chow"], names)
    assert {r["group"] for r in chow} == {"New account", "Old account A2"}
    link = chow[-1]
    assert (link["field"], link["now"], link["after"]) == ("chow_current_account", None, "the new account's id")


def test_an_applied_chow_shows_and_links_the_created_account():
    ps, _ = queue()
    chow = ps["chow"] | {"status": "applied",
                         "result": {"created": "NEW1", "old_account": "A2", "mismatches": {"new_account": {}, "link": {}}}}
    names = present.account_names([chow])
    assert present.change_rows(chow, names)[-1]["after"] == "Bellhaven of Tiffin (NEW1)"
    assert present.linked_account(chow) == "NEW1"
    assert present.linked_account(ps["chow"]) == "A2"
    assert present.linked_account(ps["create"]) is None  # nothing to link to until it is created


def test_side_by_side_flags_the_fields_that_differ():
    ps, names = queue()
    flagged = lambda cells: [c["label"] for c in cells if c["differs"]]
    side = present.side_by_side(ps["update"], names)
    assert flagged(side["website"]) == ["Name"] and flagged(side["crm"]) == ["Name"]
    chow = present.side_by_side(ps["chow"], names)
    assert flagged(chow["crm"]) == ["Parent"]
    ar = {c["label"]: c for c in chow["crm"]}["Outstanding AR"]
    assert (ar["value"], ar["alert"]) == ("$12,400", True)
    assert present.side_by_side(ps["create"], names)["crm"] is None
    assert present.side_by_side(ps["not_on_website"], names)["website"] is None


def test_sop_box_only_when_the_parent_changes():
    ps, _ = queue()
    assert present.sop(ps["chow"])["text"] == ("Lifetime revenue $84,000, outstanding AR $12,400: "
                                               "keep the old account and use a change of ownership")
    assert present.sop(ps["update"]) is None
    assert present.sop(ps["create"]) is None


def test_signals_become_chips():
    ps, _ = queue()
    sig = present.signals(ps["update"])
    assert sig["score"] == 85 and "street +50" in sig["chips"]
    assert present.signals(ps["create"])["score"] is None


def test_queue_order_puts_the_kinds_that_add_an_account_first():
    ps, _ = queue()
    ordered = present.queue_order(list(ps.values()))
    assert [p["kind"] for p in ordered] == ["chow", "create", "duplicate", "not_on_website", "update"]


def test_next_pending_follows_queue_order_and_wraps_round():
    ps, _ = queue()
    rows = list(ps.values())
    key = lambda kind: ps[kind]["key"]
    assert present.next_pending(rows, key("chow")) == key("create")
    assert present.next_pending(rows, key("update")) == key("chow")
    decided = [p | {"status": "applied"} if p["kind"] in ("create", "duplicate") else p for p in rows]
    assert present.next_pending(decided, key("create")) == key("not_on_website")
    assert present.next_pending([ps["update"] | {"status": "applied"}], key("update")) is None
    assert present.position(rows, key("duplicate")) == (3, 5)
    assert present.position(decided, key("create")) is None


def test_queue_search_matches_name_or_city():
    ps, _ = queue()
    assert present.matches(ps["create"], "batavia") and present.matches(ps["update"], "TESTVILLE")
    assert not present.matches(ps["update"], "owosso")


def test_banners_are_read_from_the_stored_result():
    ps, names = queue()
    applied = ps["update"] | {"status": "applied", "result": {"updated": "A1", "mismatches": {}}}
    b = present.banner(applied, "applied", names)
    assert (b["tone"], b["title"], b["account_id"]) == ("ok", "Written", "A1")
    assert b["text"] == "Name → Bellhaven of Chagrin Falls. The CRM stored exactly what was sent."
    odd = applied | {"result": {"updated": "A1", "mismatches": {"name": {"sent": "X", "returned": "x"}}}}
    assert present.banner(odd, "applied", names)["details"] == ["Name sent 'X', the CRM stored 'x'"]
    stale = ps["update"] | {"status": "failed", "result": {"stale": "name is now 'Y' (the proposal expected 'Z')"}}
    assert present.banner(stale, "stale", names)["title"] == "Not written"
    failed = ps["chow"] | {"status": "failed", "result": {"error": "CrmError: boom"}}
    assert present.banner(failed, "failed", names)["retry"] is True
    busy = present.banner(ps["update"] | {"status": "applying"}, "already_handled", names)
    assert busy["text"].startswith("It is applying now")
    assert present.banner(ps["update"], "made-up", names) is None


def test_newer_evidence_wins_for_names():
    ps, _ = queue()
    crm = ps["update"]["evidence"]["crm"] | {"account_id": "A3", "name": "Old Owosso Name"}
    old = ps["update"] | {"run_id": 1, "evidence": ps["update"]["evidence"] | {"crm": crm}}
    fresh = ps["duplicate"] | {"run_id": 2}
    assert present.account_names([fresh, old])["A3"] == "Bellhaven of Owosso"
    unnamed = fresh | {"evidence": fresh["evidence"] | {"survivor": {"account_id": "A9", "name": None}}}
    assert "A9" not in present.account_names([unnamed])


def test_old_duplicates_without_a_survivor_fall_back_to_the_id():
    ps, _ = queue()
    old = ps["duplicate"] | {"evidence": {k: v for k, v in ps["duplicate"]["evidence"].items() if k != "survivor"}}
    assert present.summary(old, present.account_names([old])) == "Duplicate of A3"


def test_money_and_unreadable_amounts():
    amounts = (84000, 84000.5, None, "", -500)
    assert [present.money(v) for v in amounts] == ["$84,000", "$84,000.50", "$0", "$0", "-$500"]
    assert present.money("abc") == "abc"
    ps, names = queue()
    crm = ps["chow"]["evidence"]["crm"] | {"outstanding_ar": "abc"}
    odd = ps["chow"] | {"evidence": ps["chow"]["evidence"] | {"crm": crm}}
    ar = {c["label"]: c for c in present.side_by_side(odd, names)["crm"]}["Outstanding AR"]
    assert (ar["value"], ar["alert"]) == ("abc", True)  # unreadable billing data counts as money involved


def test_state_case_is_not_a_difference():
    ps, names = queue()
    crm = ps["update"]["evidence"]["crm"] | {"billing_state": "oh "}
    p = ps["update"] | {"evidence": ps["update"]["evidence"] | {"crm": crm}}
    assert {c["label"]: c["differs"] for c in present.side_by_side(p, names)["crm"]}["City / State / Zip"] is False


def test_sop_box_for_a_direct_reparent():
    p = update({"parent_id": BH, "note": "n"}, {"parent_id": CEDAR, "lifetime_revenue": 47000, "outstanding_ar": 0})
    p["evidence"] = {"reasons": [], "sop": {"lifetime_revenue": 47000, "outstanding_ar": 0,
                                            "preserve_old_account": False}}
    assert present.sop(p)["text"] == "Lifetime revenue $47,000, outstanding AR $0: re-parenting allowed"


def test_chow_mismatches_name_the_account():
    result = {"created": "NEW1", "old_account": "A2",
              "mismatches": {"new_account": {"phone": {"sent": "(614) 555-0100", "returned": "6145550100"}},
                             "link": {"chow_current_account": {"sent": "NEW1", "returned": None}}}}
    assert present.mismatches(result) == ["New account: Phone sent '(614) 555-0100', the CRM stored '6145550100'",
                                          "Old account: CHOW current account sent 'NEW1', the CRM stored None"]


def test_banners_need_the_matching_stored_status():
    ps, names = queue()
    again = ps["update"] | {"status": "applied", "result": {"updated": "A1", "already_applied": True, "mismatches": {}}}
    assert present.banner(again, "already_applied", names)["title"] == "Already written"
    assert present.banner(ps["duplicate"] | {"status": "rejected"}, "rejected", names)["tone"] == "info"
    assert present.banner(ps["update"] | {"status": "applying"}, "unrecorded", names)["title"] == (
        "Written, but not recorded")
    # reopened by a pipeline run since: an old link must not announce it as written or stale
    assert present.banner(ps["update"], "applied", names) is None
    assert present.banner(ps["update"], "stale", names) is None


def test_an_error_banner_shows_whatever_the_status():
    ps, names = queue()
    for status in ("pending", "applying"):
        assert present.banner(ps["update"] | {"status": status}, "error", names)["title"] == "Not saved"
