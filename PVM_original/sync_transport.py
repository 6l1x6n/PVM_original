# -*- coding: utf-8 -*-
"""
PVM.core - Sync Transport (ABC)
=================================
Abstract base class for folder-based synchronization transports.
Per ТЗ §7.5: transport-agnostic Sync Engine (SyncEngine → SyncQueue → Transport).

A transport is a dumb file exchange medium (local folder synced by MEGA,
another cloud folder, etc.). It knows nothing about PVM entities — it only
moves bytes with unique file names.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple


class SyncTransport(ABC):
    """Abstract file-exchange transport.

    All implementations must be safe against concurrent readers: a file that
    appears in list_files() must be complete (writers use tmp+rename), and
    downloading must not modify the remote copy (consumers never delete files
    they read — cleanup is done by the janitor after a grace period).
    """

    @abstractmethod
    def connect(self) -> bool:
        """Validate the transport is usable (folder exists & writable).

        Returns True if ready, False otherwise. May create required
        subdirectories. Should never raise for a misconfigured transport.
        """

    @abstractmethod
    def upload(self, name: str, data: bytes) -> bool:
        """Write a file atomically. name is a unique path relative to the
        transport root. Returns True on success."""

    @abstractmethod
    def download(self, name: str) -> Optional[bytes]:
        """Read a file's content. Returns None if missing/unreadable."""

    @abstractmethod
    def list_files(self, prefix: str = "") -> List[str]:
        """List file paths (relative to root) starting with prefix."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a file. Returns True on success or if already absent."""

    @abstractmethod
    def stat(self, name: str) -> Optional[Tuple[int, float]]:
        """Return (size_bytes, mtime_epoch) or None if missing."""

    @abstractmethod
    def health(self) -> Tuple[bool, str]:
        """Return (ok, description) — used for the diagnostics dialog."""
