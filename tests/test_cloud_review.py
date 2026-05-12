from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from grocery_agent.cloud import CostcoCredentialPolicy, EnvSecretProvider, default_cloud_browser_profile
from grocery_agent.security import ApprovalTokenError, ApprovalTokenSigner


class ApprovalTokenSignerTest(unittest.TestCase):
    def test_round_trip_approval_token(self) -> None:
        signer = ApprovalTokenSigner("secret", ttl_seconds=60)
        token = signer.create("cart-123", "approve", now=100)
        payload = signer.verify(token, "approve", now=120)
        self.assertEqual(payload.cart_id, "cart-123")
        self.assertEqual(payload.action, "approve")

    def test_rejects_wrong_action_and_expired_token(self) -> None:
        signer = ApprovalTokenSigner("secret", ttl_seconds=10)
        token = signer.create("cart-123", "approve", now=100)
        with self.assertRaises(ApprovalTokenError):
            signer.verify(token, "reject", now=101)
        with self.assertRaises(ApprovalTokenError):
            signer.verify(token, "approve", now=111)

    def test_rejects_tampered_token(self) -> None:
        signer = ApprovalTokenSigner("secret")
        token = signer.create("cart-123", "approve", now=100)
        body, signature = token.split(".")
        with self.assertRaises(ApprovalTokenError):
            signer.verify(f"{body}x.{signature}", "approve", now=101)


class CloudCredentialPolicyTest(unittest.TestCase):
    def test_blocks_raw_costco_password_secret(self) -> None:
        provider = EnvSecretProvider()
        old = os.environ.get("COSTCO_PASSWORD")
        os.environ["COSTCO_PASSWORD"] = "do-not-store"
        try:
            with self.assertRaises(RuntimeError):
                CostcoCredentialPolicy(provider).assert_no_raw_password_storage()
        finally:
            if old is None:
                os.environ.pop("COSTCO_PASSWORD", None)
            else:
                os.environ["COSTCO_PASSWORD"] = old

    def test_cloud_profile_tracks_persistent_session_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = default_cloud_browser_profile(Path(tmp))
            self.assertEqual(profile.profile_dir, Path(tmp) / "chrome-profile")
            self.assertFalse(profile.has_saved_session)
            profile.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
            profile.auth_state_path.write_text("{}", encoding="utf-8")
            self.assertTrue(profile.has_saved_session)


if __name__ == "__main__":
    unittest.main()
