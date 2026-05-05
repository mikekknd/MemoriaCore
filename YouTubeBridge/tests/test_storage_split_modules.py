import sqlite3
import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


def test_storage_schema_module_initializes_bridge_tables():
    from storage_schema import init_bridge_db

    conn = sqlite3.connect(":memory:")
    try:
        init_bridge_db(conn)
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert {"connectors", "live_sessions", "live_events", "topic_packs", "live_interactions"} <= table_names


def test_storage_mapper_module_matches_bridge_storage_facade():
    from storage import BridgeStorage
    from storage_mappers import row_to_connector, vector_to_blob, blob_to_vector, cosine_similarity

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT
                'yt-main' AS connector_id,
                'YouTube Main' AS display_name,
                'secret' AS api_key,
                1 AS enabled,
                'created' AS created_at,
                'updated' AS updated_at
            """
        ).fetchone()
    finally:
        conn.close()

    assert BridgeStorage._row_to_connector(row) == row_to_connector(row)
    blob = vector_to_blob([1.0, 0.0, 0.5])
    assert blob_to_vector(blob, 3) == [1.0, 0.0, 0.5]
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
