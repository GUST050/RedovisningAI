#!/bin/bash
# Startar RedovisningAI på en Mac med ett kommando:
#
#   ./scripts/start-mac.sh
#
# Kräver Docker Desktop (reparera/installera med ./scripts/fix-docker-mac.sh). Skriptet
# startar Docker vid behov, skapar .env med slumpade hemligheter, bygger
# och startar alla delar, lägger in demodata första gången och öppnar webbläsaren.
# Det går att köra igen när som helst; det som redan är klart hoppas över.
# Kompatibelt med macOS standard-bash (3.2).

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\n\033[1;34m▶ %s\033[0m\n" "$1"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$1"; }
fail() { printf "\n\033[1;31m✗ %s\033[0m\n" "$1"; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "Skriptet är gjort för macOS. På andra system: se README.md."

for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)" && break
done
export PATH="$PATH:/usr/local/bin"

# `docker info` kan hänga när en Docker-motor har fastnat – fråga därför med tidsgräns (10 s).
docker_ok() {
  command -v docker >/dev/null 2>&1 || return 1
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

# ------------------------------------------------------------------ 1–2. Docker
say "Kontrollerar Docker"
if ! docker_ok; then
  [ -d /Applications/Docker.app ] || fail "Docker Desktop saknas eller är trasigt. Kör:  ./scripts/fix-docker-mac.sh"
  open /Applications/Docker.app 2>/dev/null || true
  echo "Om ett Docker-fönster frågar något: välj Skip / Continue without signing in."
  wait_for_docker 180 || fail "Docker startar inte. Reparera Docker med:  ./scripts/fix-docker-mac.sh"
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
# För lite ledigt utrymme gör att Dockers virtuella disk blir skrivskyddad mitt i bygget
# ("read-only file system"). Stoppa hellre här med en tydlig förklaring.
if [ "${free_gb:-99}" -lt 10 ]; then
  fail "Bara ${free_gb} GB ledigt på Macen – Docker behöver minst 10 GB. Frigör plats ( → Systeminställningar → Allmänt → Lagring) och kör skriptet igen."
fi

# Bygger en avbild i taget (mindre belastning på Dockers disk än parallella byggen).
build_all() {
  docker compose build api && docker compose build web
}

# Docker-motorns inbyggda byggmotor kan få en trasig, skrivskyddad databasfil
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
  # Återanvänd bara om byggmotorn går att starta i den aktuella Docker-motorn.
  if docker buildx inspect --bootstrap "$BUILDER" >/dev/null 2>&1; then
    export BUILDX_BUILDER="$BUILDER"
  else
    docker buildx rm "$BUILDER" >/dev/null 2>&1 || true
  fi
fi

say "Bygger programmet (första gången tar det 3–5 minuter)"
LOG=$(mktemp)
if ! build_all 2>&1 | tee "$LOG"; then
  grep -qiE "read-only file system|no space left|input/output error" "$LOG" ||
    fail "Bygget misslyckades. Klistra in de sista raderna ovan i chatten så hjälper jag dig."
  echo
  echo "Dockers byggmotor har en trasig fil – byter till en egen byggmotor."
  if ! { use_own_builder && build_all; }; then
    fail "Dockers disk är skadad. Reparera med:  ./scripts/fix-docker-mac.sh  och kör sedan det här skriptet igen."
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

# ------------------------------------------------------------------ 5. Demodata
# seed-demo är idempotent: finns demobyrån redan händer inget.
say "Säkerställer demobyrån (anna@demobyran.se)"
docker compose run --rm api redovisningai seed-demo ||
  fail "Demodata kunde inte läggas in. Klistra in raderna ovan i chatten."
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Dev-User: anna@demobyran.se' http://localhost:8000/api/me)" = "200" ] ||
  fail "Inloggningen för anna@demobyran.se fungerar inte. Klistra in raderna ovan i chatten."
ok "Demobyrån finns"

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

  Öppnar http://localhost:3000 – du är automatiskt inloggad som anna@demobyran.se (byråadmin).

  Egen SIE-fil:  Lägg till kund på startsidan → fliken Data → dra in filen.
  Stoppa:        docker compose down        (datan sparas)
  Starta igen:   ./scripts/start-mac.sh
  Radera allt:   docker compose down -v

TXT
open http://localhost:3000
