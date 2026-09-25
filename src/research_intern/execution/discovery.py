"""Read-only Azure resource selection using the dashboard's signed-in identity."""
from __future__ import annotations

from contextlib import contextmanager
import re
from urllib.parse import quote, urlsplit

from research_intern.domain.experiments import SliceError
from research_intern.execution.identity import ARM_SCOPE

ARM = "https://management.azure.com"
API = "2024-10-01"


class DiscoveryError(SliceError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def selection(value: dict, *, require_workspace=False):
    allowed = {"subscription_id", "resource_group", "workspace_name", "compute", "job_name"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise SliceError("Use the named Azure subscription, resource group, workspace, compute and job fields")
    for key, item in value.items():
        if not isinstance(item, str) or item and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.()-]{0,199}", item):
            raise SliceError(f"Enter a valid Azure {key.replace('_', ' ')}")
    sub = value.get("subscription_id", "")
    if sub and not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", sub):
        raise SliceError("Azure subscription ID must be a UUID, such as 12345678-1234-1234-1234-123456789abc")
    if require_workspace and any(not value.get(k) for k in ("subscription_id", "resource_group", "workspace_name")):
        raise SliceError("Select or enter the Azure subscription ID, resource group name and workspace name")
    return dict(value)


def resource_name(value):
    return str(value or "").rstrip("/").split("/")[-1].removeprefix("azureml:")


def safe_cloud_error(exc):
    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code == 403:
        return DiscoveryError("permission_denied", "Azure denied access. Select a resource you can access, or ask its administrator for permission.")
    if code == 401 or type(exc).__name__ in {"ClientAuthenticationError", "CredentialUnavailableError"}:
        return DiscoveryError("sign_in_required", "Azure sign-in expired or is unavailable. Reconnect Azure and retry.")
    if code == 404:
        return DiscoveryError("not_found", "Azure could not find that resource in the selected subscription and workspace. Check its exact name.")
    return DiscoveryError("connection_failed", "Azure could not be reached or returned an unexpected response. Check your connection and retry.")


class AzureDiscovery:
    def __init__(self, credential):
        self.credential = credential

    def _credential(self):
        credential = self.credential()
        if credential is None:
            raise DiscoveryError("sign_in_required", "Sign in to Azure before listing or checking resources.")
        return credential

    def _items(self, path):
        import requests
        credential = self._credential()
        url = ARM + path
        values, pages = [], 0
        try:
            with requests.Session() as session:
                while url:
                    # Never forward the Azure token to an arbitrary pagination host.
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or parsed.netloc != "management.azure.com":
                        raise DiscoveryError("connection_failed", "Azure returned an unexpected resource-list address.")
                    token = credential.get_token(ARM_SCOPE).token
                    response = session.get(url, headers={"Authorization": "Bearer " + token}, timeout=(10, 30), allow_redirects=False)
                    response.raise_for_status()
                    payload = response.json()
                    values.extend(payload.get("value", []))
                    pages += 1
                    if pages >= 20 or len(values) >= 2000:
                        return values[:2000], True
                    url = payload.get("nextLink")
            return values, False
        except DiscoveryError:
            raise
        except Exception as exc:
            raise safe_cloud_error(exc) from None

    def resources(self, kind, target):
        target = selection(target)
        sub, group, workspace = (target.get(k, "") for k in ("subscription_id", "resource_group", "workspace_name"))
        if kind == "subscriptions":
            path = "/subscriptions?api-version=2022-12-01"
        elif kind == "resource_groups" and sub:
            path = f"/subscriptions/{sub}/resourcegroups?api-version=2021-04-01"
        elif kind == "workspaces" and sub and group:
            path = f"/subscriptions/{sub}/resourceGroups/{quote(group)}/providers/Microsoft.MachineLearningServices/workspaces?api-version={API}"
        elif kind == "computes" and sub and group and workspace:
            path = f"/subscriptions/{sub}/resourceGroups/{quote(group)}/providers/Microsoft.MachineLearningServices/workspaces/{quote(workspace)}/computes?api-version={API}"
        else:
            raise SliceError("Select the subscription, resource group and workspace above this field first")
        values, truncated = self._items(path)
        items = []
        for value in values:
            name = value.get("subscriptionId") if kind == "subscriptions" else value.get("name")
            if not isinstance(name, str):
                continue
            detail = value.get("properties", {})
            items.append({"value": name, "label": value.get("displayName", name),
                          "location": value.get("location", ""),
                          "state": str(detail.get("provisioningState", value.get("state", ""))),
                          "type": str(detail.get("computeType", ""))})
        return {"items": items, "truncated": truncated, "category": "ok" if items else "empty",
                "message": "Select a resource, or enter its exact value below." if items else
                "No resources were returned. You can enter the exact value below and validate it."}

    @contextmanager
    def client(self, target):
        selection(target, require_workspace=True)
        from azure.ai.ml import MLClient
        from azure.core.pipeline.transport import RequestsTransport
        transport = RequestsTransport(connection_timeout=10, read_timeout=30)
        try:
            yield MLClient(self._credential(), target["subscription_id"], target["resource_group"],
                           target["workspace_name"], transport=transport, retry_total=0,
                           show_progress=False, enable_telemetry=False)
        except (DiscoveryError, SliceError):
            raise
        except Exception as exc:
            raise safe_cloud_error(exc) from None
        finally:
            transport.close()

    def validate(self, target):
        with self.client(target) as client:
            workspace = client.workspaces.get(target["workspace_name"])
            result = {"workspace_name": workspace.name, "location": workspace.location}
            if target.get("compute"):
                compute = client.compute.get(target["compute"])
                size_name = getattr(compute, "size", "")
                sizes = client.compute.list_sizes(location=compute.location)
                size = next((s for s in sizes if s.name.casefold() == size_name.casefold()), None)
                result.update(compute=compute.name, compute_size=size_name,
                              gpu_count=int(size.gpus) if size else None,
                              provisioning_state=str(getattr(compute.provisioning_state, "value", compute.provisioning_state)))
            return result

    @staticmethod
    def job_summary(job):
        created = getattr(getattr(job, "creation_context", None), "created_at", None)
        return {"name": job.name, "display_name": job.display_name or job.name,
                "experiment_name": job.experiment_name, "status": str(getattr(job.status, "value", job.status)),
                "compute": resource_name(getattr(job, "compute", "")),
                "created_at": created.isoformat() if created else None,
                "code": str(getattr(job, "code", "") or "").split("?", 1)[0][:500],
                "environment": str(getattr(job, "environment", "") or "").split("?", 1)[0][:500]}

    def jobs(self, target, experiment_name="", job_name=""):
        with self.client(target) as client:
            if job_name:
                selection({"job_name": job_name})
                return {"items": [self.job_summary(client.jobs.get(job_name))], "truncated": False}
            items, examined = [], 0
            for job in client.jobs.list():
                examined += 1
                if not experiment_name or job.experiment_name == experiment_name:
                    items.append(self.job_summary(job))
                if examined >= 500 or len(items) >= 100:
                    return {"items": items, "truncated": True}
            return {"items": items, "truncated": False}

    def job(self, target, name):
        with self.client(target) as client:
            return self.job_summary(client.jobs.get(name))

    def download(self, target, name, destination):
        with self.client(target) as client:
            client.jobs.download(name=name, download_path=str(destination), all=True)
