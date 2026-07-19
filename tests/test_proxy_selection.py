from __future__ import annotations

import inspect
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from grok2api.admin import settings_store
from grok2api.upstream import grok_build_adapter, proxy_pool

curl_cffi = types.ModuleType("curl_cffi")
curl_cffi.requests = types.SimpleNamespace(Session=MagicMock())
with patch.dict(sys.modules, {"curl_cffi": curl_cffi}):
    from scripts import sso_to_auth_json


class ProxySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.canonicalize = patch.object(
            proxy_pool,
            "canonicalize_proxy_line",
            side_effect=lambda raw, **_: raw,
        )
        self.outbound = patch.object(
            settings_store,
            "get_outbound_proxy_config",
            return_value=self._outbound_config(),
        )
        self.registration = patch.object(
            settings_store,
            "get_registration_config",
            return_value={"proxy": ""},
        )
        self.canonicalize.start()
        self.get_outbound_config = self.outbound.start()
        self.registration.start()
        proxy_pool.invalidate_outbound_proxy_cache()

    def tearDown(self) -> None:
        proxy_pool.invalidate_outbound_proxy_cache()
        self.registration.stop()
        self.outbound.stop()
        self.canonicalize.stop()
        self.env.stop()

    @staticmethod
    def _outbound_config(proxy: str = "", *, enabled: bool = True) -> dict:
        return {
            "enabled": enabled,
            "proxy": proxy,
            "proxy_username": "",
            "proxy_password": "",
            "proxy_strategy": "round_robin",
        }

    def test_empty_configuration_is_direct_everywhere(self) -> None:
        with patch("socket.create_connection") as connect:
            source = proxy_pool.get_outbound_proxy_source()
            registration = grok_build_adapter._proxy_pool(None)
            sso = sso_to_auth_json._proxy_kwargs()

        self.assertEqual((False, "none", []), (
            source["enabled"], source["source"], source["pool"]
        ))
        self.assertEqual([], registration)
        self.assertEqual({}, sso)
        connect.assert_not_called()

    def test_explicit_environment_proxy_is_used_without_discovery(self) -> None:
        for key in ("GROK2API_XAI_PROXY_POOL", "HTTPS_PROXY", "GROK2API_AUTO_PROXY"):
            with self.subTest(key=key), patch.dict(
                os.environ, {key: "http://env-proxy:8080"}, clear=True
            ), patch("socket.create_connection") as connect:
                proxy_pool.invalidate_outbound_proxy_cache()
                source = proxy_pool.get_outbound_proxy_source()
                self.assertEqual(["http://env-proxy:8080"], source["pool"])
                self.assertEqual("env", source["source"])
                connect.assert_not_called()

    def test_registration_form_proxy_has_highest_precedence(self) -> None:
        self.get_outbound_config.return_value = self._outbound_config(
            "http://global-proxy:8080"
        )

        pool = grok_build_adapter._proxy_pool("http://form-proxy:8080")

        self.assertEqual(["http://form-proxy:8080"], pool)

    def test_global_proxy_precedes_environment_for_registration_and_sso(self) -> None:
        self.get_outbound_config.return_value = self._outbound_config(
            "http://global-proxy:8080"
        )
        with patch.dict(
            os.environ,
            {"GROK2API_XAI_PROXY": "http://env-proxy:8080"},
            clear=True,
        ):
            proxy_pool.invalidate_outbound_proxy_cache()
            registration = grok_build_adapter._proxy_pool(None)
            sso = sso_to_auth_json._proxy_kwargs()

        self.assertEqual(["http://global-proxy:8080"], registration)
        self.assertEqual(
            {"proxies": {
                "http": "http://global-proxy:8080",
                "https": "http://global-proxy:8080",
            }},
            sso,
        )

    def test_disabled_global_proxy_is_direct(self) -> None:
        self.get_outbound_config.return_value = self._outbound_config(enabled=False)
        with patch.dict(
            os.environ,
            {"GROK2API_XAI_PROXY": "http://env-proxy:8080"},
            clear=True,
        ):
            proxy_pool.invalidate_outbound_proxy_cache()
            self.assertEqual([], grok_build_adapter._proxy_pool(None))
            self.assertEqual({}, sso_to_auth_json._proxy_kwargs())

    def test_registration_proxy_reaches_complete_sso_device_flow(self) -> None:
        session = MagicMock()
        session.get.side_effect = [
            MagicMock(url="https://accounts.x.ai/"),
            MagicMock(url="https://accounts.x.ai/device"),
        ]
        session.post.side_effect = [
            MagicMock(url="https://accounts.x.ai/consent"),
            MagicMock(url="https://accounts.x.ai/done"),
        ]
        device = {
            "device_code": "device-code",
            "user_code": "user-code",
            "verification_uri_complete": "https://accounts.x.ai/device",
            "interval": 1,
            "expires_in": 60,
        }
        form_proxy = "http://form-proxy:8080"
        with patch.object(
            sso_to_auth_json.requests, "Session", return_value=session
        ), patch.object(
            sso_to_auth_json, "_proxy_kwargs", return_value={}
        ) as proxy_kwargs, patch.object(
            sso_to_auth_json, "request_device_code", return_value=device
        ) as request_device_code, patch.object(
            sso_to_auth_json,
            "poll_token",
            return_value={"access_token": "access-token"},
        ) as poll_token:
            token = sso_to_auth_json.sso_to_token(
                "sso-cookie", quiet=True, proxy=form_proxy
            )

        self.assertEqual("access-token", token["access_token"])
        proxy_kwargs.assert_called_once_with(form_proxy)
        request_device_code.assert_called_once_with(session=session, proxy=form_proxy)
        self.assertEqual(form_proxy, poll_token.call_args.kwargs["proxy"])
        self.assertIn(
            "sso_to_token(sso, proxy=proxy)",
            inspect.getsource(grok_build_adapter._run_registration),
        )


if __name__ == "__main__":
    unittest.main()
