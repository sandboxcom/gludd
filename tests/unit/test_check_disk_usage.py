from __future__ import annotations

from scripts import check_disk_usage as disk


def test_disk_check_passes_under_both_thresholds(monkeypatch, capsys) -> None:
    monkeypatch.setattr(disk, "_gludd_tmp_size_mb", lambda: 12.5)
    monkeypatch.setattr(disk, "_disk_usage_pct", lambda: 42.0)

    assert disk.main() == 0
    assert "disk ok" in capsys.readouterr().out


def test_disk_check_fails_when_gludd_tmp_exceeds_limit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(disk, "_gludd_tmp_size_mb", lambda: disk.GLUDD_TMP_LIMIT_MB + 0.1)
    monkeypatch.setattr(disk, "_disk_usage_pct", lambda: 42.0)

    assert disk.main() == 1
    err = capsys.readouterr().err
    assert "DISK FAIL" in err
    assert "/tmp/gludd-* total" in err


def test_disk_check_fails_when_root_disk_exceeds_limit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(disk, "_gludd_tmp_size_mb", lambda: 1.0)
    monkeypatch.setattr(disk, "_disk_usage_pct", lambda: disk.DISK_USAGE_PCT_LIMIT + 0.1)

    assert disk.main() == 1
    err = capsys.readouterr().err
    assert "DISK FAIL" in err
    assert "disk usage" in err


def test_disk_check_reports_all_failures(monkeypatch, capsys) -> None:
    monkeypatch.setattr(disk, "_gludd_tmp_size_mb", lambda: disk.GLUDD_TMP_LIMIT_MB + 10)
    monkeypatch.setattr(disk, "_disk_usage_pct", lambda: disk.DISK_USAGE_PCT_LIMIT + 5)

    assert disk.main() == 1
    err = capsys.readouterr().err
    assert err.count("DISK FAIL") == 2


def test_disk_usage_parser_handles_df_output(monkeypatch) -> None:
    output = "Filesystem 512-blocks Used Available Capacity iused ifree %iused Mounted on" + chr(10)
    output += "/dev/disk3s1 100 91 9 91% 1 2 3% /" + chr(10)
    monkeypatch.setattr(disk.subprocess, "check_output", lambda *args, **kwargs: output)

    assert disk._disk_usage_pct() == 91.0


def test_disk_usage_parser_fails_open_on_bad_df(monkeypatch) -> None:
    monkeypatch.setattr(disk.subprocess, "check_output", lambda *args, **kwargs: "bad" + chr(10))

    assert disk._disk_usage_pct() == 0.0
