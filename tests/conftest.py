import pytest
from django.contrib.auth import get_user_model

PASSWORD = "S3cure-pass-phrase!"


@pytest.fixture
def password():
    return PASSWORD


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("alice@example.com", PASSWORD)


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user("bob@example.com", PASSWORD)


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client
