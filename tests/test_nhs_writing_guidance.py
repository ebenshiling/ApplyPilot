import os

import pytest

from applypilot import nhs_writing, writing_studio
from applypilot.scoring import supporting_statement as statement
import test_native_studios as native

draft_data = native.draft_data


@pytest.fixture
def workspace(tmp_path):
    yield from native.workspace.__wrapped__(tmp_path)


@pytest.fixture
def server(workspace, monkeypatch):
    yield from native.server.__wrapped__(workspace, monkeypatch)


def row(**changes):
    return {"criterion": "Experience using SQL to check reporting data", "priority": "essential", "category": "experience",
            "basis": "transferable", "source_type": "academic_project", "evidence": "Built SQL reports in a university project",
            "action": "Checked joins and duplicate records", "result": "Documented the corrected query", "confirmed": True, **changes}


def test_only_confirmed_evidence_reaches_model_as_candidate_facts():
    plan = nhs_writing.validated_plan({"criteria_evidence": [row(), row(criterion="NHS work experience", evidence="UNCONFIRMED CLAIM", confirmed=False)]}, "statement")
    facts = nhs_writing.confirmed_facts(plan)
    assert "academic project" in facts and "university project" in facts
    assert "UNCONFIRMED CLAIM" not in facts
    assert "UNCONFIRMED CLAIM" not in nhs_writing.prompt_context(plan)
    assert nhs_writing.review(plan)["essential_without_confirmed_example"] == ["NHS work experience"]


def test_field_and_priority_filter_do_not_blend_application_boxes():
    plan = nhs_writing.validated_plan({"application_field": "qualifications", "priority_filter": "desirable", "max_characters": 5000,
        "criteria_evidence": [row(category="qualifications", priority="essential"), row(category="qualifications", priority="desirable", criterion="Statistics coursework"), row()]}, "statement")
    assert [r["criterion"] for r in nhs_writing.selected_rows(plan)] == ["Statistics coursework"]
    assert nhs_writing.review(plan, "x"*5001)["within_character_limit"] is False


@pytest.mark.parametrize("kind", ["statement", "cover"])
def test_both_studios_enforce_actual_character_limits(workspace, kind):
    root, conn = workspace
    source = writing_studio.context(conn, root, draft_data(kind=kind, max_characters=100, person_spec="Essential\nSQL reporting experience", criteria_evidence=[row()]))
    check = writing_studio.validate("Verified reporting experience. "*20, source)
    assert not check["passed"]
    assert any("characters" in error for error in check["errors"])
    assert source["nhs_plan"]["person_spec"].startswith("Essential")
    assert "USER-CONFIRMED academic project" in source["facts"]


@pytest.mark.parametrize("kind", ["statement", "cover"])
def test_employer_ai_rule_blocks_generation_but_keeps_manual_drafts(workspace, kind):
    root, conn = workspace
    data = draft_data(kind=kind, employer_ai_policy="not_allowed")
    with pytest.raises(ValueError, match="not allowing AI"):
        writing_studio.generate(conn, root, data)
    assert conn.execute("SELECT COUNT(*) FROM cc_writing_requests").fetchone()[0] == 0
    assert writing_studio.save(conn, root, data)["document"]["id"]


def test_negated_cv_phrase_is_not_proof_of_nhs_employment():
    claims = statement._find_unsupported_claims("I worked for the NHS and supported hospital systems.", "No NHS experience. I would learn hospital systems.")
    assert "direct NHS or Health Board experience" in claims
    assert not statement._find_unsupported_claims("I worked for the NHS.", "I worked for the NHS as an analyst.")


def test_prompt_uses_exact_supplied_courses_not_hardcoded_full_certificates():
    plan = nhs_writing.validated_plan({"person_spec": "Essential: statistical knowledge", "max_characters": 5000}, "statement")
    messages = statement._build_prompt(variant={"name": "criteria-led", "guidance": "Evidence"}, resume_text="The Power of Statistics | Google | 2026",
                                      job={"title": "Data Analyst", "company": "Example Trust", "full_description": "SQL reporting", "_nhs_plan": plan}, criteria=[], profile={})
    text = messages[1]["content"]
    assert "The Power of Statistics" in text
    assert "Google Advanced Data Analytics Professional Certificate" not in text
    assert "Character cap: 5000" in text
    assert "PERSON SPECIFICATION:" in text


def test_nhs_plan_survives_document_version_reload(workspace):
    root, conn = workspace
    saved = writing_studio.save(conn, root, draft_data(kind="statement", criteria_evidence=[row()], application_field="experience", priority_filter="essential", max_characters=5000))
    loaded = writing_studio.document(conn, root, saved["document"]["id"])
    assert loaded["source"]["nhs_plan"]["criteria_evidence"][0]["confirmed"]
    assert "USER-CONFIRMED" not in loaded["source"]["facts"]  # no duplicated planner facts in the editable input


def test_sidebar_persistence_live_preview_and_both_planners(server):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Enable browser checks")
    from playwright.sync_api import sync_playwright
    base, root, _ = server
    from applypilot.setup_workspace import write_searches_yaml
    write_searches_yaml(root, 'queries:\n- query: Data Analyst\n  tier: 1\nlocations:\n- location: London\nboards:\n- indeed\nsmart_sites:\n- NHS Jobs\n')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base+"/workspace#pipeline")
        page.locator("#native-queries").wait_for()
        page.locator("#sidebar-toggle").click()
        assert page.locator("#sidebar-toggle").get_attribute("aria-expanded") == "false"
        assert page.locator(".rail").bounding_box()["width"] == 80
        page.get_by_role("button", name="Settings", exact=True).focus()
        page.reload()
        page.locator("#native-queries").wait_for()
        assert page.locator(".rail").bounding_box()["width"] == 80
        page.locator("#native-queries").fill("Data Analyst\nApplication Support Analyst")
        page.locator("#native-board-budget").evaluate("el=>{el.value='1';el.dispatchEvent(new Event('input',{bubbles:true}));}")
        page.wait_for_function("document.querySelector('#native-preview-content .pipeline-numbers strong')?.textContent==='1'")
        page.locator('[data-workflow="prepare"]').click()
        page.wait_for_function("document.querySelector('#native-preview-content .pipeline-numbers strong')?.textContent==='0'")
        for kind in ("statement", "cover"):
            page.locator(f'.rail [data-view="{kind}"]').click()
            page.locator("#nhs-planner").wait_for(state="attached")
            page.locator("#nhs-planner").evaluate("el=>el.open=true")
            page.locator("#nhs-add-criterion").click()
            page.locator('[data-nhs-key="criterion"]').fill("SQL reporting experience")
            page.locator('[data-nhs-key="evidence"]').fill("Built SQL reports as part of a university project")
            page.locator('[data-nhs-key="basis"]').select_option("transferable")
            page.locator('[data-nhs-key="source_type"]').select_option("academic_project")
            page.locator('[data-nhs-key="confirmed"]').check()
            page.locator("#nhs-check-plan").click()
            page.wait_for_function("document.querySelector('#nhs-plan-review')?.textContent.includes('1 criteria')")
        page.set_viewport_size({"width":390,"height":844})
        page.locator("#sidebar-toggle").click()
        assert page.locator(".rail nav").is_visible()
        page.locator("#sidebar-toggle").click()
        assert page.locator(".rail nav").is_hidden()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        assert not errors
        browser.close()
