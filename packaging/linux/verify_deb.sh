#!/usr/bin/env bash
set -euo pipefail

deb="${1:?usage: verify_deb.sh /path/to/package.deb VERSION}"
version="${2:?usage: verify_deb.sh /path/to/package.deb VERSION}"
deb="$(readlink -f "$deb")"
package="workbuddy2api"
service="workbuddy2api.service"
config="/etc/workbuddy2api/workbuddy2api.env"
data_dir="/var/lib/workbuddy2api"
base_url="http://127.0.0.1:8790"

fail() {
  echo "VERIFY FAILED: $*" >&2
  exit 1
}

wait_http() {
  local i
  for i in $(seq 1 30); do
    if curl --fail --silent "$base_url/admin" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  sudo systemctl status "$service" --no-pager || true
  sudo journalctl -u "$service" -n 100 --no-pager || true
  fail "service did not become ready"
}

field() {
  dpkg-deb --field "$deb" "$1"
}

echo "== package metadata =="
[ "$(field Package)" = "$package" ] || fail "unexpected package name"
[ "$(field Version)" = "$version" ] || fail "unexpected package version"
[ "$(field Architecture)" = "amd64" ] || fail "unexpected architecture"
field Depends | grep -q "libc6 (>= 2.35)" || fail "libc dependency missing"
field Depends | grep -q "ca-certificates" || fail "ca-certificates dependency missing"
field Depends | grep -q "adduser" || fail "adduser dependency missing"

contents="$(dpkg-deb --contents "$deb")"
printf '%s\n' "$contents" | grep -q './usr/bin/workbuddy2api$' || fail "binary missing from package"
printf '%s\n' "$contents" | grep -q './lib/systemd/system/workbuddy2api.service$' || fail "systemd unit missing"
printf '%s\n' "$contents" | grep -q './usr/share/workbuddy2api/workbuddy2api.env.example$' || fail "config example missing"
printf '%s\n' "$contents" | grep -q './usr/share/doc/workbuddy2api/changelog.gz$' || fail "Debian changelog missing"

control_dir="$(mktemp -d)"
upgrade_root="$(mktemp -d)"
trap 'rm -rf "$control_dir" "$upgrade_root"' EXIT

dpkg-deb --control "$deb" "$control_dir"
for script in postinst prerm postrm; do
  [ -x "$control_dir/$script" ] || fail "$script is not executable"
  bash -n "$control_dir/$script"
done

echo "== first install =="
sudo apt-get install -y "$deb"

command -v workbuddy2api >/dev/null || fail "installed binary not on PATH"
[ "$(workbuddy2api --version)" = "$version" ] || fail "binary version mismatch"
getent passwd workbuddy2api >/dev/null || fail "service user missing"
getent group workbuddy2api >/dev/null || fail "service group missing"
getent passwd workbuddy2api | grep -q ':/usr/sbin/nologin$' || fail "service user must use nologin"

[ -f "$config" ] || fail "config not created"
[ "$(stat -c '%U:%G:%a' "$config")" = "root:root:600" ] || fail "config permissions are not root:root 600"
[ "$(stat -c '%U:%G:%a' "$data_dir")" = "workbuddy2api:workbuddy2api:750" ] || fail "data dir permissions are incorrect"
sudo grep -Eq '^ADMIN_PASSWORD=[0-9a-f]{32}$' "$config" || fail "random admin password was not generated"
sudo grep -Eq '^ADMIN_JWT_SECRET=[0-9a-f]{64}$' "$config" || fail "random JWT secret was not generated"
sudo grep -q '^ADMIN_DATABASE_URL=sqlite:////var/lib/workbuddy2api/workbuddy.db$' "$config" || fail "SQLite default is incorrect"
sudo grep -q '^ADMIN_GATEWAY_LIMIT_BACKEND=memory$' "$config" || fail "memory rate-limit default is missing"

systemctl is-enabled --quiet "$service" || fail "service is not enabled"
systemctl is-active --quiet "$service" || fail "service is not active"
systemctl cat "$service" | grep -q '^User=workbuddy2api$' || fail "service does not run as dedicated user"
systemctl cat "$service" | grep -q '^ExecStart=/usr/bin/workbuddy2api$' || fail "service ExecStart is incorrect"

wait_http
curl --fail --silent "$base_url/static/console.css" >/dev/null
curl --fail --silent "$base_url/api/app/icon" >/dev/null
if sudo journalctl -u "$service" --no-pager | grep -E 'Traceback|ModuleNotFoundError|Failed to execute script'; then
  fail "fatal Python/PyInstaller error found in service journal"
fi
version_json="$(curl --fail --silent "$base_url/api/app/version")"
printf '%s' "$version_json" | python -c 'import json,sys; expected=sys.argv[1]; data=json.load(sys.stdin); assert data["version"] == expected, data' "$version"

admin_user="$(sudo sed -n 's/^ADMIN_USERNAME=//p' "$config")"
admin_password="$(sudo sed -n 's/^ADMIN_PASSWORD=//p' "$config")"
login_json="$(curl --fail --silent -F "username=$admin_user" -F "password=$admin_password" "$base_url/api/login")"
token="$(printf '%s' "$login_json" | python -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
[ -n "$token" ] || fail "admin login did not return a token"
curl --fail --silent -H "X-Admin-Token: $token" "$base_url/api/accounts" >/dev/null
bad_code="$(curl --silent --output /dev/null --write-out '%{http_code}' -F "username=$admin_user" -F 'password=definitely-wrong' "$base_url/api/login")"
[ "$bad_code" = "401" ] || fail "wrong password should return 401"

db="$data_dir/workbuddy.db"
if [ ! -f "$db" ]; then
  sudo ls -la "$data_dir" >&2 || true
  fail "SQLite database file was not created at $db"
fi
[ "$(stat -c '%U:%G' "$db")" = "workbuddy2api:workbuddy2api" ] || fail "SQLite owner is incorrect"
DB_PATH="$db" python - <<'PY'
import os
import sqlite3

connection = sqlite3.connect(os.environ["DB_PATH"])
try:
    table_count = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert table_count > 0, "SQLite database has no tables"
finally:
    connection.close()
PY

password_before="$admin_password"
echo 'CI_PRESERVE_MARKER=linux-deb-upgrade' | sudo tee -a "$config" >/dev/null
echo 'keep-me' | sudo tee "$data_dir/ci-preserve-marker" >/dev/null
sudo chown workbuddy2api:workbuddy2api "$data_dir/ci-preserve-marker"

echo "== real dpkg upgrade path =="
dpkg-deb --raw-extract "$deb" "$upgrade_root"
sed -i "s/^Version: .*/Version: ${version}+ci1/" "$upgrade_root/DEBIAN/control"
upgrade_deb="$control_dir/workbuddy2api-upgrade.deb"
dpkg-deb --root-owner-group --build "$upgrade_root" "$upgrade_deb" >/dev/null
sudo dpkg -i "$upgrade_deb"

[ "$(dpkg-query -W -f='${Version}' "$package")" = "${version}+ci1" ] || fail "upgrade package version not installed"
[ "$(workbuddy2api --version)" = "$version" ] || fail "application version changed in synthetic upgrade"
systemctl is-active --quiet "$service" || fail "service not active after upgrade"
wait_http
sudo grep -q '^CI_PRESERVE_MARKER=linux-deb-upgrade$' "$config" || fail "config was overwritten on upgrade"
[ "$(sudo sed -n 's/^ADMIN_PASSWORD=//p' "$config")" = "$password_before" ] || fail "admin password changed on upgrade"
[ "$(sudo cat "$data_dir/ci-preserve-marker")" = "keep-me" ] || fail "data was lost on upgrade"

echo "== remove preserves configuration and data =="
sudo dpkg -r "$package"
[ ! -e /usr/bin/workbuddy2api ] || fail "binary remained after remove"
[ ! -e /lib/systemd/system/workbuddy2api.service ] || fail "service unit remained after remove"
[ -f "$config" ] || fail "config should be preserved by remove"
[ -f "$data_dir/ci-preserve-marker" ] || fail "data should be preserved by remove"
if systemctl is-active --quiet "$service"; then
  fail "service is still active after remove"
fi

echo "== reinstall preserves configuration and data =="
sudo dpkg -i "$deb"
systemctl is-active --quiet "$service" || fail "service not active after reinstall"
wait_http
sudo grep -q '^CI_PRESERVE_MARKER=linux-deb-upgrade$' "$config" || fail "config was overwritten on reinstall"
[ "$(sudo sed -n 's/^ADMIN_PASSWORD=//p' "$config")" = "$password_before" ] || fail "admin password changed on reinstall"
[ "$(sudo cat "$data_dir/ci-preserve-marker")" = "keep-me" ] || fail "data was lost on reinstall"

echo "== purge removes config but deliberately preserves application data =="
sudo dpkg --purge "$package"
[ ! -e /usr/bin/workbuddy2api ] || fail "binary remained after purge"
[ ! -e /lib/systemd/system/workbuddy2api.service ] || fail "service unit remained after purge"
[ ! -e /etc/workbuddy2api ] || fail "config directory remained after purge"
[ -f "$data_dir/ci-preserve-marker" ] || fail "application data should be preserved by purge"

echo "Linux DEB lifecycle verification passed."
