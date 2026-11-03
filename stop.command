#!/bin/bash
# 충북 EV 요금 관제 끄기 (macOS: 이 파일을 더블클릭)
cd "$(dirname "$0")" || exit 1
ours() { [ -n "$1" ] && ps -p "$1" -o command= 2>/dev/null | grep -Eq 'server\.py|tunnel_keeper|cloudflared|localhost\.run'; }
stopped=0
if [ -f .pids ]; then
  read -r S T PORT < .pids
  for p in $T $S; do ours "$p" && kill "$p" 2>/dev/null && stopped=1; done
  if [ -n "$PORT" ]; then   # 주소 지킴이가 띄운 터널까지 확실히
    pkill -f "tunnel --no-autoupdate --url http://localhost:$PORT" 2>/dev/null
    pkill -f "80:localhost:$PORT nokey@localhost.run" 2>/dev/null
  fi
fi
rm -f .pids .public_url
if [ "$stopped" = 1 ]; then echo "사이트를 껐습니다. 다시 켜려면 run.command를 더블클릭하세요."; else echo "켜져 있는 사이트가 없습니다."; fi
sleep 3
