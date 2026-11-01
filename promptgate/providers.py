"""Model providers. Replay serves recorded responses, so tests and demos need no API key."""
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class ReplayProvider:
    """Answers from a JSON file of recorded completions keyed by case id."""

    def __init__(self, path):
        self.responses = json.loads(pathlib.Path(path).read_text())

    def complete(self, case_id, prompt, model):
        if case_id not in self.responses:
            raise KeyError(f"no recorded response for case {case_id!r}")
        r = self.responses[case_id]
        return Completion(r["text"], r["input_tokens"], r["output_tokens"], r.get("latency_ms", 0.0))


class OpenAICompatibleProvider:
    """Calls any /chat/completions endpoint with retries on 429 and 5xx responses."""

    def __init__(self, base_url, api_key_env="OPENAI_API_KEY", timeout=60, max_retries=3, backoff=1.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout, self.max_retries, self.backoff = timeout, max_retries, backoff

    def complete(self, case_id, prompt, model):
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                           "temperature": 0}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                request = urllib.request.Request(self.url, data=body, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                usage = data.get("usage", {})
                return Completion(data["choices"][0]["message"]["content"], usage.get("prompt_tokens", 0),
                                  usage.get("completion_tokens", 0), (time.perf_counter() - start) * 1000)
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt == self.max_retries:
                    raise
            except urllib.error.URLError:
                if attempt == self.max_retries:
                    raise
            time.sleep(self.backoff * 2**attempt)
        raise RuntimeError("unreachable")


def build_provider(config, suite_dir):
    kind = config.get("type")
    if kind == "replay":
        return ReplayProvider(pathlib.Path(suite_dir) / config["path"])
    if kind == "openai_compatible":
        return OpenAICompatibleProvider(config["base_url"], config.get("api_key_env", "OPENAI_API_KEY"))
    raise ValueError(f"unknown provider type {kind!r}")
