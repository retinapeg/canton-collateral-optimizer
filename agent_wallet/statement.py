"""The statement: what the agent did, and which permission allowed each thing.

Every line here comes from a `ChargeReceipt` contract on the ledger.  The
"allowed because" column is the `justification` field, which is assembled
inside the Daml choice from the values the ledger actually checked -- so it is
evidence, not our summary of events.

Refusals are shown separately and are clearly marked as *not* ledger records: a
rejected transaction commits nothing, so the only trace of it is in the agent's
own log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape

from .ledger import Mandate, Receipt, money, party_hint


@dataclass(frozen=True)
class Refusal:
    """Something the agent tried and the ledger would not do."""

    attempt: str
    payee: str
    amount: Decimal | None
    reason: str


@dataclass(frozen=True)
class Statement:
    owner: str
    agent: str
    reference: str
    cap: Decimal
    spent: Decimal
    period_limit: Decimal | None
    period_spent: Decimal
    allowed_payees: tuple[str, ...]
    expires_at: datetime
    opening_balance: Decimal
    closing_balance: Decimal
    receipts: tuple[Receipt, ...]
    refusals: tuple[Refusal, ...]
    mandate_live: bool
    authority_live: bool
    # A live view reads the ledger now, so it has no record of an opening
    # balance and no attack log.  It must not claim a reconciliation it cannot
    # actually check.
    live: bool = False

    @property
    def remaining(self) -> Decimal:
        return self.cap - self.spent

    @property
    def total_charged(self) -> Decimal:
        return sum((r.amount for r in self.receipts), Decimal("0"))

    @property
    def reconciles(self) -> bool:
        """The receipts must account for every penny that left the account."""
        return self.total_charged == self.opening_balance - self.closing_balance

    @property
    def status(self) -> str:
        if not self.authority_live:
            return "REVOKED"
        if not self.mandate_live:
            return "MANDATE ENDED"
        return "LIVE"


def build(
    *,
    mandate: Mandate,
    receipts: list[Receipt],
    refusals: list[Refusal],
    opening_balance: Decimal,
    closing_balance: Decimal,
    mandate_live: bool,
    authority_live: bool,
    live: bool = False,
) -> Statement:
    return Statement(
        owner=mandate.owner,
        agent=mandate.agent,
        reference=mandate.reference,
        cap=mandate.cap,
        spent=mandate.spent,
        period_limit=mandate.period_limit,
        period_spent=mandate.spent_in_period,
        allowed_payees=mandate.allowed_payees,
        expires_at=mandate.expires_at,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        receipts=tuple(receipts),
        refusals=tuple(refusals),
        mandate_live=mandate_live,
        authority_live=authority_live,
        live=live,
    )


# -- terminal -----------------------------------------------------------------


def render_text(s: Statement, width: int = 96) -> str:
    rule = "-" * width
    out: list[str] = [
        "=" * width,
        f"  STATEMENT  {party_hint(s.owner)} -> agent {party_hint(s.agent)}"
        f"   mandate '{s.reference}'   [{s.status}]",
        "=" * width,
        f"  Cap {money(s.cap)}   spent {money(s.spent)}   remaining {money(s.remaining)}",
    ]
    if s.period_limit is not None:
        out.append(
            f"  Period limit {money(s.period_limit)} per period"
            f"   spent this period {money(s.period_spent)}"
        )
    out += [
        f"  Allow-list: {', '.join(party_hint(p) for p in s.allowed_payees)}",
        f"  Expires: {s.expires_at:%Y-%m-%d %H:%M:%S} UTC",
        f"  Account: opening {money(s.opening_balance)}"
        f"  ->  closing {money(s.closing_balance)}",
        rule,
        "  CHARGES ON THE LEDGER",
        rule,
    ]
    if not s.receipts:
        out.append("  (none)")
    for r in s.receipts:
        out += [
            f"  {r.charged_at:%H:%M:%S}  {party_hint(r.payee):<12}"
            f"  {money(r.amount):>10}   {r.memo}",
            f"      allowed because: {_shorten_parties(r.justification)}",
        ]
    out += [rule, "  REFUSED BY THE LEDGER", rule]
    if not s.refusals:
        out.append("  (none)")
    for f in s.refusals:
        amount = "-" if f.amount is None else money(f.amount)
        out += [
            f"  {f.attempt:<34} {party_hint(f.payee):<12} {amount:>10}",
            f"      ledger said: {_shorten_parties(f.reason)}",
        ]
    out += [
        rule,
        "  These refusals are NOT ledger records.  A rejected transaction commits",
        "  nothing, so the only trace of an attempt is the agent's own log; the",
        "  charges above are the ledger's, and they are the ones that count.",
        rule,
        f"  Receipts total {money(s.total_charged)}; account fell by "
        f"{money(s.opening_balance - s.closing_balance)}"
        f"   -> {'RECONCILES' if s.reconciles else 'DOES NOT RECONCILE'}",
        "=" * width,
    ]
    return "\n".join(out)


def _shorten_parties(text: str) -> str:
    """Replace `Alice::1220abc...` with `Alice` so a line fits on a screen."""
    words = []
    for word in text.split(" "):
        if "::" in word:
            word = word.split("::", 1)[0]
        words.append(word)
    return " ".join(words)


# -- the page -----------------------------------------------------------------

_CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#1a1a1a;--muted:#6b6b6b;--line:#e3e3df;
--ok:#1c7c4a;--ok-bg:#e8f5ed;--no:#a3282d;--no-bg:#fbeceb;--accent:#2b4c7e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 2px;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
margin:28px 0 10px;font-weight:600}
.sub{color:var(--muted);margin:0 0 20px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.tag{font-size:12px;font-weight:700;letter-spacing:.06em;padding:4px 10px;border-radius:999px}
.tag.live{background:var(--ok-bg);color:var(--ok)}
.tag.dead{background:var(--no-bg);color:var(--no)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:6px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.stat .v{font-size:20px;font-variant-numeric:tabular-nums;margin-top:1px}
.bar{height:7px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:10px}
.bar > i{display:block;height:100%;background:var(--accent)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:14px;min-width:560px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
color:var(--muted);font-weight:600;padding:0 10px 8px;border-bottom:1px solid var(--line)}
td{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.why{font-size:12.5px;color:var(--muted);margin-top:3px;line-height:1.45}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:#f0f0ec;
padding:1px 5px;border-radius:4px}
.no td{background:var(--no-bg)}
.no .why{color:var(--no)}
.note{font-size:13px;color:var(--muted);margin-top:12px;padding-left:12px;
border-left:2px solid var(--line)}
.foot{margin-top:26px;font-size:12.5px;color:var(--muted)}
.foot b{color:var(--ink)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#141414;--card:#1c1c1b;--ink:#ececea;--muted:#9a9a95;--line:#2e2e2c;
--ok:#63c98d;--ok-bg:#17301f;--no:#f0888c;--no-bg:#331a1b;--accent:#7aa2d6}
:root:not([data-theme=light]) code{background:#262624}}
:root[data-theme=dark]{--bg:#141414;--card:#1c1c1b;--ink:#ececea;--muted:#9a9a95;
--line:#2e2e2c;--ok:#63c98d;--ok-bg:#17301f;--no:#f0888c;--no-bg:#331a1b;--accent:#7aa2d6}
:root[data-theme=dark] code{background:#262624}
"""


def render_html(s: Statement) -> str:
    def e(text: object) -> str:
        return escape(str(text))

    pct = 0 if s.cap == 0 else min(100, int(s.spent / s.cap * 100))
    tag = "live" if s.status == "LIVE" else "dead"

    stats = [
        ("Cap", money(s.cap)),
        ("Spent", money(s.spent)),
        ("Remaining", money(s.remaining)),
    ]
    if s.period_limit is not None:
        stats.append(
            ("This period", f"{money(s.period_spent)} / {money(s.period_limit)}")
        )

    rows = []
    for r in s.receipts:
        rows.append(
            f"<tr><td>{r.charged_at:%H:%M:%S}</td>"
            f"<td>{e(party_hint(r.payee))}<div class=why>{e(r.memo)}</div></td>"
            f"<td class=num>{e(money(r.amount))}</td>"
            f"<td class=num>{e(money(r.remaining_after))}</td></tr>"
            f"<tr><td></td><td colspan=3><div class=why>allowed because: "
            f"<code>{e(_shorten_parties(r.justification))}</code></div></td></tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=4 class=why>No charges.</td></tr>")

    refused = []
    for f in s.refusals:
        amount = "-" if f.amount is None else money(f.amount)
        refused.append(
            f"<tr class=no><td>{e(f.attempt)}</td>"
            f"<td>{e(party_hint(f.payee))}</td>"
            f"<td class=num>{e(amount)}</td></tr>"
            f"<tr class=no><td colspan=3><div class=why>ledger said: "
            f"<code>{e(_shorten_parties(f.reason))}</code></div></td></tr>"
        )
    if not refused:
        refused.append(
            "<tr><td colspan=3 class=why>"
            + (
                "Nothing is listed here. A rejected transaction commits nothing, "
                "so refusals leave no trace on the ledger and this live view "
                "cannot show them. Run <code>python -m agent_wallet.demo</code> "
                "to see the full attack log."
                if s.live
                else "Nothing was refused."
            )
            + "</td></tr>"
        )

    if s.live:
        # No recorded opening balance to check against, so claim nothing.
        reconcile = (
            f"Charges on this mandate total {money(s.total_charged)}; "
            f"the funding account now holds {money(s.closing_balance)}"
        )
        verdict = (
            "Read live from the ledger, so the opening balance is not known here "
            "and no reconciliation is asserted."
        )
    else:
        reconcile = (
            f"Receipts total {money(s.total_charged)} and the account fell by "
            f"{money(s.opening_balance - s.closing_balance)}"
        )
        verdict = "They reconcile." if s.reconciles else "They DO NOT reconcile."

    return f"""<title>Agent Wallet Statement</title>
<style>{_CSS}</style>
<div class=wrap>
  <div class=head>
    <div>
      <h1>Agent wallet statement</h1>
      <p class=sub>{e(party_hint(s.owner))} &rarr; agent
        <b>{e(party_hint(s.agent))}</b> &middot; mandate
        <code>{e(s.reference)}</code></p>
    </div>
    <span class="tag {tag}">{e(s.status)}</span>
  </div>

  <div class=card>
    <div class=grid>
      {"".join(f'<div class=stat><div class=k>{e(k)}</div><div class=v>{e(v)}</div></div>' for k, v in stats)}
    </div>
    <div class=bar><i style="width:{pct}%"></i></div>
    <div class=note>
      Allow-list: {e(", ".join(party_hint(p) for p in s.allowed_payees))}<br>
      Expires {s.expires_at:%Y-%m-%d %H:%M:%S} UTC &middot;
      account {e(money(s.opening_balance))} &rarr; {e(money(s.closing_balance))}
    </div>
  </div>

  <h2>Charges on the ledger</h2>
  <div class="card scroll">
    <table>
      <tr><th>Time</th><th>Payee</th><th class=num>Amount</th>
          <th class=num>Left after</th></tr>
      {"".join(rows)}
    </table>
  </div>

  <h2>Refused by the ledger</h2>
  <div class="card scroll">
    <table>
      <tr><th>Attempt</th><th>Payee</th><th class=num>Amount</th></tr>
      {"".join(refused)}
    </table>
    <div class=note>
      These are <b>not</b> ledger records. A rejected transaction commits
      nothing, so the only trace of an attempt is the agent's own log. The
      charges above are the ledger's, and they are the ones that count.
    </div>
  </div>

  <p class=foot>
    <b>{e(reconcile)}. {e(verdict)}</b><br>
    Every &ldquo;allowed because&rdquo; line is the <code>justification</code>
    field of a <code>ChargeReceipt</code> contract, assembled inside the Daml
    choice from the values the ledger checked. It is evidence, not a summary
    written afterwards by this program.
  </p>
</div>"""
