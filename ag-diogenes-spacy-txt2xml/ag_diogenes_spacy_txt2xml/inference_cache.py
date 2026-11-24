"""Persistent cache of deterministic model outputs, keyed by exact model and adapter identity.

A cached value is what the same pinned model, runtime and adapter code computed for the same
normalised input, so reusing it preserves every decision. A changed model file, runtime version
or adapter version gives a different cache file. Any cache failure is ignored: the caller then
simply computes again. AG_INFERENCE_CACHE=off disables the cache; another value names its folder.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

ENV = 'AG_INFERENCE_CACHE'


def default_folder():
    configured = os.environ.get(ENV)
    if configured is not None:
        return None if configured.strip().lower() in ('', '0', 'off', 'no', 'false') else Path(configured)
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.cache') / 'AG-Scaife2AntConc' / 'inference-cache'


def text_key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


class Cache:
    def __init__(self, name, identity, folder=None):
        self.identity = dict(identity)
        self.connection = None
        self.hits = self.misses = 0
        folder = default_folder() if folder is None else (None if folder is False else Path(folder))
        if folder is None:
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.path = folder / f'{name}-{text_key(self.identity)[:16]}.sqlite'
            self.connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            self.connection.execute('PRAGMA journal_mode=WAL')
            self.connection.execute('CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self.connection.execute('CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL)')
            if not self.connection.execute('SELECT 1 FROM identity').fetchone():
                self.connection.execute('INSERT INTO identity VALUES (?)', (json.dumps(self.identity, ensure_ascii=False),))
            self.connection.commit()
        except (OSError, sqlite3.Error):
            self.close()

    @property
    def enabled(self):
        return self.connection is not None

    def get_many(self, keys):
        keys = list(dict.fromkeys(keys))
        found = {}
        if self.connection is not None and keys:
            try:
                for start in range(0, len(keys), 900):
                    batch = keys[start:start + 900]
                    rows = self.connection.execute(
                        f'SELECT key, value FROM entries WHERE key IN ({",".join("?" * len(batch))})', batch)
                    found.update((key, json.loads(value)) for key, value in rows)
            except (sqlite3.Error, ValueError):
                found = {}
                self.close()
        self.hits += len(found)
        self.misses += len(keys) - len(found)
        return found

    def put_many(self, values):
        if self.connection is None or not values:
            return
        try:
            self.connection.executemany('INSERT OR REPLACE INTO entries VALUES (?, ?)',
                                        [(k, json.dumps(v, ensure_ascii=False, allow_nan=False)) for k, v in values.items()])
            self.connection.commit()
        except (sqlite3.Error, ValueError):
            self.close()

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
        self.connection = None
