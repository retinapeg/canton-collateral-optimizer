"""A spend-limited wallet for an AI agent, enforced in Daml on Canton.

The limits live in `daml/AgentWallet.daml`, not in this package.  Everything
here reads the ledger and submits commands to it; nothing here can permit a
spend the ledger would refuse, and nothing here is trusted to.
"""
