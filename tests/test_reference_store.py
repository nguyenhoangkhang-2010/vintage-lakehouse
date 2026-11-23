"""Runs the store contract against the in-memory reference store."""

from __future__ import annotations

from store_contract import StoreContract
from vintage_lakehouse.storage import AsOfStore, InMemoryStore


class TestInMemoryStore(StoreContract):
    def make_store(self) -> AsOfStore:
        return InMemoryStore()


def test_in_memory_store_satisfies_the_protocol() -> None:
    assert isinstance(InMemoryStore(), AsOfStore)
