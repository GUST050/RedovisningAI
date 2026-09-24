#!/bin/bash
# Startar RedovisningAI på en Mac med ett kommando:
#
#   ./scripts/start-mac.sh
#
# Använder en Docker-motor som redan är igång (Docker Desktop, OrbStack, Colima). Finns
# ingen fungerande motor installeras och startas Colima via Homebrew – en Docker-motor utan
# app som styrs helt från terminalen. Skriptet skapar .env med slumpade hemligheter, bygger
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

ensure_brew() {
  command -v brew >/dev/null 2>&1 && return 0
  echo "Homebrew (pakethanteraren för Mac) behövs."
  printf "Installera Homebrew nu? Du får ange ditt Mac-lösenord. [j/N] "
  read -r svar
  case "$svar" in
    j|J|ja|Ja|y|Y) /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" ;;
    *) fail "Homebrew behövs. Installera från https://brew.sh och kör skriptet igen." ;;
  esac
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)" && break
  done
  command -v brew >/dev/null 2>&1 || fail "Homebrew hittades inte efter installationen. Öppna en ny terminal och kör skriptet igen."
}

brew_formula() {  # installera formler som saknas
  local f
  for f in "$@"; do
    # En varning om länkning (t.ex. när Docker Desktop redan har lagt `docker` i PATH) ska inte
    # stoppa skriptet – kommandona kontrolleras efteråt.
    brew list --formula "$f" >/dev/null 2>&1 || brew install "$f" || true
  done
}

# Colima: Docker-motor i en liten Linux-VM, utan app. Stabilare än en trasig Docker Desktop.
start_colima() {
  say "Startar Docker-motorn Colima (ingen app behövs)"
  # Stäng en hängande Docker Desktop så att den inte krockar.
  pkill -9 -f "/Applications/Docker.app" 2>/dev/null || true
  ensure_brew
  brew_formula colima docker docker-compose docker-buildx
  command -v colima >/dev/null 2>&1 || fail "Colima kunde inte installeras. Klistra in raderna ovan i chatten."
  command -v docker >/dev/null 2>&1 || fail "Docker-kommandot kunde inte installeras. Klistra in raderna ovan i chatten."

  # Gör `docker compose` och `docker buildx` tillgängliga som tillägg till docker-kommandot.
  mkdir -p "$HOME/.docker/cli-plugins"
  ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" "$HOME/.docker/cli-plugins/docker-compose"
  ln -sfn "$(brew --prefix)/opt/docker-buildx/bin/docker-buildx" "$HOME/.docker/cli-plugins/docker-buildx"

  # Docker Desktop lämnar ofta kvar en inloggningshjälpare som inte fungerar utan appen.
  local cfg="$HOME/.docker/config.json"
  if [ -f "$cfg" ] && grep -q '"credsStore"' "$cfg" && ! command -v docker-credential-desktop >/dev/null 2>&1; then
    mv "$cfg" "$cfg.bak-$(date +%Y%m%d%H%M%S)"
    echo '{}' > "$cfg"
  fi

  if ! colima status >/dev/null 2>&1; then
    local major
    major=$(sw_vers -productVersion | cut -d. -f1)
    if [ "$major" -ge 13 ]; then
      colima start --vm-type vz --cpu 2 --memory 4 --disk 40
    else
      brew_formula qemu
      colima start --cpu 2 --memory 4 --disk 40
    fi
  fi
  docker context use colima >/dev/null 2>&1 || true
  wait_for_docker 120 || fail "Colima startade inte. Kör 'colima start' i terminalen och klistra in utskriften i chatten."
}

restart_engine() {
  if command -v colima >/dev/null 2>&1 && colima status >/dev/null 2>&1; then
    echo "Startar om Colima …"
    colima restart && wait_for_docker 120
  else
    start_colima
  fi
}

# ------------------------------------------------------------------ 1–2. Docker-motor
say "Kontrollerar Docker"
if docker_ok; then
  ok "Docker svarar"
elif command -v colima >/dev/null 2>&1; then
  start_colima
else
  # Försök med Docker Desktop om den finns, annars (eller om den inte svarar) Colima.
  if [ -d /Applications/Docker.app ]; then
    open /Applications/Docker.app 2>/dev/null || true
    wait_for_docker 90 || { echo "Docker Desktop svarar inte – byter till Colima."; start_colima; }
  else
    start_colima
  fi
fi
ok "Docker är igång ($(docker --version | cut -d, -f1), motor: $(docker context show 2>/dev/null || echo okänd))"

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
    echo "Byter Docker-motor och försöker en sista gång."
    restart_engine
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
  Stoppa:        docker compose down        (datan sparas; 'colima stop' frigör minnet)
  Starta igen:   ./scripts/start-mac.sh
  Radera allt:   docker compose down -v

TXT
open http://localhost:3000
