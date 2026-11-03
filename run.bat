@echo off
chcp 65001 >nul
rem 충북 EV 요금 관제 실행 (Windows: 이 파일을 더블클릭)
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.9 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치할 때 "Add python.exe to PATH"를 체크하세요.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행: 실행 환경을 만드는 중입니다...
  %PY% -m venv .venv || goto :fail
)
if not exist ".venv\.installed" (
  echo 필요한 패키지를 설치하는 중입니다 ^(1~3분^)...
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip >nul 2>nul
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-server.txt || goto :fail
  type nul > ".venv\.installed"
)

if exist ".env" findstr /r /c:"^EV_API_KEY=..........*" ".env" >nul && goto :run
echo.
echo 공공데이터포털 인증키^(Decoding 키^)를 붙여 넣고 Enter를 누르세요.
echo 키가 없으면 그냥 Enter: 가상 데이터로 실행합니다.
set "KEY="
set /p "KEY="
if defined KEY (
  > ".env" echo EV_API_KEY=%KEY%
  echo 키를 .env에 저장했습니다.
)

:run
echo.
echo 사이트 주소: http://localhost:8000   ^(이 창을 닫으면 꺼집니다^)
start "" "http://localhost:8000"
".venv\Scripts\python.exe" server.py
pause
exit /b 0

:fail
echo 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요.
pause
exit /b 1
