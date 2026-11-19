#!/bin/sh
# Runs one experiment scenario with Docker Compose and prints the evidence: exit code of the
# comparison server, its "unordered message rounds", the output of tests/check_logs.py (loss,
# duplicates, FIFO violations, old logs), the per-round summaries of the peers and every
# ERROR/WARNING line of the peers and of the comparison server (all rounds, not only the last).
#
# Prerequisites (on the host):
#   - git      (commit hash and clean/dirty state in the header)
#   - python3  (tests/check_logs.py; standard library only)
#   - Docker with Compose v2 ("docker compose", not the old "docker-compose")
#
# Argument / environment:
#   $1           NETEM: tc netem parameters applied only to the peer-to-peer UDP (port 6789).
#                Empty string ("") = no loss/delay. Examples: "loss 10%", "delay 20ms 10ms loss 10%".
#                If $1 is not given, the NETEM environment variable is used (default: empty).
#   AUTO_ROUNDS  rounds and messages per peer (default 20,20); check_logs uses the LAST value,
#                because every round overwrites logfile<ID>.log.
#   N_PEERS      number of peers (default 6).
#
# Must be run from the repository root. It deletes and recreates logs/ and stops the containers
# (docker compose down) at the end. If docker compose exits with a code other than 0, check_logs
# is NOT run ("SKIPPED"), compose-up.out is kept for inspection and the script exits with that code.
# Exit code: 0 only if compose, check_logs AND the search for ERROR/WARNING all pass. check_logs
# only sees the LAST round (logs are overwritten), so an ERROR or WARNING in any round prints
# "FAILED: erro ou aviso em alguma rodada" and makes the script fail even if check_logs says OK.
#
# Example (saves the output as evidence). Write OUTSIDE the repository and move it afterwards:
# a "| tee docs/evidencias/..." truncates a versioned file before the clean/dirty check runs,
# so the header would always say "dirty".
#   AUTO_ROUNDS=20,20 scripts/run_scenario.sh "delay 20ms 10ms loss 10%" | tee /tmp/fifo-depois.txt
#   mv /tmp/fifo-depois.txt docs/evidencias/fifo-depois.txt
NET="${1-${NETEM:-}}"
ROUNDS="${AUTO_ROUNDS:-20,20}"
NPEERS="${N_PEERS:-6}"
LAST_MSGS="${ROUNDS##*,}"

START=$(date +%s)
COMMIT=$(git rev-parse --short HEAD)
if [ -z "$(git status --porcelain)" ]; then TREE=clean; else TREE=dirty; fi

rm -rf logs && mkdir -p logs
echo "=== NETEM='$NET' AUTO_ROUNDS=$ROUNDS N=$NPEERS ($(date '+%Y-%m-%d %H:%M:%S'), commit $COMMIT $TREE) ==="
NETEM="$NET" AUTO_ROUNDS="$ROUNDS" N_PEERS="$NPEERS" timeout 400 \
  docker compose up --build --exit-code-from comparison > compose-up.out 2>&1
CODE=$?
echo "NETEM='$NET' código: $CODE"
echo "--- comparisonServer ---"
docker compose logs --no-log-prefix comparison | grep -iE "unordered|Found|ERROR"
if [ "$CODE" -ne 0 ]; then
  echo "--- erros dos peers ---"
  docker compose logs peer | grep -iE "ERROR" | tail -n 20
  docker compose down > /dev/null 2>&1
  echo "SKIPPED: compose saiu com código $CODE"
  echo "(saída completa do compose em compose-up.out)"
  exit "$CODE"
fi
echo "--- python3 tests/check_logs.py --since $START $NPEERS $LAST_MSGS ---"
python3 tests/check_logs.py --since "$START" "$NPEERS" "$LAST_MSGS"
CHECK=$?
echo "check_logs código: $CHECK"
echo "--- resumos por rodada, erros e avisos (peers e comparison, todas as rodadas) ---"
LINES=$(docker compose logs peer comparison 2>&1 | grep -E "finished|ERROR|WARNING")
printf '%s\n' "$LINES"
WARN=0
if printf '%s\n' "$LINES" | grep -qE "ERROR|WARNING"; then
  echo "FAILED: erro ou aviso em alguma rodada"
  WARN=1
fi
docker compose down > /dev/null 2>&1
rm -f compose-up.out
if [ "$CHECK" -ne 0 ]; then FINAL=$CHECK; else FINAL=$WARN; fi
echo "script código: $FINAL"
exit "$FINAL"
