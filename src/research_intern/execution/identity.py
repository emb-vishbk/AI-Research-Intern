"""Backend-owned, in-memory Microsoft login; never exposed to Copilot or the UI."""
from __future__ import annotations

import os
import threading
import time

ARM_SCOPE = "https://management.azure.com/.default"
DEVELOPMENT_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_credentials: dict[str, object] = {}


class AzureSignInError(RuntimeError):
    """A safe, application-owned explanation, without provider response bodies."""


def remember_credential(subscription: str, credential) -> None:
    _credentials[subscription] = credential


def forget_credential(subscription: str) -> None:
    _credentials.pop(subscription, None)


def credential_for(subscription: str):
    if subscription in _credentials:
        return _credentials[subscription]
    from azure.identity import AzureCliCredential
    return AzureCliCredential()


class AzureBrowserCredential:
    """MSAL token cache lives only in this process; refresh is silent during jobs."""

    def __init__(self):
        import msal
        self.application = msal.PublicClientApplication(
            os.environ.get("RESEARCH_INTERN_AZURE_CLIENT_ID", DEVELOPMENT_CLIENT_ID),
            authority="https://login.microsoftonline.com/" + os.environ.get("RESEARCH_INTERN_AZURE_TENANT_ID", "organizations"),
            timeout=15)
        self.account = None
        self._guard = threading.Lock()

    def sign_in(self, update, cancelled):
        flow = self.application.initiate_device_flow(scopes=[ARM_SCOPE])
        if "user_code" not in flow:
            raise AzureSignInError("Microsoft could not start browser authorization. Check organization sign-in policy.")
        update(status="waiting", message="Open Microsoft sign-in and enter this one-time code.",
               authorization_url=flow["verification_uri"], user_code=flow["user_code"],
               expires_at=min(flow["expires_at"], time.time() + 900))
        deadline = min(flow["expires_at"], time.time() + 900)

        # MSAL documents expires_at=0 as its device-flow cancellation mechanism.
        def stop_polling(_):
            if cancelled.is_set() or time.time() >= deadline:
                flow["expires_at"] = 0
                return True
            return False

        result = self.application.acquire_token_by_device_flow(flow, exit_condition=stop_polling)
        if cancelled.is_set():
            return None
        if "access_token" not in result:
            raise AzureSignInError("Microsoft authorization expired, was declined, or was blocked by organization policy. Try signing in again.")
        accounts = self.application.get_accounts()
        if not accounts:
            raise AzureSignInError("Microsoft did not return a reusable account session. Try signing in again.")
        self.account = accounts[0]
        return self.account.get("username", "Microsoft account")

    def get_token(self, *scopes, **kwargs):
        from azure.core.credentials import AccessToken
        from azure.core.exceptions import ClientAuthenticationError
        with self._guard:
            result = self.application.acquire_token_silent(
                list(scopes), account=self.account, claims_challenge=kwargs.get("claims"))
        if not result or "access_token" not in result:
            raise ClientAuthenticationError("Azure sign-in expired. Reconnect Azure from Research Intern.")
        return AccessToken(result["access_token"], int(time.time()) + int(result.get("expires_in", 300)))

    def close(self):
        # MLClient gateways are short-lived; the connection owns the token cache.
        pass
