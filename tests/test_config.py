from src.config import parse_redis_connection_string


def test_parse_local_redis():
    assert parse_redis_connection_string("redis:6379") == {"host": "redis", "port": 6379}


def test_parse_azure_managed_redis_keeps_padding_in_password():
    kwargs = parse_redis_connection_string("redis-x.eastus2.redis.azure.net:10000,ssl=true,password=AbC+/x9==")
    assert kwargs == {
        "host": "redis-x.eastus2.redis.azure.net",
        "port": 10000,
        "ssl": True,
        "password": "AbC+/x9==",
    }


def test_parse_db_option():
    assert parse_redis_connection_string("localhost:6379,db=2")["db"] == 2
