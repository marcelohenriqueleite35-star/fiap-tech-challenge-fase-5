"""Definições da Feature Store: entidade `client` e a feature view de atributos do cliente."""

from datetime import timedelta

from feast import Entity, FeatureView, Field, ValueType
from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import PostgreSQLSource
from feast.types import Int64, String

client = Entity(
    name="client",
    join_keys=["client_id"],
    value_type=ValueType.INT64,
    description="Cliente da instituição financeira",
)

# Tabela gravada por src/data_processing.py
client_features_source = PostgreSQLSource(
    name="client_features_source",
    table="client_features",
    timestamp_field="event_timestamp",
    created_timestamp_column="created_timestamp",
)

client_features_view = FeatureView(
    name="client_features",
    entities=[client],
    ttl=timedelta(days=365 * 5),
    schema=[
        Field(name="age", dtype=Int64),
        Field(name="job", dtype=String),
        Field(name="education", dtype=String),
        Field(name="housing", dtype=String),
        Field(name="loan", dtype=String),
        Field(name="previous", dtype=Int64),
        Field(name="days_since_last_contact", dtype=Int64),
        Field(name="poutcome", dtype=String),
        Field(name="customer_segment", dtype=String),
    ],
    online=True,
    source=client_features_source,
    tags={"team": "growth", "use_case": "mab_offer_selection"},
)
