"""Path checks for application-owned offline fixtures and artifacts."""

from pathlib import Path

from research_intern.domain.experiments import SliceError


def child_path(root: Path, *parts: str) -> Path:
    """Reject traversal and existing symlinks rather than following them on writes.

    This assumes the application owns the directory; it is not an OS sandbox
    against concurrent filesystem changes by another process.
    """
    root = root.resolve(strict=True)
    path = root
    for part in parts:
        if Path(part).is_absolute() or not Path(part).parts:
            raise SliceError("Expected a relative artifact path")
        for component in Path(part).parts:
            if component in (".", ".."):
                raise SliceError("Artifact paths cannot traverse directories")
            path = path / component
            if path.is_symlink():
                raise SliceError("Artifact paths cannot contain symlinks")
    if not path.resolve().is_relative_to(root):
        raise SliceError("Artifact path must remain inside its owned directory")
    return path
