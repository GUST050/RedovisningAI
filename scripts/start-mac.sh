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
if ! docker info >/dev/null 2>&1; then
  say "Startar Docker Desktop"
  open -a Docker
  echo "Första gången: godkänn villkoren i Docker-fönstret (konto behövs inte – välj Skip)."
  printf "Väntar på Docker"
  for _ in $(seq 1 90); do
    docker info >/dev/null 2>&1 && break
    printf "."
    sleep 2
  done
  echo
  docker info >/dev/null 2>&1 || fail "Docker startade inte inom 3 minuter. Öppna Docker Desktop, vänta tills det står 'Engine running' och kör skriptet igen."
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
say "Bygger och startar programmet (första gången tar det 3–5 minuter)"
docker compose up -d --build

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
