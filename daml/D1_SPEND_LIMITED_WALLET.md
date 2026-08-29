# D1: Spend-limited wallet for an AI agent

This repository's selected Cantor8 hackathon track is the Daml **D1
spend-limited wallet for an AI agent**. The official challenge specification is
in the [Cantor8 hackathon toolkit](https://github.com/Cantor8/hackathon-toolkit/blob/main/CHALLENGES.md#d1-a-spend-limited-wallet-for-an-ai-agent).

## What is ready now

The starter is integrated into the repository's existing Daml package:

- [`daml/Mandate.daml`](daml/Mandate.daml) defines the proposal and active
  mandate contracts.
- [`daml/MandateTest.daml`](daml/MandateTest.daml) proves the current security
  rules on Daml's in-memory ledger.
- The total spending cap, positive charge amount, expiry deadline, revocation,
  controller authority, and bilateral cap adjustment are enforced in Daml.

The key judged rule is enforced inside the `Charge` choice:

```daml
assertMsg "charge would exceed the cap" (spent + amount <= cap)
```

That means a client cannot bypass the cap by avoiding the Python backend and
submitting directly to Canton.

## Build and test in VS Code

Use **Terminal > Run Task** and select **Daml: Build and test**, or run:

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH="$HOME/.dpm/bin:/opt/homebrew/opt/openjdk@17/bin:$PATH"

cd daml
dpm build
dpm test
```

No Canton node, Docker container, credentials, or network connection is needed
for this test loop.

## Next implementation checkpoints

1. Add the counterparty allow-list to the mandate and reject an unapproved
   recipient on the ledger.
2. Create one immutable audit/receipt contract per successful charge, including
   the agent, counterparty, amount, time, and mandate that authorised it.
3. Connect `Charge` to a real Canton token-standard transfer instead of only
   increasing `spent`.
4. Demonstrate an autonomous purchase and a human-readable statement.
5. Only after the total cap is complete, consider per-period limits.

## Honest current boundary

The starter records and constrains charge amounts, but it does **not yet move
real Canton Coin**. The allow-list and durable audit receipt are also next-step
work. This is the intended starting point from the toolkit, not a claim that the
full D1 submission is finished.
