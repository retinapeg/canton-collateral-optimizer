# Simulation Animation Design Notes

This document provides instructions and context for designing an animation or visual demonstration (e.g., in React, SVG, or a web frontend) of the `agent_wallet` simulation.

## The Goal
The animation should demonstrate a live AI agent attempting to spend money from a wallet enforced by a Daml ledger. It must clearly highlight that **the limits are enforced by the ledger, not the agent**, and showcase the agent's new "smart" behavior of waiting for period windows to reset instead of endlessly retrying.

## Key Elements to Visualize

### 1. The Setup (The Dashboard)
*   **Treasury:** 50,000.00
*   **Agent's Total Cap:** 2,500.00
*   **Per-Period Limit:** 250.00 every 60s
*   **Approved Vendors:** Nimbus Cloud, TokenMill Inference, VectorStore, PagerWatch, Desk Supply Co
*   **Unapproved Vendors:** Accounts Receivable (Phishing), Grey Market GPUs

### 2. Routine Spending
*   Show the agent successfully paying approved vendors.
*   **Visual cue:** Green `PAID` badges, decreasing `left` balance.

### 3. The Smart Period Limit (The New Behavior)
*   When the agent hits the 250.00 limit within a 60s window, the ledger returns a `REFUSED` error (`charge would exceed the per-period limit`).
*   **The Smart Behavior:** Instead of retrying in a loop, the agent reads the exact `period_start` and `period_length` from the ledger, calculates the remaining time, and **waits**.
*   **Visual cue:** A pause state, displaying: `Period limit reached. Waiting XXs for the window to reset...`
*   Once the timer expires, the window rolls over, and spending resumes.

### 4. Incident 1: The Phishing Invoice
*   A convincing, urgent invoice from `Accounts Receivable` (an unapproved vendor).
*   The agent decides to pay it because it has budget left.
*   **The result:** `REFUSED` (payee is not on the allow-list).
*   **Takeaway:** The agent was compromised, but the ledger's allow-list held.

### 5. Incident 2: The Runaway Retry Loop
*   The agent decides it urgently needs GPU compute and enters a loop trying to buy from `Grey Market GPUs`.
*   **The result:** Repeated `REFUSED` errors from the ledger.
*   **Takeaway:** The retry loop costs the treasury nothing.

### 6. Incident 3: The Kill Switch
*   The studio owner pulls the kill switch (revokes the `SpendingAuthority` contract).
*   The agent is still running and tries to spend.
*   **The result:** Immediate `REFUSED` (`CONTRACT_NOT_FOUND`).
*   **Takeaway:** Revocation is instant (~300ms) and the agent cannot block or delay it.

## Suggested Animation Flow
1.  **Split Screen:** Show a mock terminal (the agent's perspective) on the left, and a "Ledger / Treasury" view on the right.
2.  **Highlight the Enforcer:** When a transaction is refused, visually show the rejection coming from the Ledger side, emphasizing that the Python agent is a "dumb" client.
3.  **The Wait State:** Emphasize the "Waiting..." countdown clock during period limits. This shows the agent cooperating with the ledger's reality.

Use these notes to drive the design and timing of the animation.
