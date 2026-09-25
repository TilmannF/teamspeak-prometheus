"""The container healthcheck: finding the exporter, resolving its port, probing.

``/proc`` is faked in a temporary directory and the probe is injected, so these
tests need neither Linux nor a socket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import healthcheck

OWN_PID = 999_999
SECRET = 'not-to-be-printed'


def fake_proc(tmp_path: Path, processes: dict[int, list[str] | None]) -> Path:
    """Build ``<pid>/cmdline`` files; ``None`` makes an unreadable entry."""

    proc = tmp_path / 'proc'
    proc.mkdir()
    (proc / 'self').mkdir()  # non-numeric entries exist in real /proc too
    for pid, argv in processes.items():
        entry = proc / str(pid)
        entry.mkdir()
        if argv is not None:
            (entry / 'cmdline').write_bytes(
                b'\0'.join(a.encode() for a in argv) + b'\0'
            )
    return proc


def argv_in(proc: Path) -> list[str] | None:
    return healthcheck.exporter_argv(proc, own_pid=OWN_PID)


# -- finding the exporter ----------------------------------------------------


def test_the_exporter_as_pid_1_is_found_with_its_flags(tmp_path):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py', '--metricsport', '9100']})

    assert argv_in(proc) == ['--metricsport', '9100']


def test_the_default_command_has_no_flags(tmp_path):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py']})

    assert argv_in(proc) == []


def test_the_exporter_behind_docker_init_is_found(tmp_path):
    # `docker run --init`: PID 1 is docker-init, whose own arguments are the
    # exporter command, so either process yields the same flags.
    proc = fake_proc(
        tmp_path,
        {
            1: [
                '/sbin/docker-init',
                '--',
                'python',
                '/app/app.py',
                '--metricsport',
                '9100',
            ],
            7: ['python', '/app/app.py', '--metricsport', '9100'],
        },
    )

    assert argv_in(proc) == ['--metricsport', '9100']


def test_the_exporter_behind_a_shell_wrapper_is_found(tmp_path):
    # `sh -c "python /app/app.py ..."` passes the command as one argument, so
    # the shell is not mistaken for the exporter; its child is found.
    proc = fake_proc(
        tmp_path,
        {
            1: ['sh', '-c', 'python /app/app.py --metricsport 9100'],
            7: ['python', '/app/app.py', '--metricsport', '9100'],
        },
    )

    assert argv_in(proc) == ['--metricsport', '9100']


def test_interpreter_options_before_the_script_are_ignored(tmp_path):
    proc = fake_proc(
        tmp_path, {1: ['python', '-u', '/app/app.py', '--loglevel', 'debug']}
    )

    assert argv_in(proc) == ['--loglevel', 'debug']


def test_the_lowest_matching_pid_wins(tmp_path):
    proc = fake_proc(
        tmp_path,
        {
            12: ['python', '/app/app.py', '--metricsport', '9200'],
            3: ['python', '/app/app.py', '--metricsport', '9100'],
        },
    )

    assert argv_in(proc) == ['--metricsport', '9100']


def test_a_script_merely_ending_in_app_py_is_not_the_exporter(tmp_path):
    proc = fake_proc(
        tmp_path, {1: ['python', '/srv/myapp.py', '--metricsport', '9100']}
    )

    assert argv_in(proc) is None


def test_the_healthcheck_process_itself_is_skipped(tmp_path):
    proc = fake_proc(
        tmp_path, {OWN_PID: ['python', '/app/app.py', '--metricsport', '1']}
    )

    assert argv_in(proc) is None


def test_unreadable_and_vanished_processes_are_skipped(tmp_path):
    proc = fake_proc(
        tmp_path,
        {1: None, 2: [], 5: ['python', '/app/app.py', '--metricsport', '9100']},
    )

    assert argv_in(proc) == ['--metricsport', '9100']


def test_no_exporter_process_gives_none(tmp_path):
    proc = fake_proc(tmp_path, {1: ['sleep', 'infinity']})

    assert argv_in(proc) is None


def test_a_missing_procfs_gives_none(tmp_path):
    assert argv_in(tmp_path / 'no-proc-here') is None


# -- resolving the port ------------------------------------------------------


@pytest.mark.parametrize(
    ('argv', 'env', 'expected'),
    [
        ([], {}, 8000),
        (['--metricsport', '9100'], {}, 9100),
        ([], {'METRICS_PORT': '9200'}, 9200),
        # environment variables win over flags, as in the exporter
        (['--metricsport', '9100'], {'METRICS_PORT': '9200'}, 9200),
        (['--metricsport=9100', '--ts3password', SECRET], {}, 9100),
    ],
)
def test_the_port_is_resolved_exactly_like_the_exporter(argv, env, expected):
    assert healthcheck.metrics_port(argv, env) == expected


def test_an_invalid_command_line_is_reported_without_echoing_it(capsys):
    argv = ['--ts3password', SECRET, '--metricsport', SECRET]

    with pytest.raises(healthcheck.HealthcheckError) as caught:
        healthcheck.metrics_port(argv, {})

    output = capsys.readouterr()
    assert SECRET not in str(caught.value) + output.out + output.err


def test_a_help_flag_is_reported_without_printing_the_help(capsys):
    with pytest.raises(healthcheck.HealthcheckError):
        healthcheck.metrics_port(['--help'], {})

    assert capsys.readouterr().out == ''


def test_an_invalid_configuration_is_reported_without_echoing_it():
    with pytest.raises(healthcheck.HealthcheckError, match='configuration'):
        healthcheck.metrics_port(['--ts3password', SECRET], {'METRICS_PORT': '0'})


# -- checking ----------------------------------------------------------------


def test_check_probes_the_port_of_the_running_exporter(tmp_path):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py', '--metricsport', '9100']})
    probed: list[int] = []

    port = healthcheck.check({}, proc, probed.append)

    assert port == probed[0] == 9100


def test_check_without_an_exporter_falls_back_to_the_environment(tmp_path):
    proc = fake_proc(tmp_path, {1: ['sleep', 'infinity']})
    probed: list[int] = []

    healthcheck.check({'METRICS_PORT': '9300'}, proc, probed.append)

    assert probed == [9300]


def test_a_failing_probe_propagates(tmp_path):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py']})

    def refuse(port: int) -> None:
        raise healthcheck.HealthcheckError(f'port {port} refused')

    with pytest.raises(healthcheck.HealthcheckError, match='8000'):
        healthcheck.check({}, proc, refuse)


def test_main_reports_unhealthy_with_exit_code_1(tmp_path, capsys):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py']})

    def refuse(port: int) -> None:
        raise healthcheck.HealthcheckError(f'port {port} refused')

    assert healthcheck.main(env={}, proc=proc, prober=refuse) == 1
    assert 'unhealthy: port 8000 refused' in capsys.readouterr().out


def test_main_reports_healthy_with_exit_code_0(tmp_path, capsys):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py', '--metricsport', '9100']})

    assert healthcheck.main(env={}, proc=proc, prober=lambda port: None) == 0
    assert 'port 9100 answers' in capsys.readouterr().out


# -- the password never reaches the healthcheck output ------------------------


def test_every_password_given_to_the_exporter_is_a_secret(tmp_path):
    proc = fake_proc(
        tmp_path, {1: ['python', '/app/app.py', '--ts3password', 'from-flag']}
    )

    assert set(
        healthcheck.exporter_secrets({'TEAMSPEAK_PASSWORD': 'from-env'}, proc)
    ) == {'from-flag', 'from-env'}


def test_secrets_survive_an_invalid_exporter_command_line(tmp_path):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py', '--bogus']})

    assert healthcheck.exporter_secrets({'TEAMSPEAK_PASSWORD': 'x'}, proc) == ['x']


@pytest.mark.parametrize(
    ('argv', 'env'),
    [
        (['--metricsport', '9100', '--ts3password', '9100'], {}),
        ([], {'METRICS_PORT': '9100', 'TEAMSPEAK_PASSWORD': '9100'}),
    ],
    ids=['flag', 'environment'],
)
@pytest.mark.parametrize('healthy', [True, False])
def test_a_password_equal_to_the_port_is_never_printed(
    tmp_path, capsys, argv, env, healthy
):
    proc = fake_proc(tmp_path, {1: ['python', '/app/app.py', *argv]})

    def prober(port: int) -> None:
        if not healthy:
            raise healthcheck.HealthcheckError(
                f'http://127.0.0.1:{port}/metrics did not answer'
            )

    healthcheck.main(env=env, proc=proc, prober=prober)

    out = capsys.readouterr().out
    assert '9100' not in out
    assert '*censored*' in out


def test_every_repeated_password_of_the_exporter_is_a_secret(tmp_path):
    proc = fake_proc(
        tmp_path,
        {1: ['python', '/app/app.py', '--ts3password', '9100', '--ts3password', 'x']},
    )

    assert set(healthcheck.exporter_secrets({}, proc)) == {'9100', 'x'}
