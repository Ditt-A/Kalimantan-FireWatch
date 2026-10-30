"""Restore the exact large local files recorded in the Google Drive manifest."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import subprocess


def fingerprint(path):
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            md5.update(block)
            sha256.update(block)
    return md5.hexdigest(), sha256.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path, help='Folder tujuan project')
    parser.add_argument('--remote', default='mygdrive:', help='Nama remote rclone Google Drive')
    parser.add_argument('--match', default='*', help='Pola path yang ingin dipulihkan')
    parser.add_argument('--list', action='store_true', help='Tampilkan daftar tanpa mengunduh')
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name('local_manifest.json').read_text())
    project = args.project.expanduser().resolve()
    selected = [row for row in manifest['files'] if fnmatch.fnmatchcase(row['path'], args.match)]
    if not selected:
        parser.error('Tidak ada file yang cocok dengan --match.')
    for index, row in enumerate(selected, 1):
        target = (project / row['path']).resolve()
        if not target.is_relative_to(project) or target == project:
            raise ValueError('Path manifest berada di luar folder project.')
        print(f"[{index}/{len(selected)}] {row['path']} ({row['size'] / 1e6:.1f} MB)", flush=True)
        if args.list:
            continue
        expected = (row['md5'], row['sha256'])
        if target.is_file() and target.stat().st_size == row['size'] and fingerprint(target) == expected:
            print('  Sudah cocok; dilewati.', flush=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.restore.partial')
        source = args.remote.rstrip('/') + '/' + row['existing_drive_path']
        subprocess.run([
            'rclone', 'copyto', source, str(temporary),
            '--drive-root-folder-id', manifest['drive_root_folder_id'],
            '--checksum', '--transfers', '1', '--checkers', '2',
            '--buffer-size', '8M', '--multi-thread-streams', '0',
            '--contimeout', '15s', '--timeout', '1m',
            '--retries', '3', '--low-level-retries', '2',
        ], check=True)
        if temporary.stat().st_size != row['size'] or fingerprint(temporary) != expected:
            raise RuntimeError(f"Checksum tidak cocok: {row['path']}; file tujuan dipertahankan.")
        temporary.replace(target)
    print('Selesai.', flush=True)


if __name__ == '__main__':
    main()
