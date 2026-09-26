import pytest

from src.config import Settings
from src.llm import ModelClient, ModelUnavailable


class FakeResponse:
    ok = False
    status_code = 400

    def json(self):
        return {"error": {"message": "The requested model does not exist"}}


def test_model_error_keeps_safe_http_status(monkeypatch):
    monkeypatch.setattr("src.llm.requests.post", lambda *args, **kwargs: FakeResponse())
    config = Settings(enable_llm=True, llm_provider="huggingface", hf_token="hf_test_token", llm_model="bad-model")
    with pytest.raises(ModelUnavailable, match="HTTP 400"):
        ModelClient(config).complete("system", "user")
