import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


def test_bridge_storage_uses_repository_mixins():
    from storage import BridgeStorage
    from storage_repositories import (
        ConnectorRepositoryMixin,
        DirectorStateRepositoryMixin,
        EventRepositoryMixin,
        InteractionRepositoryMixin,
        SessionRepositoryMixin,
        SummaryRepositoryMixin,
        TopicPackRepositoryMixin,
    )

    assert issubclass(BridgeStorage, ConnectorRepositoryMixin)
    assert issubclass(BridgeStorage, SessionRepositoryMixin)
    assert issubclass(BridgeStorage, EventRepositoryMixin)
    assert issubclass(BridgeStorage, TopicPackRepositoryMixin)
    assert issubclass(BridgeStorage, InteractionRepositoryMixin)
    assert issubclass(BridgeStorage, DirectorStateRepositoryMixin)
    assert issubclass(BridgeStorage, SummaryRepositoryMixin)
