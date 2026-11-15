"""Offline deployment contracts and the actual POSIX-shell template renderer."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PROXY = ROOT / 'deploy/llm-proxy'


@pytest.fixture
def shell():
    executable = shutil.which('sh') or shutil.which('bash')
    if executable is None:
        pytest.skip('POSIX shell not installed; YAML contracts still run')
    return executable


@pytest.mark.parametrize('url', [
    'https://subscription.example.invalid/clash?token=FAKE_TOKEN&format=yaml',
    'https://subscription.example.invalid/a?token=a\'"\\&value=$HOME&x=`id`',
    'https://subscription.example.invalid/a?token=$(touch marker)&x=__SUBSCRIPTION_URL__',
])
def test_shell_renderer_preserves_url_as_one_yaml_scalar(shell, tmp_path, url):
    destination = tmp_path / 'config.yaml'
    result = subprocess.run([shell, (PROXY / 'render-config.sh').as_posix(), (PROXY / 'config.template.yaml').as_posix(), destination.as_posix()],
                            env={**os.environ, 'SUBSCRIPTION_URL': url}, cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load(destination.read_text(encoding='utf-8'))
    assert config['proxy-providers']['subscription']['url'] == url
    assert not (tmp_path / 'marker').exists()
    assert result.stdout == b'' and result.stderr == b''


@pytest.mark.parametrize('url', [None, '', ' ', 'ftp://subscription.example.invalid/FAKE_TOKEN',
                               'https://subscription.example.invalid/a\ninjected: true',
                               'https://subscription.example.invalid/a\rtoken=FAKE_TOKEN',
                               'https://subscription.example.invalid/a\ttoken=FAKE_TOKEN'])
def test_bad_subscription_stops_without_printing_it(shell, tmp_path, url):
    env = {**os.environ}
    env.pop('SUBSCRIPTION_URL', None)
    if url is not None:
        env['SUBSCRIPTION_URL'] = url
    destination = tmp_path / 'config.yaml'
    result = subprocess.run([shell, (PROXY / 'render-config.sh').as_posix(), (PROXY / 'config.template.yaml').as_posix(), destination.as_posix()],
                            env=env, cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b'SUBSCRIPTION_URL' in result.stderr
    assert b'FAKE_TOKEN' not in result.stderr and b'example.invalid' not in result.stderr
    assert not destination.exists()


def test_template_cannot_enable_subscription_rules_or_controller():
    config = yaml.safe_load((PROXY / 'config.template.yaml').read_text(encoding='utf-8'))
    assert config['mixed-port'] == 7890 and config['allow-lan'] is True and config['bind-address'] == '*'
    assert config['log-level'] == 'warning' and config['mode'] == 'rule'
    assert not config.get('external-controller') and not config.get('external-controller-unix')
    assert config['dns']['enable'] is False and config['tun']['enable'] is False
    provider = config['proxy-providers']['subscription']
    assert provider['type'] == 'http' and provider['interval'] == 3600
    assert provider['health-check']['enable'] is True
    assert provider['health-check']['url'] == 'https://openrouter.ai/api/v1/models'
    group = config['proxy-groups'][0]
    assert group['type'] == 'url-test' and group['use'] == ['subscription']
    assert group['empty-fallback'] == 'REJECT'
    assert config['rules'] == ['MATCH,' + group['name']]


def test_main_compose_has_only_the_dedicated_external_network():
    config = yaml.safe_load((PROXY / 'docker-compose.yml').read_text(encoding='utf-8'))
    assert set(config['services']) == {'llm-proxy'}
    service = config['services']['llm-proxy']
    assert service['image'].startswith('metacubex/mihomo:v1.19.33@sha256:')
    assert service['networks'] == {'llm': {'aliases': ['tinvest-llm-proxy']}}
    assert config['networks']['llm']['external'] is True
    assert config['networks']['llm']['name'].startswith('${LLM_NETWORK:?')
    assert 'ports' not in service and service['expose'] == ['7890']
    assert service['read_only'] is True and service['cap_drop'] == ['ALL']
    assert service['security_opt'] == ['no-new-privileges:true']
    assert service['mem_limit'] == '128m' and service['cpus'] == 0.5 and service['pids_limit'] == 64
    assert service['restart'] == 'unless-stopped' and service['tmpfs']
    assert service['environment']['SUBSCRIPTION_URL'] == '${SUBSCRIPTION_URL:-}'
    assert all(mount['read_only'] is True for mount in service['volumes'])


def test_backend_joins_private_network_but_local_and_e2e_do_not():
    deployed = yaml.safe_load((ROOT / 'backend/docker-compose.yml').read_text(encoding='utf-8'))
    backend = deployed['services']['backend']
    assert backend['environment']['LLM_PROXY_URL'] == '${LLM_PROXY_URL:-http://tinvest-llm-proxy:7890}'
    assert deployed['networks']['llm'] == {'external': True, 'name': '${BACKEND_ALIAS}-llm'}
    assert 'llm' in backend['networks'] and 'llm' not in deployed['services']['db']['networks']
    local = yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))
    assert local['services']['backend']['environment']['LLM_PROXY_URL'] == '${LLM_PROXY_URL:-}'
    assert not any(network.get('external') for network in local['networks'].values() if network)
    overlay = yaml.safe_load((ROOT / 'docker-compose.ci.yml').read_text(encoding='utf-8'))
    assert overlay['services']['backend']['environment']['LLM_PROXY_URL'] == ''


def test_alternatives_are_also_isolated():
    for filename in ('compose.gluetun.yml', 'compose.xray.yml'):
        config = yaml.safe_load((PROXY / 'alternatives' / filename).read_text(encoding='utf-8'))
        assert set(config['networks']) == {'llm'}
        assert config['networks']['llm']['external'] is True
        assert config['networks']['llm']['name'].startswith('${LLM_NETWORK:?')
        assert all('ports' not in service for service in config['services'].values())
    assert (PROXY / 'alternatives/config.xray.example.json').is_file()


@pytest.mark.parametrize('address,state,expected', [('00000000:1ED2', '0A', 0),
                                                  ('00000000000000000000000000000000:1ED2', '0A', 0),
                                                  ('00000000:1ED2', '01', 1), ('00000000:1ED3', '0A', 1)])
def test_healthcheck_requires_exact_listening_port(shell, tmp_path, address, state, expected):
    table = tmp_path / 'tcp'
    table.write_text('  sl  local_address rem_address st\n   0: ' + address + ' 00000000:0000 ' + state + ' 0\n', encoding='ascii')
    result = subprocess.run([shell, (PROXY / 'healthcheck.sh').as_posix(), table.as_posix()], capture_output=True, timeout=10)
    assert result.returncode == expected and result.stdout == b''
