#!/usr/bin/env bash
# Runner loop: one Actions job runs scripts/fetch_live.py every 5 minutes (at :02, :07, … — off the top of the hour)
# until LOOP_MINUTES have passed, committing data/ only when it changed. config/market_calendar.yml decides
# per market whether a fetch is due, so most iterations are a cheap no-op. A failing iteration is logged and the
# loop carries on; the workflow dispatches the next loop run when this one ends.
# Env: LOOP_MINUTES (default 340), FORCE (groups to force on the first iteration only), INTERVAL (default 300 s).
set -u
interval=${INTERVAL:-300}
offset=120                                   # slots at minute ≡ 2 (mod 5)
end=$(( $(date +%s) + ${LOOP_MINUTES:-340} * 60 ))
force=${FORCE:-}
bj() { TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S'; }

git config user.name "justinchou"
git config user.email "2976188+justinchou@users.noreply.github.com"

sync() {  # pull latest main; on a data conflict keep our newer local commit
  git pull --rebase -q && return 0
  git rebase --abort 2>/dev/null
  git pull --rebase -q -X theirs
}

commit_push() {
  git add data
  if git diff --cached --quiet; then echo "no change"; return 0; fi
  git commit -q -m "chore(data): live prices $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M') Beijing time"
  for i in 1 2 3; do
    sync && git push -q && { echo "pushed $(git rev-parse --short HEAD)"; return 0; }
    sleep 5
  done
  echo "::warning::push failed; the commit stays local and goes out with the next iteration"
  return 1
}

n=0
while :; do
  n=$((n + 1))
  echo "::group::iteration $n — $(bj) Beijing time"
  sync || echo "::warning::git pull failed"
  if python scripts/fetch_live.py --force "$force"; then
    commit_push
  else
    echo "::warning::fetch failed in iteration $n"
  fi
  force=""
  echo "::endgroup::"
  now=$(date +%s)
  next=$(( (now - offset) / interval * interval + interval + offset ))
  (( next - now < 60 )) && next=$(( next + interval ))   # first iteration started just before a slot
  if (( next > end )); then echo "loop done after $n iterations — $(bj) Beijing time"; break; fi
  sleep $(( next - now ))
done
