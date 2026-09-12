import pytest
from pydantic import ValidationError

from dark_policy import PolicyError
from policy_api import password_hash, verify_password
from server import AdminBody, Password


def test_eight_character_account_password_is_accepted():
    value='Abcd1234'
    encoded=password_hash(value)
    assert verify_password(value,encoded)
    assert AdminBody(username='operator',password=value).password==value
    assert Password(old_password='current-password',new_password=value).new_password==value


def test_seven_character_account_password_is_rejected():
    value='Abc1234'
    with pytest.raises(PolicyError):
        password_hash(value)
    with pytest.raises(ValidationError):
        AdminBody(username='operator',password=value)
    with pytest.raises(ValidationError):
        Password(old_password='current-password',new_password=value)
