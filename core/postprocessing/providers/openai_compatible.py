# Responsible for: bounded text-only requests to OpenAI-compatible servers.

import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from core.postprocessing.base_provider import BaseProvider

logger = logging.getLogger("WhisperNote.postprocessing")


class NoRedirect(HTTPRedirectHandler):
    """Prevent forwarding credentials or transcription text to redirect targets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject HTTP redirects; users must configure the final endpoint."""
        return None


class OpenAICompatibleProvider(BaseProvider):
    """Use chat completions for local or cloud servers with sanitized failures."""

    def __init__(self, settings, api_key="", opener=None):
        """Capture validated settings and a secret without logging either."""
        self.settings = dict(settings)
        self._api_key = api_key
        self._opener = opener or build_opener(NoRedirect())
        self.last_diagnostics = {}

    def _request(self, endpoint, payload=None):
        started = time.perf_counter()
        status, category, usage = None, "connection_error", {}
        # Only allow bounded identifiers and numeric usage fields into diagnostics.
        # Never log URL, headers, payload, remote errors or conversation text.
        model = re.sub(r"[^a-zA-Z0-9_./:-]", "_", str(self.settings.get("model", "")))[:160]
        preset = self.settings.get("preset", "custom")
        provider = preset if preset in ("openrouter", "lm_studio") else self.settings.get("provider", "custom")
        provider = provider if provider in ("openrouter", "lm_studio", "local", "cloud") else "custom"
        self.last_diagnostics = {"started_at": datetime.now(timezone.utc).isoformat(),
                                 "provider": provider, "model": model, "endpoint": endpoint}
        logger.info("Provider HTTP started: %s", self.last_diagnostics)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        try:
            request = Request(self.settings["base_url"].rstrip("/") + endpoint,
                              data=json.dumps(payload).encode("utf-8") if payload else None,
                              headers=headers)
            with self._opener.open(request, timeout=60) as response:
                status = response.getcode() if hasattr(response, "getcode") else None
                category = "invalid_response"
                content = response.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError("Response too large")
                result = json.loads(content)
                raw_usage = result.get("usage", {}) if isinstance(result, dict) else {}
                if isinstance(raw_usage, dict):
                    usage = {key: raw_usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                             if type(raw_usage.get(key)) is int and raw_usage[key] >= 0}
                category = "success"
                return result
        except HTTPError as error:
            status = error.code
            category = "rate_limit" if status == 429 else "http_error"
            error.close()
        except TimeoutError:
            category = "timeout"
        except URLError as error:
            category = "timeout" if isinstance(error.reason, TimeoutError) else "connection_error"
        except Exception:
            pass
        finally:
            self.last_diagnostics.update(ended_at=datetime.now(timezone.utc).isoformat(),
                                         elapsed_seconds=round(time.perf_counter() - started, 3),
                                         http_status=status, category=category, usage=usage)
            logger.info("Provider HTTP finished: %s", self.last_diagnostics)
        # Raise outside the exception handler so neither context nor traceback
        # includes remote response bodies, credentials, or URL query parameters.
        if status in (401, 403):
            message = "Provider rejected authentication. Check the API key."
        elif status is not None:
            message = f"Provider returned HTTP {status}. Check its configuration."
        else:
            message = "Provider request failed or timed out. Check the server and connection."
        raise RuntimeError(message)

    def test_connection(self):
        """Validate the models endpoint without consuming a generation request."""
        result = self._request("/models")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise RuntimeError("Provider returned an invalid models response.")

    def list_models(self):
        """Read the standard models catalog, preserving true IDs and display names."""
        result = self._request("/models")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise RuntimeError("Provider returned an invalid models response.")
        models = {}
        for entry in result["data"]:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip():
                name = entry.get("name")
                models[entry["id"]] = name if isinstance(name, str) else entry["id"]
        return [{"id": key, "name": name} for key, name in sorted(models.items())]

    def process(self, text, prompt):
        """Send only text and return a nonempty assistant response."""
        started = time.perf_counter()
        try:
            return self._process(text, prompt)
        finally:
            self.last_diagnostics["refine_elapsed_seconds"] = round(time.perf_counter() - started, 3)
            logger.info("Refine finished: %s", self.last_diagnostics)

    def _process(self, text, prompt):
        result = self._request("/chat/completions", {
            "model": self.settings["model"], "temperature": self.settings["temperature"],
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": text}], "stream": False,
        })
        try:
            value = result["choices"][0]["message"]["content"]
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (KeyError, TypeError, IndexError):
            pass
        raise RuntimeError("Provider returned no usable edited text.")
