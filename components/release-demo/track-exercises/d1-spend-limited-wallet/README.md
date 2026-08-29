# D1: Spend-limited wallet for an AI agent

This is a self-contained Daml track exercise inside the main
`canton-collateral-optimizer` GitHub repository. It deliberately has its own
`daml.yaml`, source directory, tests, VS Code settings and task menu, so it does
not compile as part of the collateral optimiser.

The official challenge specification is in the
[Cantor8 hackathon toolkit](https://github.com/Cantor8/hackathon-toolkit/blob/main/CHALLENGES.md#d1-a-spend-limited-wallet-for-an-ai-agent).

## Open it as a separate VS Code project

1. In VS Code, select **File -> New Window**.
2. In the new window, select **File -> Open Folder...**.
3. Open this folder, not the parent repository:

   ```text
   track-exercises/d1-spend-limited-wallet
   ```

Opening this exact folder loads this exercise's `.vscode` configuration and
starts the Daml language server against this exercise's `daml.yaml`. The main
collateral project can remain open in a different VS Code window.

## What is ready now

- [`daml/Mandate.daml`](daml/Mandate.daml) defines the proposal and active
  mandate contracts.
- [`daml/MandateTest.daml`](daml/MandateTest.daml) proves the security rules on
  Daml's in-memory ledger.
- The total spending cap, positive charge amount, expiry deadline, revocation,
  controller authority and bilateral cap adjustment are enforced in Daml.

The key judged rule is enforced inside the `Charge` choice:

```daml
assertMsg "charge would exceed the cap" (spent + amount <= cap)
```

That means a client cannot bypass the cap by avoiding an off-ledger backend and
submitting directly to Canton.

## Build and test

In this exercise's VS Code window, use **Terminal -> Run Task** and select:

```text
Daml: Build and test exercise
```

Or run from this folder in its integrated terminal:

```bash
dpm build
dpm test
```

No Canton node, Docker container, credentials or network connection is needed
for this test loop.

## Debug the spending flow

Use **Terminal -> Run Task** and select:

```text
Daml: Debug testMandate
```

This runs only `testMandate`, prints choice coverage and writes an interactive
transaction trace to:

```text
.daml/debug-transactions/transaction-testMandate.html
```

Open that HTML file in a browser to inspect the successful proposal, charges,
bilateral cap adjustment and revocation transactions. Rejected submissions are
asserted with `submitMustFail`, so the test passes only when the over-cap charge,
owner impersonation, unilateral cap change and post-revocation charge all fail.

## Next implementation checkpoints

1. Add the counterparty allow-list to the mandate and reject an unapproved
   recipient on the ledger.
2. Create one immutable audit/receipt contract per successful charge, including
   the agent, counterparty, amount, time and mandate that authorised it.
3. Connect `Charge` to a real Canton token-standard transfer instead of only
   increasing `spent`.
4. Demonstrate an autonomous purchase and a human-readable statement.
5. Only after the total cap is complete, consider per-period limits.

## Honest current boundary

The starter records and constrains charge amounts, but it does **not yet move
real Canton Coin**. The allow-list and durable audit receipt are also next-step
work. This is the intended starting point from the toolkit, not a claim that the
full D1 submission is finished.
