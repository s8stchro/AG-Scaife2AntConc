"""Atomic replacement with bounded retries for transient Windows sharing locks; output file names."""
import os
import re
import time

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r'(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)')


def keep_name(value, limit=150):
    """A name as the user wrote it, changed only where Windows forbids it (Ἀχαρνῆς (Olson) stays so)."""
    value = _FORBIDDEN.sub('_', str(value))[:limit].strip(' .')
    if not value:
        return 'text'
    return '_' + value if _RESERVED.match(value) else value


def input_name(path):
    """The output name of an input file: its own name without the extension."""
    return keep_name(os.path.splitext(os.path.basename(path))[0])


def replace_file(source, target):
    # Retry the completed replacement, never truncate the previous file. A
    # persistent lock still raises so the caller can report/roll back the save.
    for attempt in range(9):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 8:
                raise
            time.sleep(min(0.02 * 2 ** attempt, 0.25))
