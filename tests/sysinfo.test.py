"""sysinfo probes: disk usage line and CPU temperature parsing."""

from core.sysinfo import cpu_temp_line, cpu_temperature_c, disk_space_line


def test_disk_space_line_reports_the_target_path(tmp_path):
    line = disk_space_line(tmp_path)

    assert line.startswith("disk: ")
    assert "GB used" in line
    assert str(tmp_path) in line


def test_disk_space_line_handles_a_missing_path(tmp_path):
    missing = tmp_path / "nope"

    assert disk_space_line(missing) == f"disk: n/a ({missing})"


def test_cpu_temperature_picks_the_hottest_zone(tmp_path):
    for index, millidegrees in enumerate(("42000", "55000", "48000")):
        zone = tmp_path / f"thermal_zone{index}"
        zone.mkdir()
        (zone / "temp").write_text(millidegrees)

    assert cpu_temperature_c(tmp_path) == 55.0
    assert cpu_temp_line(tmp_path) == "cpu temp: 55.0 C"


def test_cpu_temperature_ignores_unreadable_zones(tmp_path):
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text("bukan angka")

    assert cpu_temperature_c(tmp_path) is None
    assert cpu_temp_line(tmp_path) == "cpu temp: n/a"
