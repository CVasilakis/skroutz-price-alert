"""The Scrooge Alert application package (import root: ``src/``).

All intra-project imports are absolute from this package
(``from core.infrastructure.signals import ...``, ``from core.scrapers.framework.catalog import ...``);
the entry scripts
(``run.py``, ``status.py``, ``ping.py``) put ``src/`` on ``sys.path`` so they
work when invoked directly (``python3 src/core/run.py``).

Import-light contract: this ``__init__`` performs NO imports. It sits on the
path of every plugin-discovery one-liner (the shell helpers import
``core.scrapers.framework.catalog`` through it), so it must never pull in a transport
library or any heavy dependency.
"""
