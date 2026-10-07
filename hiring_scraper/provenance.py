"""Optional Git provenance for source archives and installed/container builds."""
from pathlib import Path
import subprocess


def _git_output(arguments: list[str], directory: Path) -> str | None:
    try:
        result = subprocess.run(['git', *arguments], cwd=directory,
                                capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_metadata(directory: Path) -> dict[str, str | bool | None]:
    revision = _git_output(['rev-parse', 'HEAD'], directory)
    if not revision:
        return {'git_revision': None, 'git_dirty': None}
    status = _git_output(['status', '--porcelain'], directory)
    return {'git_revision': revision, 'git_dirty': None if status is None else bool(status)}
