"""Build a reproducible Chrome install package from an explicit runtime allowlist."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'extensions' / 'application-companion'
RUNTIME_FILES = (
    'manifest.json', 'page-context.js', 'background.js', 'content.js', 'profile.js', 'best-effort.js',
    'sources.js', 'radius.js', 'history.js', 'popup.html', 'popup.js', 'options.html',
    'options.js', 'ui.css', 'application-state.js', 'vault.js', 'radius-client.js',
    'bridge.js', 'apply-watch.js', 'connection.js', 'appearance.js',
    'example.json', 'structured-example.json', 'dummy-cv.json', 'dummy-package.json',
    'dummy-return-link.txt', 'icons/radius.svg', 'icons/16.png', 'icons/32.png',
    'icons/48.png', 'icons/128.png',
)


def build(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / 'radius-application-companion.zip'
    entries = {f'extension/{name}': SOURCE / name for name in RUNTIME_FILES}
    entries['INSTALL.md'] = SOURCE / 'README.md'
    with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(entries.items()):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix('.zip.sha256').write_text(f'{digest}  {archive_path.name}\n')
    return archive_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'extension')
    print(build(parser.parse_args().output))
