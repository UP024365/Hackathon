#!/bin/bash
# 폰·외부용 인터넷 주소(Cloudflare 무료 터널)를 띄우고 계속 지켜봅니다. run.command가 nohup으로 실행합니다.
# 와이파이가 끊기거나 맥이 잠들었다 깨서 주소가 죽으면, 새 주소로 다시 띄우고 .public_url과 폰주소.txt를 고칩니다.
# (무료 터널은 한번 끊기면 같은 주소로 돌아오지 않습니다. 주소가 바뀐 기록은 tunnel_history.log)
# 사용: scripts/tunnel_keeper.sh <서버 PID> <포트>
cd "$(dirname "$0")/.." || exit 1
SPID=$1; PORT=$2
CF=".venv/cloudflared"
FAIL_LIMIT=4          # 1분 간격 확인이 연속 4번 실패하면 새 주소로
TPID=""; URL=""

nap() { sleep "$1" & wait $!; }      # 끄기 신호를 바로 받도록
stop_tunnel() { [ -n "$TPID" ] && kill "$TPID" 2>/dev/null; TPID=""; }
trap 'stop_tunnel; rm -f .public_url; exit 0' TERM INT HUP

start_tunnel() {
  URL=""
  if [ -x "$CF" ]; then
    "$CF" tunnel --no-autoupdate --url "http://localhost:$PORT" > tunnel.log 2>&1 < /dev/null &
    TPID=$!
    for _ in $(seq 1 40); do
      URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log | head -1); [ -n "$URL" ] && break; nap 1
    done
  fi
  if [ -z "$URL" ]; then      # Cloudflare가 안 되면 localhost.run
    stop_tunnel
    ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
        -R 80:localhost:"$PORT" nokey@localhost.run > tunnel.log 2>&1 < /dev/null &
    TPID=$!
    for _ in $(seq 1 25); do
      URL=$(grep -Eo 'https://[a-z0-9-]+\.lhr\.life' tunnel.log | head -1); [ -n "$URL" ] && break; nap 1
    done
  fi
  if [ -n "$URL" ]; then
    echo "$URL" > .public_url
    echo "$URL" > "폰주소.txt"
    echo "$(date '+%Y-%m-%d %H:%M') $URL" >> tunnel_history.log
    return 0
  fi
  stop_tunnel; rm -f .public_url
  return 1
}

while kill -0 "$SPID" 2>/dev/null; do
  if start_tunnel; then
    nap 60                       # 새 주소가 퍼질 시간
    fails=0
    while kill -0 "$SPID" 2>/dev/null && kill -0 "$TPID" 2>/dev/null; do
      if curl -fs -m 20 "$URL/api/health" >/dev/null 2>&1; then fails=0; else fails=$((fails + 1)); fi
      [ "$fails" -ge "$FAIL_LIMIT" ] && break
      nap 60
    done
    stop_tunnel; rm -f .public_url
  else
    nap 60                       # 인터넷이 끊긴 동안은 1분마다 다시 시도
  fi
done
stop_tunnel; rm -f .public_url
