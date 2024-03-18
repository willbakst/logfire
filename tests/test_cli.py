import io
import json
import os
import re
import shlex
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import call, patch

import pytest
import requests
import requests_mock
from dirty_equals import IsStr

from logfire import VERSION
from logfire._config import LogfireCredentials, sanitize_project_name
from logfire.cli import main
from logfire.exceptions import LogfireConfigError


@pytest.fixture
def logfire_credentials() -> LogfireCredentials:
    return LogfireCredentials(
        token='token',
        project_name='my-project',
        project_url='https://dashboard.logfire.dev',
        logfire_api_url='https://api.logfire.dev',
    )


def test_no_args(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    assert 'usage: logfire [-h] [--version]  ...' in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    main(['--version'])
    assert VERSION in capsys.readouterr().out.strip()


def test_whoami(tmp_dir_cwd: Path, logfire_credentials: LogfireCredentials, capsys: pytest.CaptureFixture[str]) -> None:
    logfire_credentials.write_creds_file(tmp_dir_cwd)
    main(shlex.split(f'whoami --data-dir {tmp_dir_cwd}'))
    # insert_assert(capsys.readouterr().err)
    assert capsys.readouterr().err == (
        f'Credentials loaded from data dir: {tmp_dir_cwd}\n' '\n' 'Logfire project URL: https://dashboard.logfire.dev\n'
    )


def test_whoami_without_data(tmp_dir_cwd: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Change to the temp dir so the test doesn't fail if executed from a folder containing logfire credentials.
    current_dir = os.getcwd()
    os.chdir(tmp_dir_cwd)
    try:
        main(['whoami'])
        # insert_assert(capsys.readouterr().err)
        assert capsys.readouterr().err == f'No Logfire credentials found in {tmp_dir_cwd}/.logfire\n'
    except SystemExit as e:
        assert e.code == 1
    finally:
        os.chdir(current_dir)


def test_whoami_default_dir(
    tmp_dir_cwd: Path, logfire_credentials: LogfireCredentials, capsys: pytest.CaptureFixture[str]
) -> None:
    logfire_credentials.write_creds_file(tmp_dir_cwd / '.logfire')
    main(['whoami'])
    # insert_assert(capsys.readouterr().err)
    assert capsys.readouterr().err == (
        f'Credentials loaded from data dir: {tmp_dir_cwd}/.logfire\n'
        '\n'
        'Logfire project URL: https://dashboard.logfire.dev\n'
    )


def test_clean(
    tmp_dir_cwd: Path,
    logfire_credentials: LogfireCredentials,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'stdin', io.StringIO('y'))
    logfire_credentials.write_creds_file(tmp_dir_cwd)
    main(shlex.split(f'clean --data-dir {str(tmp_dir_cwd)}'))
    assert capsys.readouterr().err == 'Cleaned Logfire data.\n'


def test_inspect(
    tmp_dir_cwd: Path, logfire_credentials: LogfireCredentials, capsys: pytest.CaptureFixture[str]
) -> None:
    logfire_credentials.write_creds_file(tmp_dir_cwd / '.logfire')
    main(['inspect'])
    assert capsys.readouterr().err.startswith('The following packages')


def test_auth(tmp_path: Path) -> None:
    auth_file = tmp_path / 'default.toml'
    with ExitStack() as stack:
        stack.enter_context(patch('logfire.cli.DEFAULT_FILE', auth_file))
        console = stack.enter_context(patch('logfire.cli.Console'))
        webbrowser_open = stack.enter_context(patch('webbrowser.open'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.post(
            'https://api.logfire.dev/v1/device-auth/new/',
            text='{"device_code": "DC", "frontend_auth_url": "FE_URL"}',
        )
        m.get(
            'https://api.logfire.dev/v1/device-auth/wait/DC',
            [
                dict(text='null'),
                dict(text='{"token": "fake_token", "expiration": "fake_exp"}'),
            ],
        )

        main(['auth'])

    # insert_assert(auth_file.read_text())
    assert (
        auth_file.read_text() == '[tokens."https://api.logfire.dev"]\ntoken = "fake_token"\nexpiration = "fake_exp"\n'
    )

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    # insert_assert(console_calls)
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        'print()',
        "print('Welcome to Logfire! :fire:')",
        "print('Before you can send data to Logfire, we need to authenticate you.')",
        'print()',
        "input('Press [bold]Enter[/] to open logfire.dev in your browser...')",
        'print("Please open [bold]FE_URL[/] in your browser to authenticate if it hasn\'t already.")',
        "print('Waiting for you to authenticate with Logfire...')",
        "print('Successfully authenticated!')",
        'print()',
        f"print('Your Logfire credentials are stored in [bold]{auth_file}[/]')",
    ]

    webbrowser_open.assert_called_once_with('FE_URL', new=2)


def test_auth_temp_failure(tmp_path: Path) -> None:
    auth_file = tmp_path / 'default.toml'
    with ExitStack() as stack:
        stack.enter_context(patch('logfire.cli.DEFAULT_FILE', auth_file))
        stack.enter_context(patch('logfire.cli.Console'))
        stack.enter_context(patch('logfire.cli.webbrowser.open'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.post(
            'https://api.logfire.dev/v1/device-auth/new/', text='{"device_code": "DC", "frontend_auth_url": "FE_URL"}'
        )
        m.get(
            'https://api.logfire.dev/v1/device-auth/wait/DC',
            [
                dict(exc=requests.exceptions.ConnectTimeout),
                dict(text='{"token": "fake_token", "expiration": "fake_exp"}'),
            ],
        )

        with pytest.warns(UserWarning, match=r'^Failed to poll for token\. Retrying\.\.\.$'):
            main(['auth'])


def test_auth_permanent_failure(tmp_path: Path) -> None:
    auth_file = tmp_path / 'default.toml'
    with ExitStack() as stack:
        stack.enter_context(patch('logfire.cli.DEFAULT_FILE', auth_file))
        stack.enter_context(patch('logfire.cli.Console'))
        stack.enter_context(patch('logfire.cli.webbrowser.open'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.post(
            'https://api.logfire.dev/v1/device-auth/new/', text='{"device_code": "DC", "frontend_auth_url": "FE_URL"}'
        )
        m.get('https://api.logfire.dev/v1/device-auth/wait/DC', text='Error', status_code=500)

        with pytest.warns(UserWarning, match=r'^Failed to poll for token\. Retrying\.\.\.$'):
            with pytest.raises(LogfireConfigError, match='Failed to poll for token.'):
                main(['auth'])


def test_projecs_list(default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])

        main(['projects', 'list'])

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        'print("No projects found for the current user. You can create a new project by \'logfire projects create\' command")',
    ]


def test_projecs_new_with_project_name_and_org(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new', 'myproject', '--org', 'fake_org'])

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_new_with_project_name_without_org(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))
        confirm_mock = stack.enter_context(patch('rich.prompt.Confirm.ask', side_effect=[True]))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new', 'myproject'])

    assert confirm_mock.mock_calls == [
        call('The project will be created in the organization "fake_org". Continue?', default=True),
    ]

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_new_with_project_name_and_wrong_org(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))
        confirm_mock = stack.enter_context(patch('rich.prompt.Confirm.ask', side_effect=[True]))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new', 'myproject', '--org', 'wrong_org'])

    assert confirm_mock.mock_calls == [
        call('The project will be created in the organization "fake_org". Continue?', default=True),
    ]

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_new_with_project_name_and_default_org(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new', 'myproject', '--default-org'])

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_new_with_project_name_and_default_org_multiple_organizations(
    tmp_dir_cwd: Path, default_credentials: Path
) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get(
            'https://api.logfire.dev/v1/organizations/',
            json=[{'organization_name': 'fake_org'}, {'organization_name': 'fake_default_org'}],
        )
        m.get(
            'https://api.logfire.dev/v1/account/me',
            json={'default_organization': {'organization_name': 'fake_default_org'}},
        )

        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_default_org',
            [create_project_response],
        )

        main(['projects', 'new', 'myproject', '--default-org'])

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_new_without_project_name(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))
        prompt_mock = stack.enter_context(patch('rich.prompt.Prompt.ask', side_effect=['myproject', '']))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new', '--default-org'])

    assert prompt_mock.mock_calls == [call('Enter the project name', default=sanitize_project_name(tmp_dir_cwd.name))]
    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_new_invalid_project_name(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))
        prompt_mock = stack.enter_context(patch('rich.prompt.Prompt.ask', side_effect=['myproject', '']))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new', 'invalid name', '--default-org'])

    assert prompt_mock.mock_calls == [
        call(
            "\nThe project you've entered is invalid. Valid project names:\n"
            '  * may contain lowercase alphanumeric characters\n'
            '  * may contain single hyphens\n'
            '  * may not start or end with a hyphen\n\n'
            'Enter the project name you want to use:',
            default='invalid name',
        ),
    ]
    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_without_project_name_without_org(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))
        confirm_mock = stack.enter_context(patch('rich.prompt.Confirm.ask', side_effect=[True]))
        prompt_mock = stack.enter_context(patch('rich.prompt.Prompt.ask', side_effect=['myproject', '']))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get('https://api.logfire.dev/v1/projects/', json=[])
        m.get('https://api.logfire.dev/v1/organizations/', json=[{'organization_name': 'fake_org'}])
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/projects/fake_org',
            [create_project_response],
        )

        main(['projects', 'new'])

    assert confirm_mock.mock_calls == [
        call('The project will be created in the organization "fake_org". Continue?', default=True),
    ]
    assert prompt_mock.mock_calls == [call('Enter the project name', default=sanitize_project_name(tmp_dir_cwd.name))]
    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project created successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_use(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get(
            'https://api.logfire.dev/v1/projects/',
            json=[{'organization_name': 'fake_org', 'project_name': 'myproject'}],
        )
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/organizations/fake_org/projects/myproject/write-tokens/',
            [create_project_response],
        )

        main(['projects', 'use', 'myproject', '--org', 'fake_org'])

    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project configured successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }


def test_projecs_use_wrong_project(tmp_dir_cwd: Path, default_credentials: Path) -> None:
    with ExitStack() as stack:
        stack.enter_context(patch('logfire._config.LogfireCredentials._get_user_token', return_value=''))
        console = stack.enter_context(patch('logfire.cli.Console'))
        prompt_mock = stack.enter_context(patch('rich.prompt.Prompt.ask', side_effect=['1']))

        m = requests_mock.Mocker()
        stack.enter_context(m)
        m.get(
            'https://api.logfire.dev/v1/projects/',
            json=[{'organization_name': 'fake_org', 'project_name': 'myproject'}],
        )
        create_project_response = {
            'json': {
                'project_name': 'myproject',
                'token': 'fake_token',
                'project_url': 'fake_project_url',
            }
        }
        m.post(
            'https://api.logfire.dev/v1/organizations/fake_org/projects/myproject/write-tokens/',
            [create_project_response],
        )

        main(['projects', 'use', 'wrong-project', '--org', 'fake_org'])

    assert prompt_mock.mock_calls == [
        call('Please select one of the existing project number:\n1. fake_org/myproject\n', choices=['1'], default='1')
    ]
    console_calls = [re.sub(r'^call(\(\).)?', '', str(call)) for call in console.mock_calls]
    assert console_calls == [
        IsStr(regex=r'^\(file=.*'),
        "print('Project configured successfully. You will be able to view it at: fake_project_url')",
    ]

    assert json.loads((tmp_dir_cwd / '.logfire/logfire_credentials.json').read_text()) == {
        **create_project_response['json'],
        'logfire_api_url': 'https://api.logfire.dev',
    }
