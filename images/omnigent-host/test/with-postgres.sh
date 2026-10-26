#!/usr/bin/env bash
set -uo pipefail
cd /tmp || exit

fail=0
check() {
  if [[ "$2" == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

before=$(find /tmp -maxdepth 1 -name 'pg.*' | sort)

out=$(with-postgres psql -tAc 'select 1')
check "select 1 prints 1" "$out" 1

out=$(with-postgres sh -c 'echo "$DATABASE_URL"')
check "DATABASE_URL" "$out" "postgresql://app@127.0.0.1:55432/app"

out=$(PGPORT=55499 PG_DB=routivo PG_USER=routivo with-postgres sh -c 'psql -tAc "select current_user || current_database()" && echo "$PGHOST:$PGPORT"')
check "overrides" "$out" "$(printf 'routivoroutivo\n127.0.0.1:55499')"

with-postgres false
check "exit code is kept" "$?" 1

with-postgres sh -c 'exit 7'
check "exit code 7 is kept" "$?" 7

after=$(find /tmp -maxdepth 1 -name 'pg.*' | sort)
check "no /tmp/pg.* left behind" "$after" "$before"

if pgrep -u "$(id -u)" postgres >/dev/null; then
  echo "FAIL postgres still running"
  fail=1
else
  echo "ok   no postgres left running"
fi

exit "$fail"
