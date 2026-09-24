#!/bin/bash
# Startar RedovisningAI på en Mac med ett kommando:
#
#   ./scripts/start-mac.sh
#
# Skriptet installerar Docker Desktop vid behov (via Homebrew), skapar .env med slumpade
# hemligheter, bygger och startar alla delar, lägger in demodata första gången och öppnar
# webbläsaren. Det går att köra igen när som helst; det som redan är klart hoppas över.
# Kompatibelt med macOS standard-bash (3.2).

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\n\033[1;34m▶ %s\033[0m\n" "$1"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$1"; }
fail() { printf "\n\033[1;31m✗ %s\033[0m\n" "$1"; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "Skriptet är gjort för macOS. På andra system: se README.md."

# Docker Desktops kommandon ligger här innan de länkats in i PATH.
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin"

# `docker info` kan hänga när Docker Desktop har fastnat – fråga därför med tidsgräns (10 s).
docker_ok() {
  docker info >/dev/null 2>&1 &
  local pid=$! i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 10 ]; then { kill -9 "$pid"; wait "$pid"; } 2>/dev/null || true; return 1; fi
    sleep 1
  done
  wait "$pid"
}

wait_for_docker() {  # $1 = max antal sekunder
  local start=$SECONDS
  printf "Väntar på Docker"
  while [ $((SECONDS - start)) -lt "$1" ]; do
    if docker_ok; then echo; return 0; fi
    printf "."
    sleep 3
  done
  echo
  return 1
}

# Stänger Docker Desktop helt (även om det har hängt sig) och startar det igen.
restart_docker() {
  echo "Startar om Docker Desktop (stänger alla Docker-processer först) …"
  osascript -e 'quit app "Docker"' >/dev/null 2>&1 &
  sleep 8
  pkill -9 -f "/Applications/Docker.app" 2>/dev/null || true
  sleep 3
  open -a Docker
  wait_for_docker 240
}

# ------------------------------------------------------------------ 1. Docker installerat?
if ! command -v docker >/dev/null 2>&1 && [ ! -d /Applications/Docker.app ]; then
  say "Docker saknas – installerar Docker Desktop"
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew (pakethanteraren för Mac) behövs för att installera Docker automatiskt."
    printf "Installera Homebrew nu? Du får ange ditt Mac-lösenord. [j/N] "
    read -r svar
    case "$svar" in
      j|J|ja|Ja|y|Y)
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
        [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
        ;;
      *)
        fail "Installera Docker Desktop manuellt: https://www.docker.com/products/docker-desktop/ och kör skriptet igen."
        ;;
    esac
  fi
  brew install --cask docker-desktop || brew install --cask docker
  ok "Docker Desktop installerat"
fi

# ------------------------------------------------------------------ 2. Docker igång?
say "Kontrollerar att Docker är igång"
if ! docker_ok; then
  open -a Docker
  echo "Första gången: godkänn villkoren i Docker-fönstret (konto behövs inte – välj Skip)."
  if ! wait_for_docker 180; then
    echo "Docker svarar inte – det har troligen hängt sig."
    restart_docker || fail "Docker startar inte. Öppna Docker Desktop → felsökningsikonen (🐞) → 'Reset to factory defaults', vänta tills det står 'Engine running' och kör skriptet igen."
  fi
fi
ok "Docker är igång ($(docker --version | cut -d, -f1))"

# ------------------------------------------------------------------ 3. .env med hemligheter
if [ ! -f .env ]; then
  say "Skapar .env med slumpade lösenord och nyckel"
  cp .env.example .env
  rnd() { openssl rand -hex "$1"; }
  sed -i '' \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rnd 16)|" \
    -e "s|^RAI_APP_DB_PASSWORD=.*|RAI_APP_DB_PASSWORD=$(rnd 16)|" \
    -e "s|^RAI_MASTER_KEY=.*|RAI_MASTER_KEY=$(rnd 32)|" \
    .env
  ok ".env skapad (checkas aldrig in i git)"
else
  ok ".env finns redan"
fi

# ------------------------------------------------------------------ 4. Bygg och starta
free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
if [ "${free_gb:-99}" -lt 8 ]; then
  echo "Varning: bara ${free_gb} GB ledigt på disken. Docker behöver ungefär 8 GB."
  echo "Frigör utrymme (t.ex. Inställningar → Allmänt → Lagring) om bygget misslyckas."
fi

# Bygger en avbild i taget (mindre belastning på Dockers disk än parallella byggen).
build_all() {
  docker compose build api && docker compose build web
}

# Docker Desktops inbyggda byggmotor kan få en trasig, skrivskyddad databasfil
# (/var/lib/docker/buildkit/...: read-only file system). Då byggs i stället i en egen
# byggmotor (BuildKit i en container) som har sin egen lagring. Den återanvänds nästa gång.
BUILDER=redovisningai-builder
use_own_builder() {
  docker builder prune -af >/dev/null 2>&1 || true
  if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
  fi
  docker buildx inspect --bootstrap "$BUILDER" >/dev/null
  export BUILDX_BUILDER="$BUILDER"
  ok "Bygger med egen byggmotor ($BUILDER)"
}
if docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  export BUILDX_BUILDER="$BUILDER"
fi

say "Bygger programmet (första gången tar det 3–5 minuter)"
LOG=$(mktemp)
if ! build_all 2>&1 | tee "$LOG"; then
  grep -qiE "read-only file system|no space left|input/output error" "$LOG" ||
    fail "Bygget misslyckades. Klistra in de sista raderna ovan i chatten så hjälper jag dig."
  echo
  echo "Docker Desktops byggmotor har en trasig fil – byter till en egen byggmotor."
  if ! { use_own_builder && build_all; }; then
    echo "Startar om Docker och försöker en sista gång."
    restart_docker || fail "Docker startade inte om. Öppna Docker Desktop (Program → Docker) och kör skriptet igen."
    use_own_builder
    build_all || fail "Bygget misslyckades igen. Klistra in de sista raderna ovan i chatten."
  fi
fi
rm -f "$LOG"
ok "Programmet är byggt"

say "Startar programmet"
docker compose up -d

printf "Väntar på API:t"
for _ in $(seq 1 90); do
  curl -fsS http://localhost:8000/health >/dev/null 2>&1 && break
  printf "."
  sleep 2
done
echo
curl -fsS http://localhost:8000/health >/dev/null 2>&1 || {
  docker compose ps
  docker compose logs --tail=40 migrate api
  fail "API:t svarar inte. Klistra in utskriften ovan i chatten så hjälper jag dig."
}
ok "API:t svarar"

# ------------------------------------------------------------------ 5. Demodata (bara första gången)
if [ "$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Dev-User: anna@demobyran.se' http://localhost:8000/api/me)" != "200" ]; then
  say "Lägger in demobyrån med fyra kunder"
  docker compose run --rm api redovisningai seed-demo
  ok "Demodata klar"
else
  ok "Demodata finns redan"
fi

printf "Väntar på webben"
for _ in $(seq 1 60); do
  curl -fsS http://localhost:3000/login >/dev/null 2>&1 && break
  printf "."
  sleep 2
done
echo
curl -fsS http://localhost:3000/login >/dev/null 2>&1 || {
  docker compose logs --tail=40 web
  fail "Webben svarar inte. Klistra in utskriften ovan i chatten."
}

ok "Klart!"
cat <<'TXT'

  Öppnar http://localhost:3000
  Logga in som:  anna@demobyran.se   (byråadmin)
                 lisa@demobyran.se   (läsare)

  Egen SIE-fil:  Lägg till kund på startsidan → fliken Data → dra in filen.
  Stoppa:        docker compose down        (datan sparas)
  Starta igen:   ./scripts/start-mac.sh
  Radera allt:   docker compose down -v

TXT
open http://localhost:3000
