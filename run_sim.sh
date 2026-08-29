#!/bin/bash
export JAVA_HOME=/usr/local/opt/openjdk@21
export PATH="$HOME/.daml/bin:$JAVA_HOME/bin:$PATH"
source .venv/bin/activate
cd agent_wallet
daml sandbox --json-api-port 7576 --dar .daml/dist/agent-wallet-0.0.1.dar &
SANDBOX_PID=$!
sleep 5
cd ..
python -m agent_wallet.serve --port 7575 --base-url http://localhost:7576 &
SERVE_PID=$!
sleep 5
python -m agent_wallet.simulate --speed 8
kill $SERVE_PID
kill $SANDBOX_PID
