#!/bin/sh
# Runs one experiment scenario with Docker Compose (6 peers, AUTO_ROUNDS=20,20) and prints the
# evidence: exit code of the comparison server, its "unordered message rounds", the output of
# tests/check_logs.py (loss, duplicates, FIFO violations) and the per-round summaries of the peers.
#
# Argument:
#   $1  NETEM: tc netem parameters applied only to the peer-to-peer UDP (port 6789).
#       Empty string ("") = no loss/delay. Examples: "loss 10%", "delay 20ms 10ms loss 10%".
#
# Must be run from the repository root. It deletes and recreates logs/ and stops the containers
# (docker compose down) at the end.
#
# Example (saves the output as evidence):
#   scripts/run_scenario.sh "delay 20ms 10ms loss 10%" | tee docs/evidencias/fifo-depois.txt
NET="$1"
rm -rf logs && mkdir -p logs
echo "=== NETEM='$NET' AUTO_ROUNDS=20,20 N=6 ($(date '+%Y-%m-%d %H:%M:%S'), commit $(git rev-parse --short HEAD) + working tree) ==="
NETEM="$NET" AUTO_ROUNDS=20,20 timeout 400 docker compose up --build --exit-code-from comparison > compose-up.out 2>&1
echo "NETEM='$NET' código: $?"
echo "--- comparisonServer ---"
docker compose logs --no-log-prefix comparison | grep -iE "unordered|Found|ERROR" 
echo "--- python3 tests/check_logs.py 6 20 ---"
python3 tests/check_logs.py 6 20; echo "check_logs código: $?"
echo "--- resumos por rodada (peers) ---"
docker compose logs peer | grep -iE "finished|held|ERROR" | tail -n 12
docker compose down > /dev/null 2>&1
rm -f compose-up.out
