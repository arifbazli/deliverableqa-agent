import auth


class TestGetExpectedToken:
    def test_returns_none_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)

        assert auth.get_expected_token() is None

    def test_returns_none_when_env_var_empty_string(self, monkeypatch):
        # An accidentally-set-but-empty var should behave like unset, not like a
        # valid empty-string token nobody could ever match with a real header.
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, "")

        assert auth.get_expected_token() is None

    def test_returns_token_when_set(self, monkeypatch):
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, "my-secret-token")

        assert auth.get_expected_token() == "my-secret-token"
