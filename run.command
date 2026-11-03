#!/bin/bash
# 충북 EV 요금 관제 켜기 (macOS: 이 파일을 더블클릭)
# - 맥에서 서버를 켜고, 폰·팀원이 바로 열 수 있는 인터넷 주소(https://….trycloudflare.com)를 만듭니다.
# - 켜진 뒤에는 이 창을 닫아도 계속 돌아갑니다. 끄려면 stop.command를 더블클릭하세요.
# - 처음 한 번은 패키지 설치로 1~3분 걸립니다.
# - 맥이 잠들거나 꺼지면 사이트도 멈춥니다. 노트북은 덮개를 연 채로 두세요.
cd "$(dirname "$0")" || exit 1

pause_exit() { echo; read -r -n 1 -p "아무 키나 누르면 창이 닫힙니다." ; exit "${1:-1}"; }
# 저장해 둔 PID가 정말 이 사이트의 프로세스일 때만 참 (PID 재사용 대비)
ours() { [ -n "$1" ] && ps -p "$1" -o command= 2>/dev/null | grep -Eq 'server\.py|tunnel_keeper|cloudflared|localhost\.run'; }
url_ok() { [ -s .public_url ] && curl -fs -m 20 "$(cat .public_url)/api/health" >/dev/null 2>&1; }
# 주소 지킴이: 터널을 띄우고, 주소가 죽으면 새 주소로 다시 띄움 (scripts/tunnel_keeper.sh)
start_keeper() {
  rm -f .public_url
  nohup bash scripts/tunnel_keeper.sh "$SPID" "$PORT" >/dev/null 2>&1 < /dev/null &
  KPID=$!
  echo "$SPID $KPID $PORT" > .pids
  echo "폰에서 열 수 있는 인터넷 주소를 만드는 중입니다..."
  for _ in $(seq 1 70); do [ -s .public_url ] && break; sleep 1; done
  URL=$(cat .public_url 2>/dev/null)
}
show_url() {
  echo
  echo "════════════════════════════════════════════════════════"
  if [ -n "$URL" ]; then
    echo "  폰·팀원 주소:  $URL"
    echo "  (아래 QR을 폰 카메라로 찍으면 바로 열립니다)"
    .venv/bin/python -c "import qrcode,sys; q=qrcode.QRCode(border=1); q.add_data(sys.argv[1]); q.print_ascii(invert=True)" "$URL" 2>/dev/null
  else
    echo "  인터넷 주소를 만들지 못했습니다. 같은 와이파이에서는 아래 주소로 열 수 있습니다."
  fi
  echo "  이 맥:        http://localhost:$PORT"
  echo "  같은 와이파이: http://$(ipconfig getifaddr en0 2>/dev/null || echo 'IP확인불가'):$PORT"
  echo
  echo "  이 창은 닫아도 됩니다. 사이트는 계속 켜져 있습니다."
  echo "  와이파이가 끊겼다 돌아오면 주소가 새로 바뀔 수 있습니다. 새 주소는 폰주소.txt와 사이트의 '폰으로 열기'에 나옵니다."
  echo "  끄려면 stop.command를 더블클릭하세요."
  echo "════════════════════════════════════════════════════════"
}

# ── 0. 이미 켜져 있으면: 주소가 살아 있으면 보여주고, 죽었으면 주소만 새로 ──
if [ -f .pids ]; then
  read -r OLD_S OLD_T PORT < .pids
  if ours "$OLD_S" && curl -fs "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    SPID=$OLD_S
    if ours "$OLD_T" && url_ok; then
      URL=$(cat .public_url); echo "이미 켜져 있습니다."
    else
      echo "서버는 켜져 있는데 인터넷 주소가 끊겨 있어 새로 만듭니다."
      ours "$OLD_T" && kill "$OLD_T" 2>/dev/null
      pkill -f "tunnel --no-autoupdate --url http://localhost:$PORT" 2>/dev/null
      start_keeper
    fi
    show_url; open "${URL:-http://localhost:$PORT}"; exit 0
  fi
  for p in $OLD_T $OLD_S; do ours "$p" && kill "$p" 2>/dev/null; done   # 반쯤 꺼진 상태 정리
  rm -f .pids .public_url
fi

# ── 1. Python ─────────────────────────────────────────────
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 더블클릭하세요."
  pause_exit 1
fi

# ── 2. 실행 환경과 패키지 (처음 한 번) ────────────────────
if [ ! -x .venv/bin/python ]; then
  echo "처음 실행: 실행 환경을 만드는 중입니다 ($PY)"
  "$PY" -m venv .venv || { echo "실행 환경을 만들지 못했습니다."; pause_exit 1; }
fi
if [ ! -f .venv/.installed ] || [ requirements-server.txt -nt .venv/.installed ]; then
  echo "필요한 패키지를 설치하는 중입니다 (1~3분)..."
  .venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
  .venv/bin/python -m pip install -q -r requirements-server.txt qrcode || { echo "패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요."; pause_exit 1; }
  touch .venv/.installed
fi

# ── 3. API 키 ─────────────────────────────────────────────
# 키를 여기서 묻지 않습니다. 없으면 사이트(폰 포함)의 '실제 데이터 켜기'에서 한 번 넣으면 .env에 저장됩니다.
if [ ! -f .env ] || ! grep -Eq '^EV_API_KEY=[^[:space:]]{10,}' .env; then
  echo "API 키가 아직 없습니다. 사이트가 열리면 '실제 데이터 켜기'에 인증키를 넣으세요."
fi

# ── 4. 서버 켜기 (창을 닫아도 꺼지지 않게 nohup) ───────────
PORT=8000
while lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do PORT=$((PORT + 1)); done
PORT="$PORT" nohup .venv/bin/python server.py > server.log 2>&1 < /dev/null &
SPID=$!
nohup caffeinate -i -w "$SPID" >/dev/null 2>&1 < /dev/null &   # 서버가 켜져 있는 동안 맥이 잠들지 않게
fail() { echo "$1"; kill "$SPID" 2>/dev/null; rm -f .pids .public_url; pause_exit 1; }

for _ in $(seq 1 30); do curl -fs "http://localhost:$PORT/api/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fs "http://localhost:$PORT/api/health" >/dev/null 2>&1 || { tail -20 server.log; fail "서버가 켜지지 않았습니다. 위 기록을 확인하세요."; }

# ── 5. 폰·외부용 인터넷 주소 (Cloudflare 무료 터널, 실패 시 localhost.run) ──
CF=".venv/cloudflared"
if [ ! -x "$CF" ]; then
  case "$(uname -m)" in arm64) f=cloudflared-darwin-arm64.tgz ;; *) f=cloudflared-darwin-amd64.tgz ;; esac
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/$f" | tar -xz -C .venv 2>/dev/null && chmod +x "$CF"
fi
start_keeper
show_url
open "${URL:-http://localhost:$PORT}"
exit 0
