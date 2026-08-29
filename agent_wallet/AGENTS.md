# AGENTS.md — spend-limited wallet for an AI agent (Oxford Hack track D1)

Instructions for any AI assistant or IDE working on this subproject: Claude Code,
Antigravity IDE, Cursor, or a human reading it cold. Everything needed to build, test and
demo this is here. Read it before touching anything.

---

## 1. What this is

Oxford Hackathon **track D1 — a spend-limited wallet for an AI agent**.

> Agents increasingly need to pay for things. The answer everywhere right now is "give
> the agent a hot key", which is indefensible. Canton's authorisation model is a much
> better fit. Build the wallet an agent should have.

Required by the brief:

1. A **mandate** contract: this agent may spend up to X, only with these counterparties,
   until this date.
2. The limits **enforced in Daml, not in the backend**. This is the whole point.
3. **Instant revocation** the agent cannot block or delay.
4. An **audit trail**: every action the agent took, and which permission allowed it,
   readable by a human.
5. A demo: an agent buying something on its own, and the statement afterwards.

How it is judged, in the organisers' words:

> We will try to make your agent exceed its cap, and pay someone it should not. Both must
> fail on the ledger, not in your API. Be ready to show us the line of Daml that stops
> it. Then we will revoke and try again.

Scoring weights: measures the thing 30% · survives attack 25% · works outside the demo
20% · **honesty 15%** · would it ship 10%. Overclaiming is punished harder than an
incomplete build. Say plainly what is mocked.

---

## 2. Environment — verified on this machine 2026-08-29

This is an **Intel Mac** (`uname -m` → `x86_64`). Homebrew lives at **`/usr/local`**.

> ⚠️ The repo's `CLAUDE.md` and both project READMEs say `/opt/homebrew/...`. That is the
> Apple-Silicon prefix and **does not exist here**. Every command below uses
> `/usr/local`. If a doc tells you `/opt/homebrew`, the doc is wrong for this machine.

### The export block — run this in every new terminal

```bash
export JAVA_HOME=/usr/local/opt/openjdk@21
export PATH="$HOME/.daml/bin:$JAVA_HOME/bin:$PATH"
```

### Verified present

| Tool | Version | Notes |
|---|---|---|
| Java | `openjdk 21.0.12.1` | at `/usr/local/opt/openjdk@21` |
| Daml | SDK **3.4.10** | plain Daml Assistant, `~/.daml/bin/daml` |
| Python | 3.13.5 | shared venv at repo root: `Oxford-Hack/hack/` |

`daml` prints a DPM-deprecation banner on every invocation. **Ignore it.** Silence it
with `--no-legacy-assistant-warning` if it gets noisy.

### Verified absent — do not write instructions that depend on these

| Missing | What it rules out |
|---|---|
| **DPM** (`~/.dpm` does not exist) | `dpm build` / `dpm test` / `dpm sandbox` |
| **openjdk@17** (only `openjdk@21` is in `/usr/local/Cellar`) | the parent project's documented Java 17 step |
| **Docker daemon** (down; LocalNet wants 16 GB RAM + ~6 GB images) | Canton LocalNet |
| **`C8_CLIENT_SECRET`** (issued by the Cantor8 team on the day) | shared DevNet, and therefore real Canton Coin |
| `canton-collateral-optimizer/.venv` | does not exist — this project actually uses `../hack`, matching its README, not `CLAUDE.md` |

### Toolchain decision for this subproject — read this before "fixing" anything

This subproject lives inside `canton-collateral-optimizer/` (branch `ark`) but is its own
Daml package **pinned to SDK 3.4.10 and driven by the plain `daml` CLI**.

The parent project's `daml/` package uses DPM 3.5.7 + Java 17. **This subproject
deliberately does not.** DPM and Java 17 are not installed on this machine; Daml 3.4.10
is, and it is verified working. Daml source is toolchain-agnostic — only `daml.yaml`'s
`sdk-version` differs — so switching to DPM later is a one-line change once DPM exists.

So there are now three scopes in this workspace, and they must not be mixed:

| Scope | Toolchain |
|---|---|
| `Oxford-Hack/daml-starter/` | Daml 3.4.10, plain `daml`, Java 21 |
| `canton-collateral-optimizer/daml/` | DPM 3.5.7, Java 17 (not installed here) |
| **`canton-collateral-optimizer/agent_wallet/` (this)** | **Daml 3.4.10, plain `daml`, Java 21** |

### Hard rules

- Do **not** modify or delete `Oxford-Hack/hack/` (the shared venv).
- Do **not** upgrade the Daml SDK. It stays at 3.4.10 here.
- Do **not** convert this subproject to DPM unless DPM is actually installed first.
- Do **not** touch `canton-collateral-optimizer/daml/` (the collateral package) or change
  the behaviour of `backend/canton.py` — **additive changes only** there, because
  `backend/demo.py` depends on it.
- Do **not** install anything globally. Everything here is Python **standard library
  only**; no `pip install` is required for this subproject.

---

## 3. The ledger we demo on

`daml sandbox` — a genuine Canton participant plus a local synchronizer, in one process.
No Docker, no auth, no network.

| Endpoint | Port |
|---|---|
| Wallet page + Ledger API proxy (`serve.py`) | `7575` |
| Canton JSON Ledger API v2 | `7576` |
| gRPC Ledger API | `6865` |

Canton sits on 7576 and `serve.py` takes 7575, serving the wallet page at `/` and
proxying `/v2/…` through. Every client keeps using 7575. (LocalNet would be
`2975`/`3975`/`4975` — a different thing, not used here.)

The sandbox runs on **wall-clock time**. Consequence: **expiry is tested in `daml test`
using `passTime`, not in the live demo.** Do not pretend otherwise in the README.

---

## 4. Commands

Always from `canton-collateral-optimizer/` unless stated. Always after the export block.

```bash
cd /Users/arkaj/Desktop/Hackathon/Oxford-Hack/canton-collateral-optimizer
source ../hack/bin/activate
export JAVA_HOME=/usr/local/opt/openjdk@21
export PATH="$HOME/.daml/bin:$JAVA_HOME/bin:$PATH"
```

### Dev loop — contract rules, in memory, about a second

```bash
cd agent_wallet && daml build && daml test && cd ..
```

Every script in `daml/Test.daml` must report `ok`. This is the loop you live in. No node,
no Docker, no network.

### Python unit tests (no ledger)

```bash
python -m unittest discover -s tests -v
```

### Live ledger — terminal 1

Canton listens on **7576**; the wallet takes 7575 in front of it (see below).

```bash
cd agent_wallet
daml sandbox --json-api-port 7576 --dar .daml/dist/agent-wallet-0.0.1.dar
```

Wait for the ready line before continuing.

### The simulation — the agent working live in a synthetic company

```bash
python -m agent_wallet.simulate               # ~3 min, watch it on :7575
python -m agent_wallet.simulate --speed 8     # fast
python -m agent_wallet.simulate --seed 7      # reproducible
```

`world.py` generates the workload: a studio with five approved suppliers and two nobody
approved. Limits are hit because the agent kept working, not because a script said so.
The spending window is 60s rather than a day, so it fills, blocks and **resets** during the
run. Three incidents are injected: a phishing invoice, a runaway retry loop, and the owner
revoking mid-flight. Exits non-zero if any limit was bypassed, and that detection is itself
tested.

### The demo — terminal 2

```bash
curl --fail http://localhost:7575/v2/state/ledger-end
python -m agent_wallet.demo
open agent_wallet/out/statement.html
```

`demo.py` exits **non-zero if any attack succeeded**. A green run is the claim.

### The browsable wallet — terminal 2

```bash
python -m agent_wallet.serve      # http://localhost:7575
```

Defaults are `--port 7575 --base-url http://localhost:7576`, so the wallet answers on the
port a human types and **proxies `/v2/…`, `/livez`, `/readyz` straight through to Canton**.
Every client (demo, MCP server, curl) keeps using 7575 and needs no change. It refuses to
start if `--port` and `--base-url` are the same address, because that would loop.

| Path on 7575 | Served by |
|---|---|
| `/` | the wallet statement |
| `/api/state` | this server, as JSON (JSON even when there is no mandate, or the ledger is down) |
| `/healthz` | this server |
| `/v2/…`, `/livez`, `/readyz` | proxied to Canton verbatim, rejections included |

Re-reads the ledger on every request, so it tracks the demo live. It defaults to the newest
mandate whose authority is **still live**, so a demo run's load-test mandate does not take
over the landing page. It deliberately cannot show refusals — a rejected transaction commits
nothing — and says so instead of claiming nothing was refused.

To leave Canton on 7575 instead, run the sandbox as usual and start the wallet with
`--port 8080 --base-url http://localhost:7575`.

### MCP server — a language model holds the wallet

```bash
python -m agent_wallet.mcp_server        # speaks JSON-RPC 2.0 on stdio
```

Register it (Claude Code, Antigravity IDE, and Cursor all read this shape):

```json
{
  "mcpServers": {
    "agent-wallet": {
      "command": "/Users/arkaj/Desktop/Hackathon/Oxford-Hack/hack/bin/python",
      "args": ["-m", "agent_wallet.mcp_server"],
      "cwd": "/Users/arkaj/Desktop/Hackathon/Oxford-Hack/canton-collateral-optimizer",
      "env": { "AGENT_WALLET_BASE_URL": "http://localhost:7575" }
    }
  }
}
```

---

## 5. Ledger facts you will need

**Template ids** use the package-name form (`name:` in `daml.yaml` is `agent-wallet`):

```
#agent-wallet:AgentWallet:Account
#agent-wallet:AgentWallet:Payment
#agent-wallet:AgentWallet:SpendingAuthority
#agent-wallet:AgentWallet:Mandate
#agent-wallet:AgentWallet:MandateProposal
#agent-wallet:AgentWallet:ChargeReceipt
```

**Party hints** used by the demo — real party ids come back as `hint::<hash>`:

`Bank` · `Alice` (owner) · `Shopper` (the agent) · `CoffeeShop` · `BookStore` ·
`Scammer` (never on any allow-list)

**JSON Ledger API v2**, the four calls that matter:

| Call | Purpose |
|---|---|
| `GET /v2/state/ledger-end` | offset; also a health check |
| `POST /v2/state/active-contracts-page` | read visible contracts |
| `POST /v2/commands/submit-and-wait` | create / exercise |
| `POST /v2/parties` | allocate a party |

`Decimal` goes over the wire as a **string** at 10 decimal places. `Time` is an ISO-8601
UTC string. `RelTime` is `{"microseconds": "<int as string>"}`.

**Reuse `backend/canton.py`.** It already has a stdlib-only client with `ensure_party`,
`active_contracts`, `create`, `exercise`, `ledger_end`, and a `LedgerApiError` that
carries the ledger's rejection text verbatim — which is exactly what the attack demo
needs to print. Do not write a second HTTP client.

---

## 6. Design — and why it is shaped this way

Five templates in `daml/AgentWallet.daml`. The organising idea: **the agent never holds
authority over money.** The only path from the agent to the owner's funds runs through
one choice body with six assertions in front of it.

| Template | Role |
|---|---|
| `Account` | the owner's money. `signatory bank`, `observer owner, viewers`. `Withdraw` is **`controller owner`** — the agent is not the controller, so it cannot raid the account directly. The agent *is* a viewer: it can see the whole balance and still cannot move a cent. |
| `Payment` | where value lands. `signatory bank`, `observer from, to`. |
| `SpendingAuthority` | the kill switch. `signatory owner`, `observer agent`, one choice `Revoke` **controller owner**. The agent has no choice on it at all. |
| `Mandate` | the permission. `signatory owner, agent`. `Charge` is `controller agent`. |
| `MandateProposal` | propose-and-accept, so neither party is bound without consenting. |
| `ChargeReceipt` | the audit record. `signatory owner, agent`, **no choices** — immutable, and archiving needs both signatures. |

### The line that matters

Inside a choice body you hold **the contract's signatories plus the choice's
controllers**. `Mandate` is `signatory owner, agent`, so inside `Charge` — which only the
agent exercises — the body holds **Alice's authority**. `Account.Withdraw` needs
`owner`. Satisfied.

That is delegation without a hot key: Alice's authority exists inside that one choice
body, behind those assertions, and nowhere else in the agent's reach. This paragraph is
the answer to *"show us the line of Daml that stops it"*.

### Why revocation is a separate contract

The starter's `Revoke` sits on the `Mandate` — the same contract the agent consumes on
every `Charge`. Under load the owner keeps losing the race, so revocation can be
*delayed*, which the brief explicitly forbids.

Here, `Charge` `fetch`es a `SpendingAuthority` the agent has no choice on. The owner's
revocation therefore never contends with the agent's charges: it commits first try, every
time, and every in-flight and future charge fails with `CONTRACT_NOT_ACTIVE` — on the
ledger. One switch kills every mandate that agent holds from that owner, in one
transaction.

`Mandate.Revoke` is kept as well, for tearing up a single mandate. Two levels; the demo
shows the difference.

### `Charge` — the order of checks

Each `assertMsg` is a line to point a judge at:

1. `fetch authorityCid` + owner/agent match → **revocation**
2. `now < expiresAt` → **window**
3. `amount > 0.0`
4. `payee elem allowedPayees` → **allow-list**
5. `spent + amount <= cap` → **total cap**
6. per-period check, after rolling the window forward → **period cap**
7. `exercise accountCid Withdraw` → **the money actually moves**
8. `create ChargeReceipt` with a `justification` built in Daml → **audit**
9. `create this with ...` → the new mandate state

### The audit trail

`ChargeReceipt.justification` is assembled **inside the choice**, from the values the
ledger actually checked:

```
mandate coffee-run | payee on allow-list | 32.0 <= remaining 95.5
| before expiry 2026-08-30T09:00:00Z | period 36.5 <= 40.0
```

That is the literal answer to *"every action the agent took, and which permission allowed
it, readable by a human"* — computed by the ledger, not by our backend, so it cannot be
spun after the fact.

---

## 7. Layout

```
agent_wallet/
  AGENTS.md         this file
  README.md         judge-facing: the exact enforcing lines, how to run, what is mocked
  daml.yaml         name: agent-wallet, sdk-version 3.4.10, --target=2.1
  daml/
    AgentWallet.daml
    Test.daml
  __init__.py
  ledger.py         typed wrappers over backend.canton.CantonClient
  demo.py           end-to-end story + attack suite; non-zero exit if an attack succeeds
  statement.py      receipts -> text and a self-contained HTML page
  mcp_server.py     stdlib JSON-RPC stdio MCP server
  coin.py           OPTIONAL DevNet Canton Coin rail (stretch)
  out/              generated statement.html (gitignored)
../tests/test_agent_wallet.py   pure-Python tests, picked up by the existing discover run
```

The underscore in `agent_wallet` matters: `python -m agent_wallet.demo` must work from
the project root. `daml build` scopes to `source: daml`, so the `.py` files are invisible
to the compiler.

---

## 8. Order of work, and what "done" means

| # | Work | Done when |
|---|---|---|
| 0 | `AGENTS.md`, fix `CLAUDE.md` paths, scaffold, `daml.yaml` | `daml build` succeeds |
| 1 | `Account`, `Payment`, `SpendingAuthority`, `Mandate` (total cap, allow-list, expiry, kill switch, receipt), `MandateProposal` | `daml build` green |
| 2 | `Test.daml` cases 1–10, 12–14 below | `daml test` green |
| 3 | Per-period cap + case 11 | `daml test` green |
| 4 | `ledger.py` + `demo.py` happy path on the sandbox | agent buys twice, balance moved |
| 5 | Attack suite | all seven refused **on the ledger**, messages printed |
| 6 | Kill-switch race + latency number | revoke commits first try under concurrent load |
| 7 | `statement.py` | `out/statement.html` reads like a statement |
| 8 | `mcp_server.py` + wiring | a live model holds the wallet and cannot be talked over the cap |
| 9 | `README.md` | a judge finds every enforcing line in under a minute |
| 10 | *stretch* `coin.py` | only if DevNet credentials arrive |

Do the **total cap before the per-period cap**. The brief warns that per-period looks
simple and turns into date arithmetic.

### Required test cases (`daml/Test.daml`)

Every rule needs a `submitMustFail`. Name the three the brief calls for so a judge can
grep them: `testCapUnder`, `testCapOver`, `testAfterRevoke`.

1. charge under the cap succeeds **and the balance actually moved**
2. charge over the total cap fails
3. payee not on the allow-list fails
4. charge after `expiresAt` fails (`passTime`)
5. charge after the kill switch is pulled fails
6. charge after `Mandate.Revoke` fails
7. **agent exercising `Account.Withdraw` directly fails** — the hot-key comparison
8. agent alone cannot `Adjust` its own cap
9. agent cannot `Revoke` the `SpendingAuthority`
10. owner cannot `Charge` (wrong controller)
11. per-period limit blocks a charge that is under the total cap; same charge succeeds
    after `passTime` rolls the window
12. zero and negative amounts fail
13. one kill switch disables **two** mandates at once
14. the receipt chain reconciles: `sum(receipt.amount) == accountBefore - accountAfter`

### The demo story (`demo.py`)

- Alice funds an `Account` with **10,000**; the agent is a viewer and can see all of it.
- Alice issues a `SpendingAuthority`, then proposes a mandate: **cap 100**, period limit
  **40 per 24h**, allow-list `[CoffeeShop, BookStore]`, expiry `+24h`. The agent accepts.
- The agent shops on its own: coffee 4.50, book 32.00.
- **Seven attacks, each refused by the ledger**, printing the ledger's own message: over
  the cap · payee not on the list · a direct `Account.Withdraw` raid · raising its own cap
  · pulling its own kill switch · exceeding the period limit · the owner trying to
  `Charge`.
- **Revocation under load**: fire concurrent charges from threads while Alice pulls the
  kill switch; assert the revoke committed **first try** and report the latency in ms.
  The rubric says *"bring a number"* — this is the number.
- Then a charge of 1.00 fails, and a second mandate is dead too.
- Print the statement; write `out/statement.html`.

---

## 9. Honesty register — keep this current

The rubric gives 15% to honesty and punishes overclaiming harder than an incomplete
build. Whatever is true at demo time goes here and in the README.

- **Real:** the cap, allow-list, expiry, revocation and audit record are enforced in Daml
  and committed to a real Canton participant. The money movement (`Account` →
  `Payment`), the receipt, and the cap decrement are **one atomic Daml transaction**.
- **Not real Canton Coin by default.** `Account`/`Payment` are our own templates in our
  own DAR. Amulet exists only on LocalNet/DevNet, and neither is reachable from this
  machine today.
- **The Canton Coin rail, if built, is not atomic with the cap.** A token-standard
  transfer needs a registry-issued factory plus disclosed contracts assembled off-ledger,
  so it cannot run inside a Daml choice. The design is: `Charge` creates a
  `PaymentAuthorisation` and decrements the cap atomically, then `coin.py` executes the
  Amulet transfer and exercises `Settle` or `Cancel`. **The cap is still enforced
  on-ledger; the coin leg is not atomic with it.** Say that sentence, unedited.
- **Expiry is proven in `daml test` with `passTime`, not in the live demo**, because the
  sandbox runs on wall-clock time.
- **`Mandate.accountCid` is a contract-id link.** If the owner spends from the same
  account outside the mandate, the link goes stale and charges fail until the owner
  exercises `Rebind`. Failing closed is the right direction, but it is a limitation, not
  a feature.
- Single participant in the demo, so all parties are on one node. The design does not
  depend on that — the agent is a stakeholder on `Account` precisely so it works across
  participants — but it has not been tested on a multi-participant topology.

---

## 10. If you get stuck

| Symptom | Cause |
|---|---|
| `Unable to locate a Java Runtime` | the export block was not run in this terminal |
| `/opt/homebrew/...: No such file or directory` | a doc gave you the Apple-Silicon path; use `/usr/local` |
| `daml: command not found` | `$HOME/.daml/bin` is not on `PATH` |
| `dpm: command not found` | expected — DPM is not installed and is not used here |
| `Cannot reach Canton at http://localhost:7575` | the sandbox in terminal 1 is not up yet |
| `ERR_SSL_PROTOCOL_ERROR` or `{"status":404}` on `localhost:7575` | you are hitting Canton directly instead of the wallet. Start `python -m agent_wallet.serve`, which takes 7575 and proxies Canton on 7576 |
| the proxy hangs or loops | `--port` and `--base-url` point at the same address; the sandbox must be on a different port |
| `PARTY_ALLOCATION_WITHOUT_CONNECTED_SYNCHRONIZER` | the JSON API came up just before the participant finished connecting; `backend/canton.py` already retries this one case |
| a 403 with a valid token (DevNet only) | a token says who you are; acting as a party is a separate `CanActAs` grant |

Reference docs: `Oxford-Hack/CHALLENGES.md` (the D1 brief), `Oxford-Hack/API.md` (JSON
Ledger API v2 cheat sheet), `Oxford-Hack/daml-starter/` (the starter this builds on),
`https://docs.canton.network`.
