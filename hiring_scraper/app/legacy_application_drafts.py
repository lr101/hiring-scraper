"""Recover or remove the files written by the previous preparation editor."""
from contextlib import ExitStack, contextmanager, suppress
import argparse
import errno
import os
import re
import shutil
import stat
import sys
from zipfile import ZIP_DEFLATED, ZipFile


_FOLDERS = (('application-jobs',), ('document-revisions', 'application-jobs'))


@contextmanager
def _open_folder(root, parts):
    # Resolve each component relative to an already opened private directory.
    # A legacy symlink must never redirect cleanup or export.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        try:
            fd = os.open(root, flags)
            stack.callback(os.close, fd)
            for part in parts:
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                raise
            yield None
        else:
            yield fd


def _records(fd, profile_id):
    match = re.compile(rf'{int(profile_id)}-[0-9]+(?:\.draft)?\.json').fullmatch
    return sorted(name for name in os.listdir(fd) if match(name))


def delete_profile(root, profile_id):
    for parts in _FOLDERS:
        with _open_folder(root, parts) as fd:
            if fd is None:
                continue
            for name in _records(fd, profile_id):
                with suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=fd)


def export_profile(root, profile_id, output):
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for parts in _FOLDERS:
            with _open_folder(root, parts) as fd:
                if fd is None:
                    continue
                for name in _records(fd, profile_id):
                    try:
                        record_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    except OSError as error:
                        if error.errno in (errno.ENOENT, errno.ELOOP):
                            continue
                        raise
                    with os.fdopen(record_fd, 'rb') as record:
                        if stat.S_ISREG(os.fstat(record.fileno()).st_mode):
                            with archive.open('/'.join((*parts, name)), 'w') as member:
                                shutil.copyfileobj(record, member)


def main():
    from hiring_scraper.app.codex_runtime import _root

    parser = argparse.ArgumentParser(description='Write a private ZIP of one profile’s legacy drafts to stdout.')
    parser.add_argument('profile_id', type=int)
    args = parser.parse_args()
    if args.profile_id < 1:
        parser.error('profile_id must be positive')
    if sys.stdout.isatty():
        parser.error('redirect stdout to a private ZIP file')
    export_profile(_root(), args.profile_id, sys.stdout.buffer)


if __name__ == '__main__':
    main()
