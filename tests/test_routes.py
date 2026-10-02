"""Every page renders, for the right people, with the right structure — in both chapters.

These run against a hermetic in-memory book (tests/webfix.py), so they catch the bugs the unit tests can't:
a template that stopped compiling, a handler the JS lost, a column that no longer lines up, a pay button
on a month that shouldn't have one, a rep who can see another rep's invoice."""
import glob
import re
import shutil
import subprocess
import pytest

from webfix import build, login, teardown

MANAGER_PAGES = ["/", "/?m=2026-08", "/?m=2026-09", "/?m=2026-09&open=An_Cao", "/accounts",
                 "/accounts?view=Team 1", "/underperformers", "/quiet", "/acquisitions", "/constrained",
                 "/upload", "/settings", "/logins", "/account", "/guide", "/me/guide",
                 "/pay/An Cao", "/pay/An Cao?page_no=1", "/invoice/INV00013", "/team/Team 1"]
REP_PAGES = ["/me", "/me?lang=en", "/me/watch", "/me/quiet", "/me/guide", "/account"]


@pytest.fixture(params=[False, True], ids=["contribution-only", "growth-active"])
def web(request):
    client, Session = build(growth_live=request.param)
    yield client, Session, request.param
    teardown()


def _pay_table(html):
    """(header cells, first rep row cells, footer cells) of the pay table."""
    pay = html[html.index("<h2>Pay —"):]
    head = re.search(r"<thead>(.*?)</thead>", pay, re.S).group(1)
    row = re.search(r'<tr class="rep[^>]*>(.*?)</tr>', pay, re.S).group(1)
    foot = re.search(r"<tfoot>(.*?)</tfoot>", pay, re.S).group(1)
    return (len(re.findall(r"<th", head)), len(re.findall(r"<td", row)), len(re.findall(r"<th", foot)),
            [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)])


def test_every_manager_page_renders(web):
    client, _, _ = web
    login(client)
    for path in MANAGER_PAGES:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "Traceback" not in r.text


def test_every_rep_page_renders_and_manager_pages_are_closed(web):
    client, _, _ = web
    login(client, "an")
    for path in REP_PAGES:
        assert client.get(path, follow_redirects=True).status_code == 200, path
    for path in ["/", "/accounts", "/logins", "/settings", "/pay/An Cao", "/upload"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, f"rep reached {path}"


def test_pay_table_columns_line_up_in_both_months(web):
    client, _, growth = web
    login(client)
    for m, has_pay in (("2026-08", False), ("2026-09", True)):
        th, td, tf, labels = _pay_table(client.get(f"/?m={m}").text)
        assert th == td == tf, f"{m}: head {th} row {td} foot {tf}"
        assert "Growth share" in labels and "New accounts" in labels      # always present, even before Oct 1
        assert ("Pay now" in labels) == has_pay                           # only the latest month pays


def test_past_month_has_no_pay_button_and_latest_month_does(web):
    client, _, _ = web
    login(client)
    assert 'class="paybtn"' not in client.get("/?m=2026-08").text
    assert 'class="paybtn"' in client.get("/?m=2026-09").text


def test_open_param_renders_the_row_expanded(web):
    client, _, _ = web
    login(client)
    html = client.get("/?m=2026-09&open=An_Cao").text
    assert re.search(r'id="r-An_Cao" >', html)                 # not hidden
    assert re.search(r'id="r-Garmi_Mei" hidden>', html)


def test_every_handler_the_pages_call_is_defined(web):
    client, _, _ = web
    login(client)
    for path in ["/?m=2026-09&open=An_Cao", "/pay/An Cao", "/accounts", "/upload"]:
        html = client.get(path).text
        js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
        called = set(re.findall(r'on\w+="(\w+)\(', html))
        missing = [fn for fn in called if f"function {fn}(" not in js]
        assert not missing, f"{path}: handlers used but not defined: {missing}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_scripts_parse(web):
    client, _, _ = web
    login(client)
    for path in ["/?m=2026-09&open=An_Cao", "/pay/An Cao", "/accounts", "/upload"]:
        js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", client.get(path).text, re.S))
        r = subprocess.run(["node", "--check", "-"], input=js, capture_output=True, text=True)
        assert r.returncode == 0, f"{path}: {r.stderr[-300:]}"


def test_no_template_contains_a_jinja_comment_opener():
    """'{#' inside CSS (e.g. '{#paycard') silently breaks the whole page."""
    bad = [f for f in glob.glob("app/templates/*.html") if "{#" in open(f).read()]
    assert not bad, bad


def test_recording_a_payment_clears_owed_and_shows_in_the_audit(web):
    client, Session, _ = web
    login(client)
    html = client.get("/?m=2026-09").text
    amount = re.search(r'name="associate" value="An Cao">\s*<input type="hidden" name="amount" value="([\d.]+)"', html).group(1)
    assert float(amount) > 0
    r = client.post("/pay", data={"associate": "An Cao", "amount": amount}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/?m=2026-09").text
    an = html[html.index("r-An_Cao"):]
    assert "nothing owed" in an[:3000]
    audit = client.get("/pay/An Cao").text
    assert "recorded by manager" in audit


def test_invoice_json_is_scoped_to_its_rep(web):
    client, _, _ = web
    login(client)
    j = client.get("/invoice/INV00013.json").json()
    assert j["ok"] and j["header"]["n_lines"] == len(j["lines"]) > 0
    assert client.get("/invoice/NOPE.json").status_code == 404
    rep, _ = build()[0], None
    login(rep, "an")
    garmi_invoice = [i for i in range(1, 40) if client.get(f"/invoice/INV{i:05d}.json").json().get("header", {}).get("associate") == "Garmi Mei"][0]
    assert rep.get(f"/invoice/INV{garmi_invoice:05d}.json").status_code == 404     # another rep's invoice
    assert rep.get("/invoice/INV00013.json").json()["ok"]                           # their own


def test_assignment_pin_and_unpin_via_json(web):
    client, Session, _ = web
    login(client)
    def rows(view):
        html = client.get(f"/accounts?view={view}").text
        return html[html.index("<tbody>"):html.index("</tbody>")]
    assert client.post("/accounts/assign.json", json={"account": "ACCT1", "team": "Team 2"}).json()["ok"]
    body = rows("Team 2")
    assert "ACCT1" in body and re.search(r'ACCT1.*?<span class="pin">pinned', body, re.S)
    assert client.post("/accounts/assign.json", json={"account": "ACCT1", "team": ""}).json() == {"ok": True, "team": None}
    body = rows("Team 1")
    assert "ACCT1" in body and 'class="pin"' not in body
    assert client.post("/accounts/assign.json", json={"account": "ACCT1", "team": "Team 9"}).status_code == 400
