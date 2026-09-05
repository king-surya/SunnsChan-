import json
import unittest
from unittest.mock import patch

from suns_chan.proxmox_provider import ProxmoxCredentials, ProxmoxSandboxProvider
from suns_chan.vm_sandbox import BACKEND_REAL, VMSpec


CREDS = ProxmoxCredentials("pve.lan:8006", "suns@pve", "sandbox", "SECRET-TOKEN-XYZ", node="pve1")


class FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def api_response(data) -> FakeHTTPResponse:
    return FakeHTTPResponse(json.dumps({"data": data}).encode())


class CredentialTests(unittest.TestCase):
    def test_from_env_requires_all_fields(self) -> None:
        import os

        with patch.dict(os.environ, {}, clear=False):
            for key in ("PROXMOX_HOST", "PROXMOX_USER", "PROXMOX_TOKEN_NAME", "PROXMOX_TOKEN_VALUE"):
                saved = os.environ.pop(key, None)
                try:
                    with self.assertRaises(ValueError):
                        ProxmoxCredentials.from_env()
                finally:
                    if saved is not None:
                        os.environ[key] = saved

    def test_redacted_never_contains_secret(self) -> None:
        redacted = CREDS.redacted()
        blob = json.dumps(redacted)
        self.assertNotIn("SECRET-TOKEN-XYZ", blob)
        self.assertIn("pve.lan:8006", blob)

    def test_guest_payload_carries_no_credentials(self) -> None:
        provider = ProxmoxSandboxProvider(CREDS)
        payload = provider.build_guest_command("apt install gcc && rm -rf /srv/build")
        blob = json.dumps(payload)
        self.assertNotIn("SECRET-TOKEN-XYZ", blob)
        self.assertNotIn("Authorization", blob)
        self.assertEqual(payload, {"command": "apt install gcc && rm -rf /srv/build"})

    def test_describe_is_redacted(self) -> None:
        blob = json.dumps(ProxmoxSandboxProvider(CREDS).describe())
        self.assertNotIn("SECRET-TOKEN-XYZ", blob)
        self.assertIn(BACKEND_REAL, blob)


class ProxmoxFlowTests(unittest.TestCase):
    def test_unreachable_raises_honestly(self) -> None:
        provider = ProxmoxSandboxProvider(CREDS, timeout_secs=1.0)
        with patch("suns_chan.proxmox_provider.urlopen", return_value=api_response(102)):
            sandbox_id = provider.create(VMSpec("lab", "tmpl"))
        with patch("suns_chan.proxmox_provider.urlopen", side_effect=OSError("no route")):
            with self.assertRaises(RuntimeError):
                provider.status(sandbox_id)

    def test_create_clone_and_execute_flow(self) -> None:
        calls: list = []

        def fake_open(request, timeout=None, context=None):
            calls.append((request.get_method(), request.full_url))
            url = request.full_url
            if url.endswith("/cluster/nextid"):
                return api_response(101)
            if url.endswith("/agent/exec"):
                return api_response({"pid": 7})
            if "exec-status" in url:
                import base64

                return api_response({"exited": 1, "exitcode": 0,
                                     "out-data": base64.b64encode(b"hello vm\n").decode()})
            return api_response({})

        provider = ProxmoxSandboxProvider(CREDS)
        with patch("suns_chan.proxmox_provider.urlopen", side_effect=fake_open):
            sandbox_id = provider.create(VMSpec("lab", "tmpl"))
            self.assertEqual(sandbox_id, "pve-101")
            provider.start(sandbox_id)
            result = provider.execute(sandbox_id, "echo hello vm")
        self.assertEqual(result.stdout, "hello vm\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.backend, BACKEND_REAL)
        methods = [method for method, _ in calls]
        self.assertIn("POST", methods)
        # Auth header carried the token but no URL or guest payload does.
        for _, url in calls:
            self.assertNotIn("SECRET-TOKEN-XYZ", url)

    def test_unknown_sandbox_rejected(self) -> None:
        provider = ProxmoxSandboxProvider(CREDS)
        with self.assertRaises(ValueError):
            provider.status("ghost")


if __name__ == "__main__":
    unittest.main()
