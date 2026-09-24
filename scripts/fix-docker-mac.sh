#!/bin/bash
# Reparerar Docker Desktop på Mac genom en ren ominstallation:
#
#   ./scripts/fix-docker-mac.sh
#
# 1. Stänger alla Docker-processer.
# 2. Avinstallerar med Dockers eget avinstallationsprogram och tar bort kvarlämnad data
#    (bl.a. den virtuella disken där byggmotorns trasiga, skrivskyddade fil låg).
# 3. Installerar om Docker Desktop via Homebrew och godkänner licensen från terminalen.
# 4. Startar Docker och kontrollerar att både körning OCH bygge fungerar.
#
# Inga projekt- eller kunddata påverkas; bara Dockers egna filer tas bort.
# Kompatibelt med macOS standard-bash (3.2).

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\n\033[1;34m▶ %s\033[0m\n" "$1"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$1"; }
fail() { printf "\n\033[1;31m✗ %s\033[0m\n" "$1"; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "Skriptet är gjort för macOS."
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)" && break
done
command -v brew >/dev/null 2>&1 || fail "Homebrew saknas. Installera från https://brew.sh och kör skriptet igen."

APP="${RAI_DOCKER_APP:-/Applications/Docker.app}"  # kan överstyras för test
DOCKER_BIN="$APP/Contents/Resources/bin/docker"

docker_ok() {  # docker info med tidsgräns 10 s (den kan hänga när Docker har fastnat)
  [ -x "$DOCKER_BIN" ] || return 1
  "$DOCKER_BIN" info >/dev/null 2>&1 &
  local pid=$! i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 10 ]; then { kill -9 "$pid"; wait "$pid"; } 2>/dev/null || true; return 1; fi
    sleep 1
  done
  wait "$pid"
}

# ------------------------------------------------------------------ 0. Hitta felet
say "Letar efter orsaken"
free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
echo "Ledigt på Macens disk: ${free_gb} GB"
LOGDIR="$HOME/Library/Containers/com.docker.docker/Data/log"
if [ -d "$LOGDIR" ]; then
  hits=$(grep -rhaiE "EXT4-fs error|Remounting filesystem read-only|read-only file system|No space left|I/O error" \
           "$LOGDIR" 2>/dev/null | tail -n 8 || true)
  if [ -n "$hits" ]; then
    echo "Dockers egna loggar visar:"
    echo "$hits" | cut -c1-200 | sed 's/^/    /'
  else
    echo "Dockers loggar visar inga diskfel."
  fi
fi
if [ "${free_gb:-0}" -lt 15 ]; then
  cat <<TXT

ORSAKEN: Macen har bara ${free_gb} GB ledigt. Dockers virtuella disk växer medan programmet
byggs, och när Macen tar slut på utrymme gör Docker sin disk skrivskyddad – det är exakt
felet "read-only file system". En ominstallation hjälper inte förrän det finns plats.

Frigör minst 15 GB och kör sedan skriptet igen:
  1.  → Systeminställningar → Allmänt → Lagring – se vad som tar plats.
  2. Töm papperskorgen, ta bort stora filer i Hämtade filer, gamla iOS-säkerhetskopior m.m.
  3. Om iCloud Drive är fullt och "Optimera lagring" är på kan filer inte flyttas till
     molnet – frigör plats i iCloud eller stäng av synk av Skrivbord och Dokument.
TXT
  fail "För lite ledigt utrymme (${free_gb} GB, behöver 15 GB)."
fi
ok "Tillräckligt med ledigt utrymme"

cat <<'TXT'

Det här skriptet installerar om Docker Desktop från grunden.
Du kommer att få ange ditt Mac-lösenord (tecknen syns inte när du skriver).
TXT
sudo -v || fail "Lösenordet behövs för att ta bort Dockers systemdelar."
# Håll sudo vid liv under körningen.
( while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) 2>/dev/null &

# ------------------------------------------------------------------ 1. Stäng Docker
say "Stänger Docker"
osascript -e 'quit app "Docker"' >/dev/null 2>&1 &
sleep 5
pkill -9 -f "$APP" 2>/dev/null || true
pkill -9 -f "com.docker" 2>/dev/null || true
sleep 2
ok "Docker är stängt"

# ------------------------------------------------------------------ 2. Avinstallera och rensa
say "Avinstallerar Docker Desktop"
if [ -x "$APP/Contents/MacOS/uninstall" ]; then
  "$APP/Contents/MacOS/uninstall" || true
fi
brew uninstall --cask --force docker-desktop >/dev/null 2>&1 || true
brew uninstall --cask --force docker >/dev/null 2>&1 || true

# Dockers egna filer enligt Dockers anvisning för manuell avinstallation.
if [ -d "$HOME/.docker" ]; then
  mv "$HOME/.docker" "$HOME/.docker.bak-$(date +%Y%m%d%H%M%S)"
fi
rm -rf \
  "$HOME/Library/Group Containers/group.com.docker" \
  "$HOME/Library/Application Support/Docker Desktop" \
  "$HOME/Library/Preferences/com.docker.docker.plist" \
  "$HOME/Library/Saved Application State/com.electron.docker-frontend.savedState" \
  "$HOME/Library/Logs/Docker Desktop" \
  "$HOME/Library/Caches/com.docker.docker" 2>/dev/null || true
# Den virtuella disken (Docker.raw) ligger här. macOS skyddar själva mappen, men innehållet går
# att ta bort – om terminalen har behörighet.
rm -rf "$HOME/Library/Containers/com.docker.docker/Data" 2>/dev/null || true
if [ -e "$HOME/Library/Containers/com.docker.docker/Data" ]; then
  cat <<'TXT'

macOS hindrade terminalen från att ta bort Dockers gamla disk. Ge terminalen behörighet:
  1. Systeminställningar → Integritet och säkerhet → Full skivåtkomst
  2. Slå PÅ "Visual Studio Code" (eller "Terminal" om du kör där)
  3. Stäng VS Code helt (Cmd+Q), öppna igen och kör skriptet en gång till.
TXT
  fail "Dockers gamla disk finns kvar."
fi
sudo rm -rf "$APP"
sudo rm -f /Library/PrivilegedHelperTools/com.docker.vmnetd /Library/LaunchDaemons/com.docker.vmnetd.plist \
  /Library/PrivilegedHelperTools/com.docker.socket /Library/LaunchDaemons/com.docker.socket.plist
# Kvarlämnade länkar till kommandon i den borttagna appen.
for f in /usr/local/bin/docker /usr/local/bin/docker-compose /usr/local/bin/kubectl.docker \
         /usr/local/bin/hub-tool /usr/local/bin/com.docker.cli /usr/local/bin/docker-credential-desktop \
         /usr/local/bin/docker-credential-osxkeychain /usr/local/bin/docker-credential-ecr-login; do
  [ -L "$f" ] && sudo rm -f "$f"
done
[ -d /usr/local/cli-plugins ] && sudo rm -rf /usr/local/cli-plugins
ok "Gamla Docker är borttaget"

# ------------------------------------------------------------------ 3. Installera om
say "Installerar Docker Desktop (ungefär 600 MB)"
brew install --cask docker-desktop
[ -d "$APP" ] || fail "Installationen misslyckades. Klistra in raderna ovan i chatten."
# Godkänn licensen och installera systemdelarna direkt, så att första starten inte fastnar i en dialog.
sudo "$APP/Contents/MacOS/install" --accept-license --user="${USER:-$(id -un)}" || true
ok "Docker Desktop installerat"

# ------------------------------------------------------------------ 4. Starta och kontrollera
say "Startar Docker (första starten tar 1–3 minuter)"
open "$APP"
echo "Om ett Docker-fönster frågar något: välj Skip / Continue without signing in."
printf "Väntar på Docker"
start=$SECONDS
until docker_ok; do
  [ $((SECONDS - start)) -lt 300 ] || { echo; fail "Docker startade inte inom 5 minuter. Ta en skärmbild av Docker-fönstret och skicka i chatten."; }
  printf "."
  sleep 3
done
echo
ok "Docker är igång"

export PATH="$APP/Contents/Resources/bin:$PATH"
say "Testar att köra en container"
docker run --rm hello-world >/dev/null || fail "Docker kan inte köra containrar. Klistra in raderna ovan i chatten."
ok "Körning fungerar"

say "Testar att bygga (det som var trasigt)"
printf 'FROM alpine:3.20\nRUN echo ok > /ok\n' | docker build -q -t redovisningai-byggtest - >/dev/null ||
  fail "Bygget fungerar fortfarande inte. Klistra in raderna ovan i chatten."
docker rmi redovisningai-byggtest >/dev/null 2>&1 || true
ok "Bygge fungerar"

cat <<'TXT'

  Docker är reparerat. Starta programmet med:

    ./scripts/start-mac.sh

TXT
