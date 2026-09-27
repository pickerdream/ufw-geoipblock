"""Exercise the installer's rule generation without touching the host firewall."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / 'install.sh').read_text()
PORT_PARSER = INSTALLER.split('# 2. Parse Ports', 1)[1].split('# -- Dry-Run', 1)[0]
# The section heading continues on its first line.
PORT_PARSER = PORT_PARSER.split('\n', 1)[1]
INJECTOR = INSTALLER.split('inject_rules() {', 1)[1].split('\necho "Applying IPv4', 1)[0]
INJECTOR = 'inject_rules() {' + INJECTOR


class RuleTests(unittest.TestCase):
    def parse(self, config):
        env = dict(os.environ, PORT_CONFIG=config, SOURCE_COUNTRY='JP')
        result = subprocess.run(
            ['bash', '-c', 'set -euo pipefail\n' + PORT_PARSER +
             '\nprintf "%s%s" "$GEN_RULES_V4" "$GEN_RULES_V6"'],
            env=env, text=True, capture_output=True, check=True)
        return result.stdout

    def test_manual_ports_generate_both_families(self):
        output = self.parse('22,80,443')
        for chain in ('ufw-before-input', 'ufw6-before-input'):
            for proto in ('tcp', 'udp'):
                for port in ('22', '80', '443'):
                    self.assertIn(f'-A {chain} -p {proto} --dport {port} -m geoip ! --src-cc JP', output)
        self.assertEqual(output.count('-j DROP'), 12)

    def test_csv_empty_memos_spaces_commas_and_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'ports.csv'
            config.write_bytes(b'# comment\r\n22,,block\r\n80,HTTP Web,block\r\n'
                               b'443,HTTPS, public,block\r\n3000,Dev Web,pass\r\n'
                               b'8080\r\n8443,Secure Web\r\n')
            output = self.parse(str(config))
        for port in ('22', '80', '443', '8080', '8443'):
            self.assertIn(f'--dport {port} ', output)
        self.assertNotIn('--dport 3000 ', output)
        self.assertIn('--comment "HTTP Web"', output)
        self.assertIn('--comment "HTTPS, public"', output)
        self.assertEqual(output.count('-j DROP'), 20)

    def inject(self, subnet):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'before6.rules'
            target.write_text('*filter\n:ufw6-before-forward - [0:0]\nCOMMIT\n')
            # Mock only interface discovery; use real template substitution and sed.
            script = ('set -euo pipefail\nip() { echo "1: eth0 inet6 2001:db8::1/64 scope global"; }\n'
                      + INJECTOR + '\ninject_rules "$1" "" "$2"\n'
                      + 'inject_rules "$1" "" "$2"\n')
            subprocess.run(['bash', '-c', script, 'test', str(target),
                            str(ROOT / 'ufw/geoip-rules6.template')],
                           env=dict(os.environ, TRUSTED_SUBNETS=subnet),
                           text=True, capture_output=True, check=True)
            return target.read_text()

    def test_ipv6_trust_uses_ipv6_chain_and_is_idempotent(self):
        output = self.inject('2001:db8::/64')
        self.assertEqual(output.count('-A ufw6-before-input -s 2001:db8::/64 -j ACCEPT'), 1)
        self.assertNotIn('-A ufw-before-input', output)
        self.assertEqual(output.count('# === BEGIN GEOIPBLOCK ==='), 1)

    def test_explicit_empty_trust_disables_discovery(self):
        self.assertNotIn('2001:db8', self.inject(''))


if __name__ == '__main__':
    unittest.main()
