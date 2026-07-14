#!/usr/bin/env bats

setup() {
  TEST_DIR="$(mktemp -d)"
  SCRIPT_DIR="$PWD/dist"
  INSTALL_SH="$SCRIPT_DIR/install.sh"
  export TEST_INSTALL_PREFIX="$TEST_DIR/prefix"
  export TEST_INSTALL_USER_DIR="$TEST_DIR/home/.local"
}

teardown() {
  rm -rf "$TEST_DIR"
}

@test "install.sh exists and is executable" {
  [ -f "$INSTALL_SH" ]
  [ -x "$INSTALL_SH" ]
}

@test "install.sh --help shows usage" {
  run bash "$INSTALL_SH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage"* ]] || [[ "$output" == *"usage"* ]] || [[ "$output" == *"install"* ]]
}

@test "install.sh creates install directory" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_PREFIX/bin" ]
}

@test "install.sh copies binary to prefix/bin" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -f "$TEST_INSTALL_PREFIX/bin/gludd" ]
  [ -x "$TEST_INSTALL_PREFIX/bin/gludd" ]
}

@test "install.sh creates config directory /etc/gludd" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_PREFIX/etc/gludd" ]
}

@test "install.sh creates var data directory" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_PREFIX/var/lib/gludd" ]
}

@test "install.sh creates log directory" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_PREFIX/var/log/gludd" ]
}

@test "install.sh sets proper permissions on binary" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -x "$TEST_INSTALL_PREFIX/bin/gludd" ]
}

@test "install.sh handles --prefix flag" {
  CUSTOM_PREFIX="$TEST_DIR/custom"
  run bash "$INSTALL_SH" --prefix "$CUSTOM_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$CUSTOM_PREFIX/bin" ]
  [ -d "$CUSTOM_PREFIX/etc/gludd" ]
  [ -d "$CUSTOM_PREFIX/var/lib/gludd" ]
  [ -d "$CUSTOM_PREFIX/var/log/gludd" ]
}

@test "install.sh handles --user flag (user-mode install)" {
  run bash "$INSTALL_SH" --user --prefix "$TEST_INSTALL_USER_DIR" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_USER_DIR/bin" ]
}

@test "install.sh creates system docs" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_PREFIX/share/doc/gludd" ] || [ -f "$TEST_INSTALL_PREFIX/share/doc/gludd/README.md" ]
}

@test "install.sh is idempotent (running twice does not break)" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -d "$TEST_INSTALL_PREFIX/bin" ]
  [ -f "$TEST_INSTALL_PREFIX/bin/gludd" ]
}

@test "install.sh handles missing binary gracefully" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --binary "$TEST_DIR/nonexistent-gludd" --no-systemd
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found"* ]] || [[ "$output" == *"missing"* ]] || [[ "$output" == *"No such file"* ]] || [[ "$output" == *"does not exist"* ]]
}

@test "install.sh uninstall mode removes installed files" {
  run bash "$INSTALL_SH" --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ -f "$TEST_INSTALL_PREFIX/bin/gludd" ]
  run bash "$INSTALL_SH" --uninstall --prefix "$TEST_INSTALL_PREFIX" --no-systemd
  [ "$status" -eq 0 ]
  [ ! -f "$TEST_INSTALL_PREFIX/bin/gludd" ]
}
