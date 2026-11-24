"""Local persistence for the experimental browser workbench, independent of HTTP."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import APP_NAME
from .file_io import replace_file
from .pipeline import SetupError


def state_directory():
    return Path(os.environ.get('AG_WEB_STATE') or
                Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.cache') / (APP_NAME + '-Workspace'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # dumps uses Python's C encoder; dump streams millions of small chunks for
    # a full working edition. Publish the same UTF-8 JSON with one buffered write.
    content = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
    fd, pending = tempfile.mkstemp(prefix='.writing-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(pending, path)
    finally:
        if os.path.exists(pending):
            os.remove(pending)
    return hashlib.sha256(content).hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


class Store:
    """A replaceable persistence adapter. State contains paths/IDs, not model objects."""
    def __init__(self, root=None):
        self.root = Path(root or state_directory()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'workbench.json'
        try:
            self.data = read_json(self.path)
            if self.data is not None and (self.data['schema'] != 1 or
                    not all(isinstance(self.data[k], list) for k in ('projects', 'inputs'))):
                raise ValueError('unsupported state')
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise SetupError('Could not read the workspace history. '
                             'The existing file was preserved: ' + str(self.path)) from error
        if self.data is None:
            settings = Path(os.environ.get('APPDATA') or Path.home()) / APP_NAME / 'settings.json'
            try:
                old = read_json(settings, {})
                if not isinstance(old, dict):
                    old = {}
            except (OSError, ValueError):
                old = {}
            self.data = {'schema': 1, 'projects': [], 'inputs': [], 'last_project': None,
                         'preferences': {'outdir': old.get('outdir', ''), 'diogenes': old.get('diogenes'),
                                         'greta_model': old.get('greta_model'), 'context_model': old.get('context_model'),
                                         'mode': old.get('workflow', 'quick')}}

    def save(self):
        atomic_json(self.path, self.data)
