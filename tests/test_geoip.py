import csv
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('converter', ROOT / 'maxmind-to-dbip.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def fixture(path, ipv4=None, missing_ipv6=False):
    files = {
        'Locations-en': 'geoname_id,country_iso_code,country_name\n1,JP,Japan\n2,US,"United, States"\n3,,Unknown\n',
        'Blocks-IPv4': ipv4 or ('network,geoname_id,registered_country_geoname_id\n'
                              '192.0.2.0/24,1,2\n198.51.100.0/24,,2\n203.0.113.0/24,3,2\n'),
        'Blocks-IPv6': 'network,geoname_id\n2001:db8::/32,2\n',
    }
    if missing_ipv6:
        del files['Blocks-IPv6']
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(f'GeoLite2-Country-CSV_20260927/GeoLite2-Country-{name}.csv', content)


class GeoIPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.archive = self.path / 'source.zip'
        fixture(self.archive)

    def test_conversion_and_geographic_country(self):
        output = self.path / 'out.csv'
        converter.convert(self.archive, output)
        self.assertEqual(list(csv.reader(io.StringIO(output.read_text()))), [
            ['192.0.2.0', '192.0.2.255', 'JP'],
            ['2001:db8::', '2001:db8:ffff:ffff:ffff:ffff:ffff:ffff', 'US'],
        ])

    def test_reject_invalid_inputs(self):
        cases = ['network,geoname_id\ninvalid,1\n',
                 'network,geoname_id\n192.0.2.0/24,999\n',
                 'wrong,columns\n192.0.2.0/24,1\n',
                 'network,geoname_id\n192.0.2.0/24,\n',
                 'network,geoname_id\n192.0.2.0/24,1\n192.0.2.0/25,2\n']
        for data in cases:
            with self.subTest(data=data):
                fixture(self.archive, ipv4=data)
                with self.assertRaises(ValueError):
                    converter.convert(self.archive, self.path / 'out.csv')
        fixture(self.archive, missing_ipv6=True)
        with self.assertRaises(ValueError):
            converter.convert(self.archive, self.path / 'out.csv')

    def run_update(self, provider='maxmind', failure='', credentials=True):
        bindir = self.path / 'bin'
        bindir.mkdir()
        scripts = {
            'logger': 'exit 0',
            'sleep': 'exit 0',
            'curl': '''cat > "$TEST_ROOT/auth"
printf '%s\\n' "$@" > "$TEST_ROOT/curl-args"
[ "$FAILURE" != download ] || exit 22
cp "$TEST_ROOT/source.zip" maxmind.zip''',
            'xt_geoip_dl': 'printf "192.0.2.0,192.0.2.255,JP\\n" > dbip-country-lite.csv',
            'xt_geoip_build': '''[ "$FAILURE" != build ] || exit 1
cp dbip-country-lite.csv "$TEST_ROOT/builder-input"
[ "$FAILURE" != empty ] || exit 0
printf test4 > "$2/JP.iv4"
printf test6 > "$2/JP.iv6"''',
        }
        for name, body in scripts.items():
            helper = bindir / name
            helper.write_text('#!/bin/bash\nset -e\n' + body + '\n')
            helper.chmod(0o755)
        config = self.path / 'config'
        config.write_text(f'GEOIP_SOURCE={provider}\n' + (
            'MAXMIND_ACCOUNT_ID=12345\nMAXMIND_LICENSE_KEY=test_SECRET\n' if credentials else ''))
        db = self.path / 'db'
        db.mkdir()
        (db / 'sentinel').write_text('original')
        downloads = self.path / 'downloads'
        downloads.mkdir()
        env = dict(os.environ, PATH=f'{bindir}:' + os.environ['PATH'],
                   GEOIP_CONFIG_FILE=str(config), GEOIP_DB_DIR=str(db),
                   GEOIP_LOCK_FILE=str(self.path / 'lock'), TEST_ROOT=str(self.path),
                   TMPDIR=str(downloads), FAILURE=failure)
        result = subprocess.run(['bash', str(ROOT / 'update-geoip.sh')], env=env,
                                capture_output=True, text=True)
        self.assertEqual(list(downloads.iterdir()), [])
        self.assertFalse((self.path / 'db_new').exists())
        return result, db

    def test_maxmind_update(self):
        result, db = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((db / 'JP.iv6').exists())
        self.assertEqual((self.path / 'db.old/sentinel').read_text(), 'original')
        self.assertNotIn('test_SECRET', (self.path / 'curl-args').read_text())
        self.assertNotIn('test_SECRET', result.stdout + result.stderr)
        self.assertIn('2001:db8::', (self.path / 'builder-input').read_text())

    def test_dbip_update(self):
        result, db = self.run_update(provider='dbip')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((db / 'JP.iv4').exists())
        self.assertFalse((self.path / 'auth').exists())

    def test_download_failure_preserves_database(self):
        result, db = self.run_update(failure='download')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((db / 'sentinel').read_text(), 'original')

    def test_build_failure_preserves_database(self):
        result, db = self.run_update(failure='build')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((db / 'sentinel').exists())

    def test_empty_build_preserves_database(self):
        result, db = self.run_update(failure='empty')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((db / 'sentinel').exists())

    def test_corrupt_archive_preserves_database(self):
        self.archive.write_text('invalid zip')
        result, db = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((db / 'sentinel').exists())

    def test_missing_credentials(self):
        result, db = self.run_update(credentials=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((db / 'sentinel').exists())
        self.assertFalse((self.path / 'auth').exists())

    def test_unknown_provider(self):
        result, db = self.run_update(provider='unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((db / 'sentinel').exists())


if __name__ == '__main__':
    unittest.main()
